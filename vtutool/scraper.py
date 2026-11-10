"""Collect results from the VTU results site (Selenium + OCR) and parse the result page."""
from __future__ import annotations

import queue
import random
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

import pandas as pd
from bs4 import BeautifulSoup

from .captcha import TesseractMissing, solve_captcha
from .utils import load_credit_memory, log_dir

Emit = Callable[[str, object], None]

MAX_CAPTCHA_TRIES = 25
SEM_RE = re.compile(r"^semester\W*(\d+)\b", re.I)
CELL_RE = re.compile(r"^divTable(Cell|Head)$")


class BrowserStartError(RuntimeError):
    pass


# =============================================================================== parsing
def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _value_after(lines: list[str], label: str) -> str:
    low_label = label.lower()
    for i, line in enumerate(lines):
        if line.lower().startswith(low_label):
            rest = line[len(label):].lstrip(" :\t").strip()
            if rest:
                return rest
            for nxt in lines[i + 1:i + 4]:
                nxt = nxt.strip(" :")
                if nxt:
                    return nxt
    return ""


def parse_result_page(html: str) -> tuple[str, str, list[dict]]:
    """Return (usn, student_name, rows) from one VTU result page.

    Unlike the original (one absolute XPath that broke whenever the site changed), this
    looks for the labels "University Seat Number" / "Student Name" in the page text and for
    the "Semester : N" headings followed by the marks table.
    """
    soup = BeautifulSoup(html, "html.parser")
    lines = [_clean(x) for x in soup.get_text("\n").split("\n")]
    lines = [x for x in lines if x]
    usn = _value_after(lines, "University Seat Number").upper()
    name = _value_after(lines, "Student Name").upper()

    rows: list[dict] = []
    for div in soup.find_all("div"):
        if div.find("div") is not None:
            continue
        m = SEM_RE.match(_clean(div.get_text(" ")))
        if not m:
            continue
        sem = m.group(1)

        table = div.find_next_sibling("div")
        if table is None or table.find(class_="divTableRow") is None:
            first = div.find_next(class_="divTableRow")
            table = first.parent if first is not None else None
        if table is None:
            continue

        grid = []
        for row in table.find_all(class_="divTableRow"):
            cells = [_clean(c.get_text(" ")) for c in row.find_all(class_=CELL_RE)]
            if cells:
                grid.append(cells)
        if len(grid) < 2:
            continue

        header = grid[0]
        seen: dict[str, int] = {}
        keys = []
        for h in header:                      # keep header names unique
            seen[h] = seen.get(h, 0) + 1
            keys.append(h if seen[h] == 1 else f"{h} {seen[h]}")
        code_i = next((i for i, h in enumerate(header) if "code" in h.lower()), 0)
        name_i = next((i for i, h in enumerate(header) if "name" in h.lower()), 1)

        for cells in grid[1:]:
            if len(cells) != len(header):
                continue
            rec = {"usn": usn, "student_name": name, "sem": sem,
                   "subject_code": cells[code_i], "subject_name": cells[name_i]}
            for i, (k, v) in enumerate(zip(keys, cells)):
                if i not in (code_i, name_i):
                    rec[k] = v
            rows.append(rec)
    return usn, name, rows


def parse_numbers(spec: str) -> list[int]:
    """'1-10,13,15,20-30' -> sorted list of ints. Raises ValueError on bad input."""
    out: set[int] = set()
    for part in spec.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            lo, hi = sorted((int(a), int(b)))
            out.update(range(lo, hi + 1))
        else:
            out.add(int(part))
    if not out or min(out) < 0 or max(out) > 999:
        raise ValueError("USN numbers must be between 0 and 999")
    return sorted(out)


# =============================================================================== config / summary
@dataclass
class ScrapeConfig:
    usn_prefix: str            # first 7 characters, e.g. 1CR24EC
    numbers: list[int]
    exam_sem: int
    url: str
    out_root: Path
    retries: int = 5
    delay: float = 5.0         # wait after a network error
    pause: float = 1.0         # polite pause between students (per browser)
    show_browser: bool = False
    workers: int = 1           # parallel Chrome windows (1-10). More = faster but higher IP-block risk

    @property
    def is_reval(self) -> bool:
        return "RV" in urlparse(self.url).path.upper()


@dataclass
class ScrapeSummary:
    collected: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    files: list[Path] = field(default_factory=list)
    folder: Path | None = None
    stop_reason: str = ""


# =============================================================================== browser
class VTUSession:
    def __init__(self, url: str, show_browser: bool):
        self.url = url
        self.show_browser = show_browser
        self.driver = None

    def start(self) -> None:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service

            opts = Options()
            opts.add_experimental_option("excludeSwitches", ["enable-logging"])
            opts.unhandled_prompt_behavior = "ignore"
            opts.add_argument("--window-size=1280,1000")
            opts.add_argument("--log-level=3")
            opts.page_load_strategy = "eager"      # do not wait for images/fonts: faster page loads
            if not self.show_browser:
                opts.add_argument("--headless=new")
            try:
                service = Service(log_output=subprocess.DEVNULL)
            except TypeError:
                service = Service()
            if sys.platform == "win32":
                service.creation_flags = subprocess.CREATE_NO_WINDOW   # no black console window
            self.driver = webdriver.Chrome(options=opts, service=service)
            self.driver.set_page_load_timeout(30)
        except Exception as exc:
            raise BrowserStartError(
                "Could not start Google Chrome.\n\n"
                "- Make sure Google Chrome is installed and up to date.\n"
                "- The first run needs internet so Selenium can download the matching chromedriver.\n\n"
                f"Technical detail: {type(exc).__name__}: {str(exc)[:300]}"
            ) from exc

    def stop(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    def alive(self) -> bool:
        try:
            _ = self.driver.title
            return True
        except Exception:
            return False

    def restart(self) -> None:
        self.stop()
        self.start()

    def load(self) -> None:
        self.driver.get(self.url)

    def enter_usn(self, usn: str) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.wait import WebDriverWait

        box = WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.NAME, "lns")))
        box.clear()
        box.send_keys(usn)

    def captcha_png(self) -> bytes:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.wait import WebDriverWait

        img = WebDriverWait(self.driver, 20).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, '[alt="CAPTCHA code"]')))
        return img.screenshot_as_png

    def submit(self, captcha: str) -> None:
        from selenium.webdriver.common.by import By

        box = self.driver.find_element(By.NAME, "captchacode")
        box.clear()
        box.send_keys(captcha)
        self.driver.find_element(By.ID, "submit").click()

    def wait_outcome(self, timeout: float = 15.0) -> tuple[str, str]:
        """Wait for either a JavaScript alert or the result page. -> ('alert'|'result'|'timeout', text/html)"""
        from selenium.common.exceptions import NoAlertPresentException, UnexpectedAlertPresentException

        end = time.time() + timeout
        while time.time() < end:
            try:
                alert = self.driver.switch_to.alert
                text = alert.text or ""
                alert.accept()
                return "alert", text
            except NoAlertPresentException:
                pass
            try:
                html = self.driver.page_source
            except UnexpectedAlertPresentException:
                continue
            if "divTableRow" in html:
                return "result", html
            time.sleep(0.12)
        return "timeout", ""


# =============================================================================== fetching
def _wait(stop: threading.Event, secs: float) -> None:
    stop.wait(secs)


def _fetch_one(sess: VTUSession, usn: str, cfg: ScrapeConfig, log: Callable[[str], None],
               stop: threading.Event):
    """-> (status, payload). status: ok | skip | failed | blocked | abort | stopped"""
    errors = captcha_fails = parse_fails = 0
    while not stop.is_set():
        if errors >= cfg.retries:
            return "failed", f"gave up after {errors} errors"
        if captcha_fails >= MAX_CAPTCHA_TRIES:
            return "failed", "captcha could not be read"
        try:
            sess.load()
            sess.enter_usn(usn)
            png = sess.captcha_png()
            try:
                text = solve_captcha(png)
            except TesseractMissing as exc:
                return "abort", str(exc)
            if len(text) != 6:
                captcha_fails += 1
                try:                                    # keep a few failed captchas so the OCR can be tuned
                    cdir = log_dir() / "captchas"
                    cdir.mkdir(parents=True, exist_ok=True)
                    if len(list(cdir.glob("*.png"))) < 60:
                        (cdir / f"{usn}_{text or 'empty'}_{captcha_fails}.png").write_bytes(png)
                except Exception:
                    pass
                log(f"  {usn}: captcha not readable ({text!r}) - reloading [{captcha_fails}/{MAX_CAPTCHA_TRIES}]")
                continue
            sess.submit(text)
            kind, data = sess.wait_outcome()
        except Exception as exc:                       # browser closed, timeout, network drop ...
            errors += 1
            log(f"  {usn}: browser/network problem ({type(exc).__name__}) - retry {errors}/{cfg.retries} "
                f"in {cfg.delay:.0f}s")
            _wait(stop, cfg.delay)
            if not sess.alive():
                try:
                    sess.restart()
                except BrowserStartError as bexc:
                    return "abort", str(bexc)
            continue

        if kind == "alert":
            low = data.strip().lower()
            if not low:
                return "blocked", None
            if "captcha" in low:
                captcha_fails += 1
                continue
            if "seat number" in low or "not available" in low:
                return "skip", "USN not available / invalid"
            if "not applied for reval" in low or "awaited" in low:
                return "skip", "no revaluation result for this USN"
            errors += 1
            log(f"  {usn}: site said '{data.strip()}' - retry {errors}/{cfg.retries}")
            _wait(stop, cfg.delay)
        elif kind == "result":
            page_usn, name, rows = parse_result_page(data)
            if rows:
                if not page_usn:
                    for r in rows:
                        r["usn"] = usn
                return "ok", (page_usn or usn, name, rows)
            parse_fails += 1
            dump = log_dir() / f"debug_{usn}.html"
            dump.write_text(data, encoding="utf-8", errors="ignore")
            log(f"  {usn}: result page opened but could not be read (saved {dump.name} in the log folder)")
            if parse_fails >= 2:
                return "abort", ("The website layout seems to have changed, so the marks table could not be "
                                 f"read.\nPlease send the file:\n{dump}\nto the developer/Claude to update the parser.")
        else:
            errors += 1
            log(f"  {usn}: no response from the site - retry {errors}/{cfg.retries}")
            _wait(stop, cfg.delay)
    return "stopped", None


# =============================================================================== saving
def _output_folder(cfg: ScrapeConfig) -> Path:
    batch, branch = cfg.usn_prefix[3:5], cfg.usn_prefix[-2:]
    return cfg.out_root / f"{batch} batch semester {cfg.exam_sem} {branch} VTU results"


def save_rows(rows: list[dict], cfg: ScrapeConfig, final: bool = True) -> list[Path]:
    """Write collected rows to CSV. Final files: one per (regular|arrear, semester). Autosave: one file."""
    if not rows:
        return []
    folder = _output_folder(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    df.insert(3, "exam_sem", str(cfg.exam_sem))
    kind = ["reval_" if cfg.is_reval else ""] * len(df)
    df.insert(4, "source", [k + ("regular" if s == str(cfg.exam_sem) else "arrear")
                            for k, s in zip(kind, df["sem"])])
    autosave = folder / "_autosave_partial.csv"
    if not final:
        df.to_csv(autosave, index=False, encoding="utf-8-sig")
        return [autosave]

    written: list[Path] = []
    for (source, sem), g in df.groupby(["source", "sem"], sort=True):
        usns = sorted(g["usn"].unique())
        label = source.replace("reval_", "Reval ").replace("regular", "Regular").replace("arrear", "Arrear")
        path = folder / f"Raw {label} semester {sem} {usns[0]} to {usns[-1]}.csv"
        g.to_csv(path, index=False, encoding="utf-8-sig")
        written.append(path)
    if autosave.exists():
        autosave.unlink()

    if not cfg.is_reval:
        credit_path = folder / f"Credit info {cfg.usn_prefix[-2:]}.csv"
        if not credit_path.exists():
            memory = load_credit_memory()
            subj = (df[df["sem"] == str(cfg.exam_sem)][["subject_code", "subject_name"]]
                    .drop_duplicates("subject_code").sort_values("subject_code"))
            subj.insert(2, "sem", str(cfg.exam_sem))
            subj["credits"] = [memory.get(c, {}).get("credits", "") for c in subj["subject_code"]]
            subj["max_marks"] = [memory.get(c, {}).get("max_marks", 100) for c in subj["subject_code"]]
            subj.to_csv(credit_path, index=False, encoding="utf-8-sig")
            written.append(credit_path)
    return written


# =============================================================================== main runner
def run_scrape(cfg: ScrapeConfig, emit: Emit, stop: threading.Event) -> ScrapeSummary:
    """Collect all USNs using cfg.workers Chrome windows. `stop` is also set on fatal problems."""
    log = lambda m: emit("log", m)
    summary = ScrapeSummary(folder=_output_folder(cfg))
    rows: list[dict] = []
    lock = threading.Lock()
    work: queue.Queue = queue.Queue()
    for n in cfg.numbers:
        work.put(n)
    total = len(cfg.numbers)
    st = {"done": 0, "fail": 0, "skip": 0}

    def fatal(reason: str) -> None:
        with lock:
            if not summary.stop_reason:
                summary.stop_reason = reason
        log(reason)
        stop.set()

    def worker(idx: int) -> None:
        if idx:
            stop.wait(2.0 * idx)                       # stagger the browser start-ups
        sess = VTUSession(cfg.url, cfg.show_browser)
        try:
            sess.start()
        except BrowserStartError as exc:
            fatal(str(exc))
            return
        try:
            while not stop.is_set():
                try:
                    n = work.get_nowait()
                except queue.Empty:
                    break
                usn = f"{cfg.usn_prefix}{n:03d}"
                status, payload = _fetch_one(sess, usn, cfg, log, stop)
                extra_pause = 0.0
                with lock:
                    if status in ("ok", "skip", "failed"):
                        st["done"] += 1
                    if status == "ok":
                        page_usn, name, new_rows = payload
                        rows.extend(new_rows)
                        summary.collected.append(page_usn)
                        st["fail"] = st["skip"] = 0
                        log(f"[{st['done']}/{total}] {page_usn} {name} - {len(new_rows)} subject rows")
                    elif status == "skip":
                        summary.skipped.append((usn, payload))
                        st["skip"] += 1
                        log(f"[{st['done']}/{total}] {usn} skipped: {payload}")
                        if st["skip"] > 5:
                            st["skip"] = 0
                            extra_pause = 3.0
                            log("Short cool-down to avoid an IP block ...")
                    elif status == "failed":
                        summary.failed.append((usn, payload))
                        st["fail"] += 1
                        log(f"[{st['done']}/{total}] {usn} FAILED: {payload}")
                    if status in ("ok", "skip", "failed"):
                        emit("progress", (st["done"], total))
                    if status == "ok" and st["done"] % 10 == 0:
                        try:
                            save_rows(rows, cfg, final=False)    # crash insurance
                        except Exception:
                            pass
                if status == "failed" and st["fail"] >= 3:
                    fatal("3 students in a row failed - check your internet and try again later")
                elif status == "blocked":
                    fatal("The site blocked this IP address (too many requests). Wait a while, or use a "
                          "different internet connection / mobile hotspot.")
                elif status == "abort":
                    fatal(str(payload))
                stop.wait(cfg.pause + random.uniform(0, 0.4) + extra_pause)
        finally:
            sess.stop()

    n_workers = max(1, min(10, cfg.workers, total))
    log(f"Starting {n_workers} Chrome window(s) ...")
    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(n_workers)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        if stop.is_set() and not summary.stop_reason:
            summary.stop_reason = "Stopped by user"
            log("Stopped by user.")
        summary.collected.sort()
        try:
            summary.files = save_rows(rows, cfg, final=True)
        except Exception as exc:
            summary.stop_reason += f"\nCould not save files: {exc}"
            log(f"Could not save files: {exc}")
            raise
    return summary
