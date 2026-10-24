"""`scripts/auto_pin.py`: open, supersede and relay evidence for automatic pin PRs (ledger 1632 C5, ledger 1670).

No network, no cluster, no git remote. GitHub is a fake that answers only the
paths a test gives it (anything else is HTTP 404) and records every write; the
clock and the sleep are injected, so a 15-minute registry wait and a 30-minute
dry-run wait run in microseconds.

THE PLAN'S C5 RED CASES (`plans/auto-deploy-parent-chart.md`, "C5"), each a
test below:

  - a newer tag while an older automatic PR is open: close it, open the new one;
  - an open `auto-pin-hold` issue: no PR;
  - the last merged pin's settled verdict still pending: no PR;
  - main's epoch red, or past close with no verdict: no PR;
  - a target lower than the pin: refused;
  - a superseded branch whose head is not the sha the bot pushed: the PR is
    closed with a comment and the branch is kept;
  - the newest tag's OCI chart not yet resolvable: wait up to 15 minutes, then
    take the highest version that resolves.

LEDGER 1670 (yaadgaar #247), each a test below: the dry-run result is bound to
the run this script dispatched; a stale result re-dispatches a bounded number
of times; a pending run is waited on for a bounded time; main is read again
before the PR opens; and only E1 AND E2 green leave a PR without
`auto-pin-needs-human`.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY / "scripts"))

_spec = importlib.util.spec_from_file_location("auto_pin", REPOSITORY / "scripts" / "auto_pin.py")
ap = importlib.util.module_from_spec(_spec)
sys.modules["auto_pin"] = ap
_spec.loader.exec_module(ap)

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
MAIN = "a" * 40
NEW_MAIN = "b" * 40
PUSHED = "c" * 40
FOREIGN = "d" * 40
TREE = "e" * 40
R = "/repos/yadgarhq/argocd"
V = "/repos/yadgarhq/argocd-verify"
RUNS = f"{V}/actions/workflows/pin-dryrun.yaml/runs?event=workflow_dispatch&per_page=30"
DISPATCH = f"{V}/actions/workflows/pin-dryrun.yaml/dispatches"
BULLET = re.compile(r"^\s*[-*]\s+(build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)(\([^)]+\))?!?:\s+\S.*$")


# ── fakes ────────────────────────────────────────────────────────────────────


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


class Clock:
    def __init__(self, start: dt.datetime = NOW) -> None:
        self.moment = start
        self.slept = 0.0

    def now(self) -> dt.datetime:
        return self.moment

    def sleep(self, seconds: float) -> None:
        self.slept += seconds
        self.moment += dt.timedelta(seconds=seconds)


def marker(sha: str = PUSHED, main: str = MAIN, target: str = "0.34.10", e1: str = "accept", e2: str = "green") -> ap.Marker:
    return ap.Marker(sha=sha, main=main, target=target, e1=e1, e2=e2, e2_run=7)


def pr(number: int, version: str, *, head: str = PUSHED, mark: ap.Marker | None = None, labels=("auto-pin",), state: str = "open", merged: bool = False) -> ap.PinPR:
    return ap.PinPR(
        number=number,
        branch=f"auto/pin-{version}-{head[:12]}",
        version=version,
        head_sha=head,
        state=state,
        merged=merged,
        labels=frozenset(labels),
        marker=mark if mark is not None else marker(target=version),
    )


GREEN = ap.EpochState(kind="green", reason="green verdict, run 5")


def decide(**overrides) -> ap.Decision:
    arguments = {
        "pin": "0.34.8",
        "main_sha": MAIN,
        "holds": [],
        "epoch": GREEN,
        "prs": [],
        "target": "0.34.11",
        "human_closed": frozenset(),
        "regenerate": None,
    }
    arguments.update(overrides)
    return ap.decide(**arguments)


# ── the epoch: green, red, pending, past close ──────────────────────────────

EPOCH = {"key": "k" * 64, "anchor": "f" * 40, "deadline": "2026-10-24T11:00:00Z"}


def test_a_green_verdict_for_mains_epoch_does_not_block() -> None:
    state = ap.epoch_state(EPOCH, {"verdict": {"result": "green"}, "run_id": 5}, NOW)
    assert state.kind == "green" and not state.blocks and not state.holds


def test_a_red_verdict_blocks_and_asks_for_a_hold() -> None:
    state = ap.epoch_state(EPOCH, {"verdict": {"result": "red"}, "run_id": 5}, NOW)
    assert state.kind == "red" and state.blocks and state.holds


def test_no_verdict_before_close_is_pending() -> None:
    # close = deadline + 1200 s = 11:20; now is 11:10.
    state = ap.epoch_state(EPOCH, None, dt.datetime(2026, 10, 24, 11, 10, tzinfo=UTC))
    assert state.kind == "pending" and state.blocks and not state.holds


def test_no_verdict_past_close_counts_as_red() -> None:
    state = ap.epoch_state(EPOCH, None, dt.datetime(2026, 10, 24, 11, 20, tzinfo=UTC))
    assert state.kind == "past_close" and state.blocks and state.holds


def test_no_render_key_blocks() -> None:
    assert ap.epoch_state(None, None, NOW).blocks


# ── decide: the plan's red cases ────────────────────────────────────────────


def test_a_newer_tag_with_an_older_pr_open_closes_it_and_opens_one() -> None:
    old = pr(12, "0.34.10")
    decision = decide(prs=[old])
    assert decision.action == "open" and decision.target == "0.34.11"
    assert [p.number for p in decision.supersede] == [12]


def test_an_open_hold_issue_opens_nothing() -> None:
    decision = decide(holds=[40])
    assert decision.action == "none" and "#40" in decision.reason


def test_a_pending_verdict_for_the_last_merged_pin_opens_nothing() -> None:
    decision = decide(epoch=ap.EpochState(kind="pending", reason="no verdict yet"))
    assert decision.action == "none" and "pending" in decision.reason


@pytest.mark.parametrize("kind", ["red", "past_close"])
def test_a_red_or_past_close_epoch_opens_nothing(kind: str) -> None:
    decision = decide(epoch=ap.EpochState(kind=kind, reason="x"), prs=[pr(12, "0.34.10")])
    assert decision.action == "none" and not decision.supersede


def test_no_target_newer_than_the_pin_opens_nothing() -> None:
    assert decide(target=None).action == "none"


def test_an_in_flight_pr_for_the_target_opens_nothing() -> None:
    current = pr(13, "0.34.11")
    decision = decide(prs=[current])
    assert decision.action == "none" and "#13" in decision.reason


def test_an_in_flight_pr_with_an_older_one_still_open_tidies_the_older_one() -> None:
    """open closes the old PR only AFTER the new one exists; a failure in between leaves two. The next run tidies."""
    decision = decide(prs=[pr(13, "0.34.11"), pr(12, "0.34.10")])
    assert decision.action == "tidy" and decision.keep == 13 and [p.number for p in decision.supersede] == [12]


def test_a_pr_a_human_pushed_to_is_left_alone() -> None:
    decision = decide(prs=[pr(13, "0.34.11", head=FOREIGN)])
    assert decision.action == "none" and not decision.supersede


def test_a_pr_a_human_pushed_to_is_left_alone_even_on_an_older_main() -> None:
    """Without the head guard, a human's PR built on an older main would be closed and regenerated."""
    mine = pr(13, "0.34.11", head=FOREIGN, mark=marker(target="0.34.11", main=NEW_MAIN))
    decision = decide(prs=[mine])
    assert decision.action == "none" and not decision.supersede and "human" in decision.reason


def test_regenerate_never_closes_a_pr_a_human_pushed_to() -> None:
    decision = decide(prs=[pr(13, "0.34.11", head=FOREIGN)], regenerate=13)
    assert decision.action == "none" and not decision.supersede


def test_a_needs_human_pr_for_the_target_is_left_alone_even_on_a_new_main() -> None:
    waiting = pr(13, "0.34.11", labels=("auto-pin", "auto-pin-needs-human"), mark=marker(target="0.34.11", main=NEW_MAIN))
    assert decide(prs=[waiting]).action == "none"


def test_a_pr_built_on_an_older_main_is_regenerated() -> None:
    stale = pr(13, "0.34.11", mark=marker(target="0.34.11", main=NEW_MAIN))
    decision = decide(prs=[stale])
    assert decision.action == "open" and [p.number for p in decision.supersede] == [13]


def test_the_regenerate_input_closes_and_reopens_an_in_flight_pr() -> None:
    decision = decide(prs=[pr(13, "0.34.11")], regenerate=13)
    assert decision.action == "open" and [p.number for p in decision.supersede] == [13]


def test_a_version_a_human_closed_is_not_reopened() -> None:
    assert decide(human_closed=frozenset({"0.34.11"})).action == "none"


def test_an_open_pr_for_a_newer_version_than_the_target_blocks() -> None:
    assert decide(prs=[pr(14, "0.34.12")]).action == "none"


def test_a_same_version_duplicate_is_tidied_and_the_newest_kept() -> None:
    """Two open PRs for the target on the same main: keep the newest (listed first), close the other."""
    decision = decide(prs=[pr(14, "0.34.11"), pr(13, "0.34.11")])
    assert decision.action == "tidy" and decision.keep == 14 and [p.number for p in decision.supersede] == [13]


def test_a_regenerate_closes_every_same_version_duplicate() -> None:
    decision = decide(prs=[pr(14, "0.34.11"), pr(13, "0.34.11")], regenerate=14)
    assert decision.action == "open" and sorted(p.number for p in decision.supersede) == [13, 14]


def test_a_human_touched_older_pr_is_never_superseded() -> None:
    """The head guard covers every PR auto-pin would close, not only the target's."""
    decision = decide(prs=[pr(12, "0.34.10", head=FOREIGN)])
    assert decision.action == "open" and decision.supersede == ()


def test_a_human_touched_same_version_duplicate_is_never_tidied() -> None:
    decision = decide(prs=[pr(14, "0.34.11"), pr(13, "0.34.11", head=FOREIGN)])
    assert decision.action == "none" and decision.supersede == ()


def test_a_recent_failed_attempt_backs_off() -> None:
    decision = decide(backoff="auto/pin-0.34.11-cccccccccccc failed 1 h ago")
    assert decision.action == "none" and "back off" in decision.reason


# ── the target ──────────────────────────────────────────────────────────────

TAGS = ["v0.34.8", "v0.34.9", "v0.34.10", "v0.34.11", "v0.35.0-rc.1", "junk"]


def test_the_target_is_the_highest_resolvable_release() -> None:
    clock = Clock()
    assert ap.resolvable_target("0.34.8", TAGS, lambda v: True, now=clock.now, sleep=clock.sleep) == "0.34.11"
    assert clock.slept == 0


def test_a_prerelease_is_never_a_target() -> None:
    clock = Clock()
    assert ap.resolvable_target("0.34.11", TAGS, lambda v: True, now=clock.now, sleep=clock.sleep) is None


def test_a_target_lower_than_the_pin_is_refused() -> None:
    clock = Clock()
    with pytest.raises(ap.Refused, match="lower"):
        ap.resolvable_target("0.34.12", TAGS, lambda v: True, now=clock.now, sleep=clock.sleep)


def test_an_unresolvable_newest_chart_waits_15_minutes_then_falls_back() -> None:
    clock = Clock()
    asked: list[str] = []

    def resolves(version: str) -> bool:
        asked.append(version)
        return version != "0.34.11"

    target = ap.resolvable_target("0.34.8", TAGS, resolves, now=clock.now, sleep=clock.sleep)
    assert target == "0.34.10"
    assert 900 <= clock.slept <= 960
    assert asked.count("0.34.11") >= 2


def test_a_newest_chart_that_resolves_during_the_wait_is_taken() -> None:
    clock = Clock()
    target = ap.resolvable_target("0.34.8", TAGS, lambda v: clock.slept >= 120, now=clock.now, sleep=clock.sleep)
    assert target == "0.34.11" and clock.slept < 900


# ── supersede: close with a comment, delete the branch only when it is ours ─

OLD_BRANCH = f"auto/pin-0.34.10-{PUSHED[:12]}"


def closing_routes(head: str) -> dict:
    return {
        ("GET", f"{R}/pulls/12"): {"state": "open", "head": {"sha": head, "ref": OLD_BRANCH}},
        ("POST", f"{R}/issues/12/comments"): {},
        ("PATCH", f"{R}/pulls/12"): {},
        ("DELETE", f"{R}/git/refs/heads/{OLD_BRANCH}"): None,
    }


def test_a_superseded_branch_with_a_foreign_head_is_closed_with_a_comment_and_kept() -> None:
    api = Fake(closing_routes(FOREIGN))
    ap.close_superseded(api, pr(12, "0.34.10"), "0.34.11", by=30)
    writes = api.writes()
    assert ("PATCH", f"{R}/pulls/12", {"state": "closed"}) in writes
    comment = [body for method, path, body in writes if path.endswith("/comments")]
    assert comment and "0.34.11" in comment[0]["body"] and "#30" in comment[0]["body"] and "kept" in comment[0]["body"]
    assert not [w for w in writes if w[0] == "DELETE"]


def test_a_superseded_branch_with_the_bots_head_is_closed_and_deleted() -> None:
    api = Fake(closing_routes(PUSHED))
    ap.close_superseded(api, pr(12, "0.34.10"), "0.34.11", by=30)
    writes = api.writes()
    assert ("PATCH", f"{R}/pulls/12", {"state": "closed"}) in writes
    assert ("DELETE", f"{R}/git/refs/heads/{OLD_BRANCH}", None) in writes


def test_a_pr_that_is_already_closed_is_not_touched() -> None:
    api = Fake({("GET", f"{R}/pulls/12"): {"state": "closed", "head": {"sha": PUSHED}}})
    ap.close_superseded(api, pr(12, "0.34.10"), "0.34.11", by=30)
    assert api.writes() == []


def test_tidy_closes_the_older_prs_naming_the_one_in_flight() -> None:
    api = Fake(closing_routes(PUSHED))
    ap.tidy(api, [pr(12, "0.34.10")], "0.34.11", keep=13)
    comment = [body for method, path, body in api.writes() if path.endswith("/comments")][0]
    assert "#13" in comment["body"] and ("PATCH", f"{R}/pulls/12", {"state": "closed"}) in api.writes()


# ── reading the PRs ─────────────────────────────────────────────────────────


def api_pr(number: int, branch: str, *, login: str = "yadgarhq-bot[bot]", state: str = "open", body: str = "", merged: str | None = None) -> dict:
    return {
        "number": number,
        "state": state,
        "merged_at": merged,
        "user": {"login": login},
        "head": {"ref": branch, "sha": PUSHED},
        "labels": [{"name": "auto-pin"}],
        "body": body,
    }


def test_only_the_bots_auto_pin_branches_are_pin_prs() -> None:
    body = ap.marker_line(marker(target="0.34.10"))
    listing = [
        api_pr(12, "auto/pin-0.34.10-cccccccccccc", body=body),
        api_pr(11, "auto/pin-0.34.9"),
        api_pr(13, "auto/pin-0.34.11-cccccccccccc", login="m-agahi"),
        api_pr(14, "chore/pin-0.34.11"),
        api_pr(15, "auto/pin-0.34.11-nothex"),
    ]
    api = Fake({("GET", f"{R}/pulls?state=all&sort=created&direction=desc"): listing})
    found = ap.pin_prs(api)
    assert [p.number for p in found] == [12, 11]
    assert found[0].version == "0.34.10" and found[0].marker == marker(target="0.34.10")


def test_a_version_closed_by_a_human_is_remembered_and_one_closed_by_the_bot_is_not() -> None:
    closed = [pr(20, "0.34.11", state="closed"), pr(21, "0.34.11", state="closed"), pr(22, "0.34.11", state="closed", merged=True)]
    api = Fake(
        {
            ("GET", f"{R}/issues/20"): {"closed_by": {"login": "m-agahi"}},
            ("GET", f"{R}/issues/21"): {"closed_by": {"login": "yadgarhq-bot[bot]"}},
        }
    )
    assert ap.human_closed(api, closed, "0.34.11") == frozenset({"0.34.11"})


def test_human_closed_reads_only_the_targets_prs() -> None:
    """One GET per closed PR of the TARGET version only, not of every version ever pinned."""
    closed = [pr(20, "0.34.10", state="closed"), pr(21, "0.34.9", state="closed")]
    api = Fake()
    assert ap.human_closed(api, closed, "0.34.11") == frozenset()
    assert api.calls == []


def test_the_marker_round_trips_and_hides_from_the_template() -> None:
    line = ap.marker_line(marker())
    assert line.startswith("<!--") and line.endswith("-->")
    assert ap.parse_marker(f"## What\n\n{line}\n") == marker()
    assert ap.parse_marker("no marker here") is None


# ── back-off: a recent failed attempt for the same main waits ──────────────

ORPHAN = "9" * 40
MATCHING = f"{R}/git/matching-refs/heads/auto/pin-0.34.11"


def e1_status(age_hours: float, main: str = MAIN, creator: str = "github-actions[bot]") -> dict:
    created = NOW - dt.timedelta(hours=age_hours)
    return {
        "context": "pin / e1",
        "state": "success",
        "description": ap.status_description("e1", "accept", sha=ORPHAN, main=main, run_id=None),
        "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "creator": {"login": creator},
    }


def backoff_api(statuses: list[dict], ref: str = f"refs/heads/auto/pin-0.34.11-{ORPHAN[:12]}") -> Fake:
    return Fake(
        {
            ("GET", MATCHING): [{"ref": ref, "object": {"sha": ORPHAN}}],
            ("GET", f"{R}/commits/{ORPHAN}/statuses"): statuses,
        }
    )


def test_an_orphan_with_a_young_e1_on_the_same_main_backs_off() -> None:
    reason = ap.backoff(backoff_api([e1_status(1)]), "0.34.11", MAIN, NOW, open_heads=frozenset())
    assert reason and "auto/pin-0.34.11" in reason


@pytest.mark.parametrize(
    ("statuses", "why"),
    [
        ([e1_status(ap.BACKOFF_HOURS + 1)], "old"),
        ([e1_status(1, main=NEW_MAIN)], "another main"),
        ([e1_status(1, creator="m-agahi")], "not github-actions"),
        ([], "no status"),
    ],
)
def test_no_back_off_otherwise(statuses: list[dict], why: str) -> None:
    assert ap.backoff(backoff_api(statuses), "0.34.11", MAIN, NOW, open_heads=frozenset()) is None, why


def test_a_branch_with_an_open_pr_is_not_an_orphan() -> None:
    assert ap.backoff(backoff_api([e1_status(1)]), "0.34.11", MAIN, NOW, open_heads=frozenset({ORPHAN})) is None


def test_a_prefix_match_of_another_version_is_not_an_orphan() -> None:
    """matching-refs for auto/pin-0.34.1 also returns auto/pin-0.34.11-…; the version must match exactly."""
    api = backoff_api([e1_status(1)], ref=f"refs/heads/auto/pin-0.34.110-{ORPHAN[:12]}")
    assert ap.backoff(api, "0.34.11", MAIN, NOW, open_heads=frozenset()) is None


# ── push: one verified commit on main through the Git Data API ──────────────

NEW_BRANCH = f"auto/pin-0.34.11-{PUSHED[:12]}"


def push_routes(orphans: list[dict] | None = None, commits: dict | None = None) -> dict:
    routes = {
        ("GET", f"{R}/branches/main"): {"commit": {"sha": MAIN}},
        ("GET", f"{R}/git/commits/{MAIN}"): {"tree": {"sha": TREE}},
        ("GET", MATCHING): orphans or [],
        ("POST", f"{R}/git/blobs"): lambda body: {"sha": "1" * 40},
        ("POST", f"{R}/git/trees"): {"sha": "2" * 40},
        ("POST", f"{R}/git/commits"): {"sha": PUSHED},
        ("POST", f"{R}/git/refs"): {"ref": f"refs/heads/{NEW_BRANCH}"},
    }
    for orphan in orphans or []:
        routes[("DELETE", f"{R}/git/{orphan['ref']}")] = None
    for sha, commit in (commits or {}).items():
        routes[("GET", f"{R}/commits/{sha}")] = commit
    return routes


def tree_with(tmp_path: Path, extra: str | None = None) -> Path:
    for name in ap.PIN_FILES:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(f"{name}\n")
    return tmp_path


def state(**overrides) -> ap.State:
    values = {"main_sha": MAIN, "pin": "0.34.8", "target": "0.34.11", "title": "fix: move the yadgar pin to parent chart 0.34.11"}
    values.update(overrides)
    return ap.State(**values)


def test_push_makes_one_commit_whose_only_parent_is_main_on_a_branch_named_for_it(tmp_path: Path) -> None:
    api = Fake(push_routes())
    sha = ap.push(api, tree_with(tmp_path), state(), changed=sorted(ap.PIN_FILES), open_heads=frozenset())
    assert sha == PUSHED
    commit = [body for method, path, body in api.calls if (method, path) == ("POST", f"{R}/git/commits")][0]
    assert commit["parents"] == [MAIN] and commit["tree"] == "2" * 40
    assert "author" not in commit and "committer" not in commit  # GitHub signs an App's API commit
    tree = [body for method, path, body in api.calls if (method, path) == ("POST", f"{R}/git/trees")][0]
    assert tree["base_tree"] == TREE and sorted(e["path"] for e in tree["tree"]) == sorted(ap.PIN_FILES)
    assert ("POST", f"{R}/git/refs", {"ref": f"refs/heads/{NEW_BRANCH}", "sha": PUSHED}) in api.calls
    # push closes nothing: the superseded PRs close only after the new one opens.
    assert not [w for w in api.writes() if w[0] == "PATCH" or w[1].endswith("/comments")]


def test_push_refuses_a_change_outside_the_three_pin_files(tmp_path: Path) -> None:
    api = Fake(push_routes())
    with pytest.raises(ap.Refused, match="README"):
        ap.push(api, tree_with(tmp_path), state(), changed=[*sorted(ap.PIN_FILES), "README.md"], open_heads=frozenset())
    assert api.writes() == []


@pytest.mark.parametrize(
    "changed", [[], ["scripts/chart_pin.json", "scripts/gates/yadgar_render.sha256"]], ids=["empty", "no-application"]
)
def test_push_refuses_a_change_set_that_is_not_a_pin(tmp_path: Path, changed: list[str]) -> None:
    """C3's rule: a non-empty subset of the three pin files that includes applications/yadgar.yaml."""
    api = Fake(push_routes())
    with pytest.raises(ap.Refused, match="applications/yadgar.yaml"):
        ap.push(api, tree_with(tmp_path), state(), changed=changed, open_heads=frozenset())
    assert api.writes() == []


def test_push_stops_when_main_moved_since_the_gate(tmp_path: Path) -> None:
    api = Fake(push_routes())
    with pytest.raises(ap.MainMoved):
        ap.push(api, tree_with(tmp_path), state(main_sha=NEW_MAIN), changed=sorted(ap.PIN_FILES), open_heads=frozenset())
    assert api.writes() == []


def bot_commit(verified: bool = True, login: str = "yadgarhq-bot[bot]") -> dict:
    return {"author": {"login": login}, "parents": [{"sha": MAIN}], "commit": {"verification": {"verified": verified}}}


ORPHAN_REF = {"ref": f"refs/heads/auto/pin-0.34.11-{ORPHAN[:12]}", "object": {"sha": ORPHAN}}


def test_a_stale_orphan_branch_the_bot_made_is_deleted(tmp_path: Path) -> None:
    api = Fake(push_routes([ORPHAN_REF], {ORPHAN: bot_commit()}))
    ap.push(api, tree_with(tmp_path), state(), changed=sorted(ap.PIN_FILES), open_heads=frozenset())
    assert ("DELETE", f"{R}/git/{ORPHAN_REF['ref']}", None) in api.calls


@pytest.mark.parametrize("commit", [bot_commit(verified=False), bot_commit(login="m-agahi")], ids=["unverified", "foreign"])
def test_an_orphan_that_is_not_provably_the_bots_is_kept(tmp_path: Path, commit: dict) -> None:
    api = Fake(push_routes([ORPHAN_REF], {ORPHAN: commit}))
    assert ap.push(api, tree_with(tmp_path), state(), changed=sorted(ap.PIN_FILES), open_heads=frozenset()) == PUSHED
    assert not [w for w in api.writes() if w[0] == "DELETE"]


def test_an_open_prs_branch_is_never_an_orphan(tmp_path: Path) -> None:
    api = Fake(push_routes([ORPHAN_REF], {ORPHAN: bot_commit()}))
    ap.push(api, tree_with(tmp_path), state(), changed=sorted(ap.PIN_FILES), open_heads=frozenset({ORPHAN}))
    assert not [w for w in api.writes() if w[0] == "DELETE"]


# ── E2: dispatch, bind to OUR run, bounded waits and re-dispatch ───────────


class Verify:
    """argocd-verify: each dispatch makes one run; `script` says how each run ends and what `find` then returns."""

    def __init__(self, clock: Clock, script: list[dict], main: str = MAIN) -> None:
        self.clock = clock
        self.script = script
        self.runs: list[dict] = []
        self.api = Fake(
            {
                ("POST", DISPATCH): self._dispatch,
                ("GET", RUNS): lambda _b: {"workflow_runs": list(reversed(self.runs))},
            }
        )
        self.repo = Fake({("GET", f"{R}/branches/main"): {"commit": {"sha": main}}})

    def _dispatch(self, body: dict) -> None:
        number = len(self.runs)
        run_id = 100 + number
        step = self.script[min(number, len(self.script) - 1)]
        created = self.clock.now() + dt.timedelta(seconds=5)
        self.runs.append(
            {
                "id": run_id,
                "display_title": f"pin-dryrun {body['inputs']['argocd_sha']}",
                "event": "workflow_dispatch",
                "actor": {"login": step.get("actor", "yadgarhq-settled-dispatch[bot]")},
                "created_at": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "status": "queued",
                "conclusion": None,
                "_ends": self.clock.now() + dt.timedelta(seconds=step.get("takes", 60)),
                "_step": step,
            }
        )
        self.api.routes[("GET", f"{V}/actions/runs/{run_id}")] = lambda _b, i=number: self._run(i)

    def _run(self, index: int) -> dict:
        run = self.runs[index]
        if self.clock.now() >= run["_ends"]:
            run["status"], run["conclusion"] = "completed", run["_step"].get("conclusion", "success")
        return {k: v for k, v in run.items() if not k.startswith("_")}

    def find(self, sha: str, main_sha: str, pin: str) -> dict | None:
        if not self.runs:
            return None
        step = self.runs[-1]["_step"]
        if "find" not in step:
            return None
        verdict, run_offset = step["find"]
        return {"found": True, "verdict": verdict, "reason": verdict, "run_id": self.runs[-1]["id"] + run_offset}


def relay(verify: Verify, clock: Clock) -> ap.DryRun:
    return ap.relay_dryrun(
        verify.api,
        verify.repo,
        sha=PUSHED,
        main_sha=MAIN,
        pin="0.34.8",
        find=verify.find,
        now=clock.now,
        sleep=clock.sleep,
    )


def test_a_green_result_of_our_own_run_is_green() -> None:
    clock = Clock()
    verify = Verify(clock, [{"find": ("green", 0)}])
    result = relay(verify, clock)
    assert result.result == "green" and result.run_id == 100
    assert verify.api.writes() == [("POST", DISPATCH, {"ref": "main", "inputs": {"argocd_sha": PUSHED}})]


def test_a_red_result_of_our_own_run_is_red_and_not_redispatched() -> None:
    clock = Clock()
    verify = Verify(clock, [{"find": ("red", 0), "conclusion": "failure"}])
    assert relay(verify, clock).result == "red"
    assert len(verify.runs) == 1


def test_find_is_bound_to_the_run_we_dispatched() -> None:
    """A green artifact from ANOTHER run (a hand dispatch, an older run) never counts: dispatch again."""
    clock = Clock()
    verify = Verify(clock, [{"find": ("green", 99)}, {"find": ("green", 0)}])
    result = relay(verify, clock)
    assert result.result == "green" and result.run_id == 101 and len(verify.runs) == 2


def test_a_run_by_another_actor_is_not_bound() -> None:
    clock = Clock()
    verify = Verify(clock, [{"find": ("green", 0), "actor": "m-agahi"}])
    with pytest.raises(ap.InfrastructureError):
        relay(verify, clock)


def test_a_stale_result_redispatches_a_bounded_number_of_times() -> None:
    clock = Clock()
    verify = Verify(clock, [{"find": ("stale", 0)}])
    with pytest.raises(ap.InfrastructureError, match="stale"):
        relay(verify, clock)
    assert len(verify.runs) == ap.MAX_DISPATCHES


def test_a_cancelled_run_is_dispatched_again() -> None:
    clock = Clock()
    verify = Verify(clock, [{"conclusion": "cancelled"}, {"find": ("green", 0)}])
    assert relay(verify, clock).result == "green" and len(verify.runs) == 2


def test_a_run_that_uploads_nothing_is_never_green() -> None:
    """VERIFY_ENABLED off: the job is skipped, the run concludes, no artifact. Infrastructure, not green."""
    clock = Clock()
    verify = Verify(clock, [{"conclusion": "success"}])
    with pytest.raises(ap.InfrastructureError):
        relay(verify, clock)


def test_a_pending_run_is_waited_on_for_a_bounded_time() -> None:
    clock = Clock()
    verify = Verify(clock, [{"find": ("green", 0), "takes": 10_000}])
    with pytest.raises(ap.InfrastructureError, match="pending"):
        relay(verify, clock)
    assert ap.DRYRUN_WAIT_SECONDS <= clock.slept <= ap.DRYRUN_WAIT_SECONDS + 300
    assert len(verify.runs) == 1


def test_the_whole_relay_stops_at_its_deadline_before_the_token_does() -> None:
    """Three slow stale runs would outlive the 60-minute verify token: the relay stops at RELAY_SECONDS instead."""
    clock = Clock()
    verify = Verify(clock, [{"find": ("stale", 0), "takes": 1500}])
    with pytest.raises(ap.InfrastructureError, match="deadline"):
        relay(verify, clock)
    assert len(verify.runs) == 2 and clock.slept <= ap.RELAY_SECONDS + 60


def test_main_moving_during_the_dry_run_stops_the_relay() -> None:
    clock = Clock()
    verify = Verify(clock, [{"find": ("stale", 0)}], main=NEW_MAIN)
    with pytest.raises(ap.MainMoved):
        relay(verify, clock)


# ── statuses: terse, parseable, under GitHub's 140 characters ──────────────


def test_the_status_description_names_the_sha_main_and_run_and_parses_back() -> None:
    text = ap.status_description("e2", "green", sha=PUSHED, main=MAIN, run_id=39421942793)
    assert len(text) <= 140
    assert ap.parse_status_description(text) == {"check": "e2", "result": "green", "sha": PUSHED, "main": MAIN, "run": 39421942793}


@pytest.mark.parametrize(("accept", "state_", "word"), [(True, "success", "accept"), (False, "failure", "needs-human")])
def test_the_e1_status_carries_the_verdict(accept: bool, state_: str, word: str) -> None:
    api = Fake({("POST", f"{R}/statuses/{PUSHED}"): {}})
    ap.post_e1(api, PUSHED, MAIN, accept)
    [(_, _, body)] = api.writes()
    assert body["context"] == "pin / e1" and body["state"] == state_
    assert ap.parse_status_description(body["description"]) == {"check": "e1", "result": word, "sha": PUSHED, "main": MAIN}


@pytest.mark.parametrize(("result", "state_"), [("green", "success"), ("red", "failure")])
def test_the_e2_status_carries_the_verdict_and_the_run(result: str, state_: str) -> None:
    api = Fake({("POST", f"{R}/statuses/{PUSHED}"): {}})
    ap.post_e2(api, PUSHED, MAIN, ap.DryRun(result=result, run_id=7, reason=""))
    [(_, _, body)] = api.writes()
    assert body["context"] == "pin / kind-dryrun" and body["state"] == state_ and body["target_url"].endswith("/runs/7")
    assert ap.parse_status_description(body["description"])["run"] == 7


# ── open: recheck main, E1 AND E2, the body ─────────────────────────────────

PIN_BODY = (
    "## What\n\nmoves the pin.\n\n## Why\n\nADR-0946.\n\n## Changelog\n\n"
    "- fix: move the yadgar pin to parent chart 0.34.11, carrying gateway 0.12.29\n\n"
    "## Verification\n\n- **E1 render diff.** ok\n"
    "- **Not covered here:** E2, the kind-yadgar dry run, is a separate check.\n\n## Risk\n\nrollback.\n"
)


def open_routes(main: str = MAIN, holds: list | None = None, created: object = None) -> dict:
    return {
        ("GET", f"{R}/branches/main"): {"commit": {"sha": main}},
        ("GET", f"{R}/issues?labels=auto-pin-hold&state=open"): holds or [],
        ("POST", f"{R}/pulls"): created if created is not None else {"number": 30},
        ("POST", f"{R}/issues/30/labels"): [],
        **closing_routes(PUSHED),
    }


def opened(e1: bool, e2: str, **kwargs) -> tuple[Fake, int | None]:
    api = Fake(open_routes(**kwargs))
    number = ap.open_pr(
        api,
        state(sha=PUSHED, superseded=(12,)),
        e1_accept=e1,
        dryrun=ap.DryRun(result=e2, run_id=7, reason=e2),
        pin_body=PIN_BODY,
        check_body=lambda body: [],
        superseded=[pr(12, "0.34.10")],
    )
    return api, number


def test_the_superseded_pr_closes_only_after_the_new_one_exists() -> None:
    api, number = opened(True, "green")
    writes = [(m, p) for m, p, _ in api.writes()]
    assert writes.index(("POST", f"{R}/pulls")) < writes.index(("PATCH", f"{R}/pulls/12"))
    comment = [b for m, p, b in api.writes() if p.endswith("/12/comments")][0]
    assert "#30" in comment["body"]


def test_a_failed_create_leaves_the_old_pr_open() -> None:
    def refuse(_body: object) -> None:
        raise ap.ApiError(422, "POST", "/pulls")

    api = Fake(open_routes(created=refuse))
    with pytest.raises(ap.ApiError):
        ap.open_pr(
            api, state(sha=PUSHED, superseded=(12,)), e1_accept=True, dryrun=ap.DryRun(result="green", run_id=7, reason=""),
            pin_body=PIN_BODY, check_body=lambda b: [], superseded=[pr(12, "0.34.10")],
        )
    assert not [w for w in api.writes() if w[0] == "PATCH" or w[1].endswith("/comments")]


def labels_of(api: Fake) -> list[str]:
    return [body["labels"] for method, path, body in api.calls if path.endswith("/labels")][0]


def test_e1_and_e2_green_leaves_only_the_auto_pin_label() -> None:
    api, number = opened(True, "green")
    assert number == 30 and labels_of(api) == ["auto-pin"]


@pytest.mark.parametrize(("e1", "e2"), [(False, "green"), (True, "red"), (False, "red")])
def test_anything_but_e1_and_e2_green_is_needs_human(e1: bool, e2: str) -> None:
    api, number = opened(e1, e2)
    assert number == 30 and labels_of(api) == ["auto-pin", "auto-pin-needs-human"]


def test_main_is_read_again_before_the_pr_opens() -> None:
    api = Fake(open_routes(main=NEW_MAIN))
    with pytest.raises(ap.MainMoved):
        ap.open_pr(api, state(sha=PUSHED), e1_accept=True, dryrun=ap.DryRun(result="green", run_id=7, reason=""), pin_body=PIN_BODY, check_body=lambda b: [])
    assert api.writes() == []


def test_a_hold_that_opened_during_the_run_stops_the_open() -> None:
    api = Fake(open_routes(holds=[{"number": 41}]))
    assert ap.open_pr(api, state(sha=PUSHED), e1_accept=True, dryrun=ap.DryRun(result="green", run_id=7, reason=""), pin_body=PIN_BODY, check_body=lambda b: []) is None
    assert api.writes() == []


def test_a_body_the_template_check_refuses_is_never_opened() -> None:
    api = Fake(open_routes())
    with pytest.raises(ap.Refused, match="template"):
        ap.open_pr(api, state(sha=PUSHED), e1_accept=True, dryrun=ap.DryRun(result="green", run_id=7, reason=""), pin_body=PIN_BODY, check_body=lambda b: ["missing section `## Risk`"])
    assert api.writes() == []


def test_the_body_keeps_the_template_one_bullet_and_carries_e2_and_the_marker() -> None:
    api, _ = opened(True, "green")
    created = [body for method, path, body in api.calls if (method, path) == ("POST", f"{R}/pulls")][0]
    body = created["body"]
    assert [l[3:] for l in body.splitlines() if l.startswith("## ")] == ["What", "Why", "Changelog", "Verification", "Risk"]
    changelog = [l for l in body.split("## Changelog\n", 1)[1].split("\n## ", 1)[0].splitlines() if l.strip()]
    assert len(changelog) == 1 and BULLET.match(changelog[0])
    assert "Not covered here" not in body and "E2 kind-yadgar dry run" in body and "#12" in body
    assert ap.parse_marker(body) == ap.Marker(sha=PUSHED, main=MAIN, target="0.34.11", e1="accept", e2="green", e2_run=7)
    assert created["head"] == NEW_BRANCH and created["base"] == "main"
    # The title is pin_bump's Changelog bullet, as the hand pins #136-#139 carry it.
    assert created["title"] == "fix: move the yadgar pin to parent chart 0.34.11, carrying gateway 0.12.29"


def test_holds_are_read_with_the_reader_token_when_one_is_given() -> None:
    """The release App token holds no `issues` scope; the job's GITHUB_TOKEN (issues: read) reads the holds."""
    api = Fake(open_routes())
    del api.routes[("GET", f"{R}/issues?labels=auto-pin-hold&state=open")]
    reader = Fake({("GET", f"{R}/issues?labels=auto-pin-hold&state=open"): [{"number": 41}]})
    result = ap.open_pr(
        api, state(sha=PUSHED), e1_accept=True, dryrun=ap.DryRun(result="green", run_id=7, reason=""), pin_body=PIN_BODY, check_body=lambda b: [], reader=reader
    )
    assert result is None and api.writes() == []


# ── the workflow: gated, never merges, never reads the payload ─────────────

WORKFLOW = REPOSITORY / ".github" / "workflows" / "auto-pin.yaml"


def test_the_workflow_is_gated_on_the_variable_and_never_merges() -> None:
    text = WORKFLOW.read_text()
    workflow = yaml.safe_load(text)
    assert workflow["permissions"] == {}
    triggers = workflow[True]
    assert triggers["repository_dispatch"] == {"types": ["chart-published"]}
    assert "schedule" in triggers and "workflow_dispatch" in triggers
    for name, job in workflow["jobs"].items():
        assert "vars.AUTO_PIN_ENABLED == 'true'" in job["if"], name
    assert "client_payload" not in text
    assert "/merge" not in text and "AUTO_PIN_MERGE" not in text.split("\njobs:", 1)[1]


def test_an_evidence_dispatch_is_never_evicted_by_the_cron() -> None:
    """A pending run is cancelled when a newer one queues in its group, cancel-in-progress or not. So the
    concurrency group is per job: the cron's `pin` group never holds an `evidence_only` dispatch."""
    workflow = yaml.safe_load(WORKFLOW.read_text())
    assert "concurrency" not in workflow
    pin, evidence = workflow["jobs"]["pin"]["concurrency"], workflow["jobs"]["evidence"]["concurrency"]
    assert pin == {"group": "auto-pin", "cancel-in-progress": False}
    assert "inputs.evidence_only" in evidence["group"] and evidence["group"] != "auto-pin" and evidence["cancel-in-progress"] is False


def test_the_gate_token_only_reads() -> None:
    steps = {s.get("id"): s for s in yaml.safe_load(WORKFLOW.read_text())["jobs"]["pin"]["steps"]}
    assert steps["verify1"]["with"]["permission-actions"] == "read"
    assert steps["verify2"]["with"]["permission-actions"] == "write"


# ── the gate's own reads ────────────────────────────────────────────────────


def gate_reads(epoch: ap.EpochState = GREEN, calls: list | None = None) -> ap.GateReads:
    clock = Clock()

    def read_epoch(sha: str) -> ap.EpochState:
        (calls if calls is not None else []).append(sha)
        return epoch

    return ap.GateReads(epoch=read_epoch, tags=lambda: TAGS, resolves=lambda v: True, now=clock.now, sleep=clock.sleep)


def gate_api() -> Fake:
    return Fake(
        {
            ("GET", f"{R}/branches/main"): {"commit": {"sha": MAIN}},
            ("GET", f"{R}/issues?labels=auto-pin-hold&state=open"): [],
            ("GET", f"{R}/pulls?state=all&sort=created&direction=desc"): [],
            ("GET", MATCHING): [],
        }
    )


def test_the_gate_opens_nothing_when_the_checkout_is_not_mains_head(tmp_path: Path) -> None:
    calls: list = []
    out = ap.run_gate(gate_api(), tree_with(tmp_path), gate_reads(calls=calls), regenerate=None, checkout=lambda _t: NEW_MAIN)
    assert out["action"] == "none" and "checkout" in out["reason"] and calls == []


def test_the_gate_opens_for_the_newest_release(tmp_path: Path) -> None:
    tree = tree_with(tmp_path)
    (tree / "applications/yadgar.yaml").write_text("spec:\n  source:\n    targetRevision: 0.34.8\n")
    out = ap.run_gate(gate_api(), tree, gate_reads(), regenerate=None, checkout=lambda _t: MAIN)
    assert out["action"] == "open" and out["target"] == "0.34.11" and out["main_sha"] == MAIN


# ── evidence for a human pin PR ─────────────────────────────────────────────

MOVES = "@@ -66,7 +66,7 @@\n-    targetRevision: 0.34.8\n+    targetRevision: 0.34.11\n"


def evidence_api(*, state_: str = "open", base: str = "main", repo: str = "yadgarhq/argocd", patch: str | None = MOVES) -> Fake:
    pull = {"state": state_, "base": {"ref": base}, "head": {"sha": FOREIGN, "repo": {"full_name": repo}}}
    entry = {"filename": "applications/yadgar.yaml"}
    if patch is not None:
        entry["patch"] = patch
    return Fake({("GET", f"{R}/pulls/50"): pull, ("GET", f"{R}/pulls/50/files"): [entry]})


def test_evidence_takes_an_open_same_repo_pin_pr_against_main() -> None:
    assert ap.evidence_target(evidence_api(), 50) == FOREIGN


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"state_": "closed"}, "not open"),
        ({"base": "release"}, "base"),
        ({"repo": "someone/argocd"}, "fork"),
        ({"patch": "@@ -1 +1 @@\n-# a\n+# b\n"}, "targetRevision"),
        # An added blank line just before an unchanged targetRevision context line.
        ({"patch": "@@ -66,2 +66,3 @@\n+\n     targetRevision: 0.34.8\n"}, "targetRevision"),
        # A removed line plus an added blank line before an unchanged context line: no `+` targetRevision at all.
        ({"patch": "@@ -68,2 +68,2 @@\n-    targetRevision: 0.34.7\n+\n     targetRevision: 0.34.8\n"}, "targetRevision"),
        # The same value rewritten is not a move.
        ({"patch": "@@ -69 +69 @@\n-    targetRevision: 0.34.8\n+    targetRevision: 0.34.8 \n"}, "targetRevision"),
        # The files API leaves `patch` out of a large diff: fail closed.
        ({"patch": None}, "no patch"),
    ],
)
def test_evidence_refuses_anything_but_a_pin_pr(kwargs: dict, match: str) -> None:
    with pytest.raises(ap.Refused, match=match):
        ap.evidence_target(evidence_api(**kwargs), 50)


def test_the_requirements_are_hash_pinned() -> None:
    lines = (REPOSITORY / "scripts" / "auto_pin.requirements.txt").read_text().splitlines()
    pins = [l for l in lines if re.match(r"^[a-z]", l)]
    assert {p.split("==")[0] for p in pins} >= {"pyyaml", "pytest"}
    assert all("--hash=sha256:" in "".join(lines[lines.index(p) + 1 : lines.index(p) + 2]) for p in pins)


def test_the_cli_answers_usage_with_2() -> None:
    finished = subprocess.run([sys.executable, str(REPOSITORY / "scripts" / "auto_pin.py"), "nope"], capture_output=True, text=True)
    assert finished.returncode == 2


def test_the_state_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    original = state(sha=PUSHED, superseded=(12, 13))
    ap.save_state(path, original)
    assert ap.load_state(path) == original
    assert json.loads(path.read_text())["target"] == "0.34.11"
