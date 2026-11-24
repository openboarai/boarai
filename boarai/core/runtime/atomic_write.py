"""Crash-safe text file writes.

``open(path, "w")`` truncates the target to zero length before any byte is
written, so a crash or power loss between the truncate and the flush leaves an
empty file. For a credential store that means every API key is gone with no
recovery path.

Write to a sibling temp file, flush it through to stable storage, then
:func:`os.replace` it onto the target. ``replace`` is atomic on both POSIX and
Windows, so a reader sees either the complete old content or the complete new
content, never a partial write.

The temp name is unique per call rather than a fixed ``.tmp`` suffix, so two
concurrent writers to the same target cannot clobber each other's temp file.
"""

from __future__ import annotations

import itertools
import logging
import os
from pathlib import Path
from typing import Union

logger = logging.getLogger(__name__)

# Monotonic counter combined with the pid so temp names are unique across
# threads in this process and across processes sharing a directory.
_counter = itertools.count(1)


def _tmp_path_for(target: Path) -> Path:
    """Build a unique sibling temp path for ``target``."""
    return target.with_name(f"{target.name}.{os.getpid()}.{next(_counter)}.tmp")


def _remove_quietly(path: Path) -> None:
    """Delete a leftover temp file without raising over the original error."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Failed to remove temp file %s: %s", path, exc)


def write_text_atomic(path: Union[str, Path], content: str, *, encoding: str = "utf-8") -> None:
    """Write ``content`` to ``path`` so an interrupted write cannot corrupt it."""
    target = Path(path)
    tmp = _tmp_path_for(target)
    try:
        with open(tmp, "w", encoding=encoding, newline="") as f:
            f.write(content)
            f.flush()
            try:
                os.fsync(f.fileno())
            except (OSError, AttributeError) as exc:
                # Some filesystems and network shares reject fsync. The
                # replace below is still atomic, only the durability window
                # is wider, so this degrades rather than fails the write.
                logger.debug("fsync unsupported for %s: %s", tmp, exc)
        os.replace(tmp, target)
    except BaseException:
        _remove_quietly(tmp)
        raise


async def write_text_atomic_async(
    path: Union[str, Path],
    content: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Async :func:`write_text_atomic`, keeping blocking I/O off the event loop."""
    import asyncio

    await asyncio.to_thread(write_text_atomic, path, content, encoding=encoding)
