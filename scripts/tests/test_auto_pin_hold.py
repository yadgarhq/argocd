"""The `auto-pin-hold` writers (ledger 1632 C6, ADR-1015): `auto_pin.py` `hold-epoch`, `hold-version` and `hold-settled`.

No network, no git remote. GitHub is a fake that answers only the paths a test
gives it (anything else is HTTP 404) and records every write.

TWO KINDS OF HOLD SHARE THE LABEL, each keyed by a hidden marker in the issue
body (never the title alone):

  - EPOCH (plan section 5): main's settled epoch K is red, or past close with
    no verdict. Written by `auto-pin.yaml` after the gate (the guarantee) and
    by `settled-dispatch.yaml` (the fast path). Red cases: `result: red` → one
    issue; done with no result → one issue; green → none; an open issue for K
    already → none; the same run twice → still one issue; epoch before close
    → no issue and no PR; red verdict → one issue and no PR.
  - VERSION (ADR-1015): auto-pin computed NEEDS-HUMAN for target V. It opens
    NO PR; it ensures one issue naming V and the reasons, updated in place,
    and closes any open automatic PR for V the bot still owns. A hold a human
    closed for V is the clear: V is never held or opened again automatically.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "scripts"))

if "auto_pin" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("auto_pin", REPOSITORY / "scripts" / "auto_pin.py")
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["auto_pin"] = _module
    _spec.loader.exec_module(_module)
ap = sys.modules["auto_pin"]

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
MAIN = "a" * 40
PUSHED = "c" * 40
FOREIGN = "d" * 40
R = "/repos/yadgarhq/argocd"
OPEN_HOLDS = f"{R}/issues?labels=auto-pin-hold&state=open"
ALL_HOLDS = f"{R}/issues?labels=auto-pin-hold&state=all"
CLOSED_HOLDS = f"{R}/issues?labels=auto-pin-hold&state=closed"
EPOCH = {"key": "k" * 64, "anchor": "f" * 40, "deadline": "2026-10-24T11:00:00Z"}


class Fake:
    """A GitHub API: `routes[(METHOD, path)]` is a value or a callable of the body; anything else is HTTP 404."""

    def __init__(self, routes: dict | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: list[tuple[str, str, object]] = []

    def _answer(self, method: str, path: str, body: object = None):
        self.calls.append((method, path, body))
        if (method, path) not in self.routes:
            raise ap.ApiError(404, method, path)
        answer = self.routes[(method, path)]
        return answer(body) if callable(answer) else answer

    def get(self, path: str):
        return self._answer("GET", path)

    def post(self, path: str, body: object = None):
        return self._answer("POST", path, body)

    def patch(self, path: str, body: object = None):
        return self._answer("PATCH", path, body)

    def delete(self, path: str):
        return self._answer("DELETE", path)

    def paginate(self, path: str) -> list:
        return self._answer("GET", path)

    def writes(self) -> list[tuple[str, str, object]]:
        return [call for call in self.calls if call[0] != "GET"]


class Issues:
    """A stateful issue tracker behind the fake: a create is listed on the next read."""

    def __init__(self, existing: list[dict] | None = None) -> None:
        self.items: list[dict] = list(existing or [])

    def create(self, body: dict) -> dict:
        number = 900 + len(self.items)
        self.items.append({"number": number, "state": "open", "title": body["title"], "body": body["body"], "labels": [{"name": n} for n in body["labels"]]})
        return {"number": number}

    def edit(self, number: int):
        def apply(body: dict) -> dict:
            item = next(i for i in self.items if i["number"] == number)
            item.update(body)
            return item

        return apply

    def routes(self) -> dict:
        routes = {
            ("GET", ALL_HOLDS): lambda _b: list(self.items),
            ("GET", OPEN_HOLDS): lambda _b: [i for i in self.items if i["state"] == "open"],
            ("GET", CLOSED_HOLDS): lambda _b: [i for i in self.items if i["state"] == "closed"],
            ("POST", f"{R}/issues"): self.create,
        }
        for item in self.items:
            routes[("PATCH", f"{R}/issues/{item['number']}")] = self.edit(item["number"])
        for number in range(900, 910):
            routes.setdefault(("PATCH", f"{R}/issues/{number}"), self.edit(number))
        return routes


def red_epoch(kind: str = "red") -> ap.EpochState:
    found = {"verdict": {"result": "red"}, "run_id": 77} if kind == "red" else None
    moment = NOW if kind == "red" else dt.datetime(2026, 10, 24, 11, 30, tzinfo=UTC)
    return ap.epoch_state(EPOCH, found, moment)


# ── the epoch hold ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["red", "past_close"])
def test_a_red_or_past_close_epoch_ensures_one_hold_issue(kind: str) -> None:
    issues = Issues()
    api = Fake(issues.routes())
    number = ap.ensure_epoch_hold(api, red_epoch(kind), merge_sha=MAIN)
    posts = [w for w in api.writes() if w[0] == "POST"]
    assert number == 900 and len(posts) == 1
    body = posts[0][2]
    assert body["labels"] == ["auto-pin-hold"]
    assert body["title"] == f"auto-pin hold: epoch {'k' * 12} red"
    assert f"kind=epoch key={'k' * 64}" in body["body"] and MAIN in body["body"]
    assert ("run 77" in body["body"]) if kind == "red" else ("no verdict by close" in body["body"])


def test_the_same_epoch_run_twice_is_still_one_issue() -> None:
    issues = Issues()
    api = Fake(issues.routes())
    ap.ensure_epoch_hold(api, red_epoch(), merge_sha=MAIN)
    api = Fake(issues.routes())
    assert ap.ensure_epoch_hold(api, red_epoch(), merge_sha=MAIN) == 900
    assert [w for w in api.writes() if w[0] == "POST"] == []
    assert len(issues.items) == 1


def test_a_closed_hold_for_a_still_red_epoch_is_not_the_open_one() -> None:
    """Plan section 5: ensure one OPEN hold for a red K. A hold closed while K is still red is not it: one new hold opens, once."""
    closed = {"number": 5, "state": "closed", "title": f"auto-pin hold: epoch {'k' * 12} red", "body": ap.hold_marker("epoch", "k" * 64) + "\n\nold"}
    issues = Issues([closed])
    api = Fake(issues.routes())
    assert ap.ensure_epoch_hold(api, red_epoch(), merge_sha=MAIN) == 901
    assert [w[0] for w in api.writes()] == ["POST"], "the closed hold is neither reopened nor edited"
    api = Fake(issues.routes())
    assert ap.ensure_epoch_hold(api, red_epoch(), merge_sha=MAIN) == 901
    assert len(issues.items) == 2


def test_an_epoch_that_is_green_or_pending_writes_no_hold() -> None:
    api = Fake(Issues().routes())
    for state in (ap.epoch_state(EPOCH, {"verdict": {"result": "green"}, "run_id": 5}, NOW), ap.epoch_state(EPOCH, None, dt.datetime(2026, 10, 24, 11, 10, tzinfo=UTC))):
        assert ap.ensure_epoch_hold(api, state, merge_sha=MAIN) is None
    assert api.writes() == []


def test_an_epoch_hold_for_another_key_is_not_this_one() -> None:
    other = {"number": 5, "state": "open", "title": "auto-pin hold: epoch x red", "body": "<!-- auto-pin-hold kind=epoch key=" + "x" * 64 + " -->"}
    issues = Issues([other])
    api = Fake(issues.routes())
    assert ap.ensure_epoch_hold(api, red_epoch(), merge_sha=MAIN) == 901
    assert len(issues.items) == 2


def test_a_title_alone_is_not_the_key() -> None:
    """A hand-made issue with the bot's title but no marker is not the bot's hold for K."""
    lookalike = {"number": 5, "state": "open", "title": f"auto-pin hold: epoch {'k' * 12} red", "body": "made by hand"}
    issues = Issues([lookalike])
    api = Fake(issues.routes())
    assert ap.ensure_epoch_hold(api, red_epoch(), merge_sha=MAIN) == 901


def test_the_gate_reports_an_epoch_hold_and_opens_nothing(tmp_path: Path) -> None:
    for name in ap.PIN_FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("spec:\n  source:\n    targetRevision: 0.34.8\n" if name == ap.APPLICATION else f"{name}\n")
    api = Fake({("GET", f"{R}/branches/main"): {"commit": {"sha": MAIN}}, ("GET", OPEN_HOLDS): [], ("GET", f"{R}/pulls?state=all&sort=created&direction=desc"): []})
    reads = ap.GateReads(epoch=lambda sha: red_epoch(), tags=lambda: ["v0.34.11"], resolves=lambda v: True, now=lambda: NOW, sleep=lambda s: None)
    out = ap.run_gate(api, tmp_path, reads, regenerate=None, checkout=lambda _t: MAIN)
    assert out["action"] == "none" and out["hold"] == "epoch"
    assert out["epoch"]["key"] == "k" * 64 and out["epoch"]["kind"] == "red"
    assert api.writes() == []


def test_the_gate_reports_no_hold_for_a_pending_epoch(tmp_path: Path) -> None:
    for name in ap.PIN_FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text("spec:\n  source:\n    targetRevision: 0.34.8\n" if name == ap.APPLICATION else f"{name}\n")
    pending = ap.epoch_state(EPOCH, None, dt.datetime(2026, 10, 24, 11, 10, tzinfo=UTC))
    api = Fake({("GET", f"{R}/branches/main"): {"commit": {"sha": MAIN}}, ("GET", OPEN_HOLDS): [], ("GET", f"{R}/pulls?state=all&sort=created&direction=desc"): []})
    reads = ap.GateReads(epoch=lambda sha: pending, tags=lambda: ["v0.34.11"], resolves=lambda v: True, now=lambda: NOW, sleep=lambda s: None)
    out = ap.run_gate(api, tmp_path, reads, regenerate=None, checkout=lambda _t: MAIN)
    assert out["action"] == "none" and out.get("hold") is None


def test_hold_epoch_reads_the_state_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    issues = Issues()
    api = Fake(issues.routes())
    monkeypatch.setattr(ap, "Api", lambda token: api)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"action": "none", "reason": "red", "hold": "epoch", "main_sha": MAIN, "epoch": ap.asdict(red_epoch())}))
    assert ap.main(["hold-epoch", "--state", str(state)]) == 0
    assert len(issues.items) == 1 and MAIN in issues.items[0]["body"]


# ── the settled-dispatch fast path ──────────────────────────────────────────


def settled(tmp_path: Path, outcomes: dict[str, dict | None], epoch: dict | None, monkeypatch: pytest.MonkeyPatch, issues: Issues) -> tuple[int, Fake]:
    api = Fake(issues.routes())
    monkeypatch.setattr(ap, "Api", lambda token: api)
    monkeypatch.setattr(ap, "_settled_epoch", lambda tree, sha: epoch)
    paths = []
    for name, outcome in outcomes.items():
        path = tmp_path / name
        if outcome is not None:
            path.write_text(json.dumps(outcome))
        paths.append(str(path))
    code = ap.main(["hold-settled", "--repo", str(tmp_path), "--sha", MAIN, "--outcome", *paths])
    return code, api


def test_settled_red_ensures_one_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    issues = Issues()
    code, _ = settled(tmp_path, {"slice1.json": {"done": True, "result": "red", "run_id": 77, "dispatches": 1}}, EPOCH, monkeypatch, issues)
    assert code == 0 and len(issues.items) == 1 and "run 77" in issues.items[0]["body"]


def test_settled_done_with_no_result_ensures_one_issue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    issues = Issues()
    outcomes = {"slice1.json": {"done": False, "result": None, "run_id": None, "dispatches": 2}, "slice2.json": {"done": True, "result": None, "run_id": None, "dispatches": 1}}
    code, _ = settled(tmp_path, outcomes, EPOCH, monkeypatch, issues)
    assert code == 0 and len(issues.items) == 1 and "no verdict by close" in issues.items[0]["body"]


@pytest.mark.parametrize(
    "outcome",
    [
        {"done": True, "result": "green", "run_id": 5, "dispatches": 0},
        {"done": False, "result": None, "run_id": None, "dispatches": 3},
    ],
    ids=["green", "not-done"],
)
def test_settled_green_or_not_done_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: dict) -> None:
    issues = Issues()
    code, api = settled(tmp_path, {"slice1.json": outcome}, EPOCH, monkeypatch, issues)
    assert code == 0 and api.writes() == [] and issues.items == []


def test_settled_with_an_open_issue_for_k_already_writes_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    issues = Issues()
    settled(tmp_path, {"slice1.json": {"done": True, "result": "red", "run_id": 77, "dispatches": 1}}, EPOCH, monkeypatch, issues)
    code, api = settled(tmp_path, {"slice1.json": {"done": True, "result": "red", "run_id": 77, "dispatches": 1}}, EPOCH, monkeypatch, issues)
    assert code == 0 and [w for w in api.writes() if w[0] == "POST"] == [] and len(issues.items) == 1


def test_settled_with_no_outcome_file_is_an_infrastructure_failure_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """settled_dispatch.py writes no JSON on exit 3 (a 401/403, ADR-1015's missing grant): never a hold."""
    issues = Issues()
    code, api = settled(tmp_path, {"slice1.json": None}, EPOCH, monkeypatch, issues)
    assert code == 0 and api.writes() == [] and issues.items == []


def test_settled_with_no_render_key_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    issues = Issues()
    code, api = settled(tmp_path, {"slice1.json": {"done": True, "result": None, "run_id": None, "dispatches": 0}}, None, monkeypatch, issues)
    assert code == 0 and api.writes() == [] and issues.items == []


# ── the version hold: NEEDS-HUMAN opens no PR (ADR-1015) ────────────────────

REASONS = ["task-db: migration(s) 7 added; interim block (lifted by C10)", "platform moved 0.2.6 -> 0.2.7"]


def bot_pr(number: int, version: str, *, head: str = PUSHED, mark_sha: str = PUSHED) -> dict:
    marker = ap.marker_line(ap.Marker(sha=mark_sha, main=MAIN, target=version, e1="needs-human", e2="green", e2_run=7))
    return {
        "number": number,
        "state": "open",
        "merged_at": None,
        "user": {"login": "yadgarhq-bot[bot]"},
        "head": {"ref": f"auto/pin-{version}-{head[:12]}", "sha": head},
        "labels": [{"name": "auto-pin"}, {"name": "auto-pin-needs-human"}],
        "body": marker + "\n\nbody",
    }


def version_api(issues: Issues, pulls: list[dict]) -> Fake:
    routes = issues.routes()
    routes[("GET", f"{R}/pulls?state=all&sort=created&direction=desc")] = pulls
    for pull in pulls:
        n = pull["number"]
        routes[("GET", f"{R}/pulls/{n}")] = pull
        routes[("POST", f"{R}/issues/{n}/comments")] = {}
        routes[("PATCH", f"{R}/pulls/{n}")] = {}
        routes[("DELETE", f"{R}/git/refs/heads/{pull['head']['ref']}")] = None
    return Fake(routes)


def test_needs_human_ensures_one_version_hold_naming_the_target_and_reasons() -> None:
    issues = Issues()
    api = version_api(issues, [])
    number = ap.ensure_version_hold(api, target="0.34.12", pin="0.34.8", main_sha=MAIN, reasons=REASONS)
    assert number == 900 and len(issues.items) == 1
    item = issues.items[0]
    assert item["title"] == "auto-pin hold: parent chart 0.34.12 needs a human"
    assert "kind=version key=0.34.12" in item["body"]
    for reason in REASONS:
        assert reason in item["body"]
    assert "blocks every automatic pin" in item["body"]


def test_a_version_hold_is_updated_in_place_never_duplicated() -> None:
    issues = Issues()
    ap.ensure_version_hold(version_api(issues, []), target="0.34.12", pin="0.34.8", main_sha=MAIN, reasons=REASONS[:1])
    api = version_api(issues, [])
    assert ap.ensure_version_hold(api, target="0.34.12", pin="0.34.8", main_sha=MAIN, reasons=REASONS) == 900
    assert [w for w in api.writes() if w[0] == "POST"] == []
    assert [w[1] for w in api.writes() if w[0] == "PATCH"] == [f"{R}/issues/900"]
    assert len(issues.items) == 1 and REASONS[1] in issues.items[0]["body"]


def test_an_unchanged_version_hold_is_not_rewritten() -> None:
    issues = Issues()
    ap.ensure_version_hold(version_api(issues, []), target="0.34.12", pin="0.34.8", main_sha=MAIN, reasons=REASONS)
    api = version_api(issues, [])
    ap.ensure_version_hold(api, target="0.34.12", pin="0.34.8", main_sha=MAIN, reasons=REASONS)
    assert api.writes() == []


def test_a_hold_for_another_version_is_not_this_one() -> None:
    issues = Issues()
    ap.ensure_version_hold(version_api(issues, []), target="0.34.12", pin="0.34.8", main_sha=MAIN, reasons=REASONS)
    ap.ensure_version_hold(version_api(issues, []), target="0.34.13", pin="0.34.8", main_sha=MAIN, reasons=REASONS)
    assert len(issues.items) == 2


def test_hold_version_closes_the_bots_open_pr_for_the_target_and_keeps_a_humans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    issues = Issues()
    pulls = [bot_pr(61, "0.34.12"), bot_pr(62, "0.34.12", head=FOREIGN), bot_pr(60, "0.34.11")]
    api = version_api(issues, pulls)
    monkeypatch.setattr(ap, "Api", lambda token: api)
    state = tmp_path / "state.json"
    ap.save_state(state, ap.State(main_sha=MAIN, pin="0.34.8", target="0.34.12", title="t"))
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"accept": False, "reasons": REASONS, "pin": "0.34.8", "target": "0.34.12"}))
    assert ap.main(["hold-version", "--state", str(state), "--evidence", str(evidence)]) == 0
    writes = api.writes()
    # The issue first, then the PRs: no moment with the PR closed and no hold.
    assert writes[0][:2] == ("POST", f"{R}/issues")
    closed = [w[1] for w in writes if w[0] == "PATCH" and "/pulls/" in w[1]]
    assert closed == [f"{R}/pulls/61"], "only the bot-headed PR for the target closes; #62 is a human's, #60 another version"
    comment = next(w[2]["body"] for w in writes if w[1] == f"{R}/issues/61/comments")
    assert "#900" in comment and "ADR-1015" in comment
    assert ("DELETE", f"{R}/git/refs/heads/auto/pin-0.34.12-{PUSHED[:12]}", None) in writes


def test_hold_version_refuses_an_accept_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = version_api(Issues(), [])
    monkeypatch.setattr(ap, "Api", lambda token: api)
    state = tmp_path / "state.json"
    ap.save_state(state, ap.State(main_sha=MAIN, pin="0.34.8", target="0.34.12", title="t"))
    evidence = tmp_path / "evidence.json"
    evidence.write_text(json.dumps({"accept": True, "reasons": [], "pin": "0.34.8", "target": "0.34.12"}))
    assert ap.main(["hold-version", "--state", str(state), "--evidence", str(evidence)]) == 1
    assert api.writes() == []


def test_a_version_whose_hold_a_human_closed_is_not_opened_again() -> None:
    """Closing the hold is the clear: without this the cron would hold V again every 15 minutes."""
    decision = ap.decide(
        pin="0.34.8", main_sha=MAIN, holds=[], epoch=ap.EpochState(kind="green", reason="g"), prs=[], target="0.34.12",
        human_closed=frozenset(), regenerate=None, hold_cleared=frozenset({"0.34.12"}),
    )
    assert decision.action == "none" and "hold" in decision.reason


def test_hold_cleared_reads_closed_version_holds() -> None:
    closed = {"number": 5, "state": "closed", "title": "x", "body": "<!-- auto-pin-hold kind=version key=0.34.12 -->"}
    api = Fake(Issues([closed]).routes())
    assert ap.hold_cleared(api, "0.34.12") is True
    assert ap.hold_cleared(api, "0.34.13") is False


# ── the workflows ───────────────────────────────────────────────────────────

AUTO_PIN = REPOSITORY / ".github" / "workflows" / "auto-pin.yaml"
SETTLED = REPOSITORY / ".github" / "workflows" / "settled-dispatch.yaml"


def test_auto_pin_holds_with_its_own_github_token_and_skips_the_runner_on_needs_human() -> None:
    job = yaml.safe_load(AUTO_PIN.read_text())["jobs"]["pin"]
    assert job["permissions"]["issues"] == "write"
    steps = {s.get("id"): s for s in job["steps"]}
    assert "steps.gate.outputs.hold == 'epoch'" in steps["hold_epoch"]["if"]
    assert steps["hold_epoch"]["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert "steps.generate.outputs.verdict == 'needs-human'" in steps["hold_version"]["if"]
    assert steps["hold_version"]["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert "steps.generate.outputs.verdict == 'accept'" in steps["push"]["if"]


def test_the_dry_run_token_can_read_argocd_verify_main() -> None:
    """ADR-1015 grants Contents: read; a token minted with named permissions carries only those, so it must ask."""
    workflow = yaml.safe_load(AUTO_PIN.read_text())
    pin = {s.get("id"): s for s in workflow["jobs"]["pin"]["steps"]}
    evidence = {s.get("id"): s for s in workflow["jobs"]["evidence"]["steps"]}
    for step in (pin["verify2"], evidence["verify"]):
        assert step["with"]["permission-contents"] == "read"


def test_settled_dispatch_holds_only_from_its_own_outcome() -> None:
    job = yaml.safe_load(SETTLED.read_text())["jobs"]["dispatch"]
    assert job["permissions"] == {"contents": "read", "issues": "write"}
    step = next(s for s in job["steps"] if s.get("id") == "hold")
    assert "!cancelled()" in step["if"]
    assert step["env"]["GITHUB_TOKEN"] == "${{ github.token }}"
    assert "hold-settled" in step["run"]
