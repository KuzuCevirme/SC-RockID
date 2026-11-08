"""
SC Rock ID - Star Citizen mining radar signature identifier.

How it works (and why it is anti-cheat safe):
  * It only takes ordinary screenshots of a small screen area you choose
    (same thing as the Snipping Tool / OBS does).
  * It reads the number with the Windows built-in OCR engine.
  * It never opens, reads or writes the game process, its memory or its files.
"""
import asyncio
import ctypes
import json
import os
import queue
import re
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox

from PIL import Image, ImageEnhance, ImageGrab, ImageOps, ImageTk

import sys

if getattr(sys, "frozen", False):          # running as RockID.exe
    APP_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
else:
    APP_DIR = BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
# Settings (scan area, picked ores, window position) are saved next to the exe / script.
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
# A data.json next to the exe overrides the built-in one, so values can be edited after a patch.
DATA_PATH = os.path.join(APP_DIR, "data.json")
if not os.path.exists(DATA_PATH):
    DATA_PATH = os.path.join(BUNDLE_DIR, "data.json")

# ---------------------------------------------------------------- colours
BG = "#0b1118"
BG2 = "#131c26"
FG = "#cfe3f2"
DIM = "#6b7b8c"
ACCENT = "#4fc3f7"
GREEN = "#3ddc84"
RED = "#ff5c5c"
YELLOW = "#f5c542"
COFFEE_URL = "https://buymeacoffee.com/KuzuCevirme"
REFERRAL_CODE = "STAR-SFQ9-CL7C"
REFERRAL_URL = "https://www.robertsspaceindustries.com/enlist?referral=" + REFERRAL_CODE
TIER_ORDER =["Legendary", "Epic", "Rare", "Uncommon", "Common", "Other"]

DEFAULT_CONFIG = {
    "region": None,            # [left, top, right, bottom] in screen pixels
    "targets": ["quantainium"],
    "tolerance": 0,
    "interval_ms": 400,
    "hold_s": 2.0,
    "show_refinery": False,
    "show_debug": False,
    "pos": [60, 60],
    "alpha": 0.9,
}


# ---------------------------------------------------------------- helpers
def set_dpi_aware():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def virtual_screen():
    m = ctypes.windll.user32.GetSystemMetrics
    return m(76), m(77), m(78), m(79)  # x, y, w, h


def exclude_from_capture(win):
    """Keep our own windows out of screenshots so we never OCR ourselves."""
    try:
        win.update_idletasks()
        hwnd = int(win.wm_frame(), 16)
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x11)  # WDA_EXCLUDEFROMCAPTURE
    except Exception:
        pass


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def fmt(n):
    return f"{n:,}"


# ---------------------------------------------------------------- matching
class Matcher:
    def __init__(self, data):
        self.entries = data["signatures"]
        self.by_id = {e["id"]: e for e in self.entries}

    def match(self, value, tolerance=0):
        out = []
        for e in self.entries:
            base = e["sig"]
            max_n = e.get("max", 12)
            n = round(value / base)
            if 1 <= n <= max_n and abs(value - n * base) <= tolerance:
                out.append((e, n, value - n * base))
        out.sort(key=lambda r: (abs(r[2]), r[1]))
        return out


# ---------------------------------------------------------------- OCR
_FIX = str.maketrans({"O": "0", "o": "0", "D": "0", "Q": "0", "I": "1", "l": "1",
                      "|": "1", "i": "1", "S": "5", "s": "5", "B": "8", "Z": "2",
                      "z": "2", "G": "6"})
_NUM = re.compile(r"\d{1,3}(?:[,.' ]\d{3})+|\d+")


def parse_numbers(lines, lo=1000, hi=200000):
    found = []
    for line in lines:
        words = []
        for w in line:
            digits = sum(c.isdigit() for c in w)
            if digits and digits * 2 >= len(w):
                w = w.translate(_FIX)
            words.append(w)
        text = " ".join(words)
        for m in _NUM.findall(text):
            v = int(re.sub(r"\D", "", m))
            if lo <= v <= hi and v not in found:
                found.append(v)
    return found[:6]


class WinOcr:
    def __init__(self):
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import (BitmapAlphaMode, BitmapPixelFormat,
                                                    SoftwareBitmap)
        from winrt.windows.storage.streams import DataWriter
        self._SB, self._PF, self._AM, self._DW = SoftwareBitmap, BitmapPixelFormat, BitmapAlphaMode, DataWriter
        engine = None
        try:
            engine = OcrEngine.try_create_from_language(Language("en-US"))
        except Exception:
            engine = None
        if engine is None:
            engine = OcrEngine.try_create_from_user_profile_languages()
        if engine is None:
            raise RuntimeError("No Windows OCR language installed.")
        self.engine = engine
        self.max_dim = OcrEngine.max_image_dimension
        self.loop = asyncio.new_event_loop()

    def _bitmap(self, img):
        img = img.convert("RGBA")
        w, h = img.size
        dw = self._DW()
        dw.write_bytes(img.tobytes("raw", "BGRA"))
        return self._SB.create_copy_from_buffer(dw.detach_buffer(), self._PF.BGRA8, w, h,
                                                self._AM.PREMULTIPLIED)

    async def _run(self, sb):
        return await self.engine.recognize_async(sb)

    def lines(self, img):
        res = self.loop.run_until_complete(self._run(self._bitmap(img)))
        return [[w.text for w in ln.words] for ln in res.lines]


def preprocess(img, invert):
    g = ImageOps.grayscale(img)
    g = ImageOps.autocontrast(g, cutoff=1)
    if invert:
        g = ImageOps.invert(g)  # HUD text is bright on dark; OCR prefers dark on bright
    w, h = g.size
    scale = max(1.5, min(5.0, 160 / max(h, 1)))
    g = g.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    pad = ImageOps.expand(g, border=20, fill=255 if invert else 0)
    return pad


def read_numbers(ocr, img):
    raw_all = []
    for invert in (True, False):
        proc = preprocess(img, invert)
        lines = ocr.lines(proc)
        raw_all.append(" | ".join(" ".join(l) for l in lines))
        nums = parse_numbers(lines)
        if nums:
            return nums, raw_all[-1], proc
    return [], " / ".join(r for r in raw_all if r), proc


# ---------------------------------------------------------------- worker thread
class Scanner(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.q = queue.Queue()
        self.stop_flag = False

    def run(self):
        try:
            ocr = WinOcr()
        except Exception as e:  # noqa
            self.q.put(("fatal", str(e)))
            return
        while not self.stop_flag:
            cfg = self.app.cfg
            region = cfg.get("region")
            if self.app.paused or not region:
                time.sleep(0.2)
                continue
            t0 = time.time()
            try:
                img = ImageGrab.grab(bbox=tuple(region), all_screens=True)
                nums, raw, proc = read_numbers(ocr, img)
                thumb = proc.copy()
                thumb.thumbnail((260, 70))
                self.q.put(("ok", nums, raw, thumb))
            except Exception as e:
                self.q.put(("err", str(e)))
            dt = time.time() - t0
            time.sleep(max(0.05, cfg.get("interval_ms", 400) / 1000 - dt))


# ---------------------------------------------------------------- region picker
class RegionPicker:
    def __init__(self, app):
        self.app = app
        app.root.withdraw()
        app.root.update()
        time.sleep(0.15)
        vx, vy, vw, vh = virtual_screen()
        self.vx, self.vy = vx, vy
        shot = ImageGrab.grab(all_screens=True)
        dim = ImageEnhance.Brightness(shot).enhance(0.55)
        self.top = t = tk.Toplevel(app.root)
        t.overrideredirect(True)
        t.attributes("-topmost", True)
        t.geometry(f"{vw}x{vh}{vx:+d}{vy:+d}")
        self.c = c = tk.Canvas(t, width=vw, height=vh, highlightthickness=0, cursor="cross", bg="black")
        c.pack()
        self.photo = ImageTk.PhotoImage(dim)
        c.create_image(0, 0, anchor="nw", image=self.photo)
        px, py = -vx + 40, -vy + 40  # top-left of primary monitor
        c.create_rectangle(px - 10, py - 10, px + 760, py + 80, fill=BG, outline=ACCENT)
        c.create_text(px, py, anchor="nw", fill=FG, font=("Segoe UI", 14, "bold"),
                      text="Drag a box tightly around the mining scan signature number (RS value).")
        c.create_text(px, py + 36, anchor="nw", fill=DIM, font=("Segoe UI", 11),
                      text="Tip: make it a bit bigger than the number so it still fits when it moves.   Esc = cancel")
        self.start = None
        self.rect = None
        c.bind("<ButtonPress-1>", self.on_down)
        c.bind("<B1-Motion>", self.on_move)
        c.bind("<ButtonRelease-1>", self.on_up)
        t.bind("<Escape>", lambda e: self.finish(None))
        t.focus_force()

    def on_down(self, e):
        self.start = (e.x, e.y)
        self.rect = self.c.create_rectangle(e.x, e.y, e.x, e.y, outline=GREEN, width=2)

    def on_move(self, e):
        if self.start:
            self.c.coords(self.rect, self.start[0], self.start[1], e.x, e.y)

    def on_up(self, e):
        if not self.start:
            return
        x0, y0 = self.start
        l, r = sorted((x0, e.x))
        t_, b = sorted((y0, e.y))
        if r - l < 8 or b - t_ < 8:
            self.finish(None)
            return
        self.finish([l + self.vx, t_ + self.vy, r + self.vx, b + self.vy])

    def finish(self, region):
        self.top.destroy()
        self.app.root.deiconify()
        self.app.root.attributes("-topmost", True)
        if region:
            self.app.cfg["region"] = region
            self.app.save()
        self.app.refresh_status()


# ---------------------------------------------------------------- ore picker / settings
class OrePicker:
    def __init__(self, app):
        self.app = app
        self.win = w = tk.Toplevel(app.root)
        w.title("SC Rock ID - what are you looking for?")
        w.configure(bg=BG)
        w.attributes("-topmost", True)
        w.resizable(False, False)
        self.vars = {}
        targets = set(app.cfg["targets"])

        top = tk.Frame(w, bg=BG)
        top.pack(fill="x", padx=10, pady=(10, 4))
        tk.Label(top, text="Pick ores to highlight GREEN  (everything else = RED)", bg=BG, fg=FG,
                 font=("Segoe UI", 11, "bold")).pack(side="left")

        btns = tk.Frame(w, bg=BG)
        btns.pack(fill="x", padx=10)
        for label, fn in (("All", self.all_), ("None", self.none), ("Rare and better", self.rare_up),
                          ("Legendary only", self.legendary)):
            self._btn(btns, label, fn).pack(side="left", padx=(0, 6))

        grid = tk.Frame(w, bg=BG)
        grid.pack(padx=10, pady=8)
        by_tier = {}
        for e in app.matcher.entries:
            by_tier.setdefault(e["tier"], []).append(e)
        col = 0
        for tier in TIER_ORDER:
            items = by_tier.get(tier)
            if not items:
                continue
            f = tk.Frame(grid, bg=BG2, padx=8, pady=6)
            f.grid(row=0, column=col, sticky="n", padx=3)
            col += 1
            tk.Label(f, text=tier.upper(), bg=BG2, fg=ACCENT, font=("Segoe UI", 9, "bold")).pack(anchor="w")
            for e in items:
                v = tk.BooleanVar(value=e["id"] in targets)
                self.vars[e["id"]] = v
                tk.Checkbutton(f, text=f"{e['name']}  {e['sig']}", variable=v, command=self.apply,
                               bg=BG2, fg=FG, selectcolor=BG, activebackground=BG2, activeforeground=FG,
                               font=("Segoe UI", 9), anchor="w").pack(anchor="w")

        s = tk.Frame(w, bg=BG)
        s.pack(fill="x", padx=10, pady=(0, 10))
        self.tol = self._spin(s, "OCR tolerance (+/-)", 0, 50, app.cfg["tolerance"])
        self.intv = self._spin(s, "Scan every (ms)", 150, 3000, app.cfg["interval_ms"], inc=50)
        self.alpha = self._spin(s, "Opacity %", 30, 100, int(app.cfg["alpha"] * 100), inc=5)
        self._btn(s, "Close", w.destroy).pack(side="right")
        exclude_from_capture(w)

    def _btn(self, parent, text, cmd):
        return tk.Button(parent, text=text, command=cmd, bg=BG2, fg=FG, activebackground=ACCENT,
                         relief="flat", font=("Segoe UI", 9), padx=8)

    def _spin(self, parent, label, lo, hi, val, inc=1):
        tk.Label(parent, text=label, bg=BG, fg=DIM, font=("Segoe UI", 9)).pack(side="left", padx=(0, 4))
        var = tk.IntVar(value=val)
        sp = tk.Spinbox(parent, from_=lo, to=hi, increment=inc, textvariable=var, width=6,
                        command=self.apply, bg=BG2, fg=FG, buttonbackground=BG2, relief="flat")
        sp.bind("<KeyRelease>", lambda e: self.apply())
        sp.pack(side="left", padx=(0, 14))
        return var

    def _set(self, pred):
        for e in self.app.matcher.entries:
            self.vars[e["id"]].set(pred(e))
        self.apply()

    def all_(self):
        self._set(lambda e: True)

    def none(self):
        self._set(lambda e: False)

    def rare_up(self):
        self._set(lambda e: e["tier"] in ("Legendary", "Epic", "Rare"))

    def legendary(self):
        self._set(lambda e: e["tier"] == "Legendary")

    def apply(self):
        cfg = self.app.cfg
        cfg["targets"] = [k for k, v in self.vars.items() if v.get()]
        try:
            cfg["tolerance"] = max(0, int(self.tol.get()))
            cfg["interval_ms"] = max(150, int(self.intv.get()))
            cfg["alpha"] = max(0.3, min(1.0, int(self.alpha.get()) / 100))
        except (tk.TclError, ValueError):
            pass
        self.app.root.attributes("-alpha", cfg["alpha"])
        self.app.save()
        self.app.render()


# ---------------------------------------------------------------- main HUD
class App:
    def __init__(self):
        self.data = load_json(DATA_PATH, None)
        if not self.data:
            raise SystemExit("data.json missing or broken")
        self.matcher = Matcher(self.data)
        self.cfg = dict(DEFAULT_CONFIG)
        self.cfg.update(load_json(CONFIG_PATH, {}))
        self.paused = False
        self.nums = []
        self.last_hit_time = 0
        self.raw = ""
        self.thumb = None
        self.error = ""
        self.countdown = 0
        self._pick_job = None

        self.root = r = tk.Tk()
        r.title("SC Rock ID")
        r.overrideredirect(True)
        r.attributes("-topmost", True)
        r.attributes("-alpha", self.cfg["alpha"])
        r.configure(bg=BG, highlightbackground=ACCENT, highlightthickness=1)
        x, y = self.cfg["pos"]
        r.geometry(f"+{x}+{y}")

        # title bar
        bar = tk.Frame(r, bg=BG2)
        bar.pack(fill="x")
        title = tk.Label(bar, text="  ◆ ROCK ID", bg=BG2, fg=ACCENT, font=("Segoe UI", 10, "bold"))
        title.pack(side="left", pady=3)
        for wdg in (bar, title):
            wdg.bind("<ButtonPress-1>", self._drag_start)
            wdg.bind("<B1-Motion>", self._drag_move)
            wdg.bind("<ButtonRelease-1>", self._drag_end)
        self._tbtn(bar, "✕", self.quit, RED).pack(side="right")
        self.pause_btn = self._tbtn(bar, "⏸", self.toggle_pause)
        self.pause_btn.pack(side="right")
        self._tbtn(bar, "⌖", self.start_pick).pack(side="right")
        self.ref_btn = self._tbtn(bar, "Refinery", self.toggle_refinery)
        self.ref_btn.pack(side="right")
        self._tbtn(bar, "Ores", lambda: OrePicker(self)).pack(side="right")
        self._tbtn(bar, "?", self.toggle_debug).pack(side="right")

        self.body = tk.Frame(r, bg=BG, padx=10, pady=6)
        self.body.pack(fill="both")
        self.targets_lbl = tk.Label(r, bg=BG, fg=DIM, font=("Segoe UI", 8), anchor="w",
                                    justify="left", wraplength=360, padx=10)
        self.targets_lbl.pack(fill="x")
        self.ref_frame = tk.Frame(r, bg=BG2, padx=10, pady=6)
        self.dbg_frame = tk.Frame(r, bg=BG, padx=10, pady=4)
        self.dbg_img = tk.Label(self.dbg_frame, bg=BG)
        self.dbg_img.pack(anchor="w")
        self.dbg_txt = tk.Label(self.dbg_frame, bg=BG, fg=DIM, font=("Consolas", 8), anchor="w",
                                justify="left", wraplength=360)
        self.dbg_txt.pack(anchor="w")

        # footer: support links
        foot = tk.Frame(r, bg=BG2, padx=6, pady=4)
        foot.pack(side="bottom", fill="x")
        self._link_btn(foot, "☕ Buy me a coffee", lambda: webbrowser.open(COFFEE_URL),
                       fg="#1a1a1a", bg="#ffdd00").pack(side="left")
        tk.Label(foot, text="  Referral:", bg=BG2, fg=DIM, font=("Segoe UI", 8)).pack(side="left")
        tk.Label(foot, text=REFERRAL_CODE, bg=BG2, fg=FG, font=("Consolas", 9, "bold")).pack(side="left", padx=(3, 4))
        self.copy_btn = self._link_btn(foot, "Copy", self.copy_referral)
        self.copy_btn.pack(side="left", padx=(0, 3))
        self._link_btn(foot, "Open ↗", lambda: webbrowser.open(REFERRAL_URL)).pack(side="left")

        r.bind("<Button-3>", self._menu)
        exclude_from_capture(r)

        self.scanner = Scanner(self)
        self.scanner.start()
        self.render()
        r.after(100, self.poll)

    # ---- small widgets
    def _tbtn(self, parent, text, cmd, hover=ACCENT):
        b = tk.Label(parent, text=f" {text} ", bg=BG2, fg=FG, font=("Segoe UI", 10), cursor="hand2")
        b.bind("<Button-1>", lambda e: cmd())
        b.bind("<Enter>", lambda e: b.configure(fg=hover))
        b.bind("<Leave>", lambda e: b.configure(fg=FG))
        return b

    def _link_btn(self, parent, text, cmd, fg=FG, bg=BG):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=ACCENT,
                         activeforeground=BG, relief="flat", bd=0, cursor="hand2",
                         font=("Segoe UI", 8, "bold"), padx=6, pady=1)

    def copy_referral(self):
        self.root.clipboard_clear()
        self.root.clipboard_append(REFERRAL_CODE)
        self.root.update()  # keep it on the clipboard
        self.copy_btn.configure(text="Copied ✓", fg=GREEN)
        self.root.after(1500, lambda: self.copy_btn.configure(text="Copy", fg=FG))

    def _menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        m.add_command(label="Pick ores…", command=lambda: OrePicker(self))
        m.add_command(label="Set scan area…", command=self.start_pick)
        m.add_command(label="Toggle refinery", command=self.toggle_refinery)
        m.add_command(label="Toggle debug", command=self.toggle_debug)
        m.add_separator()
        m.add_command(label="Quit", command=self.quit)
        m.tk_popup(e.x_root, e.y_root)

    def _drag_start(self, e):
        self._dx, self._dy = e.x_root - self.root.winfo_x(), e.y_root - self.root.winfo_y()

    def _drag_move(self, e):
        self.root.geometry(f"+{e.x_root - self._dx}+{e.y_root - self._dy}")

    def _drag_end(self, e):
        self.cfg["pos"] = [self.root.winfo_x(), self.root.winfo_y()]
        self.save()

    # ---- actions
    PICK_DELAY = 5  # seconds to switch to the game before the screen is frozen

    def start_pick(self):
        """Count down so you can switch to the game, then freeze the screen for picking."""
        if self.countdown:
            return
        self.countdown = self.PICK_DELAY
        self.render()
        self._pick_job = self.root.after(1000, self._pick_tick)

    def _pick_tick(self):
        self.countdown -= 1
        if self.countdown <= 0:
            self.pick_now()
        else:
            self.render()
            self._pick_job = self.root.after(1000, self._pick_tick)

    def pick_now(self):
        if self._pick_job:
            self.root.after_cancel(self._pick_job)
            self._pick_job = None
        self.countdown = 0
        RegionPicker(self)

    def cancel_pick(self):
        if self._pick_job:
            self.root.after_cancel(self._pick_job)
            self._pick_job = None
        self.countdown = 0
        self.render()

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause_btn.configure(text=" ▶ " if self.paused else " ⏸ ")
        self.render()

    def toggle_refinery(self):
        self.cfg["show_refinery"] = not self.cfg["show_refinery"]
        self.save()
        self.render()

    def toggle_debug(self):
        self.cfg["show_debug"] = not self.cfg["show_debug"]
        self.save()
        self.render()

    def save(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, indent=2)
        except Exception:
            pass

    def quit(self):
        self.scanner.stop_flag = True
        self.save()
        self.root.destroy()

    def refresh_status(self):
        self.render()

    # ---- scanner results
    def poll(self):
        try:
            while True:
                msg = self.scanner.q.get_nowait()
                if msg[0] == "ok":
                    _, nums, raw, thumb = msg
                    self.raw, self.thumb, self.error = raw, thumb, ""
                    if nums:
                        self.nums = nums
                        self.last_hit_time = time.time()
                    elif time.time() - self.last_hit_time > self.cfg["hold_s"]:
                        self.nums = []
                    self.render()
                elif msg[0] == "err":
                    self.error = msg[1]
                    self.render()
                elif msg[0] == "fatal":
                    messagebox.showerror("SC Rock ID", "Windows OCR could not start:\n" + msg[1] +
                                         "\n\nInstall an OCR language: Settings > Time & language > "
                                         "Language > add English (United States).", parent=self.root)
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    # ---- drawing
    def render(self):
        for w in self.body.winfo_children():
            w.destroy()
        targets = set(self.cfg["targets"])
        tol = self.cfg["tolerance"]
        detected_ids = []

        if self.countdown:
            self._line(f"Switch to the game now…  {self.countdown}", YELLOW, size=13, bold=True)
            self._line("The screen will freeze, then drag a box around the signature number.", FG, size=9)
            row = tk.Frame(self.body, bg=BG)
            row.pack(anchor="w", pady=(6, 2))
            self._big_btn(row, "Pick now", self.pick_now).pack(side="left", padx=(0, 6))
            self._big_btn(row, "Cancel", self.cancel_pick, bg=BG2, fg=FG).pack(side="left")
        elif not self.cfg.get("region"):
            self._line("Set up the scan area", ACCENT, size=13, bold=True)
            self._line("1.  Start Star Citizen (Borderless or Windowed mode).", FG, size=9)
            self._line("2.  Open the mining scanner so a signature number is on screen.", FG, size=9)
            self._line(f"3.  Click the button below. You get {self.PICK_DELAY} seconds to switch to the game.", FG, size=9)
            self._big_btn(self.body, "📍  Pick signature area", self.start_pick).pack(anchor="w", pady=(8, 2))
        elif self.paused:
            self._line("Paused", DIM)
        elif not self.nums:
            self._line("Waiting for signature…", DIM)
        else:
            for v in self.nums:
                matches = self.matcher.match(v, tol)
                row = tk.Frame(self.body, bg=BG)
                row.pack(fill="x", anchor="w", pady=1)
                if not matches:
                    color, text = DIM, "unknown signature"
                else:
                    wanted = [m for m in matches if m[0]["id"] in targets]
                    if len(wanted) == len(matches):
                        color = GREEN
                    elif wanted:
                        color = YELLOW
                    else:
                        color = RED
                    parts = []
                    for e, n, diff in matches[:3]:
                        detected_ids.append(e["id"])
                        s = f"{e['name']} ×{n}"
                        if diff:
                            s += f" ({diff:+d})"
                        parts.append(s)
                    text = "  or  ".join(parts)
                tk.Label(row, text="●", bg=BG, fg=color, font=("Segoe UI", 16)).pack(side="left")
                tk.Label(row, text=fmt(v), bg=BG, fg=FG, font=("Consolas", 15, "bold"),
                         width=7, anchor="e").pack(side="left", padx=(2, 8))
                tk.Label(row, text=text, bg=BG, fg=color, font=("Segoe UI", 12, "bold"),
                         anchor="w", justify="left", wraplength=300).pack(side="left")
                if matches and matches[0][0].get("note"):
                    tk.Label(self.body, text="     " + matches[0][0]["note"], bg=BG, fg=DIM,
                             font=("Segoe UI", 8), anchor="w").pack(fill="x")
        if self.error:
            self._line("Capture error: " + self.error[:80], RED, size=8)

        names = [self.matcher.by_id[t]["name"] for t in self.cfg["targets"] if t in self.matcher.by_id]
        self.targets_lbl.configure(
            text=("Looking for: " + ", ".join(names)) if names else "Looking for: nothing (click Ores)")

        # refinery panel
        self.ref_frame.pack_forget()
        self.dbg_frame.pack_forget()
        self.ref_btn.configure(fg=ACCENT if self.cfg["show_refinery"] else FG)
        if self.cfg["show_refinery"]:
            self._render_refinery(detected_ids)
            self.ref_frame.pack(fill="x", pady=(4, 0))
        if self.cfg["show_debug"]:
            reg = self.cfg.get("region")
            self.dbg_txt.configure(text=f"area {reg}\nOCR: {self.raw[:200] or '-'}")
            if self.thumb is not None:
                self._dbg_photo = ImageTk.PhotoImage(self.thumb)
                self.dbg_img.configure(image=self._dbg_photo)
            self.dbg_frame.pack(fill="x")

    def _line(self, text, color, size=11, bold=False):
        tk.Label(self.body, text=text, bg=BG, fg=color, font=("Segoe UI", size, "bold" if bold else "normal"),
                 anchor="w", justify="left", wraplength=380).pack(fill="x")

    def _big_btn(self, parent, text, cmd, bg=ACCENT, fg=BG):
        return tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg, activebackground=GREEN,
                         activeforeground=BG, relief="flat", bd=0, cursor="hand2",
                         font=("Segoe UI", 10, "bold"), padx=12, pady=4)

    def _render_refinery(self, detected_ids):
        for w in self.ref_frame.winfo_children():
            w.destroy()
        tk.Label(self.ref_frame, text="BEST REFINERIES (yield bonus)", bg=BG2, fg=ACCENT,
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x")
        ids = []
        for i in detected_ids + list(self.cfg["targets"]):
            e = self.matcher.by_id.get(i)
            if e and e["cat"] == "Ship" and i not in ids:
                ids.append(i)
        if not ids:
            tk.Label(self.ref_frame, text="Select ship ores to see refineries.", bg=BG2, fg=DIM,
                     font=("Segoe UI", 9), anchor="w").pack(fill="x")
        refs = self.data.get("refineries", {})
        no_bonus = []
        shown = 0
        for i in ids:
            if shown >= 8:
                break
            ranked = sorted(((b["bonus"][i], name, b["system"]) for name, b in refs.items() if i in b["bonus"]),
                            key=lambda t: -t[0])
            name = self.matcher.by_id[i]["name"]
            hl = i in detected_ids
            if ranked:
                top = ranked[0][0]
                best = [r for r in ranked if r[0] == top]
                txt = ", ".join(n if s in n else f"{n} ({s})" for _, n, s in best[:3])
                if len(best) > 3:
                    txt += f" +{len(best) - 3} more"
                others = [r for r in ranked if r[0] < top][:2]
                if others:
                    txt += "   |  " + ", ".join(f"{n} +{p}%" for p, n, _ in others)
                line = f"{name}: +{top}%  {txt}"
            else:
                no_bonus.append(name)
                continue
            shown += 1
            tk.Label(self.ref_frame, text=line, bg=BG2, fg=GREEN if hl else FG,
                     font=("Segoe UI", 9, "bold" if hl else "normal"), anchor="w", justify="left",
                     wraplength=380).pack(fill="x")
        if no_bonus:
            tk.Label(self.ref_frame, text="No station bonus (any refinery): " + ", ".join(no_bonus),
                     bg=BG2, fg=DIM, font=("Segoe UI", 8), anchor="w", justify="left",
                     wraplength=380).pack(fill="x")
        tip = self.data.get("method_tip")
        if tip:
            tk.Label(self.ref_frame, text=tip, bg=BG2, fg=DIM, font=("Segoe UI", 8), anchor="w",
                     justify="left", wraplength=380).pack(fill="x", pady=(3, 0))
        tk.Label(self.ref_frame, text=f"Data: patch {self.data.get('patch', '?')} - confirm at terminal",
                 bg=BG2, fg=DIM, font=("Segoe UI", 7), anchor="w").pack(fill="x")

    def run(self):
        self.root.mainloop()


def selftest():
    """RockID.exe --selftest : checks OCR + data, writes selftest.txt next to the exe."""
    from PIL import ImageDraw, ImageFont
    out = []
    try:
        data = load_json(DATA_PATH, None)
        out.append(f"data: {DATA_PATH} ({len(data['signatures'])} signatures)")
        img = Image.new("RGB", (180, 44), (8, 20, 30))
        ImageDraw.Draw(img).text((8, 8), "6,340", fill=(140, 220, 255),
                                 font=ImageFont.truetype("arial.ttf", 26))
        nums, raw, _ = read_numbers(WinOcr(), img)
        hits = [(e["name"], n) for e, n, _ in Matcher(data).match(nums[0])] if nums else []
        out.append(f"ocr: {nums} raw={raw!r} -> {hits}")
        out.append("RESULT: PASS" if hits == [("Quantainium", 2)] else "RESULT: FAIL")
    except Exception as e:
        out.append(f"RESULT: ERROR {e!r}")
    with open(os.path.join(APP_DIR, "selftest.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))


if __name__ == "__main__":
    set_dpi_aware()
    if "--selftest" in sys.argv:
        selftest()
        raise SystemExit(0)
    try:
        import winrt.windows.media.ocr  # noqa: F401
    except ImportError:
        r = tk.Tk()
        r.withdraw()
        messagebox.showerror("SC Rock ID", "Missing OCR package. Run install.bat first.")
        raise SystemExit(1)
    App().run()
