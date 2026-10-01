"""`scripts/check_chart_pin.py`'s two halves, offline (ledger 1206).

THE CHART-TAG HALF READS A FILE IN THIS REPOSITORY NOW. Until `infra` retired
it fetched `yadgarhq/deploy`'s `infra/yadgar-app.yaml` over the contents API.
The `yadgar` Application moved here as `applications/yadgar.yaml` (option A,
ADR-0828), so that half reads the local file and needs no request at all. It
is tested against the real file and against a copy with the pin moved.

THE PLATFORM-VERSION HALF STILL NEEDS THE NETWORK, so its request is replaced
here by a stub that returns a `Chart.yaml`. What is tested is the comparison,
not GitHub. The real request runs in the `chart-pin` CI job.
"""

from __future__ import annotations

import importlib.util
import shutil
import urllib.error
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPT = REPOSITORY / "scripts" / "check_chart_pin.py"


def load_script():
    spec = importlib.util.spec_from_file_location("check_chart_pin", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script():
    return load_script()


@pytest.fixture
def application(tmp_path: Path) -> Path:
    """A writable copy of `applications/yadgar.yaml`."""
    path = tmp_path / "yadgar.yaml"
    shutil.copyfile(REPOSITORY / "applications" / "yadgar.yaml", path)
    return path


def forbid_network(monkeypatch: pytest.MonkeyPatch, script) -> None:
    def refuse(*_args, **_kwargs):
        raise AssertionError("the chart-tag half made a request")

    monkeypatch.setattr(script, "fetch_contents", refuse)


def test_the_running_application_is_this_repositorys_own_file(script) -> None:
    assert script.APPLICATION == REPOSITORY / "applications" / "yadgar.yaml"
    assert not hasattr(script, "DEPLOY_REPO")


def test_the_chart_tag_half_is_green_on_the_repository(script, monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_network(monkeypatch, script)
    pin = script.committed_pin()
    assert script.running_target_revision() == pin["chart_tag"].removeprefix("v")
    assert script.check_chart_tag(pin["chart_tag"]) is None


def test_a_stale_chart_tag_reddens(script, monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_network(monkeypatch, script)
    error = script.check_chart_tag("v0.3.7")
    assert error is not None
    assert "`chart_tag` is `v0.3.7`" in error and "applications/yadgar.yaml" in error


def test_a_moved_pin_reddens(script, application: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_network(monkeypatch, script)
    pin = script.committed_pin()["chart_tag"]
    text = application.read_text()
    line = f"    targetRevision: {pin.removeprefix('v')}\n"
    assert text.count(line) == 1
    application.write_text(text.replace(line, "    targetRevision: 0.3.15\n"))
    error = script.check_chart_tag(pin, application)
    assert error is not None and "targetRevision: 0.3.15" in error


def test_a_missing_application_reddens(script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_network(monkeypatch, script)
    error = script.check_chart_tag("v0.3.13", tmp_path / "absent.yaml")
    assert error is not None and "could not read" in error and "FileNotFoundError" in error


def test_an_application_with_no_chart_source_reddens(script, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    forbid_network(monkeypatch, script)
    path = tmp_path / "yadgar.yaml"
    path.write_text("spec:\n  source:\n    repoURL: https://github.com/yadgarhq/argocd\n    path: x\n")
    error = script.check_chart_tag("v0.3.13", path)
    assert error is not None and "no chart source" in error


def test_the_multi_source_form_is_still_read_by_its_chart_entry(script, tmp_path: Path) -> None:
    """Not this repository's shape, but a `ref:` entry listed first must not be read as the chart."""
    path = tmp_path / "yadgar.yaml"
    path.write_text(
        "spec:\n  sources:\n    - repoURL: https://github.com/x/y\n      ref: self\n"
        "    - repoURL: ghcr.io/yadgarhq/charts\n      chart: yadgar\n      targetRevision: 0.3.13\n"
    )
    assert script.running_target_revision(path) == "0.3.13"


def chart_yaml(version: str) -> str:
    return f"apiVersion: v2\nname: yadgar\ndependencies:\n  - name: platform\n    version: {version}\n"


def test_the_platform_version_half_compares(script, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    def fetch(repo: str, path: str, ref: str | None = None) -> str:
        seen.append((repo, path, ref))
        return chart_yaml("0.1.21")

    monkeypatch.setattr(script, "fetch_contents", fetch)
    assert script.check_platform_version("v0.3.13", "0.1.21") is None
    assert seen == [("yadgarhq/chart", "chart/Chart.yaml", "v0.3.13")]
    error = script.check_platform_version("v0.3.13", "0.1.20")
    assert error is not None and "`0.1.21`" in error


def test_an_unreachable_chart_repository_reddens(script, monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(script, "fetch_contents", fetch)
    error = script.check_platform_version("v0.3.13", "0.1.21")
    assert error is not None and "URLError" in error


def test_main_needs_no_request_beyond_the_chart(script, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    calls = []

    def fetch(repo: str, path: str, ref: str | None = None) -> str:
        calls.append(repo)
        return chart_yaml(script.committed_pin()["platform_version"])

    monkeypatch.setattr(script, "fetch_contents", fetch)
    assert script.main() == 0
    assert calls == ["yadgarhq/chart"]
    assert "applications/yadgar.yaml" in capsys.readouterr().out
