"""Tool plumbing.

A Tool is a name, a JSON schema the model uses to call it, and a Python
function that does the work. The registry collects tools and can hand the
Anthropic API the schema list plus a name->function map for the run loop.

Keeping the schema next to the implementation means the two never drift
apart, which is the usual bug with hand-maintained tool definitions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

ToolFn = Callable[..., Any]


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: ToolFn

    def to_spec(self) -> dict[str, Any]:
        """The shape the Anthropic tools parameter wants."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool '{tool.name}' already registered")
        self._tools[tool.name] = tool

    def add(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
    ) -> Callable[[ToolFn], ToolFn]:
        """Decorator form. Registers the wrapped function as a tool."""

        def deco(fn: ToolFn) -> ToolFn:
            self.register(Tool(name, description, input_schema, fn))
            return fn

        return deco

    def specs(self) -> list[dict[str, Any]]:
        return [t.to_spec() for t in self._tools.values()]

    def executors(self) -> dict[str, Callable[[dict[str, Any]], Any]]:
        # The run loop passes the raw input dict, so adapt fn(**kwargs) to
        # fn(input_dict) here.
        return {name: (lambda inp, f=tool.fn: f(**inp)) for name, tool in self._tools.items()}

    def subset(self, names: list[str]) -> ToolRegistry:
        """A registry holding only the named tools, for scoping an agent."""
        out = ToolRegistry()
        for name in names:
            if name in self._tools:
                out.register(self._tools[name])
        return out

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)
