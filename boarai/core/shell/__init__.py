"""Pluggable shell execution backends.

``ShellBackend`` is the seam that answers "where does this command run":
:class:`LocalBackend` (the host, the default) and :class:`DockerBackend` (a
container reached through the ``docker`` CLI). Every backend reports a
:class:`BackendInfo` describing its isolation level and which host surface it
exposes, so the tool guard can couple approval policy to actual containment
instead of assuming the worst or the best.
"""

from .backend import (
    BackendInfo,
    DataSink,
    DockerBackend,
    IsolationLevel,
    LocalBackend,
    ShellBackend,
    ShellOutcome,
    ShellRequest,
    available_backends,
    backend_allows_contained_relaxation,
    configure_backend,
    get_shell_backend,
    isolation_at_least,
    register_backend,
    reset_shell_backend,
)

__all__ = [
    "BackendInfo",
    "DataSink",
    "DockerBackend",
    "IsolationLevel",
    "LocalBackend",
    "ShellBackend",
    "ShellOutcome",
    "ShellRequest",
    "available_backends",
    "backend_allows_contained_relaxation",
    "configure_backend",
    "get_shell_backend",
    "isolation_at_least",
    "register_backend",
    "reset_shell_backend",
]
