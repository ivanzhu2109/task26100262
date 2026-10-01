"""Reproducible scale test: deep cause chain, suppressed fan-out, partial
tail inheritance; logical expansion exceeds 1e9 frames.

Layout (fixed shapes, no randomness in structure):
  c{i}   : cause chain, i = 0..CHAIN-1, one explicit frame "c{i}#0",
           omitted = parent full depth -> full stack is [c{i}#0 .. c{0}#0]
  s{i}   : suppressed child of c{p_i}, three explicit frames "s{i}#0..2",
           omitted = min(i % 5, p_i + 1) -> inherits the parent's LAST
           omitted frames, i.e. [c{m-1}#0 .. c{0}#0]
"""

from __future__ import annotations

import os
import resource
import time

from jvmstack import ExceptionRecord, Relation, StackReport

CHAIN = 50_000
SUPP = 50_000
READS = 20_000
MAX_READ = 100

LOAD_BUDGET_S = 10.0
READ_BUDGET_S = 5.0
RSS_BUDGET_MIB = 384


def parent_of_supp(i: int) -> int:
    return (i * 7) % CHAIN


def omitted_of_supp(i: int) -> int:
    return min(i % 5, parent_of_supp(i) + 1)


def chain_depth(i: int) -> int:
    return i + 1


def supp_depth(i: int) -> int:
    return 3 + omitted_of_supp(i)


def expected_frame(exc_index: int, is_supp: bool, offset: int) -> str:
    if not is_supp:
        return f"c{exc_index - offset}#0"
    if offset < 3:
        return f"s{exc_index}#{offset}"
    m = omitted_of_supp(exc_index)
    return f"c{m - 1 - (offset - 3)}#0"


def build_records():
    records = []
    records.append(ExceptionRecord("c0", None, None, ("c0#0",), 0))
    for i in range(1, CHAIN):
        records.append(
            ExceptionRecord(f"c{i}", f"c{i-1}", Relation.CAUSE, (f"c{i}#0",), i)
        )
    for i in range(SUPP):
        p = parent_of_supp(i)
        records.append(
            ExceptionRecord(
                f"s{i}",
                f"c{p}",
                Relation.SUPPRESSED,
                (f"s{i}#0", f"s{i}#1", f"s{i}#2"),
                omitted_of_supp(i),
            )
        )
    return records


def peak_rss_mib() -> float:
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if os.uname().sysname == "Darwin":
        return rss / (1 << 20)
    return rss / (1 << 10)


def test_scale_load_and_reads():
    records = build_records()
    total_explicit = sum(len(r.frames) for r in records)
    assert len(records) == CHAIN + SUPP == 100_000
    assert total_explicit == CHAIN + 3 * SUPP == 200_000
    logical_total = sum(range(1, CHAIN + 1)) + sum(
        supp_depth(i) for i in range(SUPP)
    )
    assert logical_total > 1_000_000_000

    report = StackReport()
    t0 = time.perf_counter()
    report.load(records)
    load_s = time.perf_counter() - t0

    for i in (0, 1, CHAIN // 2, CHAIN - 1):
        assert report.depth(f"c{i}") == chain_depth(i)
        assert report.first_frame(f"c{i}") == f"c{i}#0"
    for i in (0, 1, SUPP // 2, SUPP - 1):
        assert report.depth(f"s{i}") == supp_depth(i)
        assert report.first_frame(f"s{i}") == f"s{i}#0"

    state = 20261002
    queries = []
    for _ in range(READS):
        state = (state * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        idx = state % (CHAIN + SUPP)
        state = (state * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        if idx < CHAIN:
            eid, is_supp, ei = f"c{idx}", False, idx
            depth = chain_depth(idx)
        else:
            ei = idx - CHAIN
            eid, is_supp = f"s{ei}", True
            depth = supp_depth(ei)
        offset = state % (depth + 10)
        state = (state * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        length = 1 + state % MAX_READ
        queries.append((eid, is_supp, ei, offset, length))

    t0 = time.perf_counter()
    checked_frames = 0
    for eid, is_supp, ei, offset, length in queries:
        got = report.read_frames(eid, offset, length)
        depth = chain_depth(ei) if not is_supp else supp_depth(ei)
        want_len = max(0, min(length, depth - offset))
        assert len(got) == want_len, (eid, offset, length)
        for j, frame in enumerate(got):
            assert frame == expected_frame(ei, is_supp, offset + j), (
                eid,
                offset,
                j,
            )
        checked_frames += len(got)
    read_s = time.perf_counter() - t0

    rss_mib = peak_rss_mib()
    print(
        f"\n[scale] exceptions={len(records)} explicit_frames={total_explicit} "
        f"logical_frames={logical_total:,}"
    )
    print(f"[scale] load={load_s:.2f}s (budget {LOAD_BUDGET_S}s)")
    print(
        f"[scale] reads={READS} frames_checked={checked_frames:,} "
        f"read_time={read_s:.2f}s (budget {READ_BUDGET_S}s)"
    )
    print(f"[scale] peak_rss={rss_mib:.1f} MiB (budget {RSS_BUDGET_MIB} MiB)")

    assert load_s < LOAD_BUDGET_S
    assert read_s < READ_BUDGET_S
    assert rss_mib < RSS_BUDGET_MIB
