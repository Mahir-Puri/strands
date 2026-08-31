"""Thin wrapper around the Anthropic Messages API.

The rest of the codebase talks to the model through this one class so that
retries, the audit trail, and the tool-use loop live in a single place. If
you swap providers, this is the only file that has to change.

The important piece is run_with_tools. That is the actual agent loop: send
the conversation, if the model asks for a tool run it, feed the result back,
repeat until the model stops asking or we hit the step cap.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from .config import Settings
from .memory import Memory
from .retry import RetryPolicy, with_retry

# A tool executor takes the model's input dict and returns a JSON-able result.
ToolExecutor = Callable[[dict[str, Any]], Any]


class LLMError(RuntimeError):
    pass


class LLMClient:
    def __init__(self, settings: Settings, memory: Memory):
        self.settings = settings
        self.memory = memory
        self._client = None  # lazily built so importing this file needs no key

    def _anthropic(self):
        if self._client is None:
            if not self.settings.has_anthropic:
                raise LLMError(
                    "ANTHROPIC_API_KEY is not set. Export it before running a live goal."
                )
            # Imported lazily on purpose. Tests that never make a live call do
            # not need the SDK installed or a key present.
            from anthropic import Anthropic

            self._client = Anthropic(
                api_key=self.settings.anthropic_api_key,
                timeout=self.settings.request_timeout_s,
            )
        return self._client

    def _policy(self) -> RetryPolicy:
        return RetryPolicy(attempts=self.settings.max_task_attempts)

    def complete(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 2048,
        actor: str = "llm",
    ) -> Any:
        """One round trip to the model, wrapped in retry and audited."""

        chosen = model or self.settings.model

        def _call() -> Any:
            client = self._anthropic()
            kwargs: dict[str, Any] = {
                "model": chosen,
                "max_tokens": max_tokens,
                "system": system,
                "messages": messages,
            }
            if tools:
                kwargs["tools"] = tools
            return client.messages.create(**kwargs)

        def _on_retry(attempt: int, exc: BaseException) -> None:
            self.memory.audit.record(
                kind="llm_retry",
                actor=actor,
                summary=f"attempt {attempt} failed: {type(exc).__name__}",
                error=str(exc),
            )

        response = with_retry(_call, self._policy(), on_retry=_on_retry)
        self.memory.audit.record(
            kind="llm_call",
            actor=actor,
            summary=f"model={chosen} stop={getattr(response, 'stop_reason', '?')}",
            model=chosen,
        )
        return response

    def run_with_tools(
        self,
        *,
        system: str,
        user_prompt: str,
        tools: list[dict[str, Any]],
        executors: dict[str, ToolExecutor],
        model: str | None = None,
        actor: str = "agent",
        max_steps: int | None = None,
    ) -> str:
        """The agent loop.

        Keeps handing tool results back to the model until it produces a
        final text answer or we run out of steps. The step cap is a safety
        rail: a confused agent should stop, not loop forever burning tokens.
        """

        steps = max_steps or self.settings.max_agent_steps
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_prompt}]

        for _step in range(steps):
            response = self.complete(
                system=system,
                messages=messages,
                model=model,
                tools=tools,
                actor=actor,
            )

            if response.stop_reason != "tool_use":
                return _text_of(response)

            # Record the assistant turn verbatim so the next request has the
            # full tool_use block the API expects to see echoed back.
            messages.append({"role": "assistant", "content": response.content})

            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                name = block.name
                executor = executors.get(name)
                if executor is None:
                    result: Any = {"error": f"no executor registered for tool '{name}'"}
                    is_error = True
                else:
                    try:
                        result = executor(block.input)
                        is_error = False
                    except Exception as exc:  # a tool blowing up is not fatal
                        result = {"error": str(exc)}
                        is_error = True

                self.memory.audit.record(
                    kind="tool_call",
                    actor=actor,
                    summary=f"{name} -> {'error' if is_error else 'ok'}",
                    tool=name,
                    args=_safe(block.input),
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                        "is_error": is_error,
                    }
                )

            messages.append({"role": "user", "content": tool_results})

        # Fell off the end of the step budget without a final answer.
        self.memory.audit.record(
            kind="agent_exhausted",
            actor=actor,
            summary=f"hit step cap of {steps} without finishing",
        )
        return "[agent stopped: reached step limit without a final answer]"


def _text_of(response: Any) -> str:
    parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip()


def _safe(value: Any) -> Any:
    """Trim tool args before they go in the audit log so it stays readable."""
    try:
        text = json.dumps(value, default=str)
    except TypeError:
        return {"repr": repr(value)[:500]}
    return json.loads(text) if len(text) < 2000 else {"truncated": text[:2000]}
