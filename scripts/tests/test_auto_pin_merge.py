"""`scripts/auto_pin_merge.py`: the bot merge of an automatic pin PR (ledger 1632 C6; plan section 4).

No network, no git remote. GitHub is a fake that answers only the paths a test
gives it (anything else is HTTP 404) and records every write. The E2 run read
on argocd-verify is a second fake. Each test starts from one fully green PR
(`world()`) and breaks exactly one thing.

THE PLAN'S SECTION 4 RED CASES, each a test below: two `ci / passed` (success
+ failure) → no merge; one `in_progress` → no merge; none → no merge; one
success but `pin / kind-dryrun` pending → no merge; an open hold issue → no
merge; the run's `head_sha` behind the PR head → no merge; NEEDS-HUMAN → no
merge (by the marker, and by the `auto-pin-needs-human` label: a label can
stop a merge, never permit one); two commits → no merge;
a commit sha different from the status description's sha → no merge; base
`behind` → no merge, regenerate dispatched, update-branch never called.

AND YAAGAAR #420: a forged status is refused, because the merge reads the
argocd-verify run the status names with the dispatch App's token and requires
that App as its actor. YAAGAAR #422: a `github-actions[bot]` status (any
workflow's GITHUB_TOKEN) is refused outright; the statuses are the settled-
dispatch App's, `yadgarhq-settled-dispatch[bot]`.
"""

from __future__ import annotations

import copy
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "scripts"))
for _name in ("auto_pin", "pin_dryrun", "auto_pin_merge"):
    if _name not in sys.modules:
        _spec = importlib.util.spec_from_file_location(_name, REPOSITORY / "scripts" / f"{_name}.py")
        _module = importlib.util.module_from_spec(_spec)
        sys.modules[_name] = _module
        _spec.loader.exec_module(_module)
ap = sys.modules["auto_pin"]
pd = sys.modules["pin_dryrun"]
am = sys.modules["auto_pin_merge"]

MAIN = "a" * 40
NEW_MAIN = "b" * 40
HEAD = "c" * 40
OTHER = "d" * 40
RUN = 4242
R = "/repos/yadgarhq/argocd"
V = "/repos/yadgarhq/argocd-verify"
MECHANICAL = ("applications/yadgar.yaml", "scripts/chart_pin.json", "scripts/gates/yadgar_render.sha256")
GREEN = ap.EpochState(kind="green", reason="green verdict, run 5")
CHECKS = f"{R}/commits/{HEAD}/check-runs?check_name=ci%20%2F%20passed&filter=all&per_page=100"
STATUSES = f"{R}/commits/{HEAD}/statuses"
IN_PROGRESS = f"{R}/actions/workflows/auto-pin.yaml/runs?status=in_progress"
QUEUED = f"{R}/actions/workflows/auto-pin.yaml/runs?status=queued"
DISPATCH = f"{R}/actions/workflows/auto-pin.yaml/dispatches"


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

    def put(self, path: str, body: object = None):
        return self._answer("PUT", path, body)

    def delete(self, path: str):
        return self._answer("DELETE", path)

    def paginate(self, path: str) -> list:
        return self._answer("GET", path)

    def download(self, url: str) -> bytes:
        return self._answer("GET", url)

    def writes(self) -> list[tuple[str, str, object]]:
        return [call for call in self.calls if call[0] != "GET"]


def body(sha: str = HEAD, main: str = MAIN, target: str = "0.34.11", e1: str = "accept", e2: str = "green", run: int | None = RUN) -> str:
    return ap.marker_line(ap.Marker(sha=sha, main=main, target=target, e1=e1, e2=e2, e2_run=run)) + "\n\n## Summary\n\nthe pin\n"


def status(check: str, *, sha: str = HEAD, main: str = MAIN, result: str | None = None, run: int | None = RUN, state: str = "success", creator: str = "yadgarhq-settled-dispatch[bot]") -> dict:
    context = "pin / e1" if check == "e1" else "pin / kind-dryrun"
    word = result or ("accept" if check == "e1" else "green")
    return {
        "context": context,
        "state": state,
        "creator": {"login": creator},
        "description": ap.status_description(check, word, sha=sha, main=main, run_id=run if check == "e2" else None),
    }


def pull(**overrides) -> dict:
    value = {
        "number": 61,
        "state": "open",
        "user": {"login": "yadgarhq-bot[bot]"},
        "base": {"ref": "main", "repo": {"full_name": "yadgarhq/argocd"}},
        "head": {"ref": f"auto/pin-0.34.11-{HEAD[:12]}", "sha": HEAD, "repo": {"full_name": "yadgarhq/argocd"}},
        "body": body(),
        "labels": [{"name": "auto-pin"}],
        "mergeable_state": "clean",
        "merged_at": None,
        "created_at": "2026-10-24T10:00:00Z",
    }
    value.update(overrides)
    return value


def world(overrides: dict | None = None) -> dict:
    """The routes of ONE fully green automatic PR #61. A test replaces one entry."""
    pr = pull()
    routes = {
        ("GET", f"{R}/issues?labels=auto-pin-hold&state=open"): [],
        ("GET", f"{R}/pulls?state=all&sort=created&direction=desc"): [pr],
        ("GET", f"{R}/pulls/61"): pr,
        ("GET", f"{R}/pulls/61/commits"): [
            {"sha": HEAD, "author": {"login": "yadgarhq-bot[bot]"}, "commit": {"verification": {"verified": True}}, "parents": [{"sha": MAIN}]}
        ],
        ("GET", f"{R}/pulls/61/files"): [{"filename": name, "status": "modified"} for name in MECHANICAL],
        ("GET", f"{R}/branches/main"): {"commit": {"sha": MAIN}},
        ("GET", CHECKS): {"check_runs": [{"name": "ci / passed", "status": "completed", "conclusion": "success", "app": {"id": 15368}}]},
        ("GET", STATUSES): [status("e2"), status("e1")],
        ("GET", IN_PROGRESS): {"workflow_runs": []},
        ("GET", QUEUED): {"workflow_runs": []},
        ("POST", DISPATCH): None,
        ("PUT", f"{R}/pulls/61/merge"): {"merged": True, "sha": "f" * 40},
    }
    routes.update(overrides or {})
    return routes


def reads(*, epoch: ap.EpochState = GREEN, e2: str | None = None, seen: list | None = None) -> am.MergeReads:
    def e2_run(run_id: int, sha: str, main: str) -> str | None:
        (seen if seen is not None else []).append((run_id, sha, main))
        return e2

    return am.MergeReads(epoch=lambda sha: epoch, e2_run=e2_run, mechanical=MECHANICAL, sleep=lambda s: None)


def run(routes: dict, **kwargs) -> tuple[am.Verdict, Fake]:
    api = Fake(routes)
    read = kwargs.pop("reads", None) or reads()
    verdict = am.judge_pr(api, 61, read, **kwargs)
    return verdict, api


def merged(api: Fake) -> bool:
    return any(call[0] == "PUT" for call in api.calls)


# ── green ───────────────────────────────────────────────────────────────────


def test_a_fully_green_pr_merges_squash_at_its_head_sha() -> None:
    api = Fake(world())
    seen: list = []
    verdict = am.judge_pr(api, 61, reads(seen=seen))
    assert verdict.action == "merge", verdict.reason
    assert seen == [(RUN, HEAD, MAIN)], "the E2 run is read through the API before any merge"
    am.act(api, api, verdict, regenerations_left=True)
    puts = [call for call in api.calls if call[0] == "PUT"]
    assert puts == [("PUT", f"{R}/pulls/61/merge", {"merge_method": "squash", "sha": HEAD})]


def test_a_label_never_permits_a_merge() -> None:
    """The `auto-pin` label alone is no reason to merge: a forged marker or status beside it is still refused."""
    verdict, api = run(world({("GET", STATUSES): [status("e2", creator="maxagahi"), status("e1")]}))
    assert verdict.action == "refuse", verdict.reason
    assert not merged(api)


@pytest.mark.parametrize("check", ["e1", "e2"])
def test_a_github_actions_status_is_refused(check: str) -> None:
    """YAAGAAR #422: auto-pin posts both statuses as yadgarhq-settled-dispatch[bot]. A `github-actions[bot]` one came from
    some workflow's GITHUB_TOKEN, which any argocd workflow holds: it is refused, even when it reads green."""
    other = "e1" if check == "e2" else "e2"
    verdict, api = run(world({("GET", STATUSES): [status(check, creator="github-actions[bot]"), status(other)]}))
    assert verdict.action == "refuse", verdict.reason
    assert "github-actions[bot]" in verdict.reason and "yadgarhq-settled-dispatch[bot]" in verdict.reason
    assert not merged(api)


@pytest.mark.parametrize("check", ["e1", "e2"])
def test_a_status_by_the_pr_authoring_release_app_is_refused(check: str) -> None:
    """The release App authors the PR (PIN_LOGIN) but never posts its evidence: STATUS_LOGIN is another App."""
    other = "e1" if check == "e2" else "e2"
    verdict, api = run(world({("GET", STATUSES): [status(check, creator="yadgarhq-bot[bot]"), status(other)]}))
    assert verdict.action == "refuse", verdict.reason
    assert not merged(api)


def test_a_pr_without_the_auto_pin_label_does_not_merge() -> None:
    """Plan section 4: the PR must be labelled `auto-pin`. A human who removes the label stops the merge."""
    pr = pull(labels=[])
    verdict, api = run(world({("GET", f"{R}/pulls/61"): pr}))
    assert verdict.action == "skip", verdict.reason
    assert "auto-pin" in verdict.reason
    assert not merged(api)


def test_the_needs_human_label_stops_a_merge() -> None:
    """Plan section 4 red case: the `auto-pin-needs-human` label → no merge. A label can only STOP a merge (a human's brake)."""
    pr = pull(labels=[{"name": "auto-pin"}, {"name": "auto-pin-needs-human"}])
    verdict, api = run(world({("GET", f"{R}/pulls/61"): pr}))
    assert verdict.action == "skip", verdict.reason
    assert "auto-pin-needs-human" in verdict.reason
    assert not merged(api)


@pytest.mark.parametrize(
    "files",
    [
        [{"filename": MECHANICAL[0], "status": "renamed", "previous_filename": ".github/workflows/ci.yaml"}, *[{"filename": n, "status": "modified"} for n in MECHANICAL[1:]]],
        [{"filename": MECHANICAL[0], "status": "removed"}, *[{"filename": n, "status": "modified"} for n in MECHANICAL[1:]]],
        [{"filename": MECHANICAL[0], "status": "added"}, *[{"filename": n, "status": "modified"} for n in MECHANICAL[1:]]],
    ],
    ids=["renamed-from-outside", "removed", "added"],
)
def test_a_mechanical_name_that_is_not_a_plain_modification_is_refused(files: list) -> None:
    """The names alone match MECHANICAL_FILES, but a rename deletes a file outside them, and a removal is no pin."""
    verdict, api = run(world({("GET", f"{R}/pulls/61/files"): files}))
    assert verdict.action == "refuse", verdict.reason
    assert not merged(api)


def test_a_hold_opened_after_the_judgement_stops_the_merge() -> None:
    """`act` reads the hold issues once more right before the PUT: a hold a human opens meanwhile wins."""
    api = Fake(world())
    verdict = am.judge_pr(api, 61, reads())
    assert verdict.action == "merge", verdict.reason
    api.routes[("GET", f"{R}/issues?labels=auto-pin-hold&state=open")] = [{"number": 12}]
    am.act(api, api, verdict, regenerations_left=True)
    assert not merged(api)


# ── the plan's section 4 red cases ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("runs", "action"),
    [
        (
            [
                {"name": "ci / passed", "status": "completed", "conclusion": "success", "app": {"id": 15368}},
                {"name": "ci / passed", "status": "completed", "conclusion": "failure", "app": {"id": 15368}},
            ],
            "refuse",
        ),
        ([{"name": "ci / passed", "status": "in_progress", "conclusion": None, "app": {"id": 15368}}], "wait"),
        ([], "wait"),
        ([{"name": "ci / passed", "status": "completed", "conclusion": "success", "app": {"id": 99}}], "refuse"),
    ],
    ids=["two-ci-passed", "in-progress", "none", "not-github-actions"],
)
def test_ci_passed_must_be_exactly_one_completed_success_by_github_actions(runs: list, action: str) -> None:
    verdict, api = run(world({("GET", CHECKS): {"check_runs": runs}}))
    assert verdict.action == action, verdict.reason
    assert not merged(api)


@pytest.mark.parametrize("state", ["pending", "failure", "error"])
def test_a_kind_dryrun_that_is_not_success_does_not_merge(state: str) -> None:
    verdict, _ = run(world({("GET", STATUSES): [status("e2", state=state), status("e1")]}))
    assert verdict.action != "merge"


def test_a_missing_kind_dryrun_does_not_merge() -> None:
    verdict, _ = run(world({("GET", STATUSES): [status("e1")]}))
    assert verdict.action != "merge"


def test_an_open_hold_issue_does_not_merge() -> None:
    verdict, api = run(world({("GET", f"{R}/issues?labels=auto-pin-hold&state=open"): [{"number": 9}]}))
    assert verdict.action == "wait" and "#9" in verdict.reason
    assert not merged(api)


def test_a_ci_run_behind_the_pr_head_does_not_merge() -> None:
    verdict, _ = run(world(), run_head=OTHER, run_branch=f"auto/pin-0.34.11-{HEAD[:12]}")
    assert verdict.action == "wait" and "head" in verdict.reason


def test_a_ci_run_for_another_branch_does_not_hold_this_pr_back() -> None:
    verdict, _ = run(world(), run_head=OTHER, run_branch="fix/something")
    assert verdict.action == "merge", verdict.reason


@pytest.mark.parametrize(("e1", "e2"), [("needs-human", "green"), ("accept", "red")])
def test_needs_human_in_the_marker_does_not_merge(e1: str, e2: str) -> None:
    """A human's to decide, not an anomaly: skipped, so the hourly job stays green while such a PR is open."""
    verdict, api = run(world({("GET", f"{R}/pulls/61"): pull(body=body(e1=e1, e2=e2))}))
    assert verdict.action == "skip"
    am.act(api, api, verdict, regenerations_left=True)
    assert api.writes() == []


def test_two_commits_do_not_merge() -> None:
    commits = [
        {"sha": OTHER, "author": {"login": "yadgarhq-bot[bot]"}, "commit": {"verification": {"verified": True}}, "parents": [{"sha": MAIN}]},
        {"sha": HEAD, "author": {"login": "yadgarhq-bot[bot]"}, "commit": {"verification": {"verified": True}}, "parents": [{"sha": OTHER}]},
    ]
    verdict, _ = run(world({("GET", f"{R}/pulls/61/commits"): commits}))
    assert verdict.action == "refuse" and "one commit" in verdict.reason


@pytest.mark.parametrize(
    "commit",
    [
        {"sha": HEAD, "author": {"login": "m-agahi"}, "commit": {"verification": {"verified": True}}, "parents": [{"sha": MAIN}]},
        {"sha": HEAD, "author": {"login": "yadgarhq-bot[bot]"}, "commit": {"verification": {"verified": False}}, "parents": [{"sha": MAIN}]},
        {"sha": HEAD, "author": {"login": "yadgarhq-bot[bot]"}, "commit": {"verification": {"verified": True}}, "parents": [{"sha": OTHER}]},
    ],
    ids=["human-author", "unsigned", "other-parent"],
)
def test_the_one_commit_must_be_the_bots_signed_commit_on_the_markers_main(commit: dict) -> None:
    verdict, _ = run(world({("GET", f"{R}/pulls/61/commits"): [commit]}))
    assert verdict.action == "refuse"


def test_a_status_naming_another_sha_than_the_commit_does_not_merge() -> None:
    verdict, _ = run(world({("GET", STATUSES): [status("e2", sha=OTHER), status("e1")]}))
    assert verdict.action == "refuse" and "sha" in verdict.reason


def test_base_behind_dispatches_a_regenerate_and_never_updates_the_branch() -> None:
    routes = world({("GET", f"{R}/branches/main"): {"commit": {"sha": NEW_MAIN}}})
    verdict, api = run(routes)
    assert verdict.action == "regenerate", verdict.reason
    am.act(api, api, verdict, regenerations_left=True)
    assert ("POST", DISPATCH, {"ref": "main", "inputs": {"regenerate": "61"}}) in api.writes()
    assert not merged(api)
    assert not any("update-branch" in call[1] for call in api.calls)


def test_mergeable_state_behind_dispatches_a_regenerate() -> None:
    verdict, api = run(world({("GET", f"{R}/pulls/61"): pull(mergeable_state="behind")}))
    assert verdict.action == "regenerate"
    assert not any("update-branch" in call[1] for call in api.calls)


# ── the rest of the predicate ───────────────────────────────────────────────


def test_an_author_other_than_the_release_app_does_not_merge() -> None:
    verdict, _ = run(world({("GET", f"{R}/pulls/61"): pull(user={"login": "m-agahi"})}))
    assert verdict.action == "refuse" and "author" in verdict.reason


@pytest.mark.parametrize(
    "ref",
    ["auto/pin-0.34.11", f"auto/pin-0.34.11-{OTHER[:12]}", f"auto/pin-0.34.12-{HEAD[:12]}", f"fix/pin-0.34.11-{HEAD[:12]}"],
    ids=["no-sha12", "sha12-not-head", "version-not-marker", "not-auto"],
)
def test_the_branch_must_be_auto_pin_version_sha12_of_the_head(ref: str) -> None:
    pr = pull(head={"ref": ref, "sha": HEAD, "repo": {"full_name": "yadgarhq/argocd"}})
    verdict, _ = run(world({("GET", f"{R}/pulls/61"): pr}))
    assert verdict.action == "refuse"


def test_a_fork_head_does_not_merge() -> None:
    pr = pull(head={"ref": f"auto/pin-0.34.11-{HEAD[:12]}", "sha": HEAD, "repo": {"full_name": "someone/argocd"}})
    verdict, _ = run(world({("GET", f"{R}/pulls/61"): pr}))
    assert verdict.action == "refuse"


def test_a_pr_without_the_marker_does_not_merge() -> None:
    verdict, _ = run(world({("GET", f"{R}/pulls/61"): pull(body="## Summary\n")}))
    assert verdict.action == "refuse" and "marker" in verdict.reason


def test_a_head_the_bot_did_not_push_is_skipped() -> None:
    """A human pushed to the PR: it is theirs now, skipped without a red job."""
    verdict, api = run(world({("GET", f"{R}/pulls/61"): pull(body=body(sha=OTHER))}))
    assert verdict.action == "skip"
    am.act(api, api, verdict, regenerations_left=True)
    assert api.writes() == []


def test_a_marker_for_another_version_than_the_branch_is_refused() -> None:
    verdict, _ = run(world({("GET", f"{R}/pulls/61"): pull(body=body(target="0.34.12"))}))
    assert verdict.action == "refuse"


def test_main_exits_0_on_a_skip_and_1_on_a_refusal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for marker_body, code in ((body(e1="needs-human"), 0), (body(target="0.34.12"), 1)):
        api = Fake(world({("GET", f"{R}/pulls/61"): pull(body=marker_body)}))
        monkeypatch.setattr(ap, "Api", lambda token: api)
        monkeypatch.setattr(ap, "checkout_sha", lambda tree: MAIN)
        monkeypatch.setattr(ap, "current_pin", lambda tree: "0.34.8")
        monkeypatch.setattr(ap, "_epoch_reader", lambda tree: lambda sha: GREEN)
        assert am.main(["--repo", str(tmp_path)]) == code
        assert not merged(api)


# ── two passes: the release App token only after a merge verdict (yaadgaar #424 item 2) ──


def two_pass(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, api: Fake) -> tuple[list, Path]:
    tokens: list = []
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setenv("PIN_TOKEN", "release-app-token")
    output = tmp_path / "output"
    output.touch()
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setattr(ap, "Api", lambda token: tokens.append(token) or api)
    monkeypatch.setattr(ap, "checkout_sha", lambda tree: MAIN)
    monkeypatch.setattr(ap, "current_pin", lambda tree: "0.34.8")
    monkeypatch.setattr(ap, "_epoch_reader", lambda tree: lambda sha: GREEN)
    monkeypatch.setattr(am, "_e2_reader", lambda pin: lambda run_id, sha, main: None)
    return tokens, output


def test_the_judge_pass_never_reads_the_release_app_token_and_merges_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = Fake(world())
    tokens, output = two_pass(monkeypatch, tmp_path, api)
    verdicts = tmp_path / "verdicts.json"
    assert am.main(["--repo", str(tmp_path), "--verdicts", str(verdicts)]) == 0
    assert "release-app-token" not in tokens and not merged(api)
    assert "merge=true" in output.read_text().splitlines()
    assert [v["number"] for v in json.loads(verdicts.read_text())["merge"]] == [61]


def test_the_merge_pass_merges_the_judged_pr_with_the_release_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = Fake(world())
    tokens, _ = two_pass(monkeypatch, tmp_path, api)
    verdicts = tmp_path / "verdicts.json"
    assert am.main(["--repo", str(tmp_path), "--verdicts", str(verdicts)]) == 0
    assert am.main(["--repo", str(tmp_path), "--merge", str(verdicts)]) == 0
    assert "release-app-token" in tokens and merged(api)


def test_no_merge_verdict_says_merge_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    api = Fake(world({("GET", f"{R}/pulls/61"): pull(body=body(e1="needs-human"))}))
    _, output = two_pass(monkeypatch, tmp_path, api)
    assert am.main(["--repo", str(tmp_path), "--verdicts", str(tmp_path / "v.json")]) == 0
    assert "merge=false" in output.read_text().splitlines() and "refused=false" in output.read_text().splitlines()


def test_a_refusal_in_the_judge_pass_is_reported_not_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused PR must not stop another green PR's merge step: the judge pass exits 0 and says `refused=true`; a
    last workflow step turns the job red."""
    api = Fake(world({("GET", f"{R}/pulls/61"): pull(body=body(target="0.34.12"))}))
    _, output = two_pass(monkeypatch, tmp_path, api)
    assert am.main(["--repo", str(tmp_path), "--verdicts", str(tmp_path / "v.json")]) == 0
    assert "refused=true" in output.read_text().splitlines()


@pytest.mark.parametrize(
    "files",
    [
        [{"filename": name, "status": "modified"} for name in (*MECHANICAL, ".github/workflows/ci.yaml")],
        [{"filename": name, "status": "modified"} for name in MECHANICAL[:2]],
        [{"filename": name, "status": "modified"} for name in (*MECHANICAL[:2], "scripts/pin_bump.py")],
    ],
    ids=["extra-workflow", "missing-table", "script-instead"],
)
def test_the_changed_files_must_equal_mains_mechanical_files(files: list) -> None:
    verdict, _ = run(world({("GET", f"{R}/pulls/61/files"): files}))
    assert verdict.action == "refuse" and "files" in verdict.reason


def test_the_mechanical_files_come_from_mains_code_not_the_prs() -> None:
    """The set is the one `reads` was built with (main's `pin_bump.MECHANICAL_FILES`), never anything in the PR."""
    narrower = am.MergeReads(epoch=lambda sha: GREEN, e2_run=lambda *a: None, mechanical=MECHANICAL[:2], sleep=lambda s: None)
    verdict, _ = run(world(), reads=narrower)
    assert verdict.action == "refuse"


def test_a_forged_second_green_status_naming_another_run_does_not_merge() -> None:
    """Yaadgaar #420: every `pin / kind-dryrun` must name the marker's E2 run; a forged one with its own run id is refused."""
    forged = status("e2", run=1)
    verdict, _ = run(world({("GET", STATUSES): [forged, status("e2"), status("e1")]}))
    assert verdict.action == "refuse" and "run" in verdict.reason


def test_a_forged_status_whose_run_is_not_the_dispatch_apps_does_not_merge() -> None:
    """Yaadgaar #420: the run the status names must be a pin-dryrun run the dispatch App started for this sha."""
    verdict, api = run(world(), reads=reads(e2="run 4242 was started by m-agahi, not yadgarhq-settled-dispatch[bot]"))
    assert verdict.action == "refuse" and "m-agahi" in verdict.reason
    assert not merged(api)


def test_an_e1_status_that_is_not_accept_does_not_merge() -> None:
    verdict, _ = run(world({("GET", STATUSES): [status("e2"), status("e1", result="needs-human", state="failure")]}))
    assert verdict.action == "refuse"


def test_a_status_on_another_main_does_not_merge() -> None:
    verdict, _ = run(world({("GET", STATUSES): [status("e2", main=OTHER), status("e1")]}))
    assert verdict.action == "refuse"


@pytest.mark.parametrize("kind", ["red", "pending", "past_close"])
def test_a_settled_epoch_that_is_not_green_does_not_merge(kind: str) -> None:
    verdict, _ = run(world(), reads=reads(epoch=ap.EpochState(kind=kind, reason=kind)))
    assert verdict.action == "wait"


def test_an_unknown_mergeable_state_is_polled_a_bounded_number_of_times() -> None:
    answers = iter([pull(mergeable_state="unknown"), pull(mergeable_state="unknown"), pull(mergeable_state="clean")])
    verdict, _ = run(world({("GET", f"{R}/pulls/61"): lambda _b: next(answers, pull(mergeable_state="clean"))}))
    assert verdict.action == "merge"


def test_a_mergeable_state_that_stays_unknown_waits() -> None:
    verdict, api = run(world({("GET", f"{R}/pulls/61"): pull(mergeable_state="unknown")}))
    assert verdict.action == "wait"
    assert sum(1 for c in api.calls if c[1] == f"{R}/pulls/61") <= am.MERGEABLE_POLLS + 1


@pytest.mark.parametrize("state", ["dirty", "blocked", "unstable"])
def test_a_mergeable_state_that_is_not_clean_waits(state: str) -> None:
    verdict, _ = run(world({("GET", f"{R}/pulls/61"): pull(mergeable_state=state)}))
    assert verdict.action == "wait"


def test_a_failed_ci_passed_spends_the_sha_and_regenerates() -> None:
    runs = [{"name": "ci / passed", "status": "completed", "conclusion": "failure", "app": {"id": 15368}}]
    verdict, _ = run(world({("GET", CHECKS): {"check_runs": runs}}))
    assert verdict.action == "regenerate" and "spent" in verdict.reason


def test_regenerations_are_bounded_per_version_and_main() -> None:
    """A deterministic CI failure must not loop: two bot PRs for 0.34.11 already closed on this main → stop."""
    closed = [pull(number=n, state="closed", head={"ref": f"auto/pin-0.34.11-{OTHER[:12]}", "sha": OTHER, "repo": {"full_name": "yadgarhq/argocd"}}, body=body(sha=OTHER)) for n in (58, 59)]
    api = Fake(world({("GET", f"{R}/pulls?state=all&sort=created&direction=desc"): [pull(), *closed]}))
    assert am.regenerations_left(api, "0.34.11", MAIN) is False
    verdict = am.Verdict(number=61, action="regenerate", reason="spent", head=HEAD)
    am.act(api, api, verdict, regenerations_left=False)
    assert api.writes() == []


def test_a_regenerate_is_not_dispatched_while_auto_pin_runs() -> None:
    api = Fake(world({("GET", IN_PROGRESS): {"workflow_runs": [{"id": 1}]}}))
    am.act(api, api, am.Verdict(number=61, action="regenerate", reason="behind", head=HEAD), regenerations_left=True)
    assert api.writes() == []


def test_main_moving_after_the_judgement_stops_the_merge() -> None:
    answers = iter([{"commit": {"sha": MAIN}}])
    api = Fake(world({("GET", f"{R}/branches/main"): lambda _b: next(answers, {"commit": {"sha": NEW_MAIN}})}))
    verdict = am.judge_pr(api, 61, reads())
    assert verdict.action == "merge"
    am.act(api, api, verdict, regenerations_left=True)
    assert not merged(api)


def test_a_head_that_moves_after_the_judgement_stops_the_merge() -> None:
    answers = iter([pull()])
    moved = pull(head={"ref": f"auto/pin-0.34.11-{HEAD[:12]}", "sha": OTHER, "repo": {"full_name": "yadgarhq/argocd"}})
    api = Fake(world({("GET", f"{R}/pulls/61"): lambda _b: next(answers, moved)}))
    verdict = am.judge_pr(api, 61, reads())
    assert verdict.action == "merge"
    am.act(api, api, verdict, regenerations_left=True)
    assert not merged(api)


def test_a_refused_merge_is_reported_and_not_retried() -> None:
    """`require_extra_approval_for_unattributed_changes` is unmeasured: a 405 is loud, once."""

    def refuse(_body):
        raise ap.ApiError(405, "PUT", f"{R}/pulls/61/merge")

    api = Fake(world({("PUT", f"{R}/pulls/61/merge"): refuse}))
    verdict = am.judge_pr(api, 61, reads())
    with pytest.raises(ap.ApiError):
        am.act(api, api, verdict, regenerations_left=True)
    assert sum(1 for call in api.calls if call[0] == "PUT") == 1


# ── the E2 run, read on argocd-verify with the dispatch App's token ─────────


def artifact(**result) -> bytes:
    value = {"sha": HEAD, "main_sha": MAIN, "pin": "0.34.8", "result": "green", **result}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("result.json", json.dumps(value))
    return buffer.getvalue()


def verify(**run_overrides) -> Fake:
    run_ = {
        "id": RUN,
        "path": ".github/workflows/pin-dryrun.yaml",
        "event": "workflow_dispatch",
        "head_branch": "main",
        "head_sha": "9" * 40,
        "repository": {"full_name": "yadgarhq/argocd-verify"},
        "head_repository": {"full_name": "yadgarhq/argocd-verify"},
        "actor": {"login": "yadgarhq-settled-dispatch[bot]"},
        "triggering_actor": {"login": "yadgarhq-settled-dispatch[bot]"},
        "display_title": f"pin-dryrun {HEAD}",
        "status": "completed",
        "conclusion": "success",
    }
    archive = run_overrides.pop("archive", artifact())
    run_.update(run_overrides)
    return Fake(
        {
            ("GET", f"{V}/actions/runs/{RUN}"): run_,
            ("GET", f"{V}/branches/main"): {"commit": {"sha": "8" * 40}},
            ("GET", f"{V}/compare/{'8' * 40}...{'9' * 40}"): {"status": "behind"},
            ("GET", f"{V}/actions/runs/{RUN}/artifacts"): {"artifacts": [{"name": "pin-dryrun", "expired": False, "archive_download_url": "https://x/zip"}]},
            ("GET", "https://x/zip"): archive,
        }
    )


def test_the_dispatch_apps_green_run_for_this_sha_is_e2() -> None:
    assert am.e2_run_problem(verify(), RUN, HEAD, MAIN, "0.34.8") is None


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"actor": {"login": "m-agahi"}}, "m-agahi"),
        ({"triggering_actor": {"login": "m-agahi"}}, "m-agahi"),
        ({"display_title": f"pin-dryrun {OTHER}"}, "display title"),
        ({"conclusion": "failure"}, "failure"),
        ({"status": "in_progress"}, "in_progress"),
        ({"path": ".github/workflows/settled.yaml"}, "path"),
        ({"event": "push"}, "event"),
        ({"head_branch": "feature"}, "branch"),
        ({"head_repository": {"full_name": "someone/argocd-verify"}}, "repository"),
        ({"head_sha": "7" * 40}, "default branch"),
        ({"archive": artifact(main_sha=OTHER)}, "main"),
        ({"archive": artifact(sha=OTHER)}, "sha"),
        ({"archive": artifact(result="red")}, "red"),
        ({"archive": artifact(pin="0.34.7")}, "pin"),
    ],
    ids=[
        "human-actor", "human-trigger", "other-sha", "red-run", "pending", "other-workflow", "push-event", "branch",
        "fork", "not-on-main", "artifact-main", "artifact-sha", "artifact-red", "artifact-pin",
    ],
)
def test_anything_but_the_dispatch_apps_green_run_for_this_sha_is_refused(overrides: dict, expected: str) -> None:
    api = verify(**overrides)
    api.routes[("GET", f"{V}/compare/{'8' * 40}...{'7' * 40}")] = {"status": "diverged"}
    problem = am.e2_run_problem(api, RUN, HEAD, MAIN, "0.34.8")
    assert problem is not None and expected in problem, problem


def test_a_run_with_no_artifact_is_refused() -> None:
    api = verify()
    api.routes[("GET", f"{V}/actions/runs/{RUN}/artifacts")] = {"artifacts": []}
    assert "artifact" in (am.e2_run_problem(api, RUN, HEAD, MAIN, "0.34.8") or "")


# ── the workflow ────────────────────────────────────────────────────────────

WORKFLOW = REPOSITORY / ".github" / "workflows" / "auto-pin-merge.yaml"


def test_the_merge_workflow_is_gated_runs_mains_code_and_never_updates_a_branch() -> None:
    text = WORKFLOW.read_text()
    workflow = yaml.safe_load(text)
    assert workflow["permissions"] == {}
    triggers = workflow[True]
    # zizmor's dangerous-triggers refuses workflow_run in `ci / workflows`; the schedule carries it.
    assert set(triggers) == {"schedule", "workflow_dispatch"}
    (job,) = workflow["jobs"].values()
    assert "vars.AUTO_PIN_MERGE == 'true'" in job["if"] and "github.ref == 'refs/heads/main'" in job["if"]
    assert job["concurrency"]["cancel-in-progress"] is False
    assert job["permissions"] == {"actions": "write", "checks": "read", "contents": "read", "issues": "read", "pull-requests": "read", "statuses": "read"}
    steps = job["steps"]
    checkout = steps[0]
    assert checkout["uses"].startswith("actions/checkout@") and checkout["with"]["ref"] == "main"
    assert checkout["with"]["persist-credentials"] is False
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert "update-branch" not in code
    assert "/update-branch" not in (REPOSITORY / "scripts" / "auto_pin_merge.py").read_text()
    # The ci run's branch is attacker-named data: it reaches the script by env only, never by `${{ }}` in a `run:`.
    assert all("${{" not in str(step.get("run", "")) for step in steps)
    mints = {s["id"]: s["with"] for s in steps if "create-github-app-token" in str(s.get("uses"))}
    # YAAGAAR #424 item 2: the release App token is minted only after the judge pass found a merge.
    by_id = {s.get("id"): s for s in steps}
    order = [s.get("id") for s in steps]
    assert order.index("judge") < order.index("pin") < order.index("merge") < order.index("refused")
    assert "PIN_TOKEN" not in by_id["judge"]["env"] and "--verdicts" in by_id["judge"]["run"]
    assert by_id["pin"]["if"] == "steps.judge.outputs.merge == 'true'"
    assert by_id["merge"]["if"] == "steps.judge.outputs.merge == 'true'" and "--merge" in by_id["merge"]["run"]
    assert by_id["merge"]["env"]["PIN_TOKEN"] == "${{ steps.pin.outputs.token }}"
    assert "steps.judge.outputs.refused == 'true'" in by_id["refused"]["if"] and "exit 1" in by_id["refused"]["run"]
    assert mints["verify"]["repositories"] == "argocd-verify"
    assert mints["verify"]["permission-actions"] == "read" and mints["verify"]["permission-contents"] == "read"
    assert mints["pin"]["repositories"] == "argocd"
    assert mints["pin"]["permission-contents"] == "write" and mints["pin"]["permission-pull-requests"] == "write"
    for step in steps:
        assert "uses" not in step or "@" in step["uses"] and len(step["uses"].split("@")[1].split()[0]) == 40, step
