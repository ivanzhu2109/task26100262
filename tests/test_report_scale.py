"""可复现的规模测试：深因果链 + suppressed 分叉 + 部分尾帧继承。

规模：10 万个异常、20 万条显式帧、5 万层因果链，
逻辑展开后的总帧数超过 10 亿。
记录装载耗时、2 万次片段读取耗时与峰值内存，
并用独立的慢速参照解析器核对抽取片段的准确内容。

运行：python3 -m pytest tests/test_report_scale.py -q -s
"""
from __future__ import annotations

import random
import resource
import time

from jvmstack import ExceptionRecord, Relation, StackReport

import pytest

pytestmark = pytest.mark.scale

CHAIN = 50_000          # 深因果链长度
EXTRA = 50_000          # 分叉部分异常数
TOTAL = CHAIN + EXTRA   # 10 万异常
FRAMES_PER_EX = 2       # 累计 20 万条显式帧
READS = 20_000
MAX_READ_LEN = 100


def build_records():
    records = []
    # 第一段：5 万层 cause 深链，每层 2 条显式帧并完整继承父栈。
    records.append(ExceptionRecord("c0", None, None, ["c0-f0", "c0-f1"], 0))
    for i in range(1, CHAIN):
        parent = f"c{i - 1}"
        parent_depth = 2 * i
        records.append(
            ExceptionRecord(
                f"c{i}", parent, Relation.CAUSE,
                [f"c{i}-f0", f"c{i}-f1"], parent_depth,
            )
        )
    # 第二段：挂在链中不同节点上的 suppressed 分叉（一个父可挂多个），
    # 每个 suppressed 异常再带一个 cause 子异常，N 取部分尾帧。
    for j in range(EXTRA // 2):
        anchor = f"c{(j * 997) % CHAIN}"
        anchor_depth = 2 * ((j * 997) % CHAIN + 1)
        sid = f"s{j}"
        omitted_s = (j * 13) % (anchor_depth + 1)  # 部分尾帧继承
        records.append(
            ExceptionRecord(
                sid, anchor, Relation.SUPPRESSED,
                [f"s{j}-f0", f"s{j}-f1"], omitted_s,
            )
        )
        s_depth = 2 + omitted_s
        omitted_c = (j * 7) % (s_depth + 1)
        records.append(
            ExceptionRecord(
                f"sc{j}", sid, Relation.CAUSE,
                [f"sc{j}-f0", f"sc{j}-f1"], omitted_c,
            )
        )
    return records


def reference_resolver(records):
    """独立的慢速参照：按定义逐层向上爬（迭代，不用倍增表）。"""
    parent = {}
    omitted = {}
    frames = {}
    depth = {}
    for rec in records:
        frames[rec.exception_id] = list(rec.frames)
        omitted[rec.exception_id] = rec.omitted
        parent[rec.exception_id] = rec.parent_id
        depth[rec.exception_id] = len(rec.frames) + rec.omitted

    def frame_at(eid, i):
        while i >= len(frames[eid]):
            p = parent[eid]
            i = depth[p] - omitted[eid] + (i - len(frames[eid]))
            eid = p
        return frames[eid][i]

    return frame_at, depth


def rss_bytes():
    # macOS 上 ru_maxrss 单位为字节（Linux 为 KiB）
    import sys

    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return raw if sys.platform == "darwin" else raw * 1024


def test_scale_performance_and_accuracy():
    records = build_records()
    assert len(records) == TOTAL
    assert sum(len(r.frames) for r in records) == 200_000

    frame_at, depth_map = reference_resolver(records)
    logical_total = sum(depth_map.values())
    assert logical_total > 1_000_000_000  # 逻辑展开总量超过 10 亿

    rss_before = rss_bytes()
    t0 = time.perf_counter()
    report = StackReport.load(records)
    load_s = time.perf_counter() - t0
    rss_after_load = rss_bytes()

    # 2 万次分散读取：不同异常、不同偏移（含栈中、栈尾），每次最多 100 帧
    rng = random.Random(42)
    ids = [r.exception_id for r in records]
    depths = [depth_map[eid] for eid in ids]
    queries = []
    for _ in range(READS):
        k = rng.randrange(TOTAL)
        d = depths[k]
        offset = rng.randrange(d + 1)  # 含 offset == depth（应返回空）
        length = rng.randint(0, MAX_READ_LEN)
        queries.append((ids[k], offset, length))

    t0 = time.perf_counter()
    checksum = 0
    for eid, offset, length in queries:
        checksum += len(report.read_frames(eid, offset, length))
    read_s = time.perf_counter() - t0
    rss_peak = rss_bytes()

    print(f"\n[scale] exceptions={TOTAL} explicit_frames=200000 "
          f"chain_depth={CHAIN}")
    print(f"[scale] logical_total_frames={logical_total:,}")
    print(f"[scale] load_time={load_s:.3f}s (budget 10s)")
    print(f"[scale] read_time({READS} reads)={read_s:.3f}s (budget 5s) "
          f"frames_returned={checksum:,}")
    print(f"[scale] peak_rss={rss_peak / 2**20:.1f} MiB "
          f"(delta during load ~{(rss_after_load - rss_before) / 2**20:.1f} MiB, "
          f"budget 384 MiB)")

    assert load_s < 10.0
    assert read_s < 5.0
    assert rss_peak < 384 * 2**20

    # 抽查片段内容：与独立参照解析器逐帧比对（覆盖链尾、分叉、空切片）
    sample = rng.sample(queries, 300)
    sample += [
        (f"c{CHAIN - 1}", 0, MAX_READ_LEN),                 # 最深异常栈顶
        (f"c{CHAIN - 1}", 2 * CHAIN - MAX_READ_LEN, MAX_READ_LEN),  # 栈尾
        (f"c{CHAIN - 1}", 2 * CHAIN, 10),                   # 偏移 == 深度
        ("c0", 0, 10),                                      # 截到栈尾
        ("s0", 0, MAX_READ_LEN),
        ("sc0", 0, MAX_READ_LEN),
    ]
    for eid, offset, length in sample:
        got = report.read_frames(eid, offset, length)
        d = depth_map[eid]
        expected = [
            frame_at(eid, i)
            for i in range(offset, min(offset + length, d))
        ]
        assert got == expected, (eid, offset, length)
    # 深度与起始帧抽查
    for eid, _, _ in sample:
        assert report.depth(eid) == depth_map[eid]
        expected_start = frame_at(eid, 0) if depth_map[eid] else None
        assert report.start_frame(eid) == expected_start
