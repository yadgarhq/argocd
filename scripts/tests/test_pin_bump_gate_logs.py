"""The pin bump leaves the two gate sources alone (ADR-0946, ledger 1632 C2).

`scripts/pin_bump.py` rewrites exactly three files. The two gates beside the
render table, `test_yadgar_application.py` and `test_no_two_owners.py`, used to
carry a per-pin "Re-measured" log that each pin PR had to extend by hand. That
log is asserted by nothing, so car C2 retires it: each pin's measurement is in
its PR body. Two arms:

- the bump leaves both gate files byte-identical, with the writer stubbed
  (the real one needs helm and the network). The stub pins the exact command
  and `pb.WRITER`, and an AST check holds that the real writer file writes
  `TABLE` and nothing else, so the stub stands for what the writer does;
- neither gate source carries a per-pin log any more; the render gate says
  where a measurement lives, and the other gate says what it still diffs against.
"""

from __future__ import annotations

import ast
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
        assert command == [sys.executable, str(tmp_path / pb.WRITER), "--write"], command
        (tmp_path / pb.TABLE).write_text(f"stub table for {FIXTURE_PIN}\n")
        return Finished()

    monkeypatch.setattr(pb, "writer_helm", lambda: "/bin/true")
    monkeypatch.setattr(pb.subprocess, "run", writer)
    return tmp_path


def test_a_bump_leaves_both_gate_files_byte_identical(tree: Path) -> None:
    assert pb.WRITER == GATES[0]
    before = {name: (tree / name).read_bytes() for name in (*pb.MECHANICAL_FILES, *GATES)}
    pb.write_files(tree, FIXTURE_PIN)
    for name in GATES:
        assert (tree / name).read_bytes() == before[name], name
    moved = {name for name in pb.MECHANICAL_FILES if (tree / name).read_bytes() != before[name]}
    assert moved == set(pb.MECHANICAL_FILES)


WRITE_ATTRIBUTES = {"write_text", "write_bytes", "write", "touch", "unlink", "rename", "mkdir", "rmdir", "symlink_to"}


def file_writes(source: str) -> list[str]:
    """Every call in `source` that could write, move or delete a file, as the text of its receiver."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if isinstance(function, ast.Name) and function.id == "open":
            found.append("open()")
        elif isinstance(function, ast.Attribute) and (
            function.attr in WRITE_ATTRIBUTES
            or function.attr == "open"
            or ast.unparse(function.value) in {"os", "shutil"}
        ):
            found.append(ast.unparse(function.value))
    return found


def test_the_writer_writes_the_table_and_nothing_else() -> None:
    writes = file_writes((REPOSITORY / pb.WRITER).read_text())
    assert writes == ["TABLE"], writes


@pytest.mark.parametrize(
    "line",
    [
        'GATE.write_text("x")',
        'open(path, "a")',
        'Path("scripts/gates/test_no_two_owners.py").write_text("x")',
        'shutil.copyfile(a, b)',
        'os.remove(path)',
    ],
)
def test_the_write_scan_names_a_write_to_any_other_file(line: str) -> None:
    assert file_writes(line) != ["TABLE"]


def flat(text: str) -> str:
    """Comment and docstring line breaks folded away, so a wrapped sentence matches as one."""
    return re.sub(r"\s*\n\s*(?:#\s*)?", " ", text)


DATE = r"\d{4}-\d{2}-\d{2}"
VERSION = r"\d+\.\d+\.\d+"
PER_PIN_LOG = re.compile(
    rf"re-?measured\s+(?:on\s+|at\s+)?(?:{DATE}|{VERSION}|the\s+next\s+bump)"
    rf"|\bmeasured\s+{DATE}\s+at\s+{VERSION}\s+against"
    rf"|{VERSION}\s*\([^)]*\),?\s+against\s+the\s+same\s+{VERSION}:\s*\d+\s*(?:->|\u2192)\s*\d+\s+objects",
    re.I,
)

LOG_SHAPES = [
    "Re-measured 2026-10-03 at 0.3.38 (ledger 1266): still 88 objects",
    "# RE-MEASURED AT THE NEXT BUMP, 0.3.13 -> 0.3.38 (ledger 1266)",
    "# RE-MEASURED AT 0.23.10 (platform 0.2.3, ledger 1437), against the same 0.19.1:",
    "Re-measured on 2026-10-11 at 0.23.8, carrying gateway 0.12.2",
    "Remeasured at 0.24.9, carrying seven modules",
    "Measured 2026-10-16 at 0.24.9 against the previous pin",
    "# 0.25.14 (gateway 0.12.11), against the same 0.19.1: 82 -> 86 objects and 30 changes",
    "# 0.25.27 (gateway 0.12.13, iam 0.11.10,\n# iam-db 0.10.8), against the same 0.19.1: 82 \u2192 86 objects",
    "Re-measured\n      2026-10-18 at 0.25.9",
]


@pytest.mark.parametrize("shape", LOG_SHAPES)
def test_the_log_scan_names_every_old_shape(shape: str) -> None:
    assert PER_PIN_LOG.search(flat(shape)), shape


@pytest.mark.parametrize("name", GATES)
def test_a_gate_source_carries_no_per_pin_log(name: str) -> None:
    assert PER_PIN_LOG.findall(flat((REPOSITORY / name).read_text())) == []


def test_the_render_gate_points_at_the_pr_body() -> None:
    text = flat((REPOSITORY / GATES[0]).read_text())
    assert len(re.findall(r"Each later pin bump's or values change's measurement is in the pull request body that made it", text)) == 1


def test_the_ownership_gate_states_what_it_still_diffs_against_and_not_the_pr_body() -> None:
    text = flat((REPOSITORY / GATES[1]).read_text())
    assert "`PREVIOUS_PIN` stays 0.19.1; every later pin is still diffed against it, not against the pin before (task 1507)." in text
    assert "measurement is in the pull request body" not in text
