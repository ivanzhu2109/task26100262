"""Suppressed siblings with their own causes must not share tail frames."""

from __future__ import annotations

from jvmstack import ExceptionRecord, Relation, StackReport

from .naive import NaiveExpansion


def build_records():
    return [
        ExceptionRecord("root", None, None, ("r0", "r1", "r2", "r3"), 0),
        ExceptionRecord("rootcause", "root", Relation.CAUSE, ("rc0", "rc1"), 3),
        ExceptionRecord("sup1", "root", Relation.SUPPRESSED, ("s1a", "s1b"), 2),
        ExceptionRecord("sup2", "root", Relation.SUPPRESSED, ("s2a",), 4),
        ExceptionRecord("sup1cause", "sup1", Relation.CAUSE, ("c1a",), 3),
        ExceptionRecord("sup2cause", "sup2", Relation.CAUSE, (), 2),
        ExceptionRecord("sup1nested", "sup1cause", Relation.SUPPRESSED, ("n1",), 4),
    ]


def test_suppressed_siblings_isolated():
    records = build_records()
    report = StackReport()
    report.load(records)
    naive = NaiveExpansion(records)

    assert report.read_frames("sup1", 0, 100) == ["s1a", "s1b", "r2", "r3"]
    assert report.read_frames("sup2", 0, 100) == ["s2a", "r0", "r1", "r2", "r3"]
    assert report.read_frames("sup1cause", 0, 100) == ["c1a", "s1b", "r2", "r3"]
    assert report.read_frames("sup2cause", 0, 100) == ["r2", "r3"]
    assert report.read_frames("sup1nested", 0, 100) == ["n1", "c1a", "s1b", "r2", "r3"]

    assert "s2a" not in report.read_frames("sup1", 0, 100)
    assert "s1a" not in report.read_frames("sup2", 0, 100)
    assert "c1a" not in report.read_frames("sup2cause", 0, 100)

    for rec in records:
        eid = rec.exc_id
        assert report.depth(eid) == naive.depth(eid), eid
        assert report.first_frame(eid) == naive.first_frame(eid), eid
        for offset in range(0, naive.depth(eid) + 2):
            for length in (1, 2, 100):
                assert report.read_frames(eid, offset, length) == naive.read_frames(
                    eid, offset, length
                ), (eid, offset, length)
