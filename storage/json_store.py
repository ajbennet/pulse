"""
Minimal JSON-file persistence.

Deliberately tiny and behind a narrow interface (`load`/`save`) so it can be
swapped for SQLite/Postgres later without touching the services that use it.
"""

import json
import os
from typing import Any, Optional

DEFAULT_DIR = os.path.join(os.environ.get("PULSE_DATA_DIR", "data"), "store")


class JsonStore:
    def __init__(self, base_dir: str = DEFAULT_DIR):
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def _path(self, key: str) -> str:
        return os.path.join(self.base_dir, f"{key}.json")

    def load(self, key: str, default: Optional[Any] = None) -> Any:
        path = self._path(key)
        if not os.path.exists(path):
            return default
        with open(path, "r") as fh:
            return json.load(fh)

    def save(self, key: str, value: Any) -> None:
        # Write-then-rename for atomicity.
        path = self._path(key)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(value, fh, indent=2, default=str)
        os.replace(tmp, path)

    def exists(self, key: str) -> bool:
        return os.path.exists(self._path(key))
