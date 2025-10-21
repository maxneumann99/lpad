from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

CONFIG_DIR = Path.home() / ".config" / "lpad"
CONFIG_PATH = CONFIG_DIR / "conf.json"


@dataclass
class Config:
    """Configuration for the launcher."""

    background: Optional[str] = None
    hidden: List[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> "Config":
        if not CONFIG_PATH.exists():
            return cls()

        try:
            with CONFIG_PATH.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return cls()

        background = data.get("background")
        hidden = data.get("hidden", [])
        if not isinstance(hidden, list):
            hidden = []
        return cls(background=background, hidden=hidden)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = {"background": self.background, "hidden": self.hidden}
        with CONFIG_PATH.open("w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)

    def add_hidden(self, desktop_path: str) -> None:
        if desktop_path not in self.hidden:
            self.hidden.append(desktop_path)
            self.save()

    def remove_hidden(self, desktop_path: str) -> None:
        if desktop_path in self.hidden:
            self.hidden.remove(desktop_path)
            self.save()
