"""`scripts/runner_image_pinned.py` with a second, third-party scale set (ledger 1219).

`applications/post-merge-verifier-runner.yaml` runs GitHub's stock runner image,
`ghcr.io/actions/actions-runner`, pinned by digest. The gate must still hold it
to a digest (a tag is a moving image in a pod with cluster read), but
`--verify-signature` must not ask cosign whether `yadgarhq/actions` signed it:
nobody here signs it, so that question is red on every pull request. The
exemption is that one repository: any other third-party scale-set image is
still asked, and still fails.

Offline: cosign, the registry and the subprocess are faked.

Run: python3 -m pytest scripts/tests/test_runner_image_pinned.py -q
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]

# `scripts/` is not a package, so the module is loaded by path.
_spec = importlib.util.spec_from_file_location("runner_image_pinned", REPOSITORY / "scripts" / "runner_image_pinned.py")
rip = importlib.util.module_from_spec(_spec)
sys.modules["runner_image_pinned"] = rip
_spec.loader.exec_module(rip)

FIRST = "ghcr.io/yadgarhq/estate-runner@sha256:" + "a" * 64
THIRD = "ghcr.io/actions/actions-runner@sha256:" + "b" * 64


@pytest.fixture
def in_repository(monkeypatch: pytest.MonkeyPatch) -> None:
    """The gate reads `applications/` relative to the working directory."""
    monkeypatch.chdir(REPOSITORY)


@pytest.fixture
def cosign_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Fake cosign that verifies nothing, and a reachable registry; records each call."""
    calls: list[list[str]] = []

    def fake_run(cmd, **_kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no matching signatures")

    monkeypatch.setattr(rip.shutil, "which", lambda _name: "/usr/bin/cosign")
    monkeypatch.setattr(rip, "registry_reachable", lambda: None)
    monkeypatch.setattr(rip.subprocess, "run", fake_run)
    return calls


def test_both_scale_sets_are_read(in_repository: None) -> None:
    references, problems = rip.collect()
    assert problems == []
    scale_set_images = sorted(value for _path, _line, value, in_scale_set in references if in_scale_set)
    assert [v.split("@")[0] for v in scale_set_images] == ["ghcr.io/actions/actions-runner", "ghcr.io/yadgarhq/estate-runner"]
    assert rip.unpinned(references) == []


def test_cosign_is_not_asked_about_githubs_runner_image(cosign_calls: list[list[str]]) -> None:
    references = [(Path("a.yaml"), 1, FIRST, True), (Path("b.yaml"), 2, THIRD, True)]
    problems = rip.verify_signatures(references)
    assert [call[-1] for call in cosign_calls] == [FIRST]
    assert len(problems) == 1 and FIRST in problems[0]


def test_any_other_third_party_scale_set_image_is_still_asked_and_reddens(cosign_calls: list[list[str]]) -> None:
    """Only GitHub's own runner image is exempt; any other publisher still needs our CI's signature."""
    other = "docker.io/someone/runner@sha256:" + "c" * 64
    problems = rip.verify_signatures([(Path("c.yaml"), 3, other, True)])
    assert [call[-1] for call in cosign_calls] == [other]
    assert len(problems) == 1 and other in problems[0]


def test_a_third_party_scale_set_image_on_a_tag_still_reddens() -> None:
    references = [(Path("b.yaml"), 2, "ghcr.io/actions/actions-runner:latest", True)]
    problems = rip.unpinned(references)
    assert len(problems) == 1 and "ghcr.io/actions/actions-runner:latest" in problems[0]


def test_the_success_line_does_not_call_a_third_party_image_first_party(in_repository: None) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/runner_image_pinned.py"],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPOSITORY,
        env={**os.environ},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    first, third = [line for line in result.stdout.splitlines() if line.strip()]
    assert first.startswith("1 first-party image reference(s)") and "ghcr.io/actions/" not in first
    assert third.startswith("1 upstream runner image reference(s)") and "not signature-checked" in third
    assert "ghcr.io/actions/actions-runner@sha256:" in third
