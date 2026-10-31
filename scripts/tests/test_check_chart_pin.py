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


def test_main_needs_no_request_beyond_the_chart_and_its_digest(script, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    calls = []

    def fetch(repo: str, path: str, ref: str | None = None) -> str:
        calls.append(repo)
        return chart_yaml(script.committed_pin()["platform_version"])

    def resolve(version: str) -> str:
        calls.append(f"ghcr.io:{version}")
        return script.committed_pin()["chart_digest"]

    monkeypatch.setattr(script, "fetch_contents", fetch)
    monkeypatch.setattr(script, "resolve_chart_digest", resolve)
    assert script.main() == 0
    assert calls == ["yadgarhq/chart", f"ghcr.io:{script.committed_pin()['chart_tag'].removeprefix('v')}"]
    assert "applications/yadgar.yaml" in capsys.readouterr().out


# ── the digest half (yaadgaar 250) ─────────────────────────────────────────

RECORDED = "sha256:" + "a" * 64
RETAGGED = "sha256:" + "b" * 64


def test_the_digest_half_is_green_when_the_tag_still_resolves_to_the_recorded_digest(
    script, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = []
    monkeypatch.setattr(script, "resolve_chart_digest", lambda version: seen.append(version) or RECORDED)
    assert script.check_chart_digest("v0.49.27", RECORDED) is None
    assert seen == ["0.49.27"]


def test_a_retagged_chart_reddens_naming_both_digests(script, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(script, "resolve_chart_digest", lambda version: RETAGGED)
    error = script.check_chart_digest("v0.49.27", RECORDED)
    assert error is not None and RECORDED in error and RETAGGED in error and "0.49.27" in error


def test_a_pin_with_no_recorded_digest_reddens_with_the_migration(script, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(script, "resolve_chart_digest", lambda version: RECORDED)
    for missing in (None, ""):
        error = script.check_chart_digest("v0.49.27", missing)
        assert error is not None and "`chart_digest`" in error and RECORDED in error


def test_an_unreachable_registry_reddens(script, monkeypatch: pytest.MonkeyPatch) -> None:
    def resolve(version: str) -> str:
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(script, "resolve_chart_digest", resolve)
    error = script.check_chart_digest("v0.49.27", RECORDED)
    assert error is not None and "URLError" in error


def test_main_refuses_a_retag(script, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(script, "fetch_contents", lambda *a, **k: chart_yaml(script.committed_pin()["platform_version"]))
    monkeypatch.setattr(script, "resolve_chart_digest", lambda version: RETAGGED)
    assert script.main() == 1
    assert RETAGGED in capsys.readouterr().out


def test_the_resolver_is_pin_bumps_registry_client(script) -> None:
    """One anonymous GHCR client: `pin_bump.Net.ghcr_digest`, the one that resolves module digests for E1."""
    assert script.CHART_PACKAGE == "charts/yadgar"
    assert script.pin_bump.Net.ghcr_digest is not None
