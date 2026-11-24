"""Filesystem operations owned by the kernel (``boarai.core.fs``).

Kernel modules are packages, never loose ``.py`` files at the ``core/`` root
(development spec §2.1). This package holds the primitives the WebUI file
manager needs; the HTTP surface stays in :mod:`boarai.core.api.fs`.

Public surface (import from here, not from the implementation module)::

    from boarai.core.fs import read_text, write_text, check_raw_image, FsError

``core.fs.pre_write`` / ``core.fs.post_write`` hook points share this namespace
on purpose: the package name and the hook domain are the same concept.
"""

from .textops import (
    FS_TEXT_MAX,
    RAW_IMAGE_TYPES,
    FsBadRequest,
    FsConflict,
    FsDenied,
    FsError,
    FsNotFound,
    check_raw_image,
    read_text,
    write_text,
)

__all__ = [
    "FS_TEXT_MAX",
    "RAW_IMAGE_TYPES",
    "FsBadRequest",
    "FsConflict",
    "FsDenied",
    "FsError",
    "FsNotFound",
    "check_raw_image",
    "read_text",
    "write_text",
]
