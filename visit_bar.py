"""visit_bar.py — a ~0.5 mm tall visit timer bar pinned to the top of the screen.

Usage:
    python visit_bar.py [minutes]

    minutes   total visit length in minutes (overrides the saved setting)

Behaviour:
    - Reserves a thin strip along the top edge of the primary monitor, so
      maximized windows sit below it (registered as a Windows AppBar).
    - Bar height is at most 0.5 mm (computed from the screen DPI; usually 1-2 px).
    - The strip fills left-to-right as the visit time elapses:
        green     -> more than the yellow threshold left (default 5 min)
        yellow    -> yellow threshold or less left
        red flash -> red threshold or less left (default 2 min), and overtime
    - A small always-on-top stopwatch shows elapsed and remaining time.
      Drag it anywhere with the mouse; right-click it to hide it.
    - Right-click the bar to open the settings window (total time, yellow and
      red thresholds). Settings are saved to %APPDATA%\\visit-bar\\settings.json
      and applying them restarts the timer.
    - Triple-click the bar or press Escape to close everything and restore
      the work area.
"""

import argparse
import ctypes
import json
import os
import sys
import time
import tkinter as tk
from ctypes import wintypes

user32 = ctypes.windll.user32
shell32 = ctypes.windll.shell32
gdi32 = ctypes.windll.gdi32

ABM_NEW = 0x00000000
ABM_REMOVE = 0x00000001
ABM_SETPOS = 0x00000003
ABE_TOP = 1

LOGPIXELSY = 90
SM_CXSCREEN = 0

MM_PER_INCH = 25.4
BAR_MM = 0.5

COLOR_BG = "#101010"
COLOR_GREEN = "#00c832"
COLOR_YELLOW = "#ffd800"
COLOR_RED = "#ff2020"
COLOR_RED_DIM = "#400000"

TICK_MS = 100

DEFAULT_SETTINGS = {
    "total_minutes": 12.0,
    "yellow_minutes": 5.0,
    "red_minutes": 2.0,
}


class APPBARDATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uCallbackMessage", wintypes.UINT),
        ("uEdge", wintypes.UINT),
        ("rc", wintypes.RECT),
        ("lParam", wintypes.LPARAM),
    ]


# --- Settings persistence ---

def config_path():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "vihsihtlihmiht", "settings.json")


def load_settings():
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    for key in settings:
        value = data.get(key)
        if isinstance(value, (int, float)) and value > 0:
            settings[key] = float(value)
    return settings


def save_settings(settings):
    path = config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)


# --- Platform helpers ---

def make_dpi_aware():
    """Ask Windows not to scale our window, so 0.5 mm stays 0.5 mm."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        user32.SetProcessDPIAware()


def half_mm_in_pixels():
    """Convert 0.5 mm to physical pixels, rounding down (never exceed 0.5 mm)."""
    hdc = user32.GetDC(0)
    try:
        dpi = gdi32.GetDeviceCaps(hdc, LOGPIXELSY)
    finally:
        user32.ReleaseDC(0, hdc)
    return max(1, int(dpi * BAR_MM / MM_PER_INCH))


def fmt_mmss(seconds):
    m, s = divmod(int(abs(seconds)), 60)
    return f"{m:02d}:{s:02d}"


class VisitBar:
    def __init__(self, settings):
        self.settings = dict(settings)
        self.settings_window = None
        self.apply_settings()
        self.start = time.monotonic()

        self.root = tk.Tk()
        self.root.overrideredirect(True)          # no title bar / borders
        self.root.attributes("-topmost", True)    # always on top
        self.root.configure(bg=COLOR_BG)

        self.width = user32.GetSystemMetrics(SM_CXSCREEN)
        self.height = half_mm_in_pixels()

        self.canvas = tk.Canvas(
            self.root,
            width=self.width,
            height=self.height,
            bg=COLOR_BG,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.pack()
        self.fill_rect = self.canvas.create_rectangle(
            0, 0, 0, self.height, fill=COLOR_GREEN, width=0
        )

        self.root.bind("<Escape>", lambda e: self.close())
        self.root.bind("<Triple-Button-1>", lambda e: self.close())
        self.root.bind("<Button-3>", lambda e: self.open_settings())
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._build_stopwatch()

        self.root.update_idletasks()
        self.root.update()

        self._register_appbar()
        self.root.after(TICK_MS, self._tick)

    def apply_settings(self, restart=False):
        self.total_seconds = max(1.0, self.settings["total_minutes"] * 60)
        self.yellow_seconds = self.settings["yellow_minutes"] * 60
        self.red_seconds = self.settings["red_minutes"] * 60
        if restart:
            self.start = time.monotonic()

    # --- Settings window ---

    def open_settings(self):
        if self.settings_window is not None:
            try:
                self.settings_window.lift()
                self.settings_window.focus_force()
                return
            except tk.TclError:
                self.settings_window = None

        win = tk.Toplevel(self.root)
        self.settings_window = win
        win.title("vihsihtlihmiht settings")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.configure(padx=14, pady=12)

        fields = [
            ("Total visit length (minutes)", "total_minutes"),
            ("Bar turns yellow at (minutes left)", "yellow_minutes"),
            ("Bar flashes red at (minutes left)", "red_minutes"),
        ]
        entries = {}
        for row, (label, key) in enumerate(fields):
            tk.Label(win, text=label).grid(row=row, column=0, sticky="w", pady=3)
            entry = tk.Entry(win, width=8)
            entry.insert(0, f"{self.settings[key]:g}")
            entry.grid(row=row, column=1, sticky="e", padx=(10, 0), pady=3)
            entries[key] = entry

        status = tk.Label(win, text="", fg=COLOR_RED)
        status.grid(row=len(fields), column=0, columnspan=2, sticky="w")

        def apply():
            try:
                values = {k: float(e.get()) for k, e in entries.items()}
            except ValueError:
                status.configure(text="Please enter numbers only.")
                return
            if any(v <= 0 for v in values.values()):
                status.configure(text="All values must be greater than zero.")
                return
            if values["red_minutes"] > values["yellow_minutes"]:
                status.configure(text="Red threshold must be at or below yellow.")
                return
            self.settings.update(values)
            save_settings(self.settings)
            self.apply_settings(restart=True)
            win.destroy()
            self.settings_window = None

        buttons = tk.Frame(win)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=2, pady=(10, 0), sticky="e")
        tk.Button(buttons, text="Apply and restart", command=apply).pack(side="left", padx=4)
        tk.Button(buttons, text="Close", command=win.destroy).pack(side="left")

    # --- Stopwatch window ---

    def _build_stopwatch(self):
        self.watch = tk.Toplevel(self.root)
        self.watch.overrideredirect(True)
        self.watch.attributes("-topmost", True)
        self.watch.configure(bg=COLOR_BG)

        self.elapsed_var = tk.StringVar(value="00:00")
        self.remaining_var = tk.StringVar()

        self.elapsed_label = tk.Label(
            self.watch,
            textvariable=self.elapsed_var,
            fg=COLOR_GREEN,
            bg=COLOR_BG,
            font=("Consolas", 14, "bold"),
        )
        self.elapsed_label.pack(padx=10, pady=(4, 0))
        self.remaining_label = tk.Label(
            self.watch,
            textvariable=self.remaining_var,
            fg="#888888",
            bg=COLOR_BG,
            font=("Consolas", 9),
        )
        self.remaining_label.pack(padx=10, pady=(0, 4))

        self.watch.bind("<Button-1>", self._drag_start)
        self.watch.bind("<B1-Motion>", self._drag_move)
        self.watch.bind("<Button-3>", lambda e: self.watch.withdraw())
        self.watch.bind("<Escape>", lambda e: self.close())

        self.watch.update_idletasks()
        x = self.width - self.watch.winfo_reqwidth() - 8
        y = self.height + 4
        self.watch.geometry(f"+{x}+{y}")

    def _drag_start(self, event):
        self._drag = (event.x, event.y)

    def _drag_move(self, event):
        x = self.watch.winfo_x() + event.x - self._drag[0]
        y = self.watch.winfo_y() + event.y - self._drag[1]
        self.watch.geometry(f"+{x}+{y}")

    # --- AppBar handling (reserves screen space) ---

    def _hwnd(self):
        frame = self.root.wm_frame()
        return int(frame, 16) if isinstance(frame, str) else int(frame)

    def _register_appbar(self):
        self.abd = APPBARDATA()
        self.abd.cbSize = ctypes.sizeof(APPBARDATA)
        self.abd.hWnd = self._hwnd()
        self.abd.uCallbackMessage = 0
        shell32.SHAppBarMessage(ABM_NEW, ctypes.byref(self.abd))

        self.abd.uEdge = ABE_TOP
        self.abd.rc.left = 0
        self.abd.rc.top = 0
        self.abd.rc.right = self.width
        self.abd.rc.bottom = self.height
        shell32.SHAppBarMessage(ABM_SETPOS, ctypes.byref(self.abd))

        rc = self.abd.rc
        self.root.geometry(
            f"{rc.right - rc.left}x{rc.bottom - rc.top}+{rc.left}+{rc.top}"
        )

    def _unregister_appbar(self):
        shell32.SHAppBarMessage(ABM_REMOVE, ctypes.byref(self.abd))

    # --- Timer / drawing ---

    def _tick(self):
        elapsed = time.monotonic() - self.start
        remaining = self.total_seconds - elapsed

        # Stopwatch readout: elapsed counts up; second line shows time left,
        # or overtime in red once the visit has run past its length.
        self.elapsed_var.set(fmt_mmss(elapsed))
        if remaining >= 0:
            self.remaining_var.set("-" + fmt_mmss(remaining))
            self.remaining_label.configure(fg="#888888")
        else:
            self.remaining_var.set("+" + fmt_mmss(remaining))
            self.remaining_label.configure(fg=COLOR_RED)

        if remaining <= self.red_seconds:
            # Flash the whole strip red (dim phase gives the blink effect).
            on = int(time.monotonic() * 2.5) % 2 == 0
            color = COLOR_RED if on else COLOR_RED_DIM
            self.canvas.itemconfig(self.fill_rect, fill=color)
            self.canvas.coords(self.fill_rect, 0, 0, self.width, self.height)
        else:
            color = COLOR_YELLOW if remaining <= self.yellow_seconds else COLOR_GREEN
            fraction = min(1.0, elapsed / self.total_seconds)
            self.canvas.itemconfig(self.fill_rect, fill=color)
            self.canvas.coords(
                self.fill_rect, 0, 0, int(fraction * self.width), self.height
            )
            self.elapsed_label.configure(fg=color)

        self.root.after(TICK_MS, self._tick)

    # --- Lifecycle ---

    def close(self):
        try:
            self._unregister_appbar()
        finally:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


def main():
    parser = argparse.ArgumentParser(description="Tiny top-of-screen visit timer bar.")
    parser.add_argument(
        "minutes",
        type=float,
        nargs="?",
        default=None,
        help="total visit length in minutes (overrides the saved setting)",
    )
    args = parser.parse_args()

    settings = load_settings()
    if args.minutes is not None:
        settings["total_minutes"] = args.minutes

    make_dpi_aware()
    VisitBar(settings).run()


if __name__ == "__main__":
    main()
