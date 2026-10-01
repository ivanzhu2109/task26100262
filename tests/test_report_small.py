"""小规模正确性测试：与独立逐层展开的参照实现对比。"""
from __future__ import annotations

import random

import pytest

from jvmstack import ExceptionRecord, Relation, StackReport


def expand_all(records):
    """参照实现：按定义逐层展开每个异常的完整栈。"""
    by_id = {}
    full = {}
    for rec in records:
        by_id[rec.exception_id] = rec
        inherited = []
        if rec.parent_id is not None and rec.omitted > 0:
            parent_full = full[rec.parent_id]
            inherited = parent_full[len(parent_full) - rec.omitted :]
        full[rec.exception_id] = list(rec.frames) + inherited
    return full


def check_against_reference(report, records):
    full = expand_all(records)
    for rec in records:
        eid = rec.exception_id
        expected = full[eid]
        assert report.depth(eid) == len(expected), eid
        expected_start = expected[0] if expected else None
        assert report.start_frame(eid) == expected_start, eid
        # 全量切片对比（小规模下逐点扫描所有偏移与长度组合）
        d = len(expected)
        for offset in range(d + 2):  # 含 offset == d 与超出栈尾
            for length in (0, 1, 2, 3, d + 2):
                got = report.read_frames(eid, offset, length)
                assert got == expected[offset : offset + length], (
                    eid,
                    offset,
                    length,
                )


def test_single_root_only():
    records = [
        ExceptionRecord("root", None, None, ["a", "b", "c"], 0),
    ]
    check_against_reference(StackReport.load(records), records)


def test_root_with_empty_frames():
    records = [ExceptionRecord("root", None, None, [], 0)]
    report = StackReport.load(records)
    assert report.depth("root") == 0
    assert report.start_frame("root") is None
    assert report.read_frames("root", 0, 10) == []
    check_against_reference(report, records)


def test_cause_chain_partial_tail_inheritance():
    # 连续多层只继承部分尾帧
    records = [
        ExceptionRecord("e0", None, None, ["f0", "f1", "f2", "f3", "f4"], 0),
        ExceptionRecord("e1", "e0", Relation.CAUSE, ["g0", "g1"], 3),
        ExceptionRecord("e2", "e1", Relation.CAUSE, ["h0"], 4),
        ExceptionRecord("e3", "e2", Relation.CAUSE, [], 2),
        ExceptionRecord("e4", "e3", Relation.CAUSE, ["x0", "x1", "x2"], 1),
    ]
    check_against_reference(StackReport.load(records), records)


def test_empty_explicit_frames_in_chain():
    records = [
        ExceptionRecord("e0", None, None, ["a", "b"], 0),
        ExceptionRecord("e1", "e0", Relation.CAUSE, [], 2),
        ExceptionRecord("e2", "e1", Relation.CAUSE, [], 2),
        ExceptionRecord("e3", "e2", Relation.CAUSE, ["c"], 1),
    ]
    check_against_reference(StackReport.load(records), records)


def test_duplicate_frames_keep_positions():
    records = [
        ExceptionRecord("e0", None, None, ["dup", "dup", "dup"], 0),
        ExceptionRecord("e1", "e0", Relation.CAUSE, ["dup", "dup"], 2),
        ExceptionRecord("e2", "e1", Relation.CAUSE, ["dup"], 3),
    ]
    report = StackReport.load(records)
    check_against_reference(report, records)
    # 内容相同的帧仍占各自位置
    assert report.depth("e2") == 4
    assert report.read_frames("e2", 0, 100) == ["dup", "dup", "dup", "dup"]


def test_suppressed_siblings_with_own_causes():
    # 一个父异常拥有多个 suppressed 子异常，子异常又各有 cause；
    # 兄弟异常不得误用彼此的尾帧。
    records = [
        ExceptionRecord("root", None, None, ["r0", "r1", "r2", "r3"], 0),
        ExceptionRecord("s1", "root", Relation.SUPPRESSED, ["s1a"], 2),
        ExceptionRecord("s2", "root", Relation.SUPPRESSED, ["s2a", "s2b"], 3),
        ExceptionRecord("s1c", "s1", Relation.CAUSE, ["s1c0"], 2),
        ExceptionRecord("s2c", "s2", Relation.CAUSE, ["s2c0"], 1),
        ExceptionRecord("s2cc", "s2c", Relation.CAUSE, [], 2),
    ]
    report = StackReport.load(records)
    check_against_reference(report, records)
    # s1 的完整栈: [s1a, r2, r3]；s1c 继承 s1 的最后 2 帧
    assert report.read_frames("s1c", 0, 100) == ["s1c0", "r2", "r3"]
    # s2 的完整栈: [s2a, s2b, r1, r2, r3]；s2c 继承最后 1 帧
    assert report.read_frames("s2c", 0, 100) == ["s2c0", "r3"]
    # s2cc 继承 s2c 的最后 2 帧
    assert report.read_frames("s2cc", 0, 100) == ["s2c0", "r3"]


def test_inheritance_uses_parent_full_stack_not_explicit():
    # 继承的是父异常完整栈（含父自己继承的帧）的最后 N 帧
    records = [
        ExceptionRecord("e0", None, None, ["a0", "a1", "a2"], 0),
        ExceptionRecord("e1", "e0", Relation.CAUSE, ["b0"], 2),  # [b0,a1,a2]
        ExceptionRecord("e2", "e1", Relation.CAUSE, ["c0"], 2),  # [c0,a1,a2]
    ]
    report = StackReport.load(records)
    check_against_reference(report, records)
    assert report.read_frames("e2", 0, 100) == ["c0", "a1", "a2"]


def test_full_inheritance_n_equals_parent_depth():
    records = [
        ExceptionRecord("e0", None, None, ["a", "b", "c"], 0),
        ExceptionRecord("e1", "e0", Relation.CAUSE, ["x"], 3),
        ExceptionRecord("e2", "e1", Relation.SUPPRESSED, [], 4),
    ]
    report = StackReport.load(records)
    check_against_reference(report, records)
    assert report.read_frames("e2", 0, 100) == ["x", "a", "b", "c"]
    assert report.start_frame("e2") == "x"


def test_slice_truncation_and_out_of_range():
    records = [
        ExceptionRecord("e0", None, None, ["a", "b"], 0),
        ExceptionRecord("e1", "e0", Relation.CAUSE, ["c"], 1),
    ]
    report = StackReport.load(records)
    assert report.read_frames("e1", 1, 100) == ["b"]  # 截到栈尾
    assert report.read_frames("e1", 2, 5) == []  # 偏移 == 深度
    assert report.read_frames("e1", 99, 5) == []  # 偏移超出栈尾
    assert report.read_frames("e1", 0, 0) == []  # 零长度
    with pytest.raises(ValueError):
        report.read_frames("e1", -1, 1)


def test_unknown_exception_id():
    report = StackReport.load([ExceptionRecord("e0", None, None, ["a"], 0)])
    with pytest.raises(KeyError):
        report.depth("nope")
    with pytest.raises(KeyError):
        report.start_frame("nope")
    with pytest.raises(KeyError):
        report.read_frames("nope", 0, 1)


def test_randomized_reports_against_reference():
    rng = random.Random(20261002)
    for trial in range(30):
        n = rng.randint(1, 40)
        records = []
        for i in range(n):
            eid = f"e{i}"
            if i == 0:
                frames = [f"f{i}-{j}" for j in range(rng.randint(0, 5))]
                records.append(ExceptionRecord(eid, None, None, frames, 0))
                continue
            parent_idx = rng.randrange(i)
            parent_rec = records[parent_idx]
            parent_depth = len(parent_rec.frames) + parent_rec.omitted
            omitted = rng.randint(0, parent_depth)
            relation = rng.choice([Relation.CAUSE, Relation.SUPPRESSED])
            frames = [f"f{i}-{j}" for j in range(rng.randint(0, 5))]
            # 随机注入重复帧
            if frames and rng.random() < 0.3:
                frames = [frames[0]] * len(frames)
            records.append(
                ExceptionRecord(eid, parent_rec.exception_id, relation,
                                frames, omitted)
            )
        check_against_reference(StackReport.load(records), records)


def test_invalid_input_rejected():
    with pytest.raises(ValueError):
        StackReport.load([])  # 空报告
    with pytest.raises(ValueError):
        StackReport.load([ExceptionRecord("a", None, None, [], 0),
                          ExceptionRecord("b", None, None, [], 0)])  # 双根
    with pytest.raises(ValueError):
        StackReport.load([ExceptionRecord("a", None, None, [], 0),
                          ExceptionRecord("a", None, None, [], 0)])  # 重复 id
    with pytest.raises(ValueError):
        StackReport.load([ExceptionRecord("a", "missing", Relation.CAUSE,
                                          [], 0)])  # 未知父异常
    with pytest.raises(ValueError):
        StackReport.load([ExceptionRecord("a", None, None, ["x"], 0),
                          ExceptionRecord("b", "a", Relation.CAUSE, [], 5)])
    # ^ N 超过父异常深度
