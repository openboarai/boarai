"""Curation package: timer-driven deduplication of global lessons."""

from .curator import (
    DEFAULT_INTERVAL,
    Curator,
    get_curator,
)

__all__ = [
    "DEFAULT_INTERVAL",
    "Curator",
    "get_curator",
]
