"""Observability flushing must include LangSmith and Langfuse safely."""
from __future__ import annotations

import sys
from types import SimpleNamespace


def test_flush_observability_flushes_langsmith_cached_client(monkeypatch):
    from src.tools.observability import flush_observability

    calls = []
    fake_client = SimpleNamespace(flush=lambda: calls.append("langsmith"))
    fake_run_helpers = SimpleNamespace(_CLIENT=fake_client)
    fake_langsmith = SimpleNamespace(
        Client=lambda: fake_client,
        run_helpers=fake_run_helpers,
    )
    monkeypatch.setitem(sys.modules, "langsmith", fake_langsmith)
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGFUSE_TRACING", "false")

    flush_observability()

    assert calls == ["langsmith"]


def test_flush_observability_is_failure_isolated(monkeypatch):
    from src.tools.observability import flush_observability

    class BrokenClient:
        def flush(self):
            raise RuntimeError("network down")

    monkeypatch.setitem(sys.modules, "langsmith", SimpleNamespace(
        Client=lambda: BrokenClient(),
        run_helpers=SimpleNamespace(_CLIENT=None),
    ))
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGFUSE_TRACING", "false")

    flush_observability()
