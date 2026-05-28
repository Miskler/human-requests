from __future__ import annotations


class UserScriptError(RuntimeError):
    """Base class for errors raised by user-provided scripts."""


class WarmupError(UserScriptError):
    """Raised when a warmup script fails."""


class MethodPipelineError(UserScriptError):
    """Raised when a method pipeline fails."""
