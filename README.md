# vihsihtlihmiht

> “You have 15 minutes—to establish rapport, do the med rec, examine the patient, listen to your preceptor’s stories, and place your orders. Here, take this.”

A tiny timer bar for medical visits (or any timed meeting) that lives along the
very top edge of your screen.

- **Hairline thin** — at most 0.5 mm tall (1–2 px), so it never gets in the way.
- **Reserves its own space** — the bar registers as a Windows AppBar, so
  maximized windows sit *below* it instead of sliding underneath.
- **Traffic-light timing** — the strip fills left to right as time passes:
  - **green** while more than 5 minutes remain
  - **yellow** at 5 minutes or less
  - **flashing red** at 2 minutes or less, and after time runs out
- **Built-in stopwatch** — a small always-on-top readout shows elapsed time and
  time remaining (or overtime, in red). Drag it anywhere; right-click hides it.
- **Configurable** — right-click the bar to set the visit length and the
  yellow/red thresholds; settings are remembered between runs.

## Screenshots

Green — filling up, plenty of time left:

![green bar](docs/bar-green.png)

Yellow — 5 minutes or less left:

![yellow bar](docs/bar-yellow.png)

Red — 2 minutes or less (flashes):

![red bar](docs/bar-red.png)

The stopwatch (draggable, always on top):

![stopwatch](docs/stopwatch.png)

Settings (right-click the bar):

![settings window](docs/settings.png)

## Download (Windows)

Grab `vihsihtlihmiht.exe` from the
[Releases](../../releases) page and double-click it. No installation, no Python
required — it runs a 12-minute visit by default.

To time a different length, create a shortcut to the exe, open its
**Properties**, and append the number of minutes to the *Target* field, e.g.
`... vihsihtlihmiht.exe 20` — or just right-click the bar and use the settings.

## Controls

| Action | Effect |
| --- | --- |
| Right-click the bar | Open settings (total time, yellow/red thresholds) |
| Triple-click the bar | Close everything (single/double clicks do nothing) |
| `Esc` | Close everything |
| Drag the stopwatch | Move it |
| Right-click the stopwatch | Hide it |

## Settings

Right-click the bar to open the settings window:

- **Total visit length** (minutes) — default 12
- **Bar turns yellow at** (minutes left) — default 5
- **Bar flashes red at** (minutes left) — default 2

*Apply and restart* saves the settings to `%APPDATA%\vihsihtlihmiht\settings.json`
and restarts the timer with the new values.

## Run from source

Requires Python 3.8+ with tkinter (included in standard Windows installs):

```
python visit_bar.py [minutes]     # overrides the saved visit length
```

`visit_bar.bat` is a convenience launcher that runs it without a console window.

## Platform support

Windows only, for now. The space-reserving mechanism is Windows-specific; Mac
and Linux versions are on the roadmap (see issues).

## License

[MIT](LICENSE)
