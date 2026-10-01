"""Independent naive expansion used as a reference implementation."""

from __future__ import annotations

from typing import Dict, List, Optional

from jvmstack import ExceptionRecord


class NaiveExpansion:
    """Expand every exception's full stack layer by layer."""

    def __init__(self, records: List[ExceptionRecord]) -> None:
        self._records: Dict[str, ExceptionRecord] = {r.exc_id: r for r in records}
        self._cache: Dict[str, List[str]] = {}

    def full_stack(self, exc_id: str) -> List[str]:
        if exc_id in self._cache:
            return self._cache[exc_id]
        rec = self._records[exc_id]
        stack = list(rec.frames)
        if rec.parent_id is not None and rec.omitted > 0:
            parent_stack = self.full_stack(rec.parent_id)
            stack.extend(parent_stack[len(parent_stack) - rec.omitted :])
        self._cache[exc_id] = stack
        return stack

    def depth(self, exc_id: str) -> int:
        return len(self.full_stack(exc_id))

    def first_frame(self, exc_id: str) -> Optional[str]:
        stack = self.full_stack(exc_id)
        return stack[0] if stack else None

    def read_frames(self, exc_id: str, offset: int, length: int) -> List[str]:
        if length <= 0 or offset < 0:
            return []
        return self.full_stack(exc_id)[offset : offset + length]
