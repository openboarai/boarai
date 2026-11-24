"""
Persistent extension state for BoarAi (v2).

Stores enable/disable overrides in .boarai/extensions/state.json with atomic
write (tmp + rename). Fixes the bug where disabling an extension was lost on
restart because state was only held in memory.

Structure:
{
  "version": 1,
  "disabled": ["ext_a", "ext_b"],
  "enabled_overrides": {"ext_c": "0.3.1"}
}
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

STATE_VERSION = 1


class ExtensionStateStore:
    """Persistent enable/disable state for extensions."""

    def __init__(self, data_dir: Path) -> None:
        """Initialize state store."""
        self._path = Path(data_dir) / "extensions" / "state.json"
        self._data: Dict[str, Any] = {
            "version": STATE_VERSION,
            "disabled": [],
            "enabled_overrides": {},
            "trusted": [],
        }
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
                if not isinstance(raw, dict):
                    logger.warning(f"Extension state file malformed: {self._path}")
                    return
            self._data["disabled"] = list(raw.get("disabled", []))
            self._data["enabled_overrides"] = dict(raw.get("enabled_overrides", {}))
            self._data["trusted"] = list(raw.get("trusted", []))
            self._data["version"] = raw.get("version", STATE_VERSION)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to load extension state {self._path}: {e}")

    def save(self) -> None:
        """Atomically persist state."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, str(self._path))
        except Exception as e:  # noqa: BLE001
            logger.error(f"Failed to save extension state {self._path}: {e}")
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    # --- Key helpers (V9: layer-precise state) ---

    @staticmethod
    def _key(name: str, source: Optional[str]) -> str:
        """Compose a layer-precise key: 'name' (legacy) or 'name@source'."""
        return f"{name}@{source}" if source else name

    # --- Queries ---

    def is_disabled(self, name: str, source: Optional[str] = None) -> bool:
        """True if the extension is disabled."""
        if self._key(name, source) in self._data["disabled"]:
            return True
        if source is not None:
            return name in self._data["disabled"]
        return name in self._data["disabled"]

    def get_disabled(self) -> List[str]:
        return list(self._data["disabled"])

    def get_enabled_override(self, name: str) -> Optional[str]:
        return self._data["enabled_overrides"].get(name)

    def get_enabled_overrides(self) -> Dict[str, str]:
        return dict(self._data["enabled_overrides"])

    def is_trusted(self, name: str, source: Optional[str] = None) -> bool:
        """True when the operator granted per-extension in-process trust."""
        return self._key(name, source) in self._data["trusted"]

    def get_trusted(self) -> List[str]:
        return list(self._data["trusted"])

    # --- Mutations ---

    def set_disabled(self, name: str, source: Optional[str] = None) -> None:
        key = self._key(name, source)
        if key not in self._data["disabled"]:
            self._data["disabled"].append(key)
            self.save()

    def set_enabled(self, name: str, source: Optional[str] = None) -> None:
        key = self._key(name, source)
        changed = False
        if key in self._data["disabled"]:
            self._data["disabled"].remove(key)
            changed = True
        if name in self._data["enabled_overrides"]:
            del self._data["enabled_overrides"][name]
            changed = True
        if changed:
            self.save()

    def set_trusted(self, name: str, source: Optional[str] = None) -> bool:
        """Grant per-extension trust; True when the list changed."""
        key = self._key(name, source)
        if key in self._data["trusted"]:
            return False
        self._data["trusted"].append(key)
        self.save()
        return True

    def set_untrusted(self, name: str, source: Optional[str] = None) -> bool:
        """Revoke per-extension trust; True when the list changed."""
        key = self._key(name, source)
        if key not in self._data["trusted"]:
            return False
        self._data["trusted"].remove(key)
        self.save()
        return True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "version": self._data["version"],
            "disabled": self.get_disabled(),
            "enabled_overrides": self.get_enabled_overrides(),
            "trusted": self.get_trusted(),
        }
