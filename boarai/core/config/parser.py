"""
Configuration file parser for BoarAi system.

This module handles reading and parsing configuration files in various formats.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Union

from boarai.core.runtime import write_text_atomic_async

from .schema import BoarAiConfig, ConfigFormat

logger = logging.getLogger(__name__)


class ConfigParser:
    """Configuration file parser."""
    
    def __init__(self, config_path: Optional[Union[str, Path]] = None):
        """Initialize config parser."""
        self.config_path = Path(config_path) if config_path else None
        self._config: Optional[BoarAiConfig] = None
        # Non-schema top-level keys (e.g. bootstrap) preserved across save()
        self._extra_top_level: Dict[str, Any] = {}
    
    async def load(self, config_path: Optional[Union[str, Path]] = None) -> BoarAiConfig:
        """Load configuration from file."""
        path = Path(config_path) if config_path else self.config_path
        
        if path is None:
            logger.info("No config file specified, using default configuration")
            return self._adopt_defaults()
        
        path = Path(path)
        
        if not path.exists():
            # A missing file is the NORMAL first-run state, not a fault: it is
            # created lazily on the first settings change. Logged at INFO (the
            # old WARNING made every fresh install look broken) and — critically
            # — self._config is still populated. Callers gate their write path
            # on ``cp._config`` (api.owner session settings, security
            # _persist_config), so leaving it None turned every save into a
            # silent no-op that still reported success, which in turn meant the
            # file could never be created at all.
            logger.info(
                "Config file not found: %s — using defaults; it will be "
                "written on the first settings change",
                path,
            )
            return self._adopt_defaults()
        
        # Determine format
        config_format = self._detect_format(path)
        
        # Load config data (corrupt / legacy python-tag YAML falls back to defaults)
        try:
            config_data = await self._read_config_file(path, config_format)
        except Exception as e:
            logger.error(
                "Failed to read config %s (%s); using defaults. "
                "Re-save settings from UI to rewrite a clean file.",
                path,
                e,
            )
            return self._adopt_defaults()
        
        if not isinstance(config_data, dict):
            logger.warning("Config file %s is not a mapping; using defaults", path)
            return self._adopt_defaults()

        # Preserve non-schema top-level keys (e.g. bootstrap) across later saves
        self._extra_top_level = {
            k: v for k, v in config_data.items()
            if k not in {
                "system", "llm", "plugin", "session", "logging",
                "security", "thinking", "run",
            }
        }
        
        # Check if this is a direct LLM config file (has providers at top level)
        # or a full BoarAi config file (has llm/system/plugin sections)
        if "providers" in config_data and "system" not in config_data and "llm" not in config_data:
            # This is a direct LLM config file, wrap it in llm section
            config_data = {"llm": config_data}
        
        # Create config object
        config = BoarAiConfig.from_dict(config_data)
        
        # Validate config
        errors = config.validate()
        if errors:
            logger.warning(f"Config validation warnings: {errors}")
        
        self._config = config
        logger.info(f"Loaded configuration from {path}")
        
        return config
    
    def _adopt_defaults(self) -> BoarAiConfig:
        """Adopt a fresh default config as the loaded state and return it."""
        self._extra_top_level = {}
        config = BoarAiConfig()
        self._config = config
        return config
    
    async def save(self, config: BoarAiConfig, config_path: Optional[Union[str, Path]] = None) -> None:
        """Save configuration to file."""
        path = Path(config_path) if config_path else self.config_path
        
        if path is None:
            raise ValueError("No config path specified")
        
        path = Path(path)
        
        # Determine format
        config_format = self._detect_format(path)
        
        # Convert to dict (enums/paths as plain values)
        config_data = config.to_dict()

        # Normalize absolute log paths that live UNDER THE DATA DIR back to a
        # data-dir-relative form so the config file stays portable across
        # machines. Relative values are resolved against the data-dir anchor on
        # load (core.runtime.resolve_data_path), so the round-trip is lossless.
        #
        # This used to relativize against ``Path(path).parent.parent.parent``,
        # which WAS the project root back when the data dir lived in-tree at
        # ``<repo>/.boarai``. Since the anchor moved to ``~/.boarai`` (outside
        # the source tree) that expression yields the HOME directory instead, so
        # every absolute log path under home got rewritten to a bogus relative
        # form — including the one LogManager._resolve_log_path writes back into
        # ``config.logging`` in place — silently relocating logs on next start.
        if isinstance(config_data.get("logging"), dict):
            try:
                from boarai.core.runtime import get_data_dir

                data_dir = get_data_dir().resolve()
            except Exception:
                data_dir = None
            if data_dir is not None:
                for key in ("file_path", "json_path"):
                    raw = config_data["logging"].get(key)
                    if isinstance(raw, str) and raw:
                        p = Path(raw)
                        if p.is_absolute():
                            try:
                                config_data["logging"][key] = str(
                                    p.resolve().relative_to(data_dir)
                                )
                            except (ValueError, OSError):
                                # Outside the data dir: a deliberate user
                                # override — keep it explicit and absolute.
                                pass

        # Merge preserved top-level keys (bootstrap, etc.) and any still on disk
        extras = dict(getattr(self, "_extra_top_level", {}) or {})
        if path.exists():
            try:
                on_disk = await self._read_config_file(path, config_format)
                if isinstance(on_disk, dict):
                    for k, v in on_disk.items():
                        if k not in config_data and k not in extras:
                            extras[k] = v
            except Exception as e:
                logger.warning("Could not merge existing keys from %s: %s", path, e)
        if extras:
            # schema keys win; extras fill gaps
            merged = {**extras, **config_data}
            config_data = merged
        
        # Write config file
        await self._write_config_file(path, config_data, config_format)
        self._config = config
        
        logger.info(f"Saved configuration to {path}")
    
    def _detect_format(self, path: Path) -> ConfigFormat:
        """Detect configuration file format from extension."""
        suffix = path.suffix.lower()
        
        if suffix in (".yaml", ".yml"):
            return ConfigFormat.YAML
        elif suffix == ".toml":
            return ConfigFormat.TOML
        elif suffix == ".json":
            return ConfigFormat.JSON
        else:
            # Default to YAML
            logger.warning(f"Unknown config file extension: {suffix}, assuming YAML")
            return ConfigFormat.YAML
    
    async def _read_config_file(self, path: Path, format: ConfigFormat) -> Dict[str, Any]:
        """Read configuration file."""
        if format == ConfigFormat.YAML:
            return await self._read_yaml(path)
        elif format == ConfigFormat.JSON:
            return await self._read_json(path)
        else:
            # For TOML, we'll use JSON as fallback
            logger.warning(f"Format {format} not fully supported, using JSON fallback")
            return await self._read_json(path)
    
    async def _read_json(self, path: Path) -> Dict[str, Any]:
        """Read JSON configuration file."""
        import aiofiles
        
        async with aiofiles.open(path, 'r', encoding='utf-8') as f:
            content = await f.read()
            return json.loads(content)
    
    async def _read_yaml(self, path: Path) -> Dict[str, Any]:
        """Read YAML configuration file."""
        import aiofiles
        import yaml
        
        async with aiofiles.open(path, 'r', encoding='utf-8') as f:
            content = await f.read()
            return yaml.safe_load(content) or {}
    
    async def _write_config_file(
        self,
        path: Path,
        data: Dict[str, Any],
        format: ConfigFormat
    ) -> None:
        """Write configuration file."""
        # Create parent directories if they don't exist
        path.parent.mkdir(parents=True, exist_ok=True)
        
        if format == ConfigFormat.YAML:
            await self._write_yaml(path, data)
        elif format == ConfigFormat.JSON:
            await self._write_json(path, data)
        else:
            # For TOML, we'll use JSON as fallback
            logger.warning(f"Format {format} not fully supported, using JSON fallback")
            await self._write_json(path, data)
    
    async def _write_json(self, path: Path, data: Dict[str, Any]) -> None:
        """Write JSON configuration file atomically."""
        content = json.dumps(data, indent=2, ensure_ascii=False)
        await write_text_atomic_async(path, content)

    async def _write_yaml(self, path: Path, data: Dict[str, Any]) -> None:
        """Write YAML configuration file atomically."""
        import yaml

        content = yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False)
        await write_text_atomic_async(path, content)
    
    @property
    def config(self) -> Optional[BoarAiConfig]:
        """Get loaded configuration."""
        return self._config


class ConfigError(Exception):
    """Configuration related errors."""
    pass
