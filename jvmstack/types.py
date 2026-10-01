"""数据类型定义：异常记录与关系枚举。"""
from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Hashable, Optional, Sequence


class Relation(enum.Enum):
    """子异常与直接父异常的关系。"""

    CAUSE = "cause"
    SUPPRESSED = "suppressed"


@dataclass(frozen=True)
class ExceptionRecord:
    """一条异常记录（按报告中出现顺序提交）。

    frames 按栈顶到栈底排列；omitted 为 JVM "... N more" 的省略数量 N。
    根异常 parent_id 为 None、relation 为 None 且 omitted 为 0。
    """

    exception_id: Hashable
    parent_id: Optional[Hashable]
    relation: Optional[Relation]
    frames: Sequence[str]
    omitted: int
