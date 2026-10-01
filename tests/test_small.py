"""Small reports: every summary and slice must match naive expansion."""

from __future__ import annotations

import random

import pytest

from jvmstack import ExceptionRecord, Relation, StackReport

from .naive import NaiveExpansion


def check_report(records):
    report = StackReport()
    report.load(records)
    naive = NaiveExpansion(records)
    for rec in records:
        eid = rec.exc_id
        assert report.depth(eid) == naive.depth(eid), eid
        assert report.first_frame(eid) == naive.first_frame(eid), eid
        depth = naive.depth(eid)
        probes = {0, 1, depth - 1, depth, depth + 5}
        probes.update(range(0, depth + 1, max(1, depth // 7)))
        for offset in probes:
            for length in (0, 1, 2, 3, 100):
                got = report.read_frames(eid, offset, length)
                want = naive.read_frames(eid, offset, length)
                assert got == want, (eid, offset, length, got, want)
    return report


def test_single_root_only():
    records = [ExceptionRecord("r", None, None, ("a", "b", "c"), 0)]
    check_report(records)


def test_empty_explicit_frames_and_empty_stack():
    records = [
        ExceptionRecord("root", None, None, (), 0),
        ExceptionRecord("mid", "root", Relation.CAUSE, (), 0),
        ExceptionRecord("leaf", "mid", Relation.CAUSE, ("x",), 0),
    ]
    report = check_report(records)
    assert report.depth("root") == 0
    assert report.first_frame("root") is None
    assert report.read_frames("root", 0, 10) == []
    assert report.read_frames("mid", 3, 4) == []


def test_duplicate_frames_keep_positions():
    records = [
        ExceptionRecord("root", None, None, ("f", "f", "f"), 0),
        ExceptionRecord("child", "root", Relation.CAUSE, ("f", "f"), 2),
    ]
    report = check_report(records)
    assert report.read_frames("child", 0, 10) == ["f", "f", "f", "f"]


def test_multi_level_partial_tail_inheritance():
    records = [
        ExceptionRecord("a", None, None, ("a0", "a1", "a2", "a3", "a4"), 0),
        ExceptionRecord("b", "a", Relation.CAUSE, ("b0",), 3),
        ExceptionRecord("c", "b", Relation.CAUSE, (), 2),
        ExceptionRecord("d", "c", Relation.CAUSE, ("d0", "d1"), 2),
        ExceptionRecord("e", "d", Relation.SUPPRESSED, (), 3),
    ]
    report = check_report(records)
    assert report.read_frames("e", 0, 100) == ["d1", "a3", "a4"]


def test_inheritance_from_full_parent_stack_not_explicit():
    records = [
        ExceptionRecord("a", None, None, ("a0", "a1"), 0),
        ExceptionRecord("b", "a", Relation.CAUSE, ("b0",), 2),
        ExceptionRecord("c", "b", Relation.CAUSE, ("c0",), 3),
    ]
    report = check_report(records)
    assert report.read_frames("c", 0, 100) == ["c0", "b0", "a0", "a1"]


def test_random_fuzz():
    rng = random.Random(20261002)
    for trial in range(30):
        n = rng.randint(1, 40)
        records = []
        depths = {}
        for i in range(n):
            eid = f"e{i}"
            frames = tuple(f"f{i}_{j}" for j in range(rng.randint(0, 5)))
            if not depths:
                records.append(ExceptionRecord(eid, None, None, frames, 0))
                depths[eid] = len(frames)
                continue
            parent = rng.choice(list(depths))
            relation = rng.choice([Relation.CAUSE, Relation.SUPPRESSED])
            omitted = rng.randint(0, depths[parent])
            records.append(ExceptionRecord(eid, parent, relation, frames, omitted))
            depths[eid] = len(frames) + omitted
        check_report(records)


def test_report_is_read_only_after_load():
    report = StackReport()
    report.load([ExceptionRecord("r", None, None, ("a",), 0)])
    with pytest.raises(RuntimeError):
        report.load([ExceptionRecord("r2", None, None, (), 0)])
    with pytest.raises(KeyError):
        report.depth("missing")
