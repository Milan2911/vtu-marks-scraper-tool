"""Smoke test of main.py with a stubbed tkinter (real Tk isn't available in every build sandbox).
Constructs the whole window, drives the main handlers and checks the pure helper functions."""
import sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class Var:
    def __init__(self, master=None, value=None): self.v = value
    def get(self): return self.v
    def set(self, v): self.v = v
    def trace_add(self, *a): pass


class Stub:
    def __init__(self, *a, **k): self.a = a
    def __getattr__(self, n): return lambda *a, **k: Stub()
    def __iter__(self): return iter(())
    def __int__(self): return 100
    def __float__(self): return 96.0
    def __index__(self): return 100
    def configure(self, *a, **k): return None
    def _n(self, *a): return 100
    __add__ = __radd__ = __sub__ = __rsub__ = __mul__ = __rmul__ = __truediv__ = __rtruediv__ = _n
    __floordiv__ = __rfloordiv__ = _n
    def __lt__(self, o): return False
    __gt__ = __le__ = __ge__ = __lt__


class Window(Stub): pass


def mod(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items(): setattr(m, k, v)
    m.__getattr__ = lambda n: Stub
    sys.modules[name] = m
    return m


tk = mod("tkinter", Tk=Window, Toplevel=Window, StringVar=Var, BooleanVar=Var, TclError=Exception)
tk.ttk = mod("tkinter.ttk")
tk.messagebox = mod("tkinter.messagebox")
tk.filedialog = mod("tkinter.filedialog")
tk.font = mod("tkinter.font")

import main

# pure helpers
assert main.fmt_duration(75) == "1 min 15 s" and main.fmt_duration(9) == "9 s"
assert main.log_level("[1/5] X Y - 7 subject rows") == "ok"
assert main.log_level("  X: captcha not readable") == "warn"
assert main.log_level("1CR FAILED: boom") == "err"
assert main.log_level("WARNING: Tesseract OCR not found") == "warn"
assert main.file_tag("C:/x/Raw Reval 1CR.csv") == "REVAL" and main.file_tag("a.csv") == "CSV"

app = main.App()
app._on_clog("hello"); app._on_progress((1, 4)); app._update_mode()
app._busy(True, "x"); app._busy(False)
app._rows = [("1CR24EC001", "A B", "8.1", "8.4", "+0.30"), ("1CR24EC002", "C D", "7", "7", "")]
app._sync_files_ui()
print("GUI construction smoke test OK")
dlg = main.CreditsDialog(app, [("21CS51", "Software Engineering"), ("21CS52", "Computer Networks")], {})
dlg._save()
print("Credits dialog smoke test OK")
