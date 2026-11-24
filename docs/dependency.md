> **English** | [简体中文](dependency_zh.md)

# Dependency Management (core/dependency)

Installation and verification of the Python packages declared by extensions (manifest `pip_dependencies`).

## Principles

- **Checks are import probes, not metadata reads**: a half-uninstalled or broken package still advertises metadata but fails at runtime — an `__import__` failure means missing (package name with hyphens converted to underscores before probing).
- **Installation is serialised** to keep concurrent pip processes from fighting over the same target directory.
- Probe results are cached (`_installed_cache`); the same package is never probed twice.

## Interface

`DependencyManager` (`get_dependency_manager()`): `check_package(spec)` reports whether the package can currently be imported; the install path accepts full version specs (e.g. `aiohttp>=3.8.0`) and verifies after extracting the package name.

## Boundaries

- **Core dependencies are not installed here**: entry modules never reach the network at import time; core dependencies have exactly one explicit install path, `--ensure-deps` (in the runtime bootstrap module).
- This module serves the extension loading chain: before loading an extension that declares `pip_dependencies`, the loader checks/installs through here.
- The task system's script executor handles its own runtime environment and does not share this cache.
