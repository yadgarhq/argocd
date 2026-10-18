"""The pin bump leaves the two gate sources alone (ADR-0946, ledger 1632 C2).

`scripts/pin_bump.py` rewrites exactly three files. The two gates beside the
render table, `test_yadgar_application.py` and `test_no_two_owners.py`, used to
carry a per-pin "Re-measured" log that each pin PR had to extend by hand. That
log is asserted by nothing, so car C2 retires it: each pin's measurement is in
its PR body. Two arms:

- the bump leaves both gate files byte-identical, with the writer stubbed
  (the real one needs helm and the network; it writes the table alone);
- neither gate source carries a per-pin log any more, and each says where the
  measurement lives.
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("pin_bump", REPOSITORY / "scripts" / "pin_bump.py")
pb = importlib.util.module_from_spec(_spec)
sys.modules["pin_bump"] = pb
_spec.loader.exec_module(pb)

GATES = ("scripts/gates/test_yadgar_application.py", "scripts/gates/test_no_two_owners.py")
FIXTURE_PIN = "9.9.9"


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A copy of the five files a bump could touch, with the writer stubbed to what it really writes: the table."""
    for name in (*pb.MECHANICAL_FILES, *GATES):
        target = tmp_path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPOSITORY / name, target)

    class Finished:
        returncode = 0
        stderr = ""

    def writer(command, **_):
        (tmp_path / pb.TABLE).write_text(f"stub table for {FIXTURE_PIN}\n")
        return Finished()

    monkeypatch.setattr(pb, "writer_helm", lambda: "/bin/true")
    monkeypatch.setattr(pb.subprocess, "run", writer)
    return tmp_path


def test_a_bump_leaves_both_gate_files_byte_identical(tree: Path) -> None:
    before = {name: (tree / name).read_bytes() for name in (*pb.MECHANICAL_FILES, *GATES)}
    pb.write_files(tree, FIXTURE_PIN)
    for name in GATES:
        assert (tree / name).read_bytes() == before[name], name
    moved = {name for name in pb.MECHANICAL_FILES if (tree / name).read_bytes() != before[name]}
    assert moved == set(pb.MECHANICAL_FILES)


PER_PIN_LOG = re.compile(r"re-measured\s+(?:at\s+)?(?:\d{4}-\d{2}-\d{2}|\d+\.\d+\.\d+|the next bump)", re.I)


@pytest.mark.parametrize("name", GATES)
def test_a_gate_source_carries_no_per_pin_log(name: str) -> None:
    text = (REPOSITORY / name).read_text()
    assert PER_PIN_LOG.findall(text) == []


@pytest.mark.parametrize("name", GATES)
def test_a_gate_source_points_at_the_pr_body(name: str) -> None:
    text = (REPOSITORY / name).read_text()
    assert len(re.findall(r"measurement is in the pull request body", re.sub(r"\s*\n\s*(?:#\s*)?", " ", text))) == 1
