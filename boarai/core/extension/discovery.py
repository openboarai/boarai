"""
Extension discovery for BoarAi extension system.

This module discovers extensions from fixed paths.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from .types import ExtensionType

logger = logging.getLogger(__name__)


class ExtensionDiscovery:
    """Extension discovery (v2, two-layer)."""
    
    # System extensions directory
    SYSTEM_EXTENSIONS_DIR = "boar/extensions"
    
    # User extensions directory
    USER_EXTENSIONS_DIR = ".boarai/extensions"
    
    # Type directory mapping
    TYPE_DIRS = {
        ExtensionType.CHANNEL: "channels",
        ExtensionType.PLUGIN: "plugins",
        ExtensionType.TOOL: "tools",
        ExtensionType.SKILL: "skills",
        ExtensionType.MCP: "mcp",
    }
    
    def __init__(self, project_root: Optional[Path] = None):
        """Initialize discovery."""
        if project_root is None:
            # The runtime anchor set once at startup — NOT the process cwd.
            # The server is routinely launched from an arbitrary directory
            # (IDE run button, a detached restart, `python -m boarai` from
            # elsewhere), and a cwd-based system dir silently discovers ZERO
            # extensions: no WebUI channel, no tools, no routes, and only a
            # DEBUG line to show for it (the WebUI then 404s at /).
            from boarai.core.runtime import get_project_root

            project_root = get_project_root() or Path.cwd()
        
        self._project_root = Path(project_root)
        from boarai.core.runtime import resolve_data_dir
        data_dir = resolve_data_dir(self._project_root)
        self._system_dir = self._project_root / self.SYSTEM_EXTENSIONS_DIR
        self._user_dir = data_dir / "extensions"
    
    def discover_all(self) -> Dict[str, List[Path]]:
        """Discover all extensions."""
        results = {}
        
        for ext_type in ExtensionType:
            type_dir_name = self.TYPE_DIRS[ext_type]
            
            if ext_type is ExtensionType.SKILL:
                # SKILL.md skills are managed by core.extension.skill.SkillStore
                # (not manifest-based extensions); discovery delegates entirely.
                results[ext_type.value] = []
                continue
            
            # Discover system extensions
            system_exts = self._discover_in_directory(
                self._system_dir / type_dir_name,
            )
            
            # Discover user extensions
            user_exts = self._discover_in_directory(
                self._user_dir / type_dir_name,
            )
            
            # Combine by name, user > system
            all_exts = self._merge_by_name([system_exts, user_exts])
            results[ext_type.value] = all_exts
        
        return results

    @staticmethod
    def _merge_by_name(layers: List[List[Path]]) -> List[Path]:
        """Merge layer lists by extension directory name (later layer wins)."""
        merged: Dict[str, Path] = {}
        for layer in layers:
            for path in layer:
                merged[path.name] = path
        return list(merged.values())
    
    def discover_by_type(self, extension_type: str) -> List[Path]:
        """Discover extensions by type."""
        try:
            ext_type = ExtensionType(extension_type)
        except ValueError:
            logger.error(f"Invalid extension type: {extension_type}")
            return []
        
        if ext_type is ExtensionType.SKILL:
            # Managed by SkillStore (SKILL.md), not the manifest-based loader.
            return []
        
        type_dir_name = self.TYPE_DIRS[ext_type]
        
        # Discover system extensions
        system_exts = self._discover_in_directory(
            self._system_dir / type_dir_name,
        )
        
        # Discover user extensions
        user_exts = self._discover_in_directory(
            self._user_dir / type_dir_name,
        )
        
        return self._merge_by_name([system_exts, user_exts])
    
    def _discover_in_directory(self, directory: Path) -> List[Path]:
        """Discover extensions in directory."""
        extensions = []
        
        if not directory.exists():
            logger.debug(f"Directory not found: {directory}")
            return extensions
        
        # Scan directory
        for item in directory.iterdir():
            if not item.is_dir():
                continue
            
            # Check for manifest.yaml (canonical) or extension.yaml (legacy alias)
            manifest_file = item / "manifest.yaml"
            if not manifest_file.exists():
                manifest_file = item / "extension.yaml"
            if not manifest_file.exists():
                logger.debug(f"No manifest found in {item}")
                continue
            
            # Check for extension.py (entry point)
            extension_file = item / "extension.py"
            if not extension_file.exists():
                logger.debug(f"No extension.py found in {item}")
                continue
            
            extensions.append(item)
        
        return extensions
    
    def get_extension_path(self, extension_name: str, extension_type: str) -> Optional[Path]:
        """Get extension path by name and type."""
        try:
            ext_type = ExtensionType(extension_type)
        except ValueError:
            return None
        
        type_dir_name = self.TYPE_DIRS.get(ext_type, "")
        
        # Check user directory first (highest priority)
        user_path = self._user_dir / type_dir_name / extension_name
        if user_path.exists() and self._has_manifest(user_path):
            return user_path
        
        # Check system directory
        system_path = self._system_dir / type_dir_name / extension_name
        if system_path.exists() and self._has_manifest(system_path):
            return system_path
        
        return None

    @staticmethod
    def _has_manifest(ext_dir: Path) -> bool:
        """True if the dir has manifest.yaml (canonical) or extension.yaml (legacy)."""
        return (ext_dir / "manifest.yaml").exists() or (ext_dir / "extension.yaml").exists()
