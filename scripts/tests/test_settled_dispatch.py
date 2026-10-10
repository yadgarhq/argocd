"""`scripts/settled_dispatch.py`: dispatch the settled-state gate on every argocd main merge (ledger 1435).

No network and no cluster. GitHub is a fake API object (a path it does not hold
answers HTTP 404); the clock and the sleep are injected, so a 90-minute window
runs in microseconds.

WHAT IS ASSERTED, and each has a red case below:

  1. `epoch` reads K, A and the deadline exactly as `settled_gate.derive` does,
     from git and PyYAML alone (no helm, no render).
  2. `nudge` stops as soon as a verdict for (K, A) exists, green or red, and
     dispatches nothing then. The red belongs to the gate, not to this loop.
  3. `nudge` never dispatches while any settled.yaml run is not `completed`:
     a pending human trial or rejudge sits in the gate's one concurrency
     slot, and a new dispatch would evict it.
  4. The dispatch body is exactly `{"ref": "main"}`: never `rejudge`, never
     `argocd_sha`.
  5. Before the deadline a missing verdict means "keep polling"; after it,
     dispatches go on until deadline + grace (estate's DEADLINE_MARGIN is 30
     minutes), then the loop closes with "no verdict" (exit 1).
  6. A time slice ends before the 60-minute App token does; the next slice
     picks up (exit 0, done false).
  7. No K (a refused render key): nothing to dispatch for, done at once.
  8. `GitHub.post` sends a JSON POST with an unredirected bearer token.
"""

from __future__ import annotations

import datetime as dt
import http.client
import importlib.util
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "scripts"))

_spec = importlib.util.spec_from_file_location("settled_dispatch", REPOSITORY / "scripts" / "settled_dispatch.py")
sd = importlib.util.module_from_spec(_spec)
sys.modules["settled_dispatch"] = sd
_spec.loader.exec_module(sd)
sg = sd.settled_gate

UTC = dt.timezone.utc
REPO = "yadgarhq/argocd-verify"
WORKFLOW = ".github/workflows/settled.yaml"
SELF_ID = 1001
KEY = "e" * 64
ANCHOR = "1" * 40
DEADLINE = "2026-10-10T13:05:00Z"
EPOCH = {"key": KEY, "anchor": ANCHOR, "deadline": DEADLINE}
RUNS = f"/repos/{REPO}/actions/workflows/settled.yaml/runs?per_page=20"
DISPATCH = f"/repos/{REPO}/actions/workflows/settled.yaml/dispatches"


# ── 1. epoch ─────────────────────────────────────────────────────────────────


def application(pin: str) -> str:
    source = {"repoURL": "ghcr.io/yadgarhq/charts", "chart": "yadgar", "targetRevision": pin, "helm": {"valuesObject": {"a": 1}}}
    return yaml.safe_dump({"apiVersion": "argoproj.io/v1alpha1", "kind": "Application", "spec": {"source": source}})


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.clock = dt.datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
        path.mkdir()
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, date: str | None = None) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        if date:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        command = ["git", "-C", str(self.path), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args]
        return subprocess.run(command, capture_output=True, text=True, check=True, env=env).stdout.strip()

    def commit(self, files: dict[str, str]) -> str:
        for name, content in files.items():
            (self.path / name).parent.mkdir(parents=True, exist_ok=True)
            (self.path / name).write_text(content)
        self.git("add", "-A")
        self.clock += dt.timedelta(hours=1)
        self.git("commit", "-q", "--allow-empty", "-m", "c", date=self.clock.strftime("%Y-%m-%dT%H:%M:%SZ"))
        return self.git("rev-parse", "HEAD")


def test_epoch_is_derives_k_a_and_deadline(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "argocd")
    anchor = repo.commit({sg.APPLICATION: application("0.3.38"), sg.TABLE: "targetRevision  0.3.38\n"})
    head = repo.commit({"README.md": "unrelated\n"})
    derived = sg.derive(repo.path, head, render=lambda _application: [])
    epoch = sd.epoch(repo.path, head)
    assert epoch == {"key": derived["key"], "anchor": derived["anchor"], "deadline": derived["deadline"]}
    assert epoch["anchor"] == anchor


def test_epoch_moves_with_a_render_change(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "argocd")
    repo.commit({sg.APPLICATION: application("0.3.38"), sg.TABLE: "targetRevision  0.3.38\n"})
    bump = repo.commit({sg.APPLICATION: application("0.3.39"), sg.TABLE: "targetRevision  0.3.39\n"})
    assert sd.epoch(repo.path, bump)["anchor"] == bump


def test_epoch_of_a_refused_key_is_none(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "argocd")
    head = repo.commit({sg.APPLICATION: "spec:\n  source: [a]\n", sg.TABLE: "x\n"})
    assert sd.epoch(repo.path, head) is None


# ── fakes ────────────────────────────────────────────────────────────────────


def verdict_zip(result: str, key: str = KEY, anchor: str = ANCHOR) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("verdict.json", json.dumps({"K": key, "A": anchor, "result": result, "clause": "SECRET-CLAUSE"}))
    return buffer.getvalue()


class FakeGitHub:
    """GitHub as settled.yaml would answer it. `on_dispatch` lets a test make the gate answer after N dispatches."""

    def __init__(self, *, runs: list[dict] | None = None, verdict_after: int | None = None, result: str = "green", epoch: dict = EPOCH) -> None:
        self.epoch = epoch
        self.runs = runs or []
        self.verdict_after = verdict_after
        self.result = result
        self.dispatched: list[dict] = []
        self.requested: list[str] = []

    def _artifacts(self) -> list[dict]:
        if self.verdict_after is None or len(self.dispatched) < self.verdict_after:
            return []
        run = {"id": 77, "repository_id": SELF_ID, "head_repository_id": SELF_ID, "head_branch": "main"}
        return [{"id": 7, "name": "settled-verdict", "expired": False, "created_at": "2026-10-10T12:10:00Z", "archive_download_url": "https://zip/7", "workflow_run": run}]

    def get(self, path: str):
        self.requested.append(path)
        if path == f"/repos/{REPO}":
            return {"id": SELF_ID, "default_branch": "main"}
        if path == RUNS:
            return {"workflow_runs": self.runs}
        if path == f"/repos/{REPO}/actions/runs/77":
            return {"id": 77, "path": WORKFLOW, "event": "workflow_dispatch", "head_repository": {"id": SELF_ID}}
        raise sg.GitHubError(404, path)

    def paginate(self, path: str, key: str | None = None) -> list:
        self.requested.append(path)
        if path == f"/repos/{REPO}/actions/artifacts?name=settled-verdict":
            return self._artifacts()
        raise sg.GitHubError(404, path)

    def download(self, url: str) -> bytes:
        return verdict_zip(self.result, self.epoch["key"], self.epoch["anchor"])

    def post(self, path: str, body: dict) -> None:
        assert path == DISPATCH, path
        self.dispatched.append(body)


class Clock:
    def __init__(self, start: dt.datetime) -> None:
        self.at = start
        self.slept: list[float] = []

    def now(self) -> dt.datetime:
        return self.at

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.slept.append(seconds)
        self.at += dt.timedelta(seconds=seconds)


def run(github: FakeGitHub, clock: Clock, *, slice_seconds: int = 3300, epoch: dict = EPOCH) -> dict:
    return sd.nudge(github, epoch, now=clock.now, sleep=clock.sleep, slice_seconds=slice_seconds, interval=240, grace=1200)


AFTER_MERGE = dt.datetime(2026, 10, 10, 12, 6, tzinfo=UTC)  # one minute after A's commit (deadline - 3900 s + 60 s)


# ── 2. stop at a verdict ─────────────────────────────────────────────────────


@pytest.mark.parametrize("result", ["green", "red"])
def test_an_existing_verdict_ends_the_loop_with_no_dispatch(result: str) -> None:
    github = FakeGitHub(verdict_after=0, result=result)
    outcome = run(github, Clock(AFTER_MERGE))
    assert outcome == {"done": True, "result": result, "run_id": 77, "dispatches": 0}
    assert github.dispatched == []


def test_the_loop_dispatches_until_the_gate_answers() -> None:
    github = FakeGitHub(verdict_after=3)
    clock = Clock(AFTER_MERGE)
    outcome = run(github, clock)
    assert outcome["done"] is True and outcome["result"] == "green" and outcome["dispatches"] == 3
    assert clock.slept == [240, 240, 240]


# ── 3. never evict a pending run ─────────────────────────────────────────────


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "pending", "requested"])
def test_no_dispatch_while_a_settled_run_is_not_completed(status: str) -> None:
    github = FakeGitHub(runs=[{"id": 5, "status": status}, {"id": 4, "status": "completed"}])
    run(github, Clock(AFTER_MERGE), slice_seconds=600)
    assert github.dispatched == []


def test_completed_runs_do_not_block_a_dispatch() -> None:
    github = FakeGitHub(runs=[{"id": 4, "status": "completed"}], verdict_after=1)
    assert run(github, Clock(AFTER_MERGE))["dispatches"] == 1


# ── 4. the dispatch body ─────────────────────────────────────────────────────


def test_the_dispatch_body_is_main_and_nothing_else() -> None:
    github = FakeGitHub(verdict_after=2)
    run(github, Clock(AFTER_MERGE))
    assert github.dispatched == [{"ref": "main"}, {"ref": "main"}]


# ── 5. the window ────────────────────────────────────────────────────────────


def test_after_the_deadline_dispatches_go_on_until_grace_then_close() -> None:
    github = FakeGitHub()
    clock = Clock(dt.datetime(2026, 10, 10, 13, 0, tzinfo=UTC))  # five minutes before the deadline
    outcome = run(github, clock)
    assert outcome["done"] is True and outcome["result"] is None
    assert clock.at == dt.datetime(2026, 10, 10, 13, 25, tzinfo=UTC)  # deadline + 1200 s exactly
    assert outcome["dispatches"] == 7  # 13:00, :04, :08, :12, :16, :20, :24 — none at the close itself


def test_a_window_already_closed_dispatches_nothing() -> None:
    github = FakeGitHub()
    outcome = run(github, Clock(dt.datetime(2026, 10, 10, 13, 30, tzinfo=UTC)))
    assert outcome == {"done": True, "result": None, "run_id": None, "dispatches": 0}
    assert github.dispatched == []


# ── 6. the time slice ────────────────────────────────────────────────────────


def test_a_slice_ends_before_the_token_and_hands_on() -> None:
    github = FakeGitHub()
    clock = Clock(AFTER_MERGE)
    outcome = run(github, clock, slice_seconds=1000)
    assert outcome["done"] is False
    assert clock.at == AFTER_MERGE + dt.timedelta(seconds=1000)


# ── 7. no K ──────────────────────────────────────────────────────────────────


def test_cli_with_no_key_is_done_without_any_api_call(tmp_path: Path) -> None:
    repo = Repo(tmp_path / "argocd")
    head = repo.commit({sg.APPLICATION: "spec:\n  source: [a]\n", sg.TABLE: "x\n"})
    github = FakeGitHub()
    out = tmp_path / "out.json"
    assert sd.main(["nudge", "--repo", str(repo.path), "--sha", head, "--out", str(out)], github=github) == 0
    assert json.loads(out.read_text())["done"] is True
    assert github.requested == [] and github.dispatched == []


# ── CLI exit codes and public output ─────────────────────────────────────────


def cli(tmp_path: Path, github: FakeGitHub, start: dt.datetime, capsys) -> tuple[int, dict, str]:
    repo = Repo(tmp_path / "argocd")
    head = repo.commit({sg.APPLICATION: application("0.3.38"), sg.TABLE: "targetRevision  0.3.38\n"})
    github.epoch = sd.epoch(repo.path, head)
    clock = Clock(start)
    out = tmp_path / "out.json"
    argv = ["nudge", "--repo", str(repo.path), "--sha", head, "--out", str(out), "--slice-seconds", "3300"]
    code = sd.main(argv, github=github, now=clock.now, sleep=clock.sleep)
    return code, json.loads(out.read_text()), capsys.readouterr().out


def test_cli_found_exits_0_and_never_prints_the_clause(tmp_path: Path, capsys) -> None:
    code, written, printed = cli(tmp_path, FakeGitHub(verdict_after=0, result="red"), dt.datetime(2026, 10, 10, 10, 1, tzinfo=UTC), capsys)
    assert code == 0 and written["result"] == "red"
    assert "SECRET-CLAUSE" not in printed and "SECRET-CLAUSE" not in json.dumps(written)


def test_cli_closed_without_a_verdict_exits_1(tmp_path: Path, capsys) -> None:
    code, written, _ = cli(tmp_path, FakeGitHub(), dt.datetime(2026, 10, 10, 12, 0, tzinfo=UTC), capsys)
    assert code == 1 and written["done"] is True and written["result"] is None


def test_cli_api_failure_exits_3(tmp_path: Path, capsys) -> None:
    class Broken(FakeGitHub):
        def paginate(self, path: str, key: str | None = None) -> list:
            raise sg.GitHubError(403, path)

    repo = Repo(tmp_path / "argocd")
    head = repo.commit({sg.APPLICATION: application("0.3.38"), sg.TABLE: "targetRevision  0.3.38\n"})
    out = tmp_path / "out.json"
    clock = Clock(dt.datetime(2026, 10, 10, 10, 1, tzinfo=UTC))
    assert sd.main(["nudge", "--repo", str(repo.path), "--sha", head, "--out", str(out)], github=Broken(), now=clock.now, sleep=clock.sleep) == 3


# ── 8. GitHub.post ───────────────────────────────────────────────────────────


def test_post_sends_json_with_an_unredirected_token(monkeypatch) -> None:
    seen = {}

    class Response:
        status = 204

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return b""

    def urlopen(request, timeout):
        seen.update(method=request.get_method(), url=request.full_url, body=json.loads(request.data), auth=request.unredirected_hdrs.get("Authorization"), redirected=request.headers.get("Authorization"))
        return Response()

    monkeypatch.setattr(sg.urllib.request, "urlopen", urlopen)
    sg.GitHub(token="t0k").post(DISPATCH, {"ref": "main"})
    assert seen == {"method": "POST", "url": sg.GITHUB_API + DISPATCH, "body": {"ref": "main"}, "auth": "Bearer t0k", "redirected": None}


def test_post_http_error_is_a_github_error_naming_the_method(monkeypatch) -> None:
    def urlopen(request, timeout):
        raise sg.urllib.error.HTTPError(request.full_url, 403, "no", {}, None)

    monkeypatch.setattr(sg.urllib.request, "urlopen", urlopen)
    with pytest.raises(sg.GitHubError, match=r"^POST .*: HTTP 403"):
        sg.GitHub(token="t").post(DISPATCH, {"ref": "main"})


# ── 9. transient GitHub failures ─────────────────────────────────────────────


class Flaky(FakeGitHub):
    """The first `fail` artifact listings answer `error`; later ones answer as FakeGitHub does."""

    def __init__(self, error: Exception, fail: int = 1, **kwargs) -> None:
        super().__init__(**kwargs)
        self.error = error
        self.fail = fail

    def paginate(self, path: str, key: str | None = None) -> list:
        if self.fail > 0:
            self.fail -= 1
            raise self.error
        return super().paginate(path, key)


@pytest.mark.parametrize(
    "error",
    [sg.GitHubError(502, "/x"), sg.GitHubError(429, "/x"), sg.GitHubUnavailable("GET /x: connection reset")],
    ids=["5xx", "429", "network"],
)
def test_a_transient_failure_is_retried_on_the_next_pass(error: Exception) -> None:
    github = Flaky(error, verdict_after=0)
    clock = Clock(AFTER_MERGE)
    outcome = run(github, clock)
    assert outcome["done"] is True and outcome["result"] == "green"
    assert clock.slept == [240]


def test_transient_failures_still_end_at_the_close() -> None:
    github = Flaky(sg.GitHubError(503, "/x"), fail=10**6)
    clock = Clock(dt.datetime(2026, 10, 10, 13, 0, tzinfo=UTC))
    outcome = run(github, clock)
    assert outcome == {"done": True, "result": None, "run_id": None, "dispatches": 0}
    assert clock.at == dt.datetime(2026, 10, 10, 13, 25, tzinfo=UTC)


@pytest.mark.parametrize("status", [401, 403, 404])
def test_a_credential_or_permission_failure_is_not_retried(status: int) -> None:
    class Refused(FakeGitHub):
        def post(self, path: str, body: dict) -> None:
            raise sg.GitHubError(status, path, "POST")

    clock = Clock(AFTER_MERGE)
    with pytest.raises(sg.GitHubError):
        run(Refused(), clock)
    assert clock.slept == []


def test_an_unreadable_verdict_is_not_retried() -> None:
    class Garbled(FakeGitHub):
        def download(self, url: str) -> bytes:
            return b"not a zip"

    with pytest.raises(sg.InfrastructureError):
        run(Garbled(verdict_after=0), Clock(AFTER_MERGE))


@pytest.mark.parametrize("raised", [ConnectionResetError("reset"), http.client.IncompleteRead(b"x"), http.client.RemoteDisconnected("gone")])
def test_a_dropped_connection_is_github_unavailable(monkeypatch, raised: Exception) -> None:
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            raise raised

    monkeypatch.setattr(sg.urllib.request, "urlopen", lambda request, timeout: Response())
    with pytest.raises(sg.GitHubUnavailable):
        sg.GitHub(token="t").get("/repos/x")
    monkeypatch.setattr(sg.urllib.request, "urlopen", lambda request, timeout: (_ for _ in ()).throw(raised))
    with pytest.raises(sg.GitHubUnavailable):
        sg.GitHub(token="t").post(DISPATCH, {"ref": "main"})
