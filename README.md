# Launchpad

A simple fullscreen application launcher inspired by macOS Launchpad.

## Features

- Discovers applications from `/usr/share/applications` and `~/.local/share/applications`.
- Displays icons in a 5x7 grid with pagination and keyboard/arrow navigation.
- Instant search field focused on startup.
- Ability to hide applications via the right-click context menu.
- Wallpaper background configured via `~/.config/lpad/config.json` (falls back to detected desktop wallpaper or a plain white fill).

## Requirements

- Python 3.9 – 3.11
- PyQt5

Install dependencies with:

```bash
python -m pip install PyQt5
```

## Running

```bash
python main.py
```

## Configuration

An optional configuration file can be placed at `~/.config/lpad/config.json`:

```json
{
  "background_image": "/path/to/wallpaper.png"
}
```

If `background_image` is missing or the file cannot be found, Launchpad attempts to reuse the current desktop wallpaper (supported on common GNOME and XFCE setups). When that also fails, the launcher uses a plain white background.
