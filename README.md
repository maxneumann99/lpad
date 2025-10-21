# lpad

Fullscreen application launcher inspired by macOS Launchpad. The launcher is written in Python and uses Qt for its user interface.

## Features

- Discovers desktop applications from `/usr/share/applications` and `~/.local/share/applications` (including nested folders).
- Presents applications in a 7×5 grid with smooth page animations and pagination dots.
- Instant search field focused on startup for keyboard-driven filtering.
- Keyboard (arrow keys), mouse wheel, on-screen arrows for page navigation, and Escape to exit.
- Right-click context menu to hide unwanted launchers, persisted in `~/.config/lpad/conf.json`.
- Background image configurable via `~/.config/lpad/conf.json`; falls back to desktop wallpaper (GNOME via `gsettings`) or a plain white backdrop.
- Automatic icon label contrast based on background brightness.

## Requirements

- Python 3.9 or newer
- [PyQt5](https://www.riverbankcomputing.com/software/pyqt/intro)

Install dependencies with:

```bash
pip install .
```

## Configuration

Configuration is stored at `~/.config/lpad/conf.json`. Example:

```json
{
  "background": "/path/to/background.jpg",
  "hidden": [
    "/usr/share/applications/example.desktop"
  ]
}
```

- `background` – optional path to a custom background image.
- `hidden` – list of desktop files to hide from the launcher. You can populate this list from the UI via the right-click menu.

## Running

After installation, start the launcher with:

```bash
lpad
```

or directly from the source tree without installing:

```bash
python3 main.py
```

You can also execute it as a module if you prefer:

```bash
python -m lpad
```

The window opens fullscreen, showing the first page of applications. Start typing to filter, use the arrows or mouse wheel to change pages, and press <kbd>Esc</kbd> to exit.
