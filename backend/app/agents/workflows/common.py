"""Helpers shared by the workflow graphs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.runnables import RunnableConfig

from app.agents.runtime import RunContext, run_context

NodeFn = Callable[[dict[str, Any], RunContext], dict[str, Any] | None]


def node(fn: NodeFn) -> Any:
    """Adapt ``fn(state, ctx)`` to a LangGraph node ``(state, config)``: each node gets its own
    database session bound to the run (committed when the node finishes). Typed ``Any`` because
    LangGraph's ``add_node`` overloads are tied to each graph's state type."""

    def wrapped(state: dict[str, Any], config: RunnableConfig) -> dict[str, Any]:
        with run_context(dict(config)) as ctx:
            return fn(state, ctx) or {}

    wrapped.__name__ = fn.__name__
    return wrapped
