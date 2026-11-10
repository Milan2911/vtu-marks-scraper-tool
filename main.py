"""VTU Marks Scraper Tool V1.0 - GUI entry point.

Pure tkinter (no extra packages): a dark sidebar, card based pages, custom drawn buttons / switch /
progress bar, a colour-coded activity log and a searchable results table.
"""
from __future__ import annotations

import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import tkinter as tk
import traceback
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from vtutool.utils import (APP_NAME, APP_TITLE, APP_VERSION, asset_path, configure_tesseract, load_credit_memory,
                           load_settings, log_dir, save_credit_memory, save_settings, setup_logging)

log = setup_logging()

# ======================================================================================= design tokens
C = {
    "bg": "#F3F5F9", "card": "#FFFFFF", "border": "#E2E8F0",
    "text": "#0F172A", "muted": "#64748B", "faint": "#94A3B8",
    "accent": "#4F46E5", "accent_hover": "#4338CA", "accent_press": "#3730A3", "accent_soft": "#EEF2FF",
    "success": "#059669", "success_soft": "#ECFDF5", "warn": "#B45309", "warn_soft": "#FEF3C7",
    "danger": "#DC2626", "danger_hover": "#B91C1C", "danger_press": "#991B1B", "danger_soft": "#FEE2E2",
    "input_bg": "#F8FAFC", "input_border": "#CBD5E1", "focus": "#6366F1",
    "sidebar": "#0B1220", "sidebar_hover": "#151E31", "sidebar_active": "#1B2740",
    "sidebar_text": "#94A3B8", "sidebar_badge": "#1E293B",
    "log_bg": "#0B1220", "log_head": "#111A2E", "log_fg": "#CBD5E1",
}
FONT = "Segoe UI"
MONO = "Consolas"
_SCALE = 1.0  # set from the real screen DPI when the window is created


def px(n: float) -> int:
    """Scale a pixel size for high-DPI screens (fonts already scale by themselves)."""
    return int(round(n * _SCALE))


def F(size: int = 10, weight: str = "normal"):
    return (FONT, size, weight)


# ======================================================================================= helpers
HELP_SECTIONS = [
    ("Quick start", "num", [
        "Open Collect marks. Enter the USN prefix (first 7 characters, e.g. 1CR24EC), the USN numbers "
        "(1-120 or 1-10,13,15), the semester and paste the result page link (ends with index.php).",
        "Press Start collecting. The raw CSV files and a Credit info file are saved in your output folder.",
        "When revaluation results are published, collect again with the reval link (it contains 'RV') "
        "using the same USN range and semester.",
        "Open Analyze & SGPA, add the raw files, press Analyze and enter the subject credits when asked "
        "(they are remembered for next time).",
        "Open the Excel report from the output folder.",
    ]),
    ("Collecting tips", "dot", [
        "If the link contains 'RV' the app automatically collects REVALUATION results.",
        "Collect regular results first. Use Stop & save at any time - everything collected so far is saved.",
        "Google Chrome and an internet connection are needed while collecting.",
        "Please do not hammer the site: keep a pause of 1 second or more between students.",
    ]),
    ("What you get from Analyze", "dot", [
        "One Excel file with Summary, Student Results, Subject Analysis (with chart) and SGPA.",
        "When revaluation files are included: Reval Summary, Reval Details and SGPA Before vs After.",
        "Add the Raw Regular file (or Raw Arrear alone for makeup exams). Raw Reval and Credit info are optional.",
    ]),
    ("Good to know", "dot", [
        "SGPA grade table (VTU 2022 scheme): 90+ = 10, 80+ = 9, 70+ = 8, 60+ = 7, 55+ = 6, 50+ = 5, 40+ = 4.",
        "Logs for troubleshooting are in the 'logs' folder inside  ~/" + APP_NAME + ".",
    ]),
]


def has_internet(timeout: float = 6.0) -> bool:
    try:
        socket.create_connection(("results.vtu.ac.in", 443), timeout=timeout).close()
        return True
    except OSError:
        try:
            socket.create_connection(("1.1.1.1", 443), timeout=timeout).close()
            return True
        except OSError:
            return False


def fmt_duration(sec: float) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h} h {m:02d} min"
    if m:
        return f"{m} min {s:02d} s"
    return f"{s} s"


def log_level(text: str) -> str:
    """Pick the colour of an activity-log line."""
    t = text.lower().strip()
    if t.startswith("warning"):
        return "warn"
    if any(k in t for k in ("failed", "error", "could not", "cannot")):
        return "err"
    if any(k in t for k in ("captcha not readable", "retry", "skipped", "cool-down", "stopp", "no response")):
        return "warn"
    if t.startswith(("done", "saved")) or "reachable" in t or "subject rows" in t:
        return "ok"
    if t.startswith(("---", "starting", "checking")):
        return "head"
    return "info"


def file_tag(path: str) -> str:
    n = Path(path).name.lower()
    for key, tag in (("reval", "REVAL"), ("arrear", "ARREAR"), ("regular", "REGULAR"), ("credit", "CREDITS")):
        if key in n:
            return tag
    return "CSV"


def open_folder(path) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


def rounded(cv, x1, y1, x2, y2, r, **kw):
    """Rounded rectangle on a canvas."""
    r = max(0, min(r, (x2 - x1) / 2, (y2 - y1) / 2))
    pts = [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y1 + r, x2, y2 - r, x2, y2 - r,
           x2, y2, x2 - r, y2, x2 - r, y2, x1 + r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r,
           x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


# ======================================================================================= widgets
class Button(tk.Canvas):
    """Flat rounded button with hover / pressed / disabled looks. Works with .config(state=..., text=...)."""

    STYLES = {
        "primary": dict(bg=C["accent"], hover=C["accent_hover"], press=C["accent_press"], fg="#FFFFFF", border=None,
                        dis_bg="#C7D2FE", dis_fg="#FFFFFF", dis_border=None),
        "secondary": dict(bg="#FFFFFF", hover="#F1F5F9", press="#E2E8F0", fg=C["text"], border=C["input_border"],
                          dis_bg="#F8FAFC", dis_fg=C["faint"], dis_border="#E2E8F0"),
        "danger": dict(bg=C["danger"], hover=C["danger_hover"], press=C["danger_press"], fg="#FFFFFF", border=None,
                       dis_bg="#FECACA", dis_fg="#FFFFFF", dis_border=None),
        "ghost": dict(bg=None, hover="#E6EAF2", press="#D9DFEA", fg=C["muted"], border=None,
                      dis_bg=None, dis_fg=C["faint"], dis_border=None),
        "ghost_dark": dict(bg=None, hover="#1E293B", press="#273449", fg="#CBD5E1", border=None,
                           dis_bg=None, dis_fg="#475569", dis_border=None),
    }

    def __init__(self, master, text, command=None, style="secondary", width=None, height=38, font_size=10):
        self._parent_bg = master.cget("bg")
        self._font = tkfont.Font(family=FONT, size=font_size, weight="bold")
        self._text = text
        self._command = command
        self._kind = style
        self._bw = width or (self._font.measure(text) + px(36))
        self._h = px(height)
        self._btn_state = "normal"
        self._hover = False
        self._pressed = False
        super().__init__(master, width=self._bw, height=self._h, bg=self._parent_bg, highlightthickness=0, bd=0,
                         cursor="hand2")
        self.bind("<Configure>", self._on_resize)
        self.bind("<Enter>", lambda e: self._set(hover=True))
        self.bind("<Leave>", lambda e: self._set(hover=False, pressed=False))
        self.bind("<ButtonPress-1>", lambda e: self._set(pressed=True))
        self.bind("<ButtonRelease-1>", self._release)
        self._draw()

    def _on_resize(self, e):
        self._bw, self._h = e.width, e.height
        self._draw()

    def _set(self, **kw):
        if self._btn_state == "disabled":
            return
        self._hover = kw.get("hover", self._hover)
        self._pressed = kw.get("pressed", self._pressed)
        self._draw()

    def _release(self, e):
        was = self._pressed
        self._set(pressed=False)
        if was and self._btn_state != "disabled" and 0 <= e.x <= self._bw and 0 <= e.y <= self._h and self._command:
            self._command()

    def _draw(self):
        self.delete("all")
        s = self.STYLES[self._kind]
        if self._btn_state == "disabled":
            bg, fg, border = s["dis_bg"], s["dis_fg"], s["dis_border"]
        else:
            bg = s["press"] if self._pressed else s["hover"] if self._hover else s["bg"]
            fg, border = s["fg"], s["border"]
        if bg is None:
            bg = self._parent_bg
        w, h = max(self._bw, 4), max(self._h, 4)
        rounded(self, 1, 1, w - 1, h - 1, px(9), fill=bg, outline=border or bg)
        self.create_text(w / 2, h / 2, text=self._text, fill=fg, font=self._font)

    def configure(self, cnf=None, **kw):
        if "state" in kw:
            self._btn_state = kw.pop("state")
            super().configure(cursor="arrow" if self._btn_state == "disabled" else "hand2")
            self._draw()
        if "text" in kw:
            self._text = kw.pop("text")
            self._draw()
        if "command" in kw:
            self._command = kw.pop("command")
        if cnf or kw:
            return super().configure(cnf, **kw)
        return None

    config = configure


class Pill(tk.Canvas):
    """Small rounded status chip."""

    KINDS = {"neutral": ("#E8ECF3", "#475569"), "accent": (C["accent_soft"], C["accent"]),
             "ok": (C["success_soft"], C["success"]), "warn": (C["warn_soft"], C["warn"]),
             "err": (C["danger_soft"], C["danger"])}

    def __init__(self, master, text="", kind="neutral"):
        self._font = tkfont.Font(family=FONT, size=8, weight="bold")
        self._h = px(24)
        super().__init__(master, height=self._h, width=px(40), bg=master.cget("bg"), highlightthickness=0, bd=0)
        self.set(text, kind)

    def set(self, text, kind="neutral"):
        bg, fg = self.KINDS[kind]
        w = self._font.measure(text) + px(24)
        self.delete("all")
        super().configure(width=w)
        rounded(self, 0, 0, w, self._h, self._h / 2, fill=bg, outline=bg)
        self.create_text(w / 2, self._h / 2, text=text, fill=fg, font=self._font)


class Switch(tk.Frame):
    """On/off toggle bound to a BooleanVar."""

    def __init__(self, master, text, variable, bg=None):
        bg = bg or C["card"]
        super().__init__(master, bg=bg, cursor="hand2")
        self._var = variable
        self._bw, self._h = px(42), px(24)
        self._cv = tk.Canvas(self, width=self._bw, height=self._h, bg=bg, highlightthickness=0, bd=0, cursor="hand2")
        self._cv.pack(side="left")
        lbl = tk.Label(self, text=text, bg=bg, fg=C["text"], font=F(10), cursor="hand2")
        lbl.pack(side="left", padx=(px(10), 0))
        for w in (self._cv, lbl, self):
            w.bind("<Button-1>", lambda e: self._var.set(not self._var.get()))
        variable.trace_add("write", lambda *a: self._draw())
        self._draw()

    def _draw(self):
        on = bool(self._var.get())
        cv, w, h = self._cv, self._bw, self._h
        cv.delete("all")
        rounded(cv, 1, 1, w - 1, h - 1, h / 2, fill=C["accent"] if on else "#CBD5E1", outline="")
        pad = px(3)
        d = h - 2 * pad
        x = w - pad - d if on else pad
        cv.create_oval(x, pad, x + d, pad + d, fill="#FFFFFF", outline="")


class ProgressBar(tk.Canvas):
    def __init__(self, master, bg=None, height=10):
        super().__init__(master, height=px(height), bg=bg or C["card"], highlightthickness=0, bd=0)
        self._value = 0.0
        self.bind("<Configure>", lambda e: self._draw())

    def set(self, value: float):
        self._value = max(0.0, min(100.0, float(value)))
        self._draw()

    def _draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 4 or h < 4:
            return
        rounded(self, 0, 0, w, h, h / 2, fill="#E2E8F0", outline="")
        if self._value > 0:
            fw = max(h, w * self._value / 100.0)
            rounded(self, 0, 0, fw, h, h / 2, fill=C["success"] if self._value >= 100 else C["accent"], outline="")


class Card(tk.Frame):
    """White panel with a thin border and an optional title. Put children into .content."""

    def __init__(self, master, title=None, subtitle=None, pad=18):
        super().__init__(master, bg=C["card"], highlightthickness=1, highlightbackground=C["border"],
                         highlightcolor=C["border"], bd=0)
        body = tk.Frame(self, bg=C["card"])
        body.pack(fill="both", expand=True, padx=px(pad), pady=px(pad))
        if title:
            tk.Label(body, text=title, font=F(11, "bold"), bg=C["card"], fg=C["text"], anchor="w").pack(fill="x")
            if subtitle:
                tk.Label(body, text=subtitle, font=F(9), bg=C["card"], fg=C["muted"], anchor="w",
                         justify="left").pack(fill="x", pady=(px(2), 0))
            tk.Frame(body, bg=C["card"], height=px(10)).pack(fill="x")
        self.content = tk.Frame(body, bg=C["card"])
        self.content.pack(fill="both", expand=True)


class EntryBox(tk.Frame):
    """Text entry with a 1px border that turns indigo on focus and red on error."""

    def __init__(self, master, variable, width=8, mono=False):
        super().__init__(master, bg=C["input_border"])
        self._inner = tk.Frame(self, bg=C["input_bg"])
        self._inner.pack(fill="both", expand=True, padx=1, pady=1)
        self.entry = tk.Entry(self._inner, textvariable=variable, width=width, relief="flat", bd=0,
                              highlightthickness=0, bg=C["input_bg"], fg=C["text"], insertbackground=C["text"],
                              selectbackground=C["accent"], selectforeground="#FFFFFF",
                              font=(MONO, 10) if mono else F(10))
        self.entry.pack(fill="x", padx=px(10), pady=px(7))
        self._focus = False
        self._err = False
        self.entry.bind("<FocusIn>", lambda e: self._focus_set(True))
        self.entry.bind("<FocusOut>", lambda e: self._focus_set(False))

    def _focus_set(self, on):
        self._focus = on
        self._paint()

    def set_error(self, flag: bool):
        if flag != self._err:
            self._err = flag
            self._paint()

    def _paint(self):
        border = C["danger"] if self._err else C["focus"] if self._focus else C["input_border"]
        fill = "#FFFFFF" if self._focus else C["input_bg"]
        self.config(bg=border)
        self._inner.config(bg=fill)
        self.entry.config(bg=fill)


class Field(tk.Frame):
    """Label + entry (+ optional hint and button)."""

    def __init__(self, master, label, variable, hint=None, width=8, button=None, mono=False):
        super().__init__(master, bg=C["card"])
        tk.Label(self, text=label, bg=C["card"], fg=C["muted"], font=F(9, "bold"), anchor="w").pack(
            fill="x", pady=(0, px(4)))
        row = tk.Frame(self, bg=C["card"])
        row.pack(fill="x")
        self.box = EntryBox(row, variable, width=width, mono=mono)
        self.box.pack(side="left", fill="x", expand=True)
        if button:
            Button(row, button[0], button[1], style="secondary", height=36).pack(side="left", padx=(px(8), 0))
        if hint:
            tk.Label(self, text=hint, bg=C["card"], fg=C["faint"], font=F(8), anchor="w", justify="left").pack(
                fill="x", pady=(px(4), 0))
        variable.trace_add("write", lambda *a: self.box.set_error(False))

    def set_error(self, flag: bool = True):
        self.box.set_error(flag)


class Tile(tk.Frame):
    """Small statistic tile: big value + caption."""

    def __init__(self, master, caption, value="-"):
        super().__init__(master, bg=C["input_bg"], highlightthickness=1, highlightbackground=C["border"], bd=0)
        self._v = tk.Label(self, text=value, font=F(15, "bold"), bg=C["input_bg"], fg=C["text"], anchor="w")
        self._v.pack(fill="x", padx=px(14), pady=(px(10), 0))
        tk.Label(self, text=caption.upper(), font=F(8, "bold"), bg=C["input_bg"], fg=C["muted"], anchor="w").pack(
            fill="x", padx=px(14), pady=(0, px(10)))

    def set(self, value):
        self._v.config(text=value)


class ScrollFrame(tk.Frame):
    """Vertically scrollable area. Put children into .inner."""

    def __init__(self, master, bg=None, style="Modern.Vertical.TScrollbar"):
        bg = bg or C["bg"]
        super().__init__(master, bg=bg)
        self.on_resize = None
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview, style=style)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)
        self.inner.bind("<Enter>", self._bind_wheel)
        self.inner.bind("<Leave>", self._unbind_wheel)

    def _on_canvas(self, e):
        self.canvas.itemconfigure(self._win, width=e.width)
        if self.on_resize:
            self.on_resize(e.width)

    def _pointer_inside(self) -> bool:
        try:
            w = self.winfo_containing(*self.winfo_pointerxy())
        except (KeyError, tk.TclError):
            return False
        while w is not None:
            if w is self:
                return True
            w = getattr(w, "master", None)
        return False

    def _bind_wheel(self, _e=None):
        self.bind_all("<MouseWheel>", self._wheel)
        self.bind_all("<Button-4>", self._wheel)
        self.bind_all("<Button-5>", self._wheel)

    def _unbind_wheel(self, _e=None):
        if self._pointer_inside():
            return
        self.unbind_all("<MouseWheel>")
        self.unbind_all("<Button-4>")
        self.unbind_all("<Button-5>")

    def _wheel(self, e):
        if self.inner.winfo_reqheight() <= self.canvas.winfo_height():
            return
        up = getattr(e, "num", 0) == 4 or getattr(e, "delta", 0) > 0
        self.canvas.yview_scroll(-1 if up else 1, "units")


class LogView(tk.Frame):
    """Dark, colour-coded, time-stamped activity log."""

    def __init__(self, master):
        super().__init__(master, bg=C["log_bg"])
        self.text = tk.Text(self, bg=C["log_bg"], fg=C["log_fg"], font=(MONO, 9), wrap="word", relief="flat", bd=0,
                            highlightthickness=0, padx=px(14), pady=px(10), state="disabled",
                            spacing1=px(1), spacing3=px(2), cursor="arrow", height=6)
        sb = ttk.Scrollbar(self, orient="vertical", command=self.text.yview, style="Dark.Vertical.TScrollbar")
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("ts", foreground="#475569")
        self.text.tag_configure("info", foreground=C["log_fg"])
        self.text.tag_configure("ok", foreground="#34D399")
        self.text.tag_configure("warn", foreground="#FBBF24")
        self.text.tag_configure("err", foreground="#F87171")
        self.text.tag_configure("head", foreground="#A5B4FC", font=(MONO, 9, "bold"))

    def add(self, msg: str):
        t = self.text
        t.config(state="normal")
        t.insert("end", datetime.now().strftime("%H:%M:%S  "), "ts")
        t.insert("end", msg + "\n", log_level(msg))
        t.see("end")
        t.config(state="disabled")

    def clear(self):
        self.text.config(state="normal")
        self.text.delete("1.0", "end")
        self.text.config(state="disabled")


class NavItem(tk.Frame):
    """Sidebar entry with a badge and an accent bar when active."""

    def __init__(self, master, badge, text, command):
        super().__init__(master, bg=C["sidebar"], cursor="hand2")
        self._active = False
        self._hover = False
        self._bar = tk.Frame(self, width=px(4), bg=C["sidebar"])
        self._bar.pack(side="left", fill="y")
        self._badge = tk.Label(self, text=badge, width=2, font=F(9, "bold"), bg=C["sidebar_badge"],
                               fg=C["sidebar_text"], cursor="hand2")
        self._badge.pack(side="left", padx=(px(16), px(12)), pady=px(12))
        self._label = tk.Label(self, text=text, font=F(10, "bold"), bg=C["sidebar"], fg=C["sidebar_text"],
                               anchor="w", cursor="hand2")
        self._label.pack(side="left", fill="x", expand=True)
        self._parts = (self, self._bar, self._badge, self._label)
        for w in self._parts:
            w.bind("<Button-1>", lambda e: command())
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)

    def _enter(self, _e):
        self._hover = True
        self._paint()

    def _leave(self, _e):
        try:
            w = self.winfo_containing(*self.winfo_pointerxy())
        except (KeyError, tk.TclError):
            w = None
        if w in self._parts:
            return
        self._hover = False
        self._paint()

    def set_active(self, on: bool):
        self._active = on
        self._paint()

    def _paint(self):
        bg = C["sidebar_active"] if self._active else C["sidebar_hover"] if self._hover else C["sidebar"]
        self.config(bg=bg)
        self._bar.config(bg=C["accent"] if self._active else bg)
        self._badge.config(bg=C["accent"] if self._active else C["sidebar_badge"],
                           fg="#FFFFFF" if self._active else C["sidebar_text"])
        self._label.config(bg=bg, fg="#FFFFFF" if self._active else C["sidebar_text"])


# ======================================================================================= dialogs
class CreditsDialog(tk.Toplevel):
    """Ask for credits (and max marks) of every subject. result = {code: Credit} or None."""

    def __init__(self, parent, subjects, existing):
        super().__init__(parent)
        from vtutool.analysis import Credit
        self._Credit = Credit
        self.title("Subject credits")
        self.configure(bg=C["bg"])
        self.transient(parent)
        self.result = None
        self.rows = {}
        w = px(680)
        h = min(px(640), max(px(420), self.winfo_screenheight() - px(140)))
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 3)
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(px(560), px(400))

        tk.Label(self, text="Subject credits", font=F(16, "bold"), bg=C["bg"], fg=C["text"]).pack(
            anchor="w", padx=px(24), pady=(px(20), 0))
        tk.Label(self, wraplength=px(620), justify="left", font=F(9), bg=C["bg"], fg=C["muted"],
                 text="Enter the credits of each subject (check your VTU scheme booklet). Max marks is 100 for "
                      "most subjects (use 50 for 50-mark subjects). Values are remembered for next time.").pack(
            anchor="w", padx=px(24), pady=(px(4), px(12)))

        btns = tk.Frame(self, bg=C["bg"])
        btns.pack(side="bottom", fill="x", padx=px(24), pady=px(16))
        Button(btns, "Save credits", self._save, style="primary").pack(side="right")
        Button(btns, "Cancel", self.destroy, style="secondary").pack(side="right", padx=(0, px(10)))

        card = Card(self, pad=12)
        card.pack(fill="both", expand=True, padx=px(24))
        sf = ScrollFrame(card.content, bg=C["card"])
        sf.pack(fill="both", expand=True)
        grid = sf.inner
        grid.grid_columnconfigure(1, weight=1)
        for j, head in enumerate(("CODE", "SUBJECT", "CREDITS", "MAX MARKS")):
            tk.Label(grid, text=head, font=F(8, "bold"), bg=C["card"], fg=C["muted"], anchor="w").grid(
                row=0, column=j, padx=px(6), pady=(0, px(6)), sticky="w")
        for i, (code, name) in enumerate(subjects, 1):
            ex = existing.get(code)
            cv = tk.StringVar(value="" if ex is None else f"{ex.credits:g}")
            mv = tk.StringVar(value="100" if ex is None else f"{ex.max_marks:g}")
            tk.Label(grid, text=code, font=(MONO, 9, "bold"), bg=C["card"], fg=C["accent"], anchor="w").grid(
                row=i, column=0, padx=px(6), pady=px(3), sticky="w")
            tk.Label(grid, text=name[:52], font=F(9), bg=C["card"], fg=C["text"], anchor="w").grid(
                row=i, column=1, padx=px(6), pady=px(3), sticky="w")
            EntryBox(grid, cv, width=7).grid(row=i, column=2, padx=px(6), pady=px(3))
            EntryBox(grid, mv, width=7).grid(row=i, column=3, padx=px(6), pady=px(3))
            self.rows[code] = (cv, mv)
        self.bind("<Return>", lambda e: self._save())
        self.bind("<Escape>", lambda e: self.destroy())
        try:
            self.grab_set()
        except tk.TclError:
            pass

    def _save(self):
        out = {}
        for code, (cv, mv) in self.rows.items():
            try:
                c = float(cv.get().strip())
                m = float(mv.get().strip() or 100)
                if c < 0 or m <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Invalid value", f"Please enter valid numbers for {code}.", parent=self)
                return
            out[code] = self._Credit(c, m)
        self.result = out
        self.destroy()


# ======================================================================================= application
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        global _SCALE
        try:
            _SCALE = min(3.0, max(1.0, float(self.winfo_fpixels("1i")) / 96.0))
        except Exception:
            _SCALE = 1.0
        self.title(APP_TITLE)
        self.configure(bg=C["bg"])
        self._set_icon()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        w, h = min(px(1200), sw - 60), min(px(800), sh - 100)
        self.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2 - 20)}")
        self.minsize(min(px(1040), w), min(px(660), h))

        self.settings = load_settings()
        self.q: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.files: list[str] = []
        self.dataset = None
        self.last_output: Path | None = None
        self.t_start = time.time()
        self._rows: list = []
        self._toast = None
        self._toast_after = None

        self._style()
        self.sidebar = tk.Frame(self, bg=C["sidebar"], width=px(246))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        self.main = tk.Frame(self, bg=C["bg"])
        self.main.pack(side="left", fill="both", expand=True)

        self.pages: dict[str, tk.Frame] = {}
        self.nav: dict[str, NavItem] = {}
        for key in ("collect", "analyze", "help"):
            self.pages[key] = tk.Frame(self.main, bg=C["bg"])
        self._build_sidebar()
        self._build_collect()
        self._build_analyze()
        self._build_help()
        self.show_page("collect")

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._poll)
        if configure_tesseract() is None:
            self.ocr_dot.config(fg=C["danger"])
            self.ocr_lbl.config(text="OCR engine missing")
            self._clog("WARNING: Tesseract OCR not found - collecting will not work until the 'Tesseract-OCR' "
                       "folder is placed next to this program (see instructions).")
        else:
            self._clog("Ready. Fill in the settings on the left and press Start collecting.")

    # ------------------------------------------------------------------ look & feel
    def _set_icon(self):
        try:
            if sys.platform == "win32":
                ico = asset_path("icon.ico")
                if ico:
                    self.iconbitmap(default=str(ico))
                    return
            png = asset_path("icon_64.png")
            if png:
                self._icon = tk.PhotoImage(file=str(png))
                self.iconphoto(True, self._icon)
        except Exception:
            pass

    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except tk.TclError:
            pass
        try:
            for name, trough, thumb, active in (("Modern", C["bg"], "#CBD5E1", "#94A3B8"),
                                                ("Dark", C["log_bg"], "#334155", "#475569")):
                style = f"{name}.Vertical.TScrollbar"
                st.layout(style, [("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                    ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
                st.configure(style, background=thumb, troughcolor=trough, bordercolor=trough, lightcolor=thumb,
                             darkcolor=thumb, gripcount=0, width=px(10), arrowsize=px(10))
                st.map(style, background=[("active", active), ("pressed", active)])
            st.configure("Modern.Treeview", background="#FFFFFF", fieldbackground="#FFFFFF", foreground=C["text"],
                         rowheight=px(32), borderwidth=0, font=F(10))
            st.configure("Modern.Treeview.Heading", background="#F1F5F9", foreground=C["muted"],
                         font=F(9, "bold"), relief="flat", borderwidth=0, padding=(px(10), px(8)))
            st.map("Modern.Treeview", background=[("selected", C["accent_soft"])],
                   foreground=[("selected", C["accent"])])
            st.map("Modern.Treeview.Heading", background=[("active", "#E6EAF2")])
            st.layout("Modern.Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        except tk.TclError:
            log.exception("ttk styling failed (falling back to defaults)")

    # ------------------------------------------------------------------ plumbing
    def report_callback_exception(self, exc, val, tb):
        log.error("UI error:\n%s", "".join(traceback.format_exception(exc, val, tb)))
        messagebox.showerror("Unexpected error",
                             f"{exc.__name__}: {val}\n\nThe details were saved in:\n{log_dir() / 'app.log'}")

    def _poll(self):
        try:
            while True:
                kind, payload = self.q.get_nowait()
                try:
                    getattr(self, f"_on_{kind}")(payload)
                except Exception:
                    log.exception("handler %s failed", kind)
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _on_close(self):
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("Quit", "Collection is running. Stop it, save what was collected and quit?"):
                return
            self.stop_event.set()
            self.worker.join(timeout=15)
        self._save_collect_settings()
        self.destroy()

    def _clog(self, text):
        self.c_log.add(text)

    def show_page(self, key):
        for k, p in self.pages.items():
            p.pack_forget()
            self.nav[k].set_active(k == key)
        self.pages[key].pack(fill="both", expand=True)

    def toast(self, text, kind="info", ms=3800):
        colors = {"info": C["accent"], "ok": "#10B981", "err": "#EF4444", "warn": "#F59E0B"}
        self._hide_toast()
        box = tk.Frame(self, bg=C["text"])
        tk.Frame(box, bg=colors.get(kind, C["accent"]), width=px(5)).pack(side="left", fill="y")
        tk.Label(box, text=text, bg=C["text"], fg="#FFFFFF", font=F(10), justify="left",
                 wraplength=px(360)).pack(side="left", padx=px(16), pady=px(12))
        box.place(relx=1.0, rely=1.0, x=-px(24), y=-px(24), anchor="se")
        box.lift()
        self._toast = box
        self._toast_after = self.after(ms, self._hide_toast)

    def _hide_toast(self):
        if self._toast_after is not None:
            try:
                self.after_cancel(self._toast_after)
            except (tk.TclError, ValueError):
                pass
        if self._toast is not None:
            try:
                self._toast.destroy()
            except tk.TclError:
                pass
        self._toast = None
        self._toast_after = None

    def _page_header(self, page, title, subtitle):
        head = tk.Frame(page, bg=C["bg"])
        head.pack(fill="x", padx=px(28), pady=(px(24), px(16)))
        left = tk.Frame(head, bg=C["bg"])
        left.pack(side="left")
        tk.Label(left, text=title, font=F(20, "bold"), bg=C["bg"], fg=C["text"]).pack(anchor="w")
        tk.Label(left, text=subtitle, font=F(10), bg=C["bg"], fg=C["muted"]).pack(anchor="w", pady=(px(2), 0))
        right = tk.Frame(head, bg=C["bg"])
        right.pack(side="right")
        return right

    # ------------------------------------------------------------------ sidebar
    def _build_sidebar(self):
        sb = self.sidebar
        brand = tk.Frame(sb, bg=C["sidebar"])
        brand.pack(fill="x", padx=px(20), pady=(px(26), px(6)))
        size = px(46)
        logo = tk.Canvas(brand, width=size, height=size, bg=C["sidebar"], highlightthickness=0, bd=0)
        logo.pack(side="left")
        rounded(logo, 0, 0, size, size, px(13), fill=C["accent"], outline=C["accent"])
        for i, hh in enumerate((0.30, 0.48, 0.66)):
            bx = size * (0.24 + i * 0.20)
            logo.create_rectangle(bx, size * 0.78 - size * hh, bx + size * 0.13, size * 0.78, fill="#FFFFFF",
                                  outline="")
        txt = tk.Frame(brand, bg=C["sidebar"])
        txt.pack(side="left", padx=(px(12), 0))
        tk.Label(txt, text="VTU Marks", font=F(13, "bold"), bg=C["sidebar"], fg="#FFFFFF", anchor="w").pack(fill="x")
        tk.Label(txt, text="Scraper Tool", font=F(11), bg=C["sidebar"], fg=C["sidebar_text"], anchor="w").pack(fill="x")
        ver = Pill(sb, f"V{APP_VERSION}", "accent")
        ver.pack(anchor="w", padx=px(20), pady=(px(10), px(18)))
        tk.Frame(sb, bg="#1E293B", height=1).pack(fill="x", padx=px(20))
        tk.Label(sb, text="WORKSPACE", font=F(8, "bold"), bg=C["sidebar"], fg="#64748B", anchor="w").pack(
            fill="x", padx=px(22), pady=(px(18), px(8)))
        for key, badge, text in (("collect", "1", "Collect marks"), ("analyze", "2", "Analyze & SGPA"),
                                 ("help", "?", "Help & guide")):
            item = NavItem(sb, badge, text, lambda k=key: self.show_page(k))
            item.pack(fill="x", pady=1)
            self.nav[key] = item

        foot = tk.Frame(sb, bg=C["sidebar"])
        foot.pack(side="bottom", fill="x", padx=px(20), pady=px(20))
        row = tk.Frame(foot, bg=C["sidebar"])
        row.pack(fill="x")
        self.ocr_dot = tk.Label(row, text="\u25CF", font=F(10), bg=C["sidebar"], fg="#10B981")
        self.ocr_dot.pack(side="left")
        self.ocr_lbl = tk.Label(row, text="OCR engine ready", font=F(9), bg=C["sidebar"], fg=C["sidebar_text"])
        self.ocr_lbl.pack(side="left", padx=(px(6), 0))

    # ------------------------------------------------------------------ COLLECT page
    def _build_collect(self):
        s = self.settings
        page = self.pages["collect"]
        hr = self._page_header(page, "Collect marks", "Fetch results from the VTU portal for a range of students.")
        self.lbl_mode = Pill(hr, "REGULAR / ARREAR", "accent")
        self.lbl_mode.pack()

        self.v_prefix = tk.StringVar(value="1CR24EC")
        self.v_numbers = tk.StringVar(value="1-248")
        self.v_sem = tk.StringVar(value="5")
        self.v_url = tk.StringVar(value=s.get("url", "https://results.vtu.ac.in/MJ26cbcs/index.php"))
        self.v_retries = tk.StringVar(value=s.get("retries", "5"))
        self.v_delay = tk.StringVar(value=s.get("delay", "5"))
        self.v_pause = tk.StringVar(value=s.get("pause", "1.0"))
        self.v_workers = tk.StringVar(value=s.get("workers", "1"))
        self.v_show = tk.BooleanVar(value=s.get("show", False))
        self.v_out = tk.StringVar(value=str(Path.home() / "Downloads"))

        body = tk.Frame(page, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=px(28), pady=(0, px(22)))
        left = tk.Frame(body, bg=C["bg"], width=px(480))
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        right = tk.Frame(body, bg=C["bg"])
        right.pack(side="left", fill="both", expand=True, padx=(px(22), 0))

        # ---- left: action bar (pinned) + scrollable settings cards
        bar = tk.Frame(left, bg=C["bg"])
        bar.pack(side="bottom", fill="x", pady=(px(12), 0))
        self.b_check = Button(bar, "Check connection", self._check_conn, style="secondary")
        self.b_stop = Button(bar, "\u25A0  Stop & save", self._stop_collect, style="danger")
        self.b_start = Button(bar, "\u25B6  Start collecting", self._start_collect, style="primary", height=42)
        self.b_stop.config(state="disabled")
        self.b_check.pack(side="left")
        self.b_stop.pack(side="left", padx=(px(8), 0))
        self.b_start.pack(side="left", fill="x", expand=True, padx=(px(8), 0))

        sf = ScrollFrame(left)
        sf.pack(fill="both", expand=True)
        inner = sf.inner
        gap = (0, px(14))
        rpad = (0, px(10))

        c1 = Card(inner, "Students", "Which USNs to collect")
        c1.pack(fill="x", pady=gap, padx=rpad)
        g = c1.content
        self.f_prefix = Field(g, "USN prefix", self.v_prefix, hint="e.g. 1CR24EC")
        self.f_numbers = Field(g, "USN numbers", self.v_numbers, hint="1-120 or 1-10,13")
        self.f_sem = Field(g, "Semester", self.v_sem, hint="1 to 8")
        for i, (fld, wgt) in enumerate(((self.f_prefix, 4), (self.f_numbers, 5), (self.f_sem, 3))):
            fld.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else px(10), 0))
            g.grid_columnconfigure(i, weight=wgt, uniform="stu")

        c2 = Card(inner, "Result page", "Paste the link from the VTU results site")
        c2.pack(fill="x", pady=gap, padx=rpad)
        self.f_url = Field(c2.content, "Result page URL", self.v_url,
                           hint="Ends with index.php. A link containing 'RV' switches to revaluation mode.")
        self.f_url.pack(fill="x")

        c3 = Card(inner, "Speed & reliability", "Be polite to the server - avoid IP blocks")
        c3.pack(fill="x", pady=gap, padx=rpad)
        g = c3.content
        self.f_retries = Field(g, "Retries per student", self.v_retries)
        self.f_delay = Field(g, "Wait after error (s)", self.v_delay)
        self.f_pause = Field(g, "Pause between students (s)", self.v_pause)
        self.f_workers = Field(g, "Parallel browsers (1-10)", self.v_workers, hint="More = faster but riskier")
        for i, fld in enumerate((self.f_retries, self.f_delay, self.f_pause, self.f_workers)):
            fld.grid(row=i // 2, column=i % 2, sticky="ew", padx=(0 if i % 2 == 0 else px(10), 0),
                     pady=(0, px(10) if i < 2 else 0))
        g.grid_columnconfigure(0, weight=1, uniform="perf")
        g.grid_columnconfigure(1, weight=1, uniform="perf")

        c4 = Card(inner, "Output", "Where the CSV files are saved")
        c4.pack(fill="x", pady=gap, padx=rpad)
        Field(c4.content, "Save results in folder", self.v_out, button=("Browse", lambda: self._pick_dir(self.v_out))
              ).pack(fill="x")
        Switch(c4.content, "Show the browser window while collecting", self.v_show).pack(anchor="w", pady=(px(14), 0))

        self.v_url.trace_add("write", lambda *a: self._update_mode())
        self._update_mode()

        # ---- right: progress + log
        pc = Card(right, "Progress")
        pc.pack(fill="x")
        top = tk.Frame(pc.content, bg=C["card"])
        top.pack(fill="x", pady=(0, px(10)))
        self.run_pill = Pill(top, "IDLE", "neutral")
        self.run_pill.pack(side="left")
        self.c_eta = tk.Label(top, text="Waiting to start", font=F(9), bg=C["card"], fg=C["muted"])
        self.c_eta.pack(side="right")
        self.c_prog = ProgressBar(pc.content, bg=C["card"], height=10)
        self.c_prog.pack(fill="x")
        tiles = tk.Frame(pc.content, bg=C["card"])
        tiles.pack(fill="x", pady=(px(14), 0))
        self.t_done = Tile(tiles, "Completed", "0 / 0")
        self.t_speed = Tile(tiles, "Speed", "-")
        self.t_left = Tile(tiles, "Time left", "-")
        for i, t in enumerate((self.t_done, self.t_speed, self.t_left)):
            t.grid(row=0, column=i, sticky="ew", padx=(0 if i == 0 else px(10), 0))
            tiles.grid_columnconfigure(i, weight=1, uniform="tiles")

        lc = tk.Frame(right, bg=C["log_bg"], highlightthickness=1, highlightbackground=C["log_bg"])
        lc.pack(fill="both", expand=True, pady=(px(14), 0))
        lh = tk.Frame(lc, bg=C["log_head"])
        lh.pack(fill="x")
        tk.Label(lh, text="Activity log", font=F(10, "bold"), bg=C["log_head"], fg="#E2E8F0").pack(
            side="left", padx=px(14), pady=px(8))
        Button(lh, "Clear", lambda: self.c_log.clear(), style="ghost_dark", height=28, font_size=9).pack(
            side="right", padx=px(8), pady=px(4))
        self.c_log = LogView(lc)
        self.c_log.pack(fill="both", expand=True)

    def _update_mode(self):
        from urllib.parse import urlparse
        rv = "RV" in urlparse(self.v_url.get()).path.upper()
        if rv:
            self.lbl_mode.set("REVALUATION RESULTS", "warn")
        else:
            self.lbl_mode.set("REGULAR / ARREAR RESULTS", "accent")

    def _set_run_state(self, text, kind):
        self.run_pill.set(text, kind)

    def _pick_dir(self, var):
        d = filedialog.askdirectory(title="Select folder")
        if d:
            var.set(d)

    def _check_conn(self):
        self._clog("Checking internet ...")
        self.b_check.config(state="disabled")

        def work():
            self.q.put(("conn", has_internet()))

        threading.Thread(target=work, daemon=True).start()

    def _on_conn(self, ok):
        self.b_check.config(state="normal")
        if ok:
            self._clog("Internet / VTU site reachable.")
            self.toast("Connected - the VTU site is reachable.", "ok")
        else:
            self._clog("Cannot reach the internet or the VTU site.")
            self.toast("Cannot reach the internet or the VTU site.", "err")

    def _save_collect_settings(self):
        self.settings.update({"prefix": self.v_prefix.get(), "numbers": self.v_numbers.get(), "sem": self.v_sem.get(),
                              "url": self.v_url.get(), "retries": self.v_retries.get(), "delay": self.v_delay.get(),
                              "pause": self.v_pause.get(), "workers": self.v_workers.get(), "show": self.v_show.get(),
                              "out": self.v_out.get()})
        save_settings(self.settings)

    def _start_collect(self):
        from vtutool.scraper import ScrapeConfig, parse_numbers
        errs = []
        bad = []
        prefix = self.v_prefix.get().strip().upper()
        if not re.fullmatch(r"\d[A-Z]{2}\d{2}[A-Z]{2}", prefix):
            errs.append("USN prefix must look like 1CR24EC (7 characters).")
            bad.append(self.f_prefix)
        numbers = []
        try:
            numbers = parse_numbers(self.v_numbers.get())
        except ValueError:
            errs.append("USN numbers: use a format like 1-120 or 1-10,13,15.")
            bad.append(self.f_numbers)
        sem = self.v_sem.get().strip()
        if not (sem.isdigit() and 1 <= int(sem) <= 8):
            errs.append("Semester must be a number from 1 to 8.")
            bad.append(self.f_sem)
        url = self.v_url.get().strip()
        if not re.match(r"^https://results\.vtu\.ac\.in/[A-Za-z0-9_]+/index\.php", url):
            errs.append("URL must look like https://results.vtu.ac.in/XXXX/index.php")
            bad.append(self.f_url)
        try:
            retries, delay, pause = int(self.v_retries.get()), float(self.v_delay.get()), float(self.v_pause.get())
            workers = int(self.v_workers.get())
            if retries < 1 or delay < 0 or pause < 0.5 or not 1 <= workers <= 10:
                raise ValueError
        except ValueError:
            errs.append("Retries >= 1, delay >= 0, pause >= 0.5 s, parallel browsers between 1 and 10.")
            bad += [self.f_retries, self.f_delay, self.f_pause, self.f_workers]
            retries = delay = pause = workers = 0
        for fld in bad:
            fld.set_error(True)
        if errs:
            messagebox.showerror("Please fix", "\n".join(errs))
            return
        if configure_tesseract() is None:
            messagebox.showerror("Tesseract missing", "Tesseract OCR was not found.\nPut the 'Tesseract-OCR' folder "
                                 "next to this program (see the instructions) and try again.")
            return
        out = Path(self.v_out.get().strip())
        try:
            out.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Folder problem", f"Cannot use that folder:\n{exc}")
            return
        if not has_internet():
            messagebox.showerror("No internet", "Cannot reach the internet / VTU site. Check your connection.")
            return
        cfg = ScrapeConfig(prefix, numbers, int(sem), url, out, retries, delay, pause, self.v_show.get(), workers)
        self._save_collect_settings()
        self.stop_event.clear()
        self.c_prog.set(0)
        self.t_start = time.time()
        self.c_eta.config(text="Starting ...")
        self.t_done.set(f"0 / {len(numbers)}")
        self.t_speed.set("-")
        self.t_left.set("-")
        self._set_run_state("RUNNING", "accent")
        self.b_start.config(state="disabled")
        self.b_stop.config(state="normal")
        self._clog(f"--- Collecting {len(numbers)} USN(s) for {prefix}xxx ({'REVAL' if cfg.is_reval else 'regular'}) ---")
        self.worker = threading.Thread(target=self._scrape_worker, args=(cfg,), daemon=True)
        self.worker.start()

    def _scrape_worker(self, cfg):
        from vtutool.scraper import run_scrape
        try:
            summary = run_scrape(cfg, lambda k, p: self.q.put((k if k != "log" else "clog", p)), self.stop_event)
            self.q.put(("scrape_done", summary))
        except Exception:
            log.exception("scrape failed")
            self.q.put(("scrape_fail", traceback.format_exc(limit=3)))

    def _stop_collect(self):
        self.stop_event.set()
        self.b_stop.config(state="disabled")
        self._set_run_state("STOPPING", "warn")
        self._clog("Stopping ... (finishing the current student)")

    def _on_clog(self, text):
        self._clog(text)

    def _on_progress(self, payload):
        done, total = payload
        if not total:
            return
        self.c_prog.set(done / total * 100)
        el = max(time.time() - self.t_start, 0.1)
        per = el / max(done, 1)
        left = per * (total - done)
        self.t_done.set(f"{done} / {total}")
        self.t_speed.set(f"{per:.1f} s each")
        self.t_left.set(fmt_duration(left))
        self.c_eta.config(text=f"{done / total * 100:.0f}% complete")

    def _on_scrape_done(self, s):
        self.b_start.config(state="normal")
        self.b_stop.config(state="disabled")
        self.c_eta.config(text="Finished")
        self.t_left.set("0 s")
        stopped = bool(self.stop_event.is_set())
        self._set_run_state("STOPPED" if stopped else "FINISHED", "warn" if stopped else "ok")
        self._clog(f"Done. Collected {len(s.collected)}, skipped {len(s.skipped)}, failed {len(s.failed)}.")
        if s.skipped:
            self._clog("Skipped: " + ", ".join(u for u, _ in s.skipped[:30]) + (" ..." if len(s.skipped) > 30 else ""))
        for p in s.files:
            self._clog(f"Saved: {p}")
        if s.files:
            self.last_output = s.folder
            msg = f"Saved {len(s.files)} file(s) in:\n{s.folder}"
            if s.stop_reason:
                msg += f"\n\nNote: {s.stop_reason}"
            messagebox.showinfo("Collection finished", msg)
            open_folder(s.folder)
        else:
            messagebox.showwarning("No data", s.stop_reason or "No data was collected.")

    def _on_scrape_fail(self, text):
        self.b_start.config(state="normal")
        self.b_stop.config(state="disabled")
        self._set_run_state("FAILED", "err")
        messagebox.showerror("Collection failed", f"Something went wrong. Data collected so far may have been saved.\n\n"
                             f"{text}\n\nSee {log_dir() / 'app.log'}")

    # ------------------------------------------------------------------ ANALYZE page
    def _build_analyze(self):
        page = self.pages["analyze"]
        self._page_header(page, "Analyze & SGPA",
                          "Turn the collected CSV files into one Excel report with SGPA and revaluation comparison.")
        body = tk.Frame(page, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=px(28), pady=(0, px(22)))

        # ---- input files
        fc = Card(body, "Input files",
                  "Raw Regular (or Raw Arrear alone for makeup exams). Raw Reval and Credit info are optional.")
        fc.pack(fill="x")
        row = tk.Frame(fc.content, bg=C["card"])
        row.pack(fill="x")
        box = tk.Frame(row, bg=C["input_border"])
        box.pack(side="left", fill="x", expand=True)
        lb_wrap = tk.Frame(box, bg="#FFFFFF")
        lb_wrap.pack(fill="both", expand=True, padx=1, pady=1)
        self.lst = tk.Listbox(lb_wrap, height=4, selectmode="extended", relief="flat", bd=0, highlightthickness=0,
                              activestyle="none", bg="#FFFFFF", fg=C["text"], font=F(10),
                              selectbackground=C["accent_soft"], selectforeground=C["accent"])
        sb = ttk.Scrollbar(lb_wrap, orient="vertical", command=self.lst.yview, style="Modern.Vertical.TScrollbar")
        self.lst.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lst.pack(side="left", fill="both", expand=True, padx=px(6), pady=px(4))
        self.empty_lbl = tk.Label(lb_wrap, text="No files yet - click  Add files  to choose your CSV files",
                                  font=F(10), bg="#FFFFFF", fg=C["faint"], cursor="hand2")
        self.empty_lbl.place(relx=0.5, rely=0.5, anchor="center")
        self.empty_lbl.bind("<Button-1>", lambda e: self._add_files())
        bt = tk.Frame(row, bg=C["card"])
        bt.pack(side="left", padx=(px(14), 0), anchor="n")
        Button(bt, "Add files...", self._add_files, style="primary", width=px(140), height=34).pack(pady=(0, px(6)))
        Button(bt, "Remove selected", self._remove_files, style="secondary", width=px(140), height=34).pack(
            pady=(0, px(6)))
        Button(bt, "Clear all", self._clear_files, style="ghost", width=px(140), height=34).pack()

        # ---- report row
        rc = Card(body, pad=14)
        rc.pack(fill="x", pady=(px(14), 0))
        self.v_aout = tk.StringVar(value=self.settings.get("aout", str(Path.home() / "Downloads")))
        rrow = tk.Frame(rc.content, bg=C["card"])
        rrow.pack(fill="x")
        Field(rrow, "Save Excel report in", self.v_aout, button=("Browse", lambda: self._pick_dir(self.v_aout))
              ).pack(side="left", fill="x", expand=True)
        acts = tk.Frame(rrow, bg=C["card"])
        acts.pack(side="left", padx=(px(16), 0), anchor="s")
        self.b_an = Button(acts, "Analyze", self._start_analyze, style="primary", height=36)
        self.b_cred = Button(acts, "Edit credits", self._edit_credits, style="secondary", height=36)
        self.b_open = Button(acts, "Open folder", lambda: open_folder(self.v_aout.get()), style="ghost", height=36)
        for b in (self.b_an, self.b_cred, self.b_open):
            b.pack(side="left", padx=(0, px(6)))
        self.a_status = tk.Label(rc.content, text="", font=F(9), bg=C["card"], fg=C["muted"], anchor="w")
        self.a_status.pack(fill="x", pady=(px(8), 0))

        # ---- results: summary + table
        res = tk.Frame(body, bg=C["bg"])
        res.pack(fill="both", expand=True, pady=(px(14), 0))
        sc = Card(res, "Summary")
        sc.pack(side="left", fill="y", padx=(0, px(14)))
        sc.config(width=px(360))
        sc.pack_propagate(False)
        self.a_sum = tk.Text(sc.content, height=6, state="disabled", wrap="word", font=F(10), bg=C["card"],
                             fg=C["text"], relief="flat", bd=0, highlightthickness=0, cursor="arrow",
                             spacing1=px(2), spacing3=px(6))
        self.a_sum.pack(fill="both", expand=True)
        self.a_sum.tag_configure("head", foreground=C["warn"], font=F(10, "bold"))
        self.a_sum.tag_configure("muted", foreground=C["faint"])
        self._set_summary(["Run an analysis to see the summary here."], placeholder=True)

        tc = Card(res, "Student results", pad=14)
        tc.pack(side="left", fill="both", expand=True)
        trow = tk.Frame(tc.content, bg=C["card"])
        trow.pack(fill="x", pady=(0, px(8)))
        self.v_search = tk.StringVar()
        EntryBox(trow, self.v_search, width=26).pack(side="left")
        tk.Label(trow, text="Search USN or name", font=F(8), bg=C["card"], fg=C["faint"]).pack(
            side="left", padx=(px(10), 0))
        self.lbl_count = tk.Label(trow, text="", font=F(9), bg=C["card"], fg=C["muted"])
        self.lbl_count.pack(side="right")
        self.v_search.trace_add("write", lambda *a: self._refresh_table())

        twrap = tk.Frame(tc.content, bg=C["border"])
        twrap.pack(fill="both", expand=True)
        cols = ("usn", "name", "before", "after", "change")
        self.tree = ttk.Treeview(twrap, columns=cols, show="headings", height=6, style="Modern.Treeview")
        for c, t, w, anc in (("usn", "USN", 130, "w"), ("name", "Student", 260, "w"),
                             ("before", "SGPA before reval", 130, "center"), ("after", "SGPA final", 100, "center"),
                             ("change", "Change", 80, "center")):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=px(w), anchor=anc, stretch=(c == "name"))
        tsb = ttk.Scrollbar(twrap, orient="vertical", command=self.tree.yview, style="Modern.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=tsb.set)
        tsb.pack(side="right", fill="y", padx=(0, 1), pady=1)
        self.tree.pack(side="left", fill="both", expand=True, padx=(1, 0), pady=1)
        self.tree.tag_configure("odd", background="#F8FAFC")
        self.tree.tag_configure("up", foreground=C["success"])
        self.tree.tag_configure("down", foreground=C["danger"])

    def _set_summary(self, lines, placeholder=False):
        t = self.a_sum
        t.config(state="normal")
        t.delete("1.0", "end")
        for ln in lines:
            if placeholder:
                t.insert("end", ln + "\n", "muted")
            elif ln.startswith("REVALUATION"):
                t.insert("end", ln + "\n", "head")
            else:
                t.insert("end", "\u2022  " + ln + "\n")
        t.config(state="disabled")

    def _refresh_table(self):
        q = self.v_search.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for r in self._rows:
            if q and q not in str(r[0]).lower() and q not in str(r[1]).lower():
                continue
            tags = ["odd"] if shown % 2 else []
            chg = str(r[4])
            if chg.startswith("+") and chg != "+0.00":
                tags.append("up")
            elif chg.startswith("-"):
                tags.append("down")
            self.tree.insert("", "end", values=r, tags=tuple(tags))
            shown += 1
        self.lbl_count.config(text=f"{shown} of {len(self._rows)} students" if self._rows else "")

    def _sync_files_ui(self):
        if self.files:
            self.empty_lbl.place_forget()
        else:
            self.empty_lbl.place(relx=0.5, rely=0.5, anchor="center")

    def _add_files(self):
        init = str(self.last_output) if self.last_output else self.v_out.get()
        paths = filedialog.askopenfilenames(title="Select CSV files", initialdir=init if os.path.isdir(init) else None,
                                            filetypes=[("CSV files", "*.csv")])
        for p in paths:
            if p not in self.files:
                self.files.append(p)
                pp = Path(p)
                self.lst.insert("end", f"  [{file_tag(p)}]   {pp.name}      \u2014  {pp.parent}")
        self._sync_files_ui()

    def _remove_files(self):
        for i in reversed(self.lst.curselection()):
            self.lst.delete(i)
            del self.files[i]
        self._sync_files_ui()

    def _clear_files(self):
        self.lst.delete(0, "end")
        self.files.clear()
        self._sync_files_ui()

    def _busy(self, on, text=""):
        for b in (self.b_an, self.b_cred):
            b.config(state="disabled" if on else "normal")
        self.a_status.config(text=text)

    def _edit_credits(self):
        if not self.files:
            messagebox.showinfo("Add files first",
                                "Add the Raw Regular (or Raw Arrear) CSV file(s) first, then edit credits.")
            return
        try:
            from vtutool.analysis import load_dataset
            ds = load_dataset(self.files)
        except Exception as exc:
            messagebox.showerror("Cannot read files", str(exc))
            return
        self._ask_credits(ds)

    def _ask_credits(self, ds):
        from vtutool.analysis import Credit
        existing = {c: Credit(v["credits"], v.get("max_marks", 100)) for c, v in load_credit_memory().items()}
        existing.update(ds.credits_csv)
        dlg = CreditsDialog(self, ds.regular_subjects(), existing)
        self.wait_window(dlg)
        if dlg.result:
            save_credit_memory({c: {"credits": v.credits, "max_marks": v.max_marks} for c, v in dlg.result.items()})
            return dlg.result
        return None

    def _start_analyze(self):
        if not self.files:
            messagebox.showerror("No files", "Add at least the Raw Regular or Raw Arrear CSV file.")
            return
        out = self.v_aout.get().strip()
        if not out:
            messagebox.showerror("Folder", "Choose a folder for the Excel file.")
            return
        try:
            Path(out).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            messagebox.showerror("Folder problem", str(exc))
            return
        self.settings["aout"] = out
        save_settings(self.settings)
        self._busy(True, "Reading files ...")
        threading.Thread(target=self._load_worker, args=(list(self.files),), daemon=True).start()

    def _load_worker(self, files):
        from vtutool.analysis import DataError, load_dataset
        try:
            self.q.put(("dataset", load_dataset(files)))
        except DataError as exc:
            self.q.put(("an_error", str(exc)))
        except Exception:
            log.exception("load failed")
            self.q.put(("an_error", f"Unexpected error while reading the files.\nSee {log_dir() / 'app.log'}"))

    def _on_dataset(self, ds):
        from vtutool.analysis import Credit
        memory = {c: Credit(v["credits"], v.get("max_marks", 100)) for c, v in load_credit_memory().items()}
        credits = {c: memory[c] for c in {c for c, _ in ds.regular_subjects()} if c in memory}
        credits.update(ds.credits_csv)
        needed = {c for c, _ in ds.regular_subjects()}
        credits = {c: v for c, v in credits.items() if c in needed}
        if needed - set(credits):
            ans = messagebox.askyesnocancel(
                "Credits needed",
                "SGPA needs the credits of each subject.\n\nYes = enter credits now\nNo = skip SGPA\nCancel = stop")
            if ans is None:
                self._busy(False)
                return
            if ans:
                got = self._ask_credits(ds)
                if not got:
                    self._busy(False)
                    return
                credits = got
            else:
                credits = None
        self._busy(True, "Analyzing ...")
        threading.Thread(target=self._analyze_worker, args=(ds, credits, self.v_aout.get().strip()),
                         daemon=True).start()

    def _analyze_worker(self, ds, credits, outdir):
        from vtutool.analysis import DataError, MissingCredits, analyze
        from vtutool.excel_export import export_excel
        try:
            res = analyze(ds, credits)
            name = f"{res.first_usn} to {res.last_usn} VTU analysis{' (with reval)' if res.has_reval else ''}.xlsx"
            path = export_excel(res, Path(outdir) / name)
            self.q.put(("an_done", (res, path)))
        except (DataError, MissingCredits) as exc:
            self.q.put(("an_error", str(exc)))
        except PermissionError:
            self.q.put(("an_error", "Cannot write the Excel file - close it in Excel if it is open, then try again."))
        except Exception:
            log.exception("analysis failed")
            self.q.put(("an_error", f"Unexpected error during analysis.\nSee {log_dir() / 'app.log'}"))

    def _on_an_error(self, text):
        self._busy(False)
        messagebox.showerror("Analysis problem", text)

    def _on_an_done(self, payload):
        res, path = payload
        self._busy(False, f"Saved: {path}")
        self.last_output = Path(path).parent
        self._set_summary(list(res.summary_lines))
        self._rows = list(res.student_rows)
        self.v_search.set("")
        self._refresh_table()
        self.toast("Excel report saved.", "ok")
        messagebox.showinfo("Analysis complete", f"Excel file saved:\n{path}")
        open_folder(Path(path).parent)

    # ------------------------------------------------------------------ HELP page
    def _build_help(self):
        page = self.pages["help"]
        self._page_header(page, "Help & guide", f"{APP_NAME} V{APP_VERSION} - how everything works.")
        sf = ScrollFrame(page)
        sf.pack(fill="both", expand=True, padx=(px(28), px(18)), pady=(0, px(22)))
        wrap_labels = []
        for title, kind, items in HELP_SECTIONS:
            card = Card(sf.inner, title)
            card.pack(fill="x", pady=(0, px(14)), padx=(0, px(10)))
            for i, text in enumerate(items, 1):
                row = tk.Frame(card.content, bg=C["card"])
                row.pack(fill="x", pady=px(4))
                if kind == "num":
                    tk.Label(row, text=str(i), width=3, font=F(9, "bold"), bg=C["accent_soft"], fg=C["accent"]).pack(
                        side="left", anchor="n", padx=(0, px(12)), pady=px(1))
                else:
                    tk.Label(row, text="\u2022", font=F(11, "bold"), bg=C["card"], fg=C["accent"]).pack(
                        side="left", anchor="n", padx=(px(4), px(12)))
                lbl = tk.Label(row, text=text, font=F(10), bg=C["card"], fg=C["text"], justify="left", anchor="w",
                               wraplength=px(560))
                lbl.pack(side="left", fill="x", expand=True)
                wrap_labels.append(lbl)
        sf.on_resize = lambda w: [lb.config(wraplength=max(px(300), w - px(150))) for lb in wrap_labels]


def main():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    log.info("%s starting", APP_TITLE)
    try:
        App().mainloop()
    except Exception:
        log.exception("fatal")
        raise


if __name__ == "__main__":
    main()
