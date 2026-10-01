"""只读异常报告：折叠堆栈的装载与查询。"""

from __future__ import annotations

from array import array
from typing import Iterable, List, Optional

from .records import ExceptionRecord


class StackReport:
    """装载一份完整异常报告后提供只读查询。"""

    def __init__(self) -> None:
        self._loaded = False
        self._index = {}
        self._frames: List[tuple] = []
        self._flen = array("q")
        self._depth = array("q")
        self._up: List[array] = []
        self._add: List[array] = []
        self._mx: List[array] = []
        self._cont_exc = array("q")
        self._cont_off = array("q")

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self, records: Iterable[ExceptionRecord]) -> None:
        """按出现顺序装载全部异常记录。装载后报告只读。"""
        if self._loaded:
            raise RuntimeError("report already loaded")
        records = list(records)
        n = len(records)
        levels = n.bit_length() + 1
        self._up = [array("q", [-1]) * n for _ in range(levels)]
        self._add = [array("q", [0]) * n for _ in range(levels)]
        self._mx = [array("q", [0]) * n for _ in range(levels)]
        index = self._index
        frames = self._frames
        flen = self._flen
        depth = self._depth
        up, add, mx = self._up, self._add, self._mx
        cont_exc = self._cont_exc
        cont_off = self._cont_off
        for idx, rec in enumerate(records):
            index[rec.exc_id] = idx
            f = tuple(rec.frames)
            frames.append(f)
            lf = len(f)
            flen.append(lf)
            depth.append(lf + rec.omitted)
            if rec.parent_id is None:
                p = -1
                delta = 0
            else:
                p = index[rec.parent_id]
                delta = depth[p] - rec.omitted - lf
            # 倍增表第 0 层：直接父异常
            row = 0
            up[row][idx] = p
            add[row][idx] = delta
            mx[row][idx] = lf
            # 更高层：2^k 级祖先，沿父链已就绪（父先子后）
            while up[row][idx] != -1:
                mid = up[row][idx]
                up[row + 1][idx] = up[row][mid]
                add[row + 1][idx] = add[row][idx] + add[row][mid]
                mx[row + 1][idx] = max(mx[row][idx], mx[row][mid] - add[row][idx])
                row += 1
            # 续接链：显式帧耗尽后（偏移 flen）落到哪个祖先的哪一帧
            if p == -1 or rec.omitted == 0:
                cont_exc.append(-1)
                cont_off.append(0)
            else:
                h, o = self._resolve(p, depth[p] - rec.omitted)
                cont_exc.append(h)
                cont_off.append(o)
        self._loaded = True

    def _resolve(self, e: int, o: int) -> tuple:
        """把异常 e 完整栈中偏移 o 定位到 (祖先下标, 该祖先显式帧内偏移)。"""
        up, add, mx = self._up, self._add, self._mx
        for k in range(len(up) - 1, -1, -1):
            u = up[k][e]
            if u != -1 and o >= mx[k][e]:
                o += add[k][e]
                e = u
        return e, o

    def depth(self, exc_id: str) -> int:
        """指定异常的完整调用深度（含继承帧）。"""
        return self._depth[self._index[exc_id]]

    def first_frame(self, exc_id: str) -> Optional[str]:
        """指定异常完整调用栈的起始帧（栈顶）；空栈返回 None。"""
        e = self._index[exc_id]
        if self._depth[e] == 0:
            return None
        h, o = self._resolve(e, 0)
        return self._frames[h][o]

    def read_frames(self, exc_id: str, offset: int, length: int) -> List[str]:
        """按零基偏移读取连续帧片段，结果与展开后序列切片一致。"""
        e = self._index[exc_id]
        d = self._depth[e]
        if length <= 0 or offset < 0 or offset >= d:
            return []
        if offset + length > d:
            length = d - offset
        e, o = self._resolve(e, offset)
        frames = self._frames
        flen = self._flen
        cont_exc = self._cont_exc
        cont_off = self._cont_off
        out: List[str] = []
        while length > 0:
            take = flen[e] - o
            if take > length:
                take = length
            out.extend(frames[e][o : o + take])
            length -= take
            if length > 0:
                nxt = cont_exc[e]
                if nxt == -1:
                    break
                o = cont_off[e]
                e = nxt
        return out
