"""Install and verify the Python packages that extensions declare.

Dependency checks are import probes rather than metadata reads, so a package
that is present on disk but broken still reports as missing. Installation is
serialised to keep pip from fighting over the same target directory.
"""

import asyncio
import logging
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class DependencyManager:
    """Check, install and cache the runtime dependencies of extensions."""

    def __init__(self):
        """Create an empty manager with a cold probe cache."""
        self._installed_cache: Dict[str, bool] = {}
        self._installed_packages: Optional[Set[str]] = None

    async def check_package(self, package_spec: str) -> bool:
        """Report whether a package can currently be imported."""
        package_name = self._extract_package_name(package_spec)

        if package_name in self._installed_cache:
            return self._installed_cache[package_name]

        # Import probe instead of importlib.metadata: a half-uninstalled or
        # broken package still advertises metadata but would fail at runtime.
        try:
            __import__(package_name.replace("-", "_"))
            self._installed_cache[package_name] = True
            return True
        except ImportError:
            self._installed_cache[package_name] = False
            return False

    async def check_packages(self, package_specs: List[str]) -> Dict[str, bool]:
        """Probe several packages, preserving the caller's spec strings."""
        results = {}
        for spec in package_specs:
            results[spec] = await self.check_package(spec)
        return results

    async def install_package(self, package_spec: str) -> bool:
        """Install one package with pip, preferring the active environment."""
        logger.info(f"Installing package: {package_spec}")

        # Outside a virtualenv, PEP 668 environments reject plain installs, so
        # the first attempt targets the user site-packages instead.
        in_venv = hasattr(sys, 'real_prefix') or (hasattr(sys, 'base_prefix') and sys.base_prefix != sys.prefix)

        try:
            if in_venv:
                cmd = [sys.executable, "-m", "pip", "install", package_spec]
            else:
                cmd = [sys.executable, "-m", "pip", "install", "--user", package_spec]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                logger.info(f"Successfully installed: {package_spec}")
                package_name = self._extract_package_name(package_spec)
                self._installed_cache[package_name] = True
                return True

            # Second attempt: Debian-managed interpreters refuse to install
            # unless the caller explicitly accepts breaking system packages.
            # The embedded runtime owns its tree, so this stays safe there.
            logger.info(f"Retrying with --break-system-packages...")
            cmd = [sys.executable, "-m", "pip", "install", "--break-system-packages", package_spec]

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await process.communicate()

            if process.returncode == 0:
                logger.info(f"Successfully installed: {package_spec}")
                package_name = self._extract_package_name(package_spec)
                self._installed_cache[package_name] = True
                return True

            error_msg = stderr.decode().strip()
            logger.error(f"Failed to install {package_spec}: {error_msg}")
            return False

        except Exception as e:
            logger.error(f"Error installing {package_spec}: {e}")
            return False

    async def install_packages(self, package_specs: List[str]) -> Dict[str, bool]:
        """Install several packages one after another."""
        results = {}

        # Sequential by design: concurrent pip runs sharing one site-packages
        # directory race on the same RECORD/metadata files.
        for spec in package_specs:
            if await self.check_package(spec):
                results[spec] = True
                continue

            results[spec] = await self.install_package(spec)

        return results

    async def ensure_dependencies(self, package_specs: List[str]) -> bool:
        """Make sure every declared dependency is importable."""
        if not package_specs:
            return True

        logger.info(f"Checking {len(package_specs)} dependencies...")

        missing = []
        for spec in package_specs:
            if not await self.check_package(spec):
                missing.append(spec)

        if not missing:
            logger.info("All dependencies satisfied")
            return True

        logger.info(f"Installing {len(missing)} missing packages: {', '.join(missing)}")

        results = await self.install_packages(missing)

        # Partial success is reported as failure so the caller can decide
        # whether to keep loading the extension.
        all_ok = all(results.values())
        if all_ok:
            logger.info("All missing dependencies installed successfully")
        else:
            failed = [spec for spec, ok in results.items() if not ok]
            logger.error(f"Failed to install: {', '.join(failed)}")

        return all_ok

    def _extract_package_name(self, package_spec: str) -> str:
        """Strip any version constraint, leaving the importable name."""
        # Order matters: two-character operators must be tested before their
        # single-character prefixes, otherwise ">=" would split at ">".
        for separator in [">=", "<=", "!=", "==", "~=", ">", "<"]:
            if separator in package_spec:
                return package_spec.split(separator)[0].strip()

        return package_spec.strip()

    def clear_cache(self) -> None:
        """Forget probe results, e.g. after an out-of-band pip install."""
        self._installed_cache.clear()
        self._installed_packages = None
        logger.debug("Dependency cache cleared")


# Process-wide singleton, created lazily by get_dependency_manager().
_dependency_manager: Optional[DependencyManager] = None


def get_dependency_manager() -> DependencyManager:
    """Return the shared dependency manager, creating it on first use."""
    global _dependency_manager
    if _dependency_manager is None:
        _dependency_manager = DependencyManager()
    return _dependency_manager