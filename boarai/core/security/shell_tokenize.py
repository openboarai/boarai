"""Conservative shell command tokenizer for permission classification.

Splits a command into independent segments, each a list of word tokens, so the
permission check can reason about ``argv[0]`` and its arguments separately
instead of running a regex over the raw text. Handles single and double quotes,
backslash escapes, and the usual operators (``;``, ``&&``, ``||``, ``|``, ``&``,
``(``, ``)``, newlines) as segment boundaries.

This is deliberately NOT a full POSIX parser. Heredocs, command substitution,
process substitution, and arithmetic expansion are out of scope, and so is
variable expansion: tokens keep their literal text, so ``$HOME/.ssh/id_rsa``
arrives as one token rather than an expanded path.

The important property is that it reports failure instead of guessing. Any
construct it cannot represent makes :func:`tokenize_shell_segments` return
``None``, and callers must then escalate to a user prompt rather than classify
the command as read-only. A hand-rolled splitter that quietly mis-parses
``cat <<EOF`` or ``$(rm -rf ~)`` produces a segment list that looks plausible
and auto-allows the wrong thing.
"""

from __future__ import annotations

from typing import List, Optional

# Characters that start a construct this tokenizer does not model. Seeing one
# anywhere outside single quotes means the command's real behaviour cannot be
# determined from its text, so the caller must not auto-allow it.
_UNSUPPORTED_MARKERS = (
    "$(",   # command substitution (and $(( arithmetic, which contains it)
    "`",    # legacy command substitution
    "<<",   # heredoc / herestring
    "<(",   # process substitution
    ">(" ,  # process substitution
)

# Operator characters that end a segment when seen outside quotes.
_SEPARATORS = frozenset(";|&\n\r")


def tokenize_shell_segments(command: str) -> Optional[List[List[str]]]:
    """Split ``command`` into segments of word tokens."""
    if not command or not command.strip():
        return []

    if _has_unsupported_construct(command):
        return None

    segments: List[List[str]] = []
    current: List[str] = []
    buf: List[str] = []
    in_single = False
    in_double = False
    quoted = False  # current token contained a quote, so "" is a real empty arg
    i = 0
    n = len(command)

    def flush_token() -> None:
        nonlocal buf, quoted
        if buf or quoted:
            current.append("".join(buf))
        buf = []
        quoted = False

    def flush_segment() -> None:
        nonlocal current
        flush_token()
        if current:
            segments.append(current)
        current = []

    while i < n:
        ch = command[i]

        if in_single:
            # Single quotes are literal in both POSIX shells and PowerShell;
            # there is no escape sequence inside them.
            if ch == "'":
                in_single = False
            else:
                buf.append(ch)
            i += 1
            continue

        if in_double:
            if ch == "\\" and i + 1 < n:
                nxt = command[i + 1]
                # Inside double quotes a backslash is only an escape for these;
                # otherwise it is literal (bash semantics).
                if nxt in ('"', "\\", "$", "`"):
                    buf.append(nxt)
                    i += 2
                    continue
                buf.append(ch)
                i += 1
                continue
            if ch == '"':
                in_double = False
            else:
                buf.append(ch)
            i += 1
            continue

        # Unquoted context.
        if ch == "'":
            in_single = True
            quoted = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            quoted = True
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            buf.append(command[i + 1])
            i += 2
            continue
        if ch in (" ", "\t"):
            flush_token()
            i += 1
            continue
        if ch in _SEPARATORS:
            # Consume the second character of && and || so a pair is one boundary.
            if i + 1 < n and command[i + 1] == ch and ch in "&|":
                i += 1
            flush_segment()
            i += 1
            continue
        if ch in "()":
            # Subshell grouping: treat as a boundary so each side classifies
            # on its own rather than as one glued token.
            flush_segment()
            i += 1
            continue

        buf.append(ch)
        i += 1

    if in_single or in_double:
        return None  # unterminated quote: the real token boundaries are unknown

    flush_segment()
    return segments


def _has_unsupported_construct(command: str) -> bool:
    """Whether ``command`` uses a construct this tokenizer cannot represent."""
    in_single = False
    in_double = False
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_double = False
            else:
                # Command substitution still expands inside double quotes in
                # both bash and PowerShell, so it stays unsupported here.
                if _matches_marker_at(command, i):
                    return True
            i += 1
            continue
        if ch == "'":
            in_single = True
            i += 1
            continue
        if ch == '"':
            in_double = True
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            i += 2
            continue
        if _matches_marker_at(command, i):
            return True
        i += 1
    return False


def _matches_marker_at(command: str, i: int) -> bool:
    return any(command.startswith(marker, i) for marker in _UNSUPPORTED_MARKERS)


def segment_text(tokens: List[str]) -> str:
    """Rejoin tokens into a canonical single-spaced segment string."""
    return " ".join(tokens)
