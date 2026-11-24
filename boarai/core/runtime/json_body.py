"""JSON request-body parsing shared by the HTTP API handlers.

Malformed client input must surface as a 4xx. ``aiohttp``'s ``request.json()``
raises ``ValueError`` on an empty or syntactically invalid body and happily
returns a non-object JSON value, so an unguarded call reaches the handler's
catch-all ``except Exception`` and answers 500 for what is the client's mistake.

This lives in ``core.runtime`` because that package is the only level every
consumer can reach without an import cycle: ``core/api/__init__.py`` imports
``..task.api``, so ``core.task.api`` can never import back from ``core.api``.
Stdlib-only, like the other dependency-free leaves here.
"""

import json

from typing import Any, Dict, Optional, Tuple, Union

RawBody = Union[bytes, str, None]


def parse_json_object(raw: RawBody) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse a raw request body into a JSON object."""
    if raw is None:
        return None, "Empty request body"

    if isinstance(raw, bytes):
        if not raw.strip():
            return None, "Empty request body"
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            return None, f"Request body is not valid UTF-8: {e}"
    elif isinstance(raw, str):
        if not raw.strip():
            return None, "Empty request body"
        text = raw
    else:
        return None, "Request body must be JSON"

    try:
        parsed = json.loads(text)
    except ValueError as e:
        return None, f"Invalid JSON: {e}"

    if not isinstance(parsed, dict):
        return None, "Request body must be a JSON object"
    return parsed, None
