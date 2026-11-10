import sys, tempfile, threading, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent)); sys.path.insert(0, str(Path(__file__).parent))
import vtutool.scraper as sc
from test_pipeline import reg_page
class Fake:
    def __init__(s, url, show): s.usn = None
    def start(s): pass
    def stop(s): pass
    def alive(s): return True
    def load(s): time.sleep(0.05)
    def enter_usn(s, u): s.usn = u
    def captcha_png(s): return b"x"
    def submit(s, c): pass
    def wait_outcome(s, timeout=15): return ("result", reg_page(int(s.usn[-3:])))
sc.VTUSession = Fake; sc.solve_captcha = lambda p: "ABCDEF"
with tempfile.TemporaryDirectory() as t:
    for w in (1, 3):
        cfg = sc.ScrapeConfig("1AB24CS", [1, 2, 3, 4, 5], 5, "https://results.vtu.ac.in/JJ26cbcs/index.php", Path(t), pause=0.5, workers=w)
        t0 = time.time(); s = sc.run_scrape(cfg, lambda k, p: None, threading.Event())
        print(f"workers={w}: {len(s.collected)} collected in {time.time()-t0:.1f}s")
        assert s.collected == [f"1AB24CS00{i}" for i in range(1, 6)] and not s.stop_reason
print("PARALLEL OK")
