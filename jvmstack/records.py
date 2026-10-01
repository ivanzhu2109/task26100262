"""输入记录类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


class Relation(Enum):
    """子异常与直接父异常的关系。"""

    CAUSE = "cause"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True)
class ExceptionRecord:
    """一条异常记录（按出现顺序提交）。

    frames 按栈顶到栈底排列；omitted 为 "... N more" 的 N，
    表示与直接父异常完整调用栈尾部相同的帧数。
    根异常 parent_id 与 relation 为 None，omitted 为 0。
    """

    exc_id: str
    parent_id: Optional[str]
    relation: Optional[Relation]
    frames: Tuple[str, ...] = field(default_factory=tuple)
    omitted: int = 0
