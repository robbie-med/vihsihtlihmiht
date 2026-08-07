"""visit_bar_linux.py — a ~0.5 mm tall visit timer bar pinned to the top of the screen.

Linux/X11 port of visit_bar.py. Standalone: shares no code with the Windows
version, so the two can be changed independently.

Usage:
    python3 visit_bar_linux.py [minutes]

    minutes   total visit length in minutes (overrides the saved setting)

Behaviour:
    - Reserves a thin strip along the top edge of the primary monitor, so
      maximized windows sit below it. Where Windows uses an AppBar, X11 uses
      the EWMH _NET_WM_STRUT_PARTIAL hint, which every compliant window
      manager (xfwm4, KWin, Mutter, i3, Openbox) honours.
    - Bar height is at most 0.5 mm (computed from the screen DPI; usually 1-2 px).
    - The strip fills left-to-right as the visit time elapses:
        green     -> more than the yellow threshold left (default 5 min)
        yellow    -> yellow threshold or less left
        red flash -> red threshold or less left (default 2 min), and overtime
    - A small always-on-top stopwatch shows elapsed and remaining time.
      Drag it anywhere with the mouse; right-click it for a menu.
    - Right-click the bar to open the settings window (total time, yellow and
      red thresholds). Settings are saved to
      ~/.config/vihsihtlihmiht/settings.json and applying them restarts the
      timer.
    - Triple-click the bar, or use the stopwatch menu, to close everything and
      restore the work area.

Requires Python 3.8+ with tkinter (apt install python3-tk).

Wayland note: struts have no Wayland equivalent reachable from tkinter, so
under a Wayland session the bar floats on top but does not reserve space. Log
into an X11/Xorg session for the full effect.
"""

import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
import time
import tkinter as tk
import tkinter.font as tkfont

APP_NAME = "vihsihtlihmiht"

MM_PER_INCH = 25.4
BAR_MM = 0.5
# Sanity clamp: some X servers report a nonsense physical screen size, which
# would turn "0.5 mm" into an absurd number of pixels.
MAX_BAR_PX = 8

COLOR_BG = "#101010"
COLOR_GREEN = "#00c832"
COLOR_YELLOW = "#ffd800"
COLOR_RED = "#ff2020"
COLOR_RED_DIM = "#400000"

TICK_MS = 100

XA_CARDINAL = 6
PROP_MODE_REPLACE = 0

# Monospace families to try, in order, for the stopwatch readout. Consolas is
# a Windows font, so on Linux we fall through to whatever the distro ships.
MONO_FONTS = (
    "DejaVu Sans Mono",
    "Liberation Mono",
    "Noto Sans Mono",
    "Ubuntu Mono",
    "FreeMono",
    "Courier New",
)

DEFAULT_SETTINGS = {
    "total_minutes": 12.0,
    "yellow_minutes": 5.0,
    "red_minutes": 2.0,
}


# --- Settings persistence ---

def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(base, APP_NAME, "settings.json")


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


# --- X11 helpers ---

class X11:
    """Just enough Xlib, via ctypes, to set the strut hint. No dependencies."""

    def __init__(self):
        self.lib = ctypes.CDLL("libX11.so.6")
        self.lib.XOpenDisplay.restype = ctypes.c_void_p
        self.lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self.lib.XInternAtom.restype = ctypes.c_ulong
        self.lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        self.lib.XChangeProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_int,
        ]
        self.lib.XDeleteProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong
        ]
        self.lib.XQueryTree.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)),
            ctypes.POINTER(ctypes.c_uint),
        ]
        self.lib.XGetWindowProperty.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong,
            ctypes.c_long, ctypes.c_long, ctypes.c_int, ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_ulong), ctypes.POINTER(ctypes.c_ulong),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
        ]
        self.lib.XFree.argtypes = [ctypes.c_void_p]
        self.lib.XFlush.argtypes = [ctypes.c_void_p]
        self.lib.XCloseDisplay.argtypes = [ctypes.c_void_p]

        self.display = self.lib.XOpenDisplay(None)
        if not self.display:
            raise OSError("cannot open X display")
        self.window = None

    def atom(self, name):
        return self.lib.XInternAtom(self.display, name.encode("ascii"), False)

    def set_cardinals(self, window, name, values):
        # Format 32 properties are passed as an array of C long, even on 64-bit.
        data = (ctypes.c_long * len(values))(*values)
        self.lib.XChangeProperty(
            self.display, window, self.atom(name), XA_CARDINAL, 32,
            PROP_MODE_REPLACE, ctypes.byref(data), len(values),
        )

    def has_wm_state(self, window):
        actual_type = ctypes.c_ulong()
        actual_format = ctypes.c_int()
        count = ctypes.c_ulong()
        remaining = ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        self.lib.XGetWindowProperty(
            self.display, window, self.atom("WM_STATE"), 0, 2, False, 0,
            ctypes.byref(actual_type), ctypes.byref(actual_format),
            ctypes.byref(count), ctypes.byref(remaining), ctypes.byref(data),
        )
        if data:
            self.lib.XFree(data)
        return actual_type.value != 0

    def parent_of(self, window):
        root_return = ctypes.c_ulong()
        parent = ctypes.c_ulong()
        children = ctypes.POINTER(ctypes.c_ulong)()
        n_children = ctypes.c_uint()
        ok = self.lib.XQueryTree(
            self.display, window, ctypes.byref(root_return), ctypes.byref(parent),
            ctypes.byref(children), ctypes.byref(n_children),
        )
        if children:
            self.lib.XFree(children)
        return parent.value if ok else None

    def client_window(self, root):
        """Find the window the window manager actually manages.

        Tk gives every toplevel a hidden wrapper window, and it is the wrapper
        — not root.winfo_id() — that the WM reads properties from. Setting the
        strut on winfo_id() silently does nothing. The wrapper is the ancestor
        carrying WM_STATE; it only appears once the WM has adopted the window,
        so return None and let the caller retry.
        """
        window = root.winfo_id()
        for _ in range(8):
            if self.has_wm_state(window):
                return window
            parent = self.parent_of(window)
            if not parent or parent == window:
                return None
            window = parent
        return None

    def reserve_space(self, root, x, y, width, height):
        window = self.client_window(root)
        if window is None:
            return False
        bottom_edge = y + height
        # left, right, top, bottom
        self.set_cardinals(window, "_NET_WM_STRUT", [0, 0, bottom_edge, 0])
        # ...plus the start/end pair for each edge, so we claim only the span
        # of the primary monitor rather than the full virtual desktop width.
        self.set_cardinals(
            window, "_NET_WM_STRUT_PARTIAL",
            [0, 0, bottom_edge, 0, 0, 0, 0, 0, x, x + width - 1, 0, 0],
        )
        self.lib.XFlush(self.display)
        self.window = window
        return True

    def release_space(self):
        if self.window is not None:
            for name in ("_NET_WM_STRUT", "_NET_WM_STRUT_PARTIAL"):
                self.lib.XDeleteProperty(self.display, self.window, self.atom(name))
            self.lib.XFlush(self.display)
            self.window = None
        if self.display:
            self.lib.XCloseDisplay(self.display)
            self.display = None


def primary_monitor():
    """(x, y, width) of the primary monitor, or None if xrandr can't say.

    winfo_screenwidth() would return the width of the whole virtual desktop,
    which on a multi-monitor setup stretches the bar across every screen.
    """
    try:
        output = subprocess.run(
            ["xrandr", "--query"], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None

    fallback = None
    for line in output.splitlines():
        if " connected" not in line:
            continue
        match = re.search(r"\b(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", line)
        if not match:
            continue
        width, _height, x, y = (int(g) for g in match.groups())
        if " primary " in line:
            return x, y, width
        if fallback is None:
            fallback = (x, y, width)
    return fallback


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
    def __init__(self, settings, x11):
        self.x11 = x11
        self.settings = dict(settings)
        self.settings_window = None
        self.apply_settings()
        self.start = time.monotonic()

        self.root = tk.Tk()
        # NOT overrideredirect: an override-redirect window is invisible to the
        # window manager, so its strut would be ignored. A dock window is
        # undecorated and always on top, but still WM-managed.
        try:
            self.root.attributes("-type", "dock")
        except tk.TclError:
            self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=COLOR_BG)

        geometry = primary_monitor()
        if geometry is None:
            geometry = (0, 0, self.root.winfo_screenwidth())
        self.x, self.y, self.width = geometry
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
        self.root.bind("<Button-3>", lambda e: self.open_settings())
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self._build_stopwatch()

        self.root.update_idletasks()
        self.root.update()

        # The window must be mapped *and* adopted by the WM before the hint
        # will stick, so keep trying briefly.
        self._reserve_attempts = 0
        self._try_reserve()
        self.root.after(TICK_MS, self._tick)

    def _half_mm_in_pixels(self):
        """0.5 mm in physical pixels, rounded down (never exceed 0.5 mm)."""
        try:
            px = int(self.root.winfo_fpixels(f"{BAR_MM}m"))
        except tk.TclError:
            px = 1
        return max(1, min(px, MAX_BAR_PX))

    def _try_reserve(self):
        if self.x11 is None:
            return
        if self.x11.reserve_space(self.root, self.x, self.y, self.width, self.height):
            return
        self._reserve_attempts += 1
        if self._reserve_attempts < 20:
            self.root.after(100, self._try_reserve)
        else:
            print(
                "visit_bar: the window manager did not adopt the bar, so screen "
                "space could not be reserved; it will float on top instead.",
                file=sys.stderr,
            )

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
        # "splash" is undecorated and floats above normal windows, but unlike
        # "dock" it never makes the window manager reserve space for it.
        try:
            self.watch.attributes("-type", "splash")
        except tk.TclError:
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

        # The bar is a dock window, so it never takes keyboard focus and Esc
        # can't reach it — and at 1-2 px tall it is nearly impossible to click.
        # This menu is the reliable way to reach settings and to quit.
        self.menu = tk.Menu(self.watch, tearoff=0)
        self.menu.add_command(label="Settings…", command=self.open_settings)
        self.menu.add_command(label="Hide stopwatch", command=self.watch.withdraw)
        self.menu.add_separator()
        self.menu.add_command(label="Quit", command=self.close)

        for widget in (self.watch, self.elapsed_label, self.remaining_label):
            widget.bind("<Button-1>", self._drag_start)
            widget.bind("<B1-Motion>", self._drag_move)
            widget.bind("<Button-3>", self._popup_menu)
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
        try:
            if self.x11 is not None:
                self.x11.release_space()
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

    if not os.environ.get("DISPLAY"):
        if os.environ.get("WAYLAND_DISPLAY"):
            print(
                "visit_bar: Wayland session detected — the bar will float on top "
                "but cannot reserve screen space. Log into an X11/Xorg session "
                "for the full effect.",
                file=sys.stderr,
            )
        else:
            print("visit_bar: no X display found.", file=sys.stderr)
            return 1

    try:
        x11 = X11()
    except OSError:
        print(
            "visit_bar: could not talk to X11; the bar will float on top but "
            "will not reserve screen space.",
            file=sys.stderr,
        )
        x11 = None

    settings = load_settings()
    if args.minutes is not None:
        settings["total_minutes"] = args.minutes

    VisitBar(settings, x11).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
