# Launchpad

A simple fullscreen application launcher inspired by macOS Launchpad.

## Features

- Discovers applications from `/usr/share/applications` and `~/.local/share/applications`.
- Displays icons in a 5x7 grid with pagination and keyboard/arrow navigation.
- Instant search field focused on startup.
- Ability to hide applications via the right-click context menu.
- Transparent blurred background using a screenshot of the current desktop.

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
