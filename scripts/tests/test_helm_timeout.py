"""A hung `helm` fails the gate fast instead of hanging until the CI job limit (yaadgaar #164 item 3).

`helm()` in `scripts/gates/test_no_two_owners.py` runs every `helm pull oci://`
and `helm template` of the gates, `pin_bump.render` and `pin_dryrun`. It had no
timeout; one pin-evidence run hung past 15 minutes. `pin_bump.write_files` runs
the K3 writer, which runs helm, the same way. Each now has a bound and refuses
by name when it trips.

OFFLINE: a fake `helm` that sleeps stands in for the real one, and the bound is
patched down to a fraction of a second.
"""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
import time
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
GATES = REPOSITORY / "scripts" / "gates"

_spec = importlib.util.spec_from_file_location("pin_bump", REPOSITORY / "scripts" / "pin_bump.py")
pb = importlib.util.module_from_spec(_spec)
sys.modules["pin_bump"] = pb
_spec.loader.exec_module(pb)

TINY = 0.5
FAKE_SLEEP = 30


@pytest.fixture
def sleeping_helm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = tmp_path / "bin" / "helm"
    fake.parent.mkdir()
    fake.write_text(f"#!/bin/sh\nexec sleep {FAKE_SLEEP}\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ['PATH']}")


def gate_module():
    if str(GATES) not in sys.path:
        sys.path.insert(0, str(GATES))
    import importlib

    return importlib.import_module("test_no_two_owners")


def test_a_hung_helm_raises_within_the_bound_naming_command_and_bound(
    sleeping_helm: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = gate_module()
    monkeypatch.setattr(gate, "HELM_TIMEOUT_SECONDS", TINY)
    started = time.monotonic()
    with pytest.raises(AssertionError) as raised:
        gate.helm("pull", "oci://example.invalid/chart", "--version", "1.2.3")
    assert time.monotonic() - started < FAKE_SLEEP / 2
    message = str(raised.value)
    assert "helm pull oci://example.invalid/chart --version 1.2.3" in message
    assert str(TINY) in message


def test_a_hung_writer_is_refused_within_the_bound(
    sleeping_helm: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = tmp_path / "writer.py"
    writer.write_text(f"import time\ntime.sleep({FAKE_SLEEP})\n")
    monkeypatch.setattr(pb, "WRITER_TIMEOUT_SECONDS", TINY)
    started = time.monotonic()
    with pytest.raises(pb.Refused, match="timed out") as raised:
        pb.run_writer(writer)
    assert time.monotonic() - started < FAKE_SLEEP / 2
    assert str(TINY) in str(raised.value)
