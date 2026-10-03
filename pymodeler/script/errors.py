"""Error types for build scripts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Problem:
    """One validation problem, located by a step path such as ``step 4 (push_pull)``."""

    path: str
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


class ScriptError(Exception):
    """A build script could not be run; ``path`` says which step failed."""

    def __init__(self, message: str, path: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.path = path

    def __str__(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


class ScriptValidationError(ScriptError):
    """A build script failed validation; ``problems`` lists every issue found."""

    def __init__(self, problems: list[Problem]) -> None:
        self.problems = problems
        lines = "\n".join(f"  - {p}" for p in problems)
        super().__init__(f"{len(problems)} problem(s) found:\n{lines}")

    def __str__(self) -> str:
        return self.message
