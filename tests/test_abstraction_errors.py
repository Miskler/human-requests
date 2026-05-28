from __future__ import annotations

from human_requests.abstraction import MethodPipelineError, UserScriptError, WarmupError


def test_abstraction_error_hierarchy() -> None:
    assert issubclass(WarmupError, UserScriptError)
    assert issubclass(MethodPipelineError, UserScriptError)
    assert issubclass(WarmupError, RuntimeError)
    assert issubclass(MethodPipelineError, RuntimeError)
