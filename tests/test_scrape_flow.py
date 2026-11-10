"""Exercise run_scrape() with a fake browser (no Chrome/selenium needed)."""
import sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import vtutool.scraper as sc
sys.path.insert(0, str(Path(__file__).parent)); 
from test_pipeline import reg_page   # reuses fake page builder (also runs its asserts)

class FakeSession:
    script = {}          # usn -> list of outcomes, consumed in order
    def __init__(self, url, show): self.usn = None; self.calls = 0
    def start(self): pass
    def stop(self): pass
    def alive(self): return True
    def load(self): pass
    def enter_usn(self, usn): self.usn = usn
    def captcha_png(self): return b"x"
    def submit(self, c): pass
    def wait_outcome(self, timeout=15):
        return self.script[self.usn].pop(0)
sc.VTUSession = FakeSession
caps = iter(["abc12", "ABCDEF"] * 50)          # first read is bad (5 chars), next fine
sc.solve_captcha = lambda png: next(caps)

with tempfile.TemporaryDirectory() as tmp:
    FakeSession.script = {
        "1AB24CS001": [("alert", "Invalid captcha code !!!"), ("result", reg_page(1))],
        "1AB24CS002": [("alert", "University Seat Number is not available or Invalid..!")],
        "1AB24CS003": [("result", reg_page(3))],
        "1AB24CS004": [("alert", "")],            # IP blocked
        "1AB24CS005": [("result", reg_page(5))],
    }
    msgs = []
    cfg = sc.ScrapeConfig("1AB24CS", [1, 2, 3, 4, 5], 5, "https://results.vtu.ac.in/JJ26cbcs/index.php", Path(tmp), pause=0.5, delay=0)
    s = sc.run_scrape(cfg, lambda k, p: msgs.append((k, p)), threading.Event())
    print("\n".join(str(p) for k, p in msgs if k == "log"))
    assert s.collected == ["1AB24CS001", "1AB24CS003"], s.collected
    assert s.skipped[0][0] == "1AB24CS002"
    assert "blocked" in s.stop_reason.lower()          # stopped at USN 4, 5 never requested
    assert any(p.name.startswith("Raw Regular") for p in s.files)
    assert not (s.folder / "_autosave_partial.csv").exists()
print("SCRAPE FLOW OK")
