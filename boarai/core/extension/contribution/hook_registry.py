"""
HookRegistry for BoarAi extension system (v2).

Named hook points with three semantics:
- observe    : notify-only, result ignored
- blockable  : first-deny-wins — first handler returning {"blocked": True} stops
              the chain; later verdicts never clobber an earlier deny
- transform  : value flows through handlers in order; each may modify it

Namespaces:
- core.<event>            : core-pre-embedded points
- ext.<extension>.<event> : points declared by extensions themselves (P6)

Design guarantees:
- Ordered execution by ``order`` then registration sequence (no asyncio.gather).
- Every handler runs under a timeout; failures are isolated (logged, chain continues).
- Lazy binding: a handler may register before the declaring extension loads;
  it sits pending and is bound when the point is declared.
- Re-entrancy guard: trigger depth is capped to prevent infinite loops.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class HookSemantics(str, Enum):
    OBSERVE = "observe"
    BLOCKABLE = "blockable"
    TRANSFORM = "transform"


class HookResult:
    """Normalized result of a hook trigger."""

    __slots__ = ("blocked", "reason", "data", "handlers_run")

    def __init__(self, blocked: bool = False, reason: str = "", data: Any = None,
                 handlers_run: int = 0) -> None:
        self.blocked = blocked
        self.reason = reason
        self.data = data
        self.handlers_run = handlers_run

    def to_dict(self) -> Dict[str, Any]:
        return {
            "blocked": self.blocked,
            "reason": self.reason,
            "data": self.data,
            "handlers_run": self.handlers_run,
        }


@dataclass
class HookPoint:
    """A declared hook point (owned by core or an extension)."""

    name: str
    semantics: HookSemantics
    description: str
    owner: str  # "core" or extension id
    visibility: str = "public"  # public | private (private: core-only handlers)
    declared_at: float = field(default_factory=time.time)


@dataclass
class HookBinding:
    """A handler bound to a hook point."""

    point_name: str
    handler: Callable[..., Any]
    order: int = 0
    extension_id: str = ""
    seq: int = 0  # registration sequence for stable ordering


DEFAULT_HOOK_TIMEOUT = 5.0
MAX_TRIGGER_DEPTH = 8


class HookRegistry:
    """Global namespaced hook registry."""

    def __init__(self) -> None:
        self._points: Dict[str, HookPoint] = {}
        self._bindings: Dict[str, List[HookBinding]] = {}  # point -> ordered list
        self._pending: Dict[str, List[HookBinding]] = {}  # point -> awaiting declaration
        self._seq_counter = 0
        self._trigger_depth = 0

    # --- Declaration (P6: extensions can declare their own points) ---

    def declare(self, name: str, semantics: str, owner: str,
                description: str = "", visibility: str = "public") -> HookPoint:
        """Declare a hook point. Flushes any pending bindings."""
        name = self._normalize_name(name, owner)
        sem = HookSemantics(semantics)
        if name in self._points:
            existing = self._points[name]
            if existing.owner == owner:
                return existing  # idempotent re-declaration
            raise ValueError(
                f"Hook point '{name}' already declared by '{existing.owner}'"
            )
        point = HookPoint(
            name=name, semantics=sem, description=description,
            owner=owner, visibility=visibility,
        )
        self._points[name] = point
        pending = self._pending.pop(name, []) or []
        self._bindings[name] = pending
        self._bindings[name].sort(key=lambda b: (b.order, b.seq))
        # Structural noise: the point set is fixed by code, identical on
        # every startup — DEBUG so it never lands in files at default level.
        logger.debug(
            f"Hook point declared: {name} (semantics={sem.value}, owner={owner})"
        )
        return point

    def undeclare(self, name: str, owner: str) -> bool:
        """Remove a hook point declared by ``owner``. Unbinds its handlers."""
        point = self._points.get(name)
        if not point or point.owner != owner:
            return False
        del self._points[name]
        self._bindings.pop(name, None)
        self._pending.pop(name, None)
        logger.info(f"Hook point removed: {name} (owner={owner})")
        return True

    # --- Binding ---

    def register(self, point_name: str, handler: Callable[..., Any],
                 owner: str = "", order: int = 0) -> str:
        """Bind a handler to a hook point (core.* or ext.<name>.*)."""
        self._seq_counter += 1
        binding = HookBinding(
            point_name=point_name, handler=handler, order=order,
            extension_id=owner, seq=self._seq_counter,
        )
        point = self._points.get(point_name)
        if point is not None:
            if point.visibility == "private" and owner != point.owner:
                raise ValueError(
                    f"Hook point '{point_name}' is private; "
                    f"only '{point.owner}' can bind handlers"
                )
            lst = self._bindings.setdefault(point_name, [])
            lst.append(binding)
            lst.sort(key=lambda b: (b.order, b.seq))
        else:
            # Lazy binding: keep pending until the declaring extension loads
            self._pending.setdefault(point_name, []).append(binding)
            logger.debug(
                f"Hook binding pending (point not declared yet): "
                f"{point_name} <- {owner or 'core'}"
            )
        return point_name

    def pending_for(self, owner: str) -> List[HookBinding]:
        """Bindings that are still waiting for their point to be declared."""
        out: List[HookBinding] = []
        for bindings in self._pending.values():
            for binding in bindings:
                if binding.extension_id == owner:
                    out.append(binding)
        return out

    def declared_points(self) -> List[str]:
        """Every declared point name, sorted (for diagnostics and validation)."""
        return sorted(self._points)

    def unregister(self, point_name: str, handler: Callable[..., Any],
                   owner: str = "") -> bool:
        """Remove a specific handler binding."""
        for lst in (self._bindings.get(point_name), self._pending.get(point_name)):
            if not lst:
                continue
            for i, b in enumerate(lst):
                if b.handler is handler and (not owner or b.extension_id == owner):
                    lst.pop(i)
                    return True
        return False

    def unregister_all_for(self, owner: str) -> int:
        """Remove all bindings owned by an extension (on unload)."""
        count = 0
        for lst in list(self._bindings.values()):
            keep = [b for b in lst if b.extension_id != owner]
            count += len(lst) - len(keep)
            lst[:] = keep
        for lst in list(self._pending.values()):
            keep = [b for b in lst if b.extension_id != owner]
            count += len(lst) - len(keep)
            lst[:] = keep
        return count

    # --- Trigger ---

    async def trigger(self, point_name: str, payload: Any = None,
                      *, timeout: float = DEFAULT_HOOK_TIMEOUT) -> HookResult:
        """Execute all bound handlers for a hook point, in order."""
        point = self._points.get(point_name)
        if point is None:
            logger.debug(f"trigger on undeclared hook point: {point_name}")
            return HookResult(handlers_run=0)

        bindings = self._bindings.get(point_name) or []
        if not bindings:
            return HookResult(handlers_run=0)

        if self._trigger_depth >= MAX_TRIGGER_DEPTH:
            logger.error(
                f"Hook recursion depth exceeded at '{point_name}' "
                f"(max {MAX_TRIGGER_DEPTH}); aborting chain"
            )
            return HookResult(
                blocked=True, reason="hook recursion depth exceeded",
                handlers_run=0,
            )

        self._trigger_depth += 1
        try:
            data = payload
            handlers_run = 0
            for binding in bindings:
                handlers_run += 1
                try:
                    result = await asyncio.wait_for(
                        self._call_handler(binding, data), timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Hook handler timed out after {timeout}s: "
                        f"{point_name} <- {binding.extension_id or 'core'}"
                    )
                    continue
                except Exception as e:  # noqa: BLE001 — isolate bad handlers
                    logger.error(
                        f"Hook handler error at '{point_name}' "
                        f"<- {binding.extension_id or 'core'}: {e}"
                    )
                    continue

                if point.semantics == HookSemantics.BLOCKABLE:
                    if isinstance(result, dict) and result.get("blocked"):
                        return HookResult(
                            blocked=True,
                            reason=result.get("reason", "blocked by hook"),
                            data=result.get("data", data),
                            handlers_run=handlers_run,
                        )
                    if isinstance(result, dict) and "data" in result:
                        data = result["data"]
                elif point.semantics == HookSemantics.TRANSFORM:
                    data = result
                # OBSERVE: ignore handler result

            return HookResult(handlers_run=handlers_run, data=data)
        finally:
            self._trigger_depth -= 1

    async def _call_handler(self, binding: HookBinding, data: Any) -> Any:
        """Invoke a handler with the agreed signature: handler(payload)."""
        handler = binding.handler
        if asyncio.iscoroutinefunction(handler):
            return await handler(data)
        return await asyncio.to_thread(handler, data)

    # --- Introspection ---

    def get_point(self, name: str) -> Optional[HookPoint]:
        return self._points.get(name)

    def is_declared(self, name: str) -> bool:
        return name in self._points

    def list_points(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": p.name,
                "semantics": p.semantics.value,
                "owner": p.owner,
                "description": p.description,
                "visibility": p.visibility,
                "bindings": len(self._bindings.get(p.name, [])),
                "pending": len(self._pending.get(p.name, [])),
            }
            for p in self._points.values()
        ]

    def list_bindings(self, point_name: str) -> List[Dict[str, Any]]:
        return [
            {
                "point": b.point_name,
                "extension_id": b.extension_id or "core",
                "order": b.order,
                "seq": b.seq,
            }
            for b in (self._bindings.get(point_name) or [])
        ]

    @staticmethod
    def _normalize_name(name: str, owner: str) -> str:
        """Ensure a full namespaced name: core.<event> or ext.<ext>.<event>."""
        if "." in name:
            return name
        if owner == "core":
            return f"core.{name}"
        return f"ext.{owner}.{name}"


_hook_registry: Optional[HookRegistry] = None


def get_hook_registry() -> HookRegistry:
    """Get the global HookRegistry singleton."""
    global _hook_registry
    if _hook_registry is None:
        _hook_registry = HookRegistry()
    return _hook_registry


def reset_hook_registry() -> None:
    """Reset the global HookRegistry (for testing)."""
    global _hook_registry
    _hook_registry = None
