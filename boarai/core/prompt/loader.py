"""
Prompt loader for BoarAi — centralized file-based prompt management.

All prompt content lives in boar/prompts/ as plain text files.
This module loads them at import time and exposes them as constants.

Directory structure:
    boar/prompts/
    ├── system_core.md              # Core execution rules (all agents share)
    └── compression/
        ├── compress_instruction.md  # LLM instruction for history compression
        └── summary_template.md      # Template for compressed summary output
"""

import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ==================== Path Discovery ====================

# core/prompt/loader.py → core/prompt/ → core/ → project_root/ → boar/prompts/
_PROMPTS_DIR: Optional[Path] = None


def _find_prompts_dir() -> Path:
    """Locate the boar/prompts/ directory."""
    global _PROMPTS_DIR
    if _PROMPTS_DIR is not None and _PROMPTS_DIR.exists():
        return _PROMPTS_DIR

    # boarai/core/prompt/loader.py → 4 parents = project root
    project_root = Path(__file__).resolve().parent.parent.parent.parent
    candidates = [
        project_root / "boar" / "prompts",
        project_root.parent / "boar" / "prompts",
    ]

    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            _PROMPTS_DIR = candidate
            logger.debug(f"Prompts directory: {candidate}")
            return candidate

    # Fallback: search upward
    current = Path(__file__).resolve().parent
    for _ in range(10):
        candidate = current / "boar" / "prompts"
        if candidate.exists() and candidate.is_dir():
            _PROMPTS_DIR = candidate
            logger.debug(f"Prompts directory (upward search): {candidate}")
            return candidate
        current = current.parent

    raise FileNotFoundError(
        "Could not locate boar/prompts/ directory. "
        "Ensure the boar/prompts/ folder exists at the project root."
    )


def _load_file(relative_path: str) -> str:
    """Load a prompt file from boar/prompts/."""
    try:
        prompts_dir = _find_prompts_dir()
        file_path = prompts_dir / relative_path
        if file_path.exists():
            content = file_path.read_text(encoding="utf-8").strip()
            logger.info(
                f"Loaded prompt '{relative_path}' ({len(content)} chars) "
                f"from {file_path.name}"
            )
            return content
        else:
            logger.warning(f"Prompt file not found: {file_path}")
            return ""
    except Exception as e:
        logger.error(f"Failed to load prompt '{relative_path}': {e}")
        return ""


def get_prompts_dir() -> Path:
    """Get the boar/prompts/ directory path."""
    return _find_prompts_dir()


def reload_all() -> Dict[str, str]:
    """Reload all prompt files from disk."""
    global SYSTEM_CORE_PROMPT
    global COMPRESS_INSTRUCTION, SUMMARY_TEMPLATE

    SYSTEM_CORE_PROMPT = _load_file("system_core.md")
    COMPRESS_INSTRUCTION = _load_file("compression/compress_instruction.md")
    SUMMARY_TEMPLATE = _load_file("compression/summary_template.md")

    return {
        "system_core": SYSTEM_CORE_PROMPT,
        "compress_instruction": COMPRESS_INSTRUCTION,
        "summary_template": SUMMARY_TEMPLATE,
    }


# ==================== Loaded Constants ====================
# Loaded once at module import time. Call reload_all() to refresh.

SYSTEM_CORE_PROMPT: str = ""
COMPRESS_INSTRUCTION: str = ""
SUMMARY_TEMPLATE: str = ""

try:
    SYSTEM_CORE_PROMPT = _load_file("system_core.md")
    COMPRESS_INSTRUCTION = _load_file("compression/compress_instruction.md")
    SUMMARY_TEMPLATE = _load_file("compression/summary_template.md")
except FileNotFoundError as e:
    logger.warning(f"Prompts directory not found, prompts will be empty: {e}")
except Exception as e:
    logger.error(f"Failed to load prompts: {e}")
