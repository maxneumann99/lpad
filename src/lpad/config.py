"""Configuration management for Launchpad."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

CONFIG_DIR = Path.home() / ".config" / "lpad"
CONFIG_FILE = CONFIG_DIR / "conf.json"


def ensure_config_dir() -> None:
    """Ensure the configuration directory exists."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class LaunchpadConfig:
    """Configuration for the Launchpad application."""

    background_image: Optional[str] = None
    hidden_desktop_ids: List[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "LaunchpadConfig":
        ensure_config_dir()
        if not CONFIG_FILE.exists():
            return cls()
        try:
            with CONFIG_FILE.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError):
            return cls()

        background = data.get("background_image")
        hidden = data.get("hidden_desktop_ids", [])
        if not isinstance(hidden, list):
            hidden = []
        hidden = [str(x) for x in hidden if isinstance(x, (str, int))]
        return cls(background_image=background, hidden_desktop_ids=hidden)

    def save(self) -> None:
        ensure_config_dir()
        data = {
            "background_image": self.background_image,
            "hidden_desktop_ids": self.hidden_desktop_ids,
        }
        with CONFIG_FILE.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    def hide(self, desktop_id: str) -> None:
        if desktop_id not in self.hidden_desktop_ids:
            self.hidden_desktop_ids.append(desktop_id)
            self.save()

    def unhide(self, desktop_id: str) -> None:
        if desktop_id in self.hidden_desktop_ids:
            self.hidden_desktop_ids.remove(desktop_id)
            self.save()
