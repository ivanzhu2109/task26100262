"""折叠堆栈报告：装载、深度/起始帧查询、片段读取。

核心思路（全程无递归，任意因果链深度安全）：

- 异常 e 的完整栈 = 显式帧 ++ 直接父异常完整栈的最后 N 帧，故
  depth(e) = len(frames_e) + N_e，装载时 O(1) 求得。
- 从 e 的第 i 帧向上爬一层：若 i >= len(frames_e)，则等价于查询父异常
  第 i + s_e 帧，其中 s_e = depth(parent) - N_e - len(frames_e)（可为负）。
  首个使 i < len(frames) 的祖先即为该帧的来源异常。
- 用倍增表（binary lifting）加速定位：up/need/summ 三张表支持
  O(log E) 的 "解析某帧来自哪个祖先的哪条显式帧"。
- 另为每个异常预计算 next_run：其继承段首帧的来源位置。片段读取时
  只需对起始偏移做一次倍增解析，之后跨异常边界均为 O(1) 跳转。
"""
from __future__ import annotations

from array import array
from typing import Dict, Hashable, Iterable, List, Optional, Tuple

from .types import ExceptionRecord, Relation


class StackReport:
    """装载完成后只读的折叠堆栈报告。"""

    __slots__ = (
        "_ids",
        "_frames",
        "_depth",
        "_start",
        "_up",
        "_need",
        "_summ",
        "_next_run",
    )

    def __init__(self) -> None:
        self._ids: Dict[Hashable, int] = {}
        self._frames: List[List[str]] = []
        self._depth: List[int] = []
        self._start: List[Optional[str]] = []
        self._up: List[array] = []
        self._need: List[array] = []
        self._summ: List[array] = []
        self._next_run: List[Optional[Tuple[int, int]]] = []

    # ------------------------------------------------------------------ load

    @classmethod
    def load(cls, records: Iterable[ExceptionRecord]) -> "StackReport":
        report = cls()
        report._build(records)
        return report

    def _build(self, records: Iterable[ExceptionRecord]) -> None:
        ids = self._ids
        frames_col = self._frames
        depth = self._depth
        parent_of: List[int] = []
        omitted_of: List[int] = []
        root_seen = False
        count = 0
        for rec in records:
            idx = count
            if rec.exception_id in ids:
                raise ValueError(f"duplicate exception id: {rec.exception_id!r}")
            if rec.omitted < 0:
                raise ValueError(f"negative omitted count: {rec.exception_id!r}")
            if rec.parent_id is None:
                if rec.relation is not None or rec.omitted != 0:
                    raise ValueError(
                        f"root must have no relation and omitted == 0: "
                        f"{rec.exception_id!r}"
                    )
                if root_seen:
                    raise ValueError("report has more than one root exception")
                root_seen = True
                parent_idx = -1
            else:
                if rec.relation not in (Relation.CAUSE, Relation.SUPPRESSED):
                    raise ValueError(
                        f"invalid relation: {rec.exception_id!r}"
                    )
                try:
                    parent_idx = ids[rec.parent_id]
                except KeyError:
                    raise ValueError(
                        f"unknown or out-of-order parent "
                        f"{rec.parent_id!r} for {rec.exception_id!r}"
                    ) from None
            explicit = list(rec.frames)
            if parent_idx >= 0 and rec.omitted > depth[parent_idx]:
                raise ValueError(
                    f"omitted exceeds parent depth: {rec.exception_id!r}"
                )
            ids[rec.exception_id] = idx
            frames_col.append(explicit)
            parent_of.append(parent_idx)
            omitted_of.append(rec.omitted)
            depth.append(len(explicit) + rec.omitted)
            count += 1
        if count == 0:
            raise ValueError("report is empty")

        # 倍增表：up[k][e] 为 e 向上 2^k 步的祖先（-1 表示不存在）；
        # summ[k][e] 为这 2^k 步的累计下标位移；
        # need[k][e] 为安全跳过这 2^k 步所需的最小下标
        # （途经每个异常 m 都要求当前下标 >= len(frames_m)，
        #   need 即这些要求关于起始下标的最大值）。
        levels = max(1, count.bit_length())
        up = [array("q") for _ in range(levels)]
        need = [array("q") for _ in range(levels)]
        summ = [array("q") for _ in range(levels)]
        up0, need0, summ0 = up[0], need[0], summ[0]
        for e in range(count):
            p = parent_of[e]
            up0.append(p)
            need0.append(len(frames_col[e]))
            if p >= 0:
                summ0.append(depth[p] - omitted_of[e] - len(frames_col[e]))
            else:
                summ0.append(0)
        for k in range(1, levels):
            prev_up, prev_need, prev_summ = up[k - 1], need[k - 1], summ[k - 1]
            cur_up = array("q")
            cur_need = array("q")
            cur_summ = array("q")
            ap_u = cur_up.append
            ap_n = cur_need.append
            ap_s = cur_summ.append
            for e in range(count):
                mid = prev_up[e]
                if mid < 0:
                    ap_u(-1)
                    ap_n(0)
                    ap_s(0)
                else:
                    ap_u(prev_up[mid])
                    s1 = prev_summ[e]
                    ap_s(s1 + prev_summ[mid])
                    n2 = prev_need[mid] - s1
                    ap_n(prev_need[e] if prev_need[e] >= n2 else n2)
            up[k] = cur_up
            need[k] = cur_need
            summ[k] = cur_summ
        self._up, self._need, self._summ = up, need, summ

        # next_run[e]：e 的继承段首帧（完整栈下标 len(frames_e)）的来源
        # (祖先下标, 该祖先显式帧下标)；N_e == 0 时为 None。
        next_run: List[Optional[Tuple[int, int]]] = [None] * count
        for e in range(count):
            p = parent_of[e]
            n = omitted_of[e]
            if p >= 0 and n > 0:
                next_run[e] = self._resolve(p, depth[p] - n)
        self._next_run = next_run

        # 起始帧：显式帧非空取首帧，否则取继承段首帧，空栈为 None。
        start: List[Optional[str]] = []
        for e in range(count):
            explicit = frames_col[e]
            if explicit:
                start.append(explicit[0])
            else:
                nxt = next_run[e]
                start.append(None if nxt is None else frames_col[nxt[0]][nxt[1]])
        self._start = start

    # -------------------------------------------------------------- internal

    def _index_of(self, exception_id: Hashable) -> int:
        try:
            return self._ids[exception_id]
        except KeyError:
            raise KeyError(f"unknown exception id: {exception_id!r}") from None

    def _resolve(self, e: int, i: int) -> Tuple[int, int]:
        """返回 e 的完整栈第 i 帧的来源 (祖先下标, 显式帧下标)。"""
        up = self._up
        need = self._need
        summ = self._summ
        for k in range(len(up) - 1, -1, -1):
            up_k = up[k]
            u = up_k[e]
            if u >= 0 and i >= need[k][e]:
                i += summ[k][e]
                e = u
        return e, i

    # ---------------------------------------------------------------- query

    def depth(self, exception_id: Hashable) -> int:
        """指定异常的完整调用深度（含继承帧）。"""
        return self._depth[self._index_of(exception_id)]

    def start_frame(self, exception_id: Hashable) -> Optional[str]:
        """指定异常完整栈的起始帧（栈顶）；空栈返回 None。"""
        return self._start[self._index_of(exception_id)]

    def read_frames(
        self, exception_id: Hashable, offset: int, length: int
    ) -> List[str]:
        """按零基偏移读取连续帧片段，与完整展开后的切片一致。"""
        if length <= 0:
            return []
        e = self._index_of(exception_id)
        depth_e = self._depth[e]
        if offset < 0:
            raise ValueError("offset must be >= 0")
        if offset >= depth_e:
            return []
        remaining = min(length, depth_e - offset)
        frames_col = self._frames
        next_run = self._next_run
        out: List[str] = []
        extend = out.extend
        e, i = self._resolve(e, offset)
        while remaining > 0:
            explicit = frames_col[e]
            take = len(explicit) - i
            if take > remaining:
                take = remaining
            extend(explicit[i : i + take])
            remaining -= take
            if remaining > 0:
                nxt = next_run[e]
                assert nxt is not None  # 由 offset+length <= depth 保证
                e, i = nxt
        return out
