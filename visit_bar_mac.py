"""visit_bar_mac.py — a ~0.5 mm tall visit timer bar pinned below the menu bar.

macOS port of visit_bar.py. Standalone: shares no code with the Windows or
Linux versions, so the three can be changed independently.

    ⚠️  UNTESTED. Written without access to a Mac. Please report what breaks:
        https://github.com/robbie-med/vihsihtlihmiht/issues

Usage:
    python3 visit_bar_mac.py [minutes]

    minutes   total visit length in minutes (overrides the saved setting)

Differences from the Windows and Linux versions:
    - No space reservation. Windows has AppBars and X11 has struts; macOS has
      no equivalent available to an ordinary app (it needs the private
      SetSystemUIMode, or a full Cocoa agent app), so the bar is a plain
      floating window that stays on top. Maximized and full-screen windows
      will slide underneath it.
    - The bar sits just *below* the menu bar rather than at y=0, because a
      window at y=0 is drawn behind the menu bar and would be invisible. If it
      still looks hidden — notched MacBooks have a taller menu bar — nudge it
      down:
          VISIT_BAR_TOP_OFFSET=38 python3 visit_bar_mac.py

Everything else matches: traffic-light fill, flashing red, draggable
stopwatch, right-click settings. Settings are saved to
~/Library/Application Support/vihsihtlihmiht/settings.json.
"""

import argparse
import json
import os
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

APP_NAME = "vihsihtlihmiht"

BAR_MM = 0.5
# Sanity clamp, in case the display reports an implausible physical size.
MAX_BAR_PX = 8

# A window at y=0 hides behind the menu bar. 25 pt suits most Macs; notched
# models need more. Override with VISIT_BAR_TOP_OFFSET.
MENU_BAR_PX = 25

COLOR_BG = "#101010"
COLOR_GREEN = "#00c832"
COLOR_YELLOW = "#ffd800"
COLOR_RED = "#ff2020"
COLOR_RED_DIM = "#400000"

TICK_MS = 100

MONO_FONTS = ("SF Mono", "Menlo", "Monaco", "Courier New")

DEFAULT_SETTINGS = {
    "total_minutes": 12.0,
    "yellow_minutes": 5.0,
    "red_minutes": 2.0,
}


# --- Settings persistence ---

def config_path():
    return os.path.join(
        os.path.expanduser("~"), "Library", "Application Support",
        APP_NAME, "settings.json",
    )


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


def top_offset():
    try:
        return int(os.environ.get("VISIT_BAR_TOP_OFFSET", MENU_BAR_PX))
    except ValueError:
        return MENU_BAR_PX


def mono_font(root):
    try:
        available = set(tkfont.families(root))
    except tk.TclError:
        available = set()
    for family in MONO_FONTS:
        if family in available:
            return family
    return "Courier"


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

        self.x = 0
        self.y = top_offset()
        self.width = self.root.winfo_screenwidth()
        self.height = self._half_mm_in_pixels()

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

        self.root.geometry(f"{self.width}x{self.height}+{self.x}+{self.y}")

        self.root.bind("<Escape>", lambda e: self.close())
        self.root.bind("<Triple-Button-1>", lambda e: self.close())
        self.root.bind("<Button-2>", lambda e: self.open_settings())
        self.root.bind("<Control-Button-1>", lambda e: self.open_settings())
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._build_stopwatch()

        self.root.update_idletasks()
        self.root.update()
        self.root.after(TICK_MS, self._tick)

    def _half_mm_in_pixels(self):
        """0.5 mm in physical pixels, rounded down (never exceed 0.5 mm)."""
        try:
            px = int(self.root.winfo_fpixels(f"{BAR_MM}m"))
        except tk.TclError:
            px = 1
        return max(1, min(px, MAX_BAR_PX))

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
        win.title(f"{APP_NAME} settings")
        win.attributes("-topmost", True)
        win.resizable(False, False)
        win.configure(padx=14, pady=12)
        win.bind("<Escape>", lambda e: win.destroy())

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

        font = mono_font(self.root)

        self.elapsed_var = tk.StringVar(value="00:00")
        self.remaining_var = tk.StringVar()

        self.elapsed_label = tk.Label(
            self.watch,
            textvariable=self.elapsed_var,
            fg=COLOR_GREEN,
            bg=COLOR_BG,
            font=(font, 14, "bold"),
        )
        self.elapsed_label.pack(padx=10, pady=(4, 0))
        self.remaining_label = tk.Label(
            self.watch,
            textvariable=self.remaining_var,
            fg="#888888",
            bg=COLOR_BG,
            font=(font, 9),
        )
        self.remaining_label.pack(padx=10, pady=(0, 4))

        # The bar is 1-2 px tall and awkward to hit, so the stopwatch carries
        # the same actions. Right-click on a Mac is Button-2 or Control-click.
        self.menu = tk.Menu(self.watch, tearoff=0)
        self.menu.add_command(label="Settings…", command=self.open_settings)
        self.menu.add_command(label="Hide stopwatch", command=self.watch.withdraw)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.close)

        for widget in (self.watch, self.elapsed_label, self.remaining_label):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<Button-2>", self._popup_menu)
            widget.bind("<Control-Button-1>", self._popup_menu)
            widget.bind("<Triple-Button-1>", lambda e: self.close())
        self.watch.bind("<Escape>", lambda e: self.close())

        self.watch.update_idletasks()
        x = self.x + self.width - self.watch.winfo_reqwidth() - 8
        y = self.y + self.height + 4
        self.watch.geometry(f"+{x}+{y}")

    def _popup_menu(self, event):
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def _drag_start(self, event):
        self._drag = (event.x_root - self.watch.winfo_x(),
                      event.y_root - self.watch.winfo_y())

    def _drag_move(self, event):
        x = event.x_root - self._drag[0]
        y = event.y_root - self._drag[1]
        self.watch.geometry(f"+{x}+{y}")

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

    VisitBar(settings).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
