"""`scripts/pin_evidence.py`: CI's `pin-evidence` job, E1, E3 and E4 on a pin PR, E2 read as a status (ledger 1632 C3).

OFFLINE. The PR event is a fixture, the base and head `yadgar.yaml` are texts,
the renders, registry digests and module sources are stubs, and the status
reader is a stub. So this file runs in the `operator-applications` hook with
pytest and pyyaml only.

The plan's red cases for C3 (`plans/auto-deploy-parent-chart.md`, "C3"):

- a fixture PR event with no pin move passes, and judges nothing (fast);
- a pin move with an env change beside it fails;
- a pin move with a missing E2 status fails when E2 is required, and warns
  when it is not.

And the rules C3 carries:

- E1, E3 and E4 come from `pin_bump.check`, not a copy of it: an env entry
  added to a render, a moved `yadgar-*` crate tag, a moved `yadgar-store` tag
  and an env read whose key cannot be resolved each fail an automatic pin PR;
- the job installs exactly helm v3.18.4 and v4.3.0, each pinned by sha256,
  and refuses a tarball whose checksum differs;
- a NEEDS-HUMAN verdict fails an automatic PR (`auto/pin-<version>`)
  always, and fails a human one unless the PR commits an entry in
  `scripts/pin_needs_human_ack.json` for its target that names every reason
  (Max, 2026-10-30, after K3 was dropped in argocd#193). A downgrade (a
  revert) is refused on an automatic head and warned on a human one;
- the E2 status counts only when every `pin / kind-dryrun` on the head sha
  is `success`, created by `yadgarhq-settled-dispatch[bot]` (yaadgaar #422), and its description parses
  exactly as `auto_pin.DESCRIPTION` with check e2, result green, the head sha,
  the base as main and a run id (yaadgaar #420: no substring match, and a
  forged green after the bot's red cannot win).
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures"

# `scripts/` is not a package, so the modules are loaded by path.
for _name in ("pin_bump", "pin_evidence"):
    _spec = importlib.util.spec_from_file_location(_name, REPOSITORY / "scripts" / f"{_name}.py")
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_name] = _module
    _spec.loader.exec_module(_module)
pb = sys.modules["pin_bump"]
pe = sys.modules["pin_evidence"]

BASE = (REPOSITORY / "applications" / "yadgar.yaml").read_text()
PIN = pb.current_pin(BASE)
TARGET = "99.0.0"
HEAD_SHA = "b" * 40
HELMS = {"v3.18.4": "/helm3", "v4.3.0": "/helm4"}


READ_PLATFORM_TEXTS = pe.platform_texts


@pytest.fixture(autouse=True)
def no_platform_move(monkeypatch: pytest.MonkeyPatch) -> None:
    """`main` reads the six platform files from the checkout and HEAD^1; a test that drives `main` sees no platform
    move unless it says otherwise, whatever this repository's own last commit moved."""
    unmoved = {path: (REPOSITORY / path).read_text() for path in (*pb.OPERATOR_APPLICATIONS, pb.CHART_PIN)}
    monkeypatch.setattr(pe, "platform_texts", lambda repo: (dict(unmoved), dict(unmoved)))


def event(name: str) -> dict:
    return json.loads((FIXTURES / "pin_evidence" / name).read_text())


def moved(text: str = BASE, target: str = TARGET) -> str:
    return pb.rewrite_target_revision(text, target)[0]


BASE_SHA = "a" * 40


def green_status(sha: str = HEAD_SHA, main: str = BASE_SHA) -> dict:
    """What `auto_pin.post_e2` posts: `auto_pin.status_description`'s exact form."""
    return {
        "context": "pin / kind-dryrun",
        "state": "success",
        "creator": {"login": "yadgarhq-settled-dispatch[bot]"},
        "description": f"e2 green sha={sha} main={main} run=39420396928",
        "target_url": "https://github.com/yadgarhq/argocd-verify/actions/runs/39420396928",
    }


ACCEPT = {"accept": True, "reasons": [], "helms": sorted(HELMS), "changed": [], "boot_audit": {}, "migrations": {}}


def judge(
    pr_event: str, head: str, *, evidence=None, statuses=None, required=False, helms=None, base: str = BASE,
    changed=pb.MECHANICAL_FILES, differs=(), acks=None, base_acks=None, platform_texts=None,
):
    calls: list = []

    def evidence_of(head_text, pin, target, found):
        calls.append((pin, target, sorted(found)))
        return copy.deepcopy(evidence if evidence is not None else ACCEPT)

    outcome = pe.judge(
        pe.pr_of(event(pr_event)),
        base,
        head,
        evidence_of=evidence_of,
        statuses_of=lambda sha: [] if statuses is None else statuses,
        helms=HELMS if helms is None else helms,
        e2_required=required,
        changed=list(changed),
        regenerated_of=lambda target: list(differs),
        acks={} if acks is None else acks,
        base_acks={} if base_acks is None else base_acks,
        platform_texts=platform_texts,
    )
    return outcome, calls


# ── the plan's three red cases ──────────────────────────────────────────────


def test_a_pr_event_with_no_pin_move_passes_and_judges_nothing() -> None:
    outcome, calls = judge("event-human.json", BASE, helms={}, changed=["README.md"])
    assert outcome.passed, outcome.failures
    assert outcome.failures == [] and outcome.warnings == []
    assert any("no pin move" in n for n in outcome.notes), outcome.notes
    assert calls == [], "a PR that does not move targetRevision must render nothing"


def test_detect_writes_moved_false_for_no_pin_move(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pe, "texts", lambda repo, sha: (BASE, BASE))
    monkeypatch.setattr(pe, "changed_files", lambda repo: ["README.md"])
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event("event-human.json")))
    output = tmp_path / "output"
    assert pe.main(["detect", "--event", str(event_file), "--output", str(output)]) == 0
    assert output.read_text().splitlines() == ["moved=false"]


def test_detect_writes_moved_true_with_both_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pe, "texts", lambda repo, sha: (BASE, moved()))
    monkeypatch.setattr(pe, "changed_files", lambda repo: list(pb.MECHANICAL_FILES))
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event("event-human.json")))
    output = tmp_path / "output"
    assert pe.main(["detect", "--event", str(event_file), "--output", str(output)]) == 0
    assert output.read_text().splitlines() == ["moved=true", f"pin={PIN}", f"target={TARGET}"]



def fake_git(monkeypatch, parents: str) -> list:
    calls: list = []

    def run(command, **kwargs):
        calls.append(command[3:])
        out = parents if command[3] == "rev-list" else "BASE TEXT"
        return type("Finished", (), {"stdout": out})()

    monkeypatch.setattr(pe.subprocess, "run", run)
    return calls


def test_a_failing_git_is_an_error_line_and_exit_1(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(pe, "texts", lambda repo, sha: (BASE, BASE))

    def broken(repo):
        raise pe.subprocess.CalledProcessError(128, ["git", "diff"], stderr="fatal: bad revision 'HEAD^1'")

    monkeypatch.setattr(pe, "changed_files", broken)
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event("event-human.json")))
    assert pe.main(["detect", "--event", str(event_file), "--output", str(tmp_path / "output")]) == 1
    out = capsys.readouterr().out
    assert out.startswith("::error::") and "bad revision" in out, out


def test_texts_read_the_base_from_the_merge_commits_first_parent(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "applications").mkdir()
    (tmp_path / "applications" / "yadgar.yaml").write_text("HEAD TEXT")
    calls = fake_git(monkeypatch, f"{'m' * 40} {'a' * 40} {HEAD_SHA}\n")
    assert pe.texts(tmp_path, HEAD_SHA) == ("BASE TEXT", "HEAD TEXT")
    assert calls[-1] == ["show", f"{'a' * 40}:applications/yadgar.yaml"]


@pytest.mark.parametrize(
    "parents",
    [f"{'m' * 40} {'a' * 40}\n", f"{'m' * 40} {'a' * 40} {'c' * 40}\n"],
    ids=["not-a-merge-commit", "merge-of-another-head"],
)
def test_texts_refuse_a_checkout_that_is_not_this_prs_merge_commit(tmp_path: Path, monkeypatch, parents: str) -> None:
    fake_git(monkeypatch, parents)
    with pytest.raises(pe.Refused, match="merge commit"):
        pe.texts(tmp_path, HEAD_SHA)


# ── automatic heads (review of argocd#140) ──────────────────────────────────


def test_an_automatic_pr_that_moves_no_pin_fails() -> None:
    outcome, calls = judge("event-auto.json", BASE, helms={}, changed=["README.md"])
    assert not outcome.passed
    assert any("must move targetRevision" in f for f in outcome.failures), outcome.failures
    assert calls == []


@pytest.mark.parametrize(
    "changed",
    [
        [*pb.MECHANICAL_FILES, ".github/workflows/ci.yaml"],
        list(pb.MECHANICAL_FILES[:2]),
        [pb.APPLICATION, pb.CHART_PIN, "scripts/gates/test_no_two_owners.py"],
        [*pb.MECHANICAL_FILES, pb.SEED_ALLOWLIST_FILE],
    ],
    ids=["an-extra-file", "a-missing-file", "a-swapped-file", "the-seed-allowlist"],
)
def test_an_automatic_pin_pr_must_change_exactly_the_three_pin_files(changed: list) -> None:
    outcome, calls = judge("event-auto.json", moved(), statuses=[green_status()], changed=changed)
    assert not outcome.passed
    assert any("exactly" in f for f in outcome.failures), outcome.failures
    assert calls == []


def test_a_human_pin_pr_may_change_other_files() -> None:
    """C3 does not judge non-pin edits on a human head (two-owners and review do), the seed allowlist aside (ADR-1103)."""
    outcome, _ = judge("event-human.json", moved(), statuses=[green_status()], changed=[*pb.MECHANICAL_FILES, "MIGRATION_NOTES.md"])
    assert outcome.passed, outcome.failures


# ── ADR-1103: no pin PR edits the seed allowlist, human or automatic ───────


def test_a_human_pin_pr_that_edits_the_seed_allowlist_fails_before_any_render() -> None:
    """A hard failure, not a NEEDS-HUMAN reason: no acknowledgement clears it, and nothing is rendered."""
    changed = [*pb.MECHANICAL_FILES, pb.SEED_ALLOWLIST_FILE]
    outcome, calls = judge("event-human.json", moved(), statuses=[green_status()], changed=changed)
    assert outcome.failures == [
        f"a pin PR may not change {pb.SEED_ALLOWLIST_FILE}: allowlist changes land in their own non-pin PR (ADR-1103)"
    ], outcome.failures
    assert calls == []
    acked = {TARGET: {"target": TARGET, "reasons": outcome.failures, "why": "reviewed"}}
    again, _ = judge("event-human.json", moved(), statuses=[green_status()], changed=changed, acks=acked)
    assert again.failures == outcome.failures, "an acknowledgement does not clear it"


def test_a_human_pin_pr_without_the_seed_allowlist_is_unaffected() -> None:
    outcome, calls = judge("event-human.json", moved(), statuses=[green_status()], changed=list(pb.MECHANICAL_FILES))
    assert outcome.passed, outcome.failures
    assert not any("ADR-1103" in text for text in outcome.failures + outcome.warnings + outcome.notes)
    assert len(calls) == 1


def test_a_non_pin_pr_that_changes_only_the_seed_allowlist_passes() -> None:
    """The allowlist alone, with no targetRevision move, is the own non-pin PR ADR-1103 wants."""
    outcome, calls = judge("event-human.json", BASE, helms={}, changed=[pb.SEED_ALLOWLIST_FILE])
    assert outcome.passed, outcome.failures
    assert calls == []


@pytest.mark.parametrize(
    "other",
    ["MIGRATION_NOTES.md", pb.OPERATOR_APPLICATIONS[0], "scripts/migration_classifier.py", pb.CHART_PIN],
    ids=["docs", "an-operator-application", "the-classifier", "the-chart-pin"],
)
def test_detect_fails_a_non_pin_pr_that_changes_the_seed_allowlist_with_anything_else(
    other: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    """ADR-1103: allowlist changes land only in their OWN non-pin PR, so the allowlist alone, never beside a gate
    edit, a platform move, a chart pin or a note that moves no `targetRevision`."""
    monkeypatch.setattr(pe, "texts", lambda repo, sha: (BASE, BASE))
    monkeypatch.setattr(pe, "changed_files", lambda repo: sorted([pb.SEED_ALLOWLIST_FILE, other]))
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event("event-human.json")))
    output = tmp_path / "output"
    assert pe.main(["detect", "--event", str(event_file), "--output", str(output)]) == 1
    assert (
        f"a PR that changes {pb.SEED_ALLOWLIST_FILE} changes nothing else; it also changes {other} (ADR-1103)"
        in capsys.readouterr().out
    )
    assert not output.exists()


def test_judge_fails_a_non_pin_pr_that_changes_the_seed_allowlist_with_anything_else() -> None:
    """Defence in depth: `judge` applies the same rule, with the other paths listed in sorted order."""
    changed = [pb.SEED_ALLOWLIST_FILE, "scripts/pin_bump.py", "MIGRATION_NOTES.md"]
    outcome, calls = judge("event-human.json", BASE, helms={}, changed=changed)
    assert outcome.failures == [
        f"a PR that changes {pb.SEED_ALLOWLIST_FILE} changes nothing else; it also changes "
        "MIGRATION_NOTES.md, scripts/pin_bump.py (ADR-1103)"
    ], outcome.failures
    assert calls == []


def test_detect_fails_a_human_pin_pr_that_edits_the_seed_allowlist(tmp_path: Path, monkeypatch, capsys) -> None:
    """In `detect` already, so the job fails before it installs a helm."""
    monkeypatch.setattr(pe, "texts", lambda repo, sha: (BASE, moved()))
    monkeypatch.setattr(pe, "changed_files", lambda repo: [*pb.MECHANICAL_FILES, pb.SEED_ALLOWLIST_FILE])
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event("event-human.json")))
    output = tmp_path / "output"
    assert pe.main(["detect", "--event", str(event_file), "--output", str(output)]) == 1
    assert "ADR-1103" in capsys.readouterr().out
    assert not output.exists()


def test_detect_passes_a_non_pin_pr_that_changes_only_the_seed_allowlist(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pe, "texts", lambda repo, sha: (BASE, BASE))
    monkeypatch.setattr(pe, "changed_files", lambda repo: [pb.SEED_ALLOWLIST_FILE])
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event("event-human.json")))
    output = tmp_path / "output"
    assert pe.main(["detect", "--event", str(event_file), "--output", str(output)]) == 0
    assert output.read_text().splitlines() == ["moved=false"]


def test_detect_fails_an_automatic_pr_that_moves_no_pin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pe, "texts", lambda repo, sha: (BASE, BASE))
    monkeypatch.setattr(pe, "changed_files", lambda repo: ["README.md"])
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event("event-auto.json")))
    assert pe.main(["detect", "--event", str(event_file), "--output", str(tmp_path / "output")]) == 1


def test_a_bot_authored_hooks_bump_on_a_non_auto_branch_passes_as_human() -> None:
    """The release App also opens PRs that are no pin (argocd#135, `actions-adopt`): the branch decides, not the author."""
    pr = pe.pr_of(event("event-bot-hooks-bump.json"))
    assert pr.automatic is False
    outcome, calls = judge("event-bot-hooks-bump.json", BASE, helms={}, changed=[".pre-commit-config.yaml"])
    assert outcome.passed, outcome.failures
    assert calls == []


def test_detect_passes_a_bot_authored_hooks_bump(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pe, "texts", lambda repo, sha: (BASE, BASE))
    monkeypatch.setattr(pe, "changed_files", lambda repo: [".pre-commit-config.yaml"])
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event("event-bot-hooks-bump.json")))
    output = tmp_path / "output"
    assert pe.main(["detect", "--event", str(event_file), "--output", str(output)]) == 0
    assert output.read_text().splitlines() == ["moved=false"]


@pytest.mark.parametrize(
    "ref", ["auto/pin-99.0.0", "auto/pin-99.0.0-0123456789ab"], ids=["bare", "sha12-suffix"]
)
def test_an_auto_pin_branch_marks_a_human_authored_pr_automatic(ref: str) -> None:
    """C5 (argocd#141) names the bot branch `auto/pin-<v>-<sha12>`; the bare form stays automatic too."""
    pr_event = event("event-human.json")
    pr_event["pull_request"]["head"]["ref"] = ref
    assert pe.pr_of(pr_event).automatic is True


@pytest.mark.parametrize(
    "ref", ["auto/pin-99.0.0-0123456789a", "auto/pin-99.0.0-0123456789AB", "auto/pin-99.0.0-x", "auto/pin-99.0"]
)
def test_a_branch_near_the_auto_pin_shape_is_not_automatic(ref: str) -> None:
    pr_event = event("event-human.json")
    pr_event["pull_request"]["head"]["ref"] = ref
    assert pe.pr_of(pr_event).automatic is False


def test_the_suffixed_auto_fixture_fails_needs_human() -> None:
    outcome, _ = judge("event-auto-suffixed.json", moved(), evidence=NEEDS_HUMAN, statuses=[green_status()])
    assert not outcome.passed
    assert any("NEEDS-HUMAN" in f for f in outcome.failures), outcome.failures


def test_another_bot_is_not_the_pin_identity() -> None:
    """dependabot is `type: Bot` too; only the release App's login makes a PR automatic."""
    other = event("event-human.json")
    other["pull_request"]["user"] = {"login": "dependabot[bot]", "type": "Bot"}
    assert pe.pr_of(other).automatic is False


def test_a_label_does_not_make_a_pr_automatic() -> None:
    labelled = event("event-human.json")
    labelled["pull_request"]["labels"] = [{"name": "auto-pin"}]
    assert pe.pr_of(labelled).automatic is False


# ── a duplicate targetRevision key (review of argocd#140, HIGH) ─────────────

LINE = f"    targetRevision: {PIN}\n"


@pytest.mark.parametrize(
    "second", ["    targetRevision : 0.40.0\n", '    "targetRevision": 0.40.0\n'], ids=["spaced-key", "quoted-key"]
)
@pytest.mark.parametrize("side", ["head", "base"])
def test_a_duplicate_target_revision_the_line_regex_misses_is_refused(second: str, side: str) -> None:
    """PyYAML lets the last duplicate key win; the line regex sees only the first. They must agree."""
    assert BASE.count(LINE) == 1
    forged = BASE.replace(LINE, LINE + second, 1)
    assert pb.current_pin(forged) == PIN and pb.parsed_revision(forged) == "0.40.0"
    base, head = (BASE, forged) if side == "head" else (forged, BASE)
    with pytest.raises(pe.Refused, match="0.40.0"):
        pe.move_of(base, head)
    outcome, calls = judge("event-human.json", head, base=base)
    assert not outcome.passed and calls == [], outcome.failures


def test_detect_refuses_a_duplicate_target_revision(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    forged = BASE.replace(LINE, LINE + "    targetRevision : 0.40.0\n", 1)
    monkeypatch.setattr(pe, "texts", lambda repo, sha: (BASE, forged))
    monkeypatch.setattr(pe, "changed_files", lambda repo: [pb.APPLICATION])
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event("event-human.json")))
    output = tmp_path / "output"
    assert pe.main(["detect", "--event", str(event_file), "--output", str(output)]) == 1
    assert not output.exists() or "moved=false" not in output.read_text()


ENV_CHANGE = (
    "          autoscaling: { enabled: true }\n          sourceAddress",
    "          autoscaling: { enabled: true }\n          extraEnv: [{ name: NEW_KEY, value: x }]\n          sourceAddress",
)


@pytest.mark.parametrize("pr_event", ["event-human.json", "event-auto.json"])
def test_a_pin_move_with_an_env_change_beside_it_fails(pr_event: str) -> None:
    assert ENV_CHANGE[0] in BASE, "the fixture edit no longer matches applications/yadgar.yaml"
    head = moved(BASE.replace(*ENV_CHANGE, 1))
    outcome, calls = judge(pr_event, head, statuses=[green_status()])
    assert not outcome.passed
    assert any("not a pin-only change" in f for f in outcome.failures), outcome.failures
    assert calls == [], "a values change is refused before any render"


def test_a_pin_move_with_a_missing_e2_status_fails_when_required() -> None:
    outcome, _ = judge("event-auto.json", moved(), statuses=[], required=True)
    assert not outcome.passed
    assert any("pin / kind-dryrun" in f for f in outcome.failures), outcome.failures


def test_a_pin_move_with_a_missing_e2_status_warns_when_not_required() -> None:
    outcome, _ = judge("event-human.json", moved(), statuses=[], required=False)
    assert outcome.passed, outcome.failures
    assert any("pin / kind-dryrun" in w for w in outcome.warnings), outcome.warnings


@pytest.mark.parametrize("pr_event", ["event-auto.json", "event-auto-suffixed.json"])
def test_an_automatic_pin_pr_requires_e2_even_in_report_mode(pr_event: str) -> None:
    """C5 posts `pin / kind-dryrun` before it opens the PR, so on an automatic head the status already exists."""
    outcome, _ = judge(pr_event, moved(), statuses=[], required=False)
    assert not outcome.passed
    assert any("pin / kind-dryrun" in f for f in outcome.failures), outcome.failures


def test_e2_ships_in_report_mode() -> None:
    """Nothing posts `pin / kind-dryrun` yet (C5's auto-pin.yaml does), so requiring it would block every pin PR."""
    assert pe.E2_REQUIRED is False


def test_a_green_pin_with_its_e2_status_passes_when_required() -> None:
    outcome, calls = judge("event-auto.json", moved(), statuses=[green_status()], required=True)
    assert outcome.passed, outcome.failures
    assert outcome.warnings == []
    assert calls == [(PIN, TARGET, ["v3.18.4", "v4.3.0"])]


# ── E2: which status counts ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"state": "pending"}, "pending"),
        ({"state": "failure"}, "failure"),
        ({"creator": {"login": "m-agahi"}}, "m-agahi"),
        # YAAGAAR #422: the job GITHUB_TOKEN of ANY argocd workflow posts as github-actions[bot].
        ({"creator": {"login": "github-actions[bot]"}}, "github-actions[bot], not yadgarhq-settled-dispatch[bot]"),
        # The release App opens the PR; it never posts E2 (its key is an org secret, readable far beyond argocd).
        ({"creator": {"login": "yadgarhq-bot[bot]"}}, "yadgarhq-bot[bot], not yadgarhq-settled-dispatch[bot]"),
        ({"description": "green, argocd-verify run 1"}, "not the form"),
        # YAAGAAR #420: the sha only as a substring of a longer text is not the sha judged.
        ({"description": f"green for {HEAD_SHA}, argocd-verify run 39420396928"}, "not the form"),
        ({"description": f"e2 green sha={HEAD_SHA}0 main={BASE_SHA} run=1"}, "not the form"),
        ({"description": f"e2 green sha={'c' * 40} main={BASE_SHA} run=1 {HEAD_SHA}"}, "not the form"),
        ({"description": f"e2 green sha={'c' * 40} main={BASE_SHA} run=1"}, "judged sha"),
        # YAAGAAR #420: judged against another main than the base this merge commit renders.
        ({"description": f"e2 green sha={HEAD_SHA} main={'c' * 40} run=1"}, "judged against main"),
        ({"description": f"e1 accept sha={HEAD_SHA} main={BASE_SHA}"}, "check e1"),
        ({"description": f"e2 red sha={HEAD_SHA} main={BASE_SHA} run=1"}, "result red"),
        ({"description": f"e2 green sha={HEAD_SHA} main={BASE_SHA}"}, "no run"),
    ],
    ids=[
        "pending", "failure", "wrong-creator", "github-actions-creator", "release-app-creator", "free-text", "sha-substring", "sha-with-suffix", "sha-trailing",
        "other-sha", "wrong-main", "wrong-check", "red-result", "no-run",
    ],
)
def test_an_e2_status_that_is_not_a_green_bot_status_for_this_sha_fails_when_required(change: dict, expected: str) -> None:
    outcome, _ = judge("event-auto.json", moved(), statuses=[{**green_status(), **change}], required=True)
    assert not outcome.passed
    assert any(expected in f for f in outcome.failures), outcome.failures


def test_the_e2_creator_is_the_settled_dispatch_app_that_posts_it() -> None:
    """YAAGAAR #422: auto-pin posts E2 with a settled-dispatch App token, so the creator pin-evidence trusts is
    that App, the same login auto_pin's back-off and auto_pin_merge's predicate read. Not the PR author's App."""
    assert pe.E2_CREATOR == "yadgarhq-settled-dispatch[bot]" == pe.auto_pin.STATUS_LOGIN == pe.auto_pin.DISPATCH_LOGIN
    assert pe.E2_CREATOR != pe.auto_pin.PIN_LOGIN


def test_a_red_after_a_green_is_red() -> None:
    """GitHub lists a sha's statuses newest first: a red after a green is red."""
    statuses = [{**green_status(), "state": "failure"}, green_status()]
    outcome, _ = judge("event-auto.json", moved(), statuses=statuses, required=True)
    assert not outcome.passed


def test_a_forged_green_after_a_red_is_refused() -> None:
    """YAAGAAR #420: a second status, whatever its creator, cannot outvote a red.

    So the newest status cannot decide alone: a green posted after the bot's
    red would turn E2 green. EVERY `pin / kind-dryrun` on the sha must be a
    green for this head and this base, so a forgery can only make E2 red.
    """
    statuses = [green_status(), {**green_status(), "state": "failure", "description": f"e2 red sha={HEAD_SHA} main={BASE_SHA} run=1"}]
    outcome, _ = judge("event-auto.json", moved(), statuses=statuses, required=True)
    assert not outcome.passed
    assert any("failure" in f for f in outcome.failures), outcome.failures


def test_two_agreeing_greens_pass() -> None:
    outcome, _ = judge("event-auto.json", moved(), statuses=[green_status(), green_status()], required=True)
    assert outcome.passed, outcome.failures


def test_main_sets_the_base_to_the_merge_commits_first_parent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The base E2 must name is the one this merge commit renders (HEAD^1), not the event's."""
    seen: list = []
    monkeypatch.setattr(pe, "texts", lambda repo, sha: (BASE, moved()))
    monkeypatch.setattr(pe, "changed_files", lambda repo: list(pb.MECHANICAL_FILES))
    monkeypatch.setattr(pe, "merge_parent", lambda repo: "c" * 40)
    monkeypatch.setattr(pe, "judge", lambda pr, *a, **k: seen.append((pr.base_sha, callable(k.get("regenerated_of")))) or pe.Outcome())
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event("event-auto.json")))
    assert pe.main(["judge", "--event", str(event_file)]) == 0
    assert seen == [("c" * 40, True)], "main binds the regenerated files (yaadgaar #424 item 3)"


def test_main_reads_the_acknowledgements_from_the_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ack file is read from the merge checkout (`--repo`), so the PR that commits it is the PR it clears."""
    seen: list = []
    monkeypatch.setattr(pe, "texts", lambda repo, sha: (BASE, moved()))
    monkeypatch.setattr(pe, "changed_files", lambda repo: list(pb.MECHANICAL_FILES))
    monkeypatch.setattr(pe, "merge_parent", lambda repo: "c" * 40)
    monkeypatch.setattr(pe, "base_ack_text", lambda repo: json.dumps([ack(why="an older review")[TARGET]]))
    monkeypatch.setattr(pe, "judge", lambda pr, *a, **k: seen.append((k.get("acks"), k.get("base_acks"))) or pe.Outcome())
    checkout = tmp_path / "checkout"
    (checkout / pe.ACK_FILE).parent.mkdir(parents=True)
    (checkout / pe.ACK_FILE).write_text(json.dumps([ack()[TARGET]]))
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event("event-human.json")))
    assert pe.main(["judge", "--event", str(event_file), "--repo", str(checkout)]) == 0
    assert seen == [(ack(), ack(why="an older review"))], "main reads the head ack and the base's copy (HEAD^1)"
    (checkout / pe.ACK_FILE).write_text("[{}]")
    assert pe.main(["judge", "--event", str(event_file), "--repo", str(checkout)]) == 1, "a malformed ack file fails the job"


# ── the three mechanical files, regenerated (yaadgaar #424 item 3) ──────────


def test_an_automatic_pin_whose_chart_pin_or_table_is_not_the_regenerated_one_fails() -> None:
    """E1 compared only `yadgar.yaml`. `chart_pin.json` and the K3 table must be what `pin_bump.write_files` makes
    from the base for this target, byte for byte: anything else is a hand edit under an automatic head."""
    for differs in ([pb.CHART_PIN], [pb.TABLE], [pb.CHART_PIN, pb.TABLE]):
        outcome, _ = judge("event-auto.json", moved(), statuses=[green_status()], differs=differs)
        assert not outcome.passed
        assert all(any(path in failure for failure in outcome.failures) for path in differs), outcome.failures


def test_a_human_pin_whose_files_differ_from_the_regenerated_ones_warns() -> None:
    outcome, _ = judge("event-human.json", moved(), statuses=[green_status()], differs=[pb.TABLE])
    assert outcome.passed and any(pb.TABLE in warning for warning in outcome.warnings)


def test_the_regenerated_files_pass_when_they_match() -> None:
    outcome, _ = judge("event-auto.json", moved(), statuses=[green_status()])
    assert outcome.passed, outcome.failures
    assert any("regenerated" in note for note in outcome.notes)


def test_a_regeneration_that_fails_fails_an_automatic_pin() -> None:
    def broken(target):
        raise pb.Refused("scripts/gates/test_yadgar_application.py --write exited 1")

    outcome = pe.judge(
        pe.pr_of(event("event-auto.json")), BASE, moved(), evidence_of=lambda *a: copy.deepcopy(ACCEPT),
        statuses_of=lambda sha: [green_status()], helms=HELMS, changed=list(pb.MECHANICAL_FILES), regenerated_of=broken,
    )
    assert not outcome.passed and any("--write exited 1" in failure for failure in outcome.failures)


def test_regenerated_differences_seeds_the_base_and_compares_with_the_head(tmp_path: Path) -> None:
    """The tree is the BASE's three files plus the merge checkout's two writers; `write_files` runs on it for the
    target, and every file that differs from the head (the checkout) is named."""
    head = tmp_path / "head"
    for path, text in ((pb.APPLICATION, "app@target"), (pb.CHART_PIN, "pin@target"), (pb.TABLE, "hand-edited table")):
        (head / path).parent.mkdir(parents=True, exist_ok=True)
        (head / path).write_text(text)
    # No platform move: the five operator Applications are the base's at the head too (car C10 compares them).
    for path in pb.OPERATOR_APPLICATIONS:
        (head / path).write_text(f"base {path}")
    for writer in pe.WRITERS:
        (head / "scripts" / "gates" / writer).write_text(f"# {writer}")
    seen: dict = {}

    def write(tree: Path, target: str) -> None:
        seen["base"] = {path: (tree / path).read_text() for path in pb.MECHANICAL_FILES}
        seen["writers"] = sorted(p.name for p in (tree / "scripts" / "gates").glob("*.py"))
        for path, text in ((pb.APPLICATION, "app@target"), (pb.CHART_PIN, "pin@target"), (pb.TABLE, "table@target")):
            (tree / path).write_text(text)

    differs = pe.regenerated_differences(head, TARGET, base_of=lambda path: f"base {path}".encode(), write=write)
    assert differs == [pb.TABLE]
    assert seen["base"] == {path: f"base {path}" for path in pb.MECHANICAL_FILES}
    assert seen["writers"] == sorted(pe.WRITERS)


@pytest.mark.parametrize("resolved, differs", [("sha256:" + "1" * 64, []), ("sha256:" + "2" * 64, [pb.CHART_PIN])])
def test_the_regenerated_chart_pin_records_the_digest_the_tag_resolves_to_now(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resolved: str, differs: list[str]
) -> None:
    """Yaadgaar 250: the real `write_files` re-resolves the target's digest, so a head whose `chart_digest` is not
    what the tag resolves to at E1 time is not the regenerated pin. Only the K3 writer is stubbed."""
    base_pin = (REPOSITORY / pb.CHART_PIN).read_text()
    head = tmp_path / "head"
    head_files = {
        pb.APPLICATION: moved(),
        pb.CHART_PIN: pb.rewrite_chart_pin(base_pin, TARGET, digest="sha256:" + "1" * 64),
        pb.TABLE: "table@target\n",
        # No platform move: the five operator Applications are the base's at the head too (car C10 compares them).
        **{path: (REPOSITORY / path).read_text() for path in pb.OPERATOR_APPLICATIONS},
    }
    for path, text in head_files.items():
        (head / path).parent.mkdir(parents=True, exist_ok=True)
        (head / path).write_text(text)
    for writer in pe.WRITERS:
        (head / "scripts" / "gates" / writer).write_text(f"# {writer}")

    class Finished:
        returncode = 0
        stderr = ""

    def writer(command, **_):
        Path(command[1]).parents[2].joinpath(pb.TABLE).write_text("table@target\n")
        return Finished()

    monkeypatch.setattr(pb, "writer_helm", lambda: "/bin/true")
    monkeypatch.setattr(pb.subprocess, "run", writer)
    monkeypatch.setattr(pb, "chart_digest", lambda version: resolved)
    monkeypatch.setattr(pb, "target_platform", lambda version: json.loads(base_pin)["platform_version"])
    base = {pb.APPLICATION: BASE, pb.CHART_PIN: base_pin, pb.TABLE: "table@base\n"}
    base |= {path: (REPOSITORY / path).read_text() for path in pb.OPERATOR_APPLICATIONS}
    assert pe.regenerated_differences(head, TARGET, base_of=lambda path: base[path].encode()) == differs


@pytest.mark.parametrize("behind", [None, *pb.OPERATOR_APPLICATIONS])
def test_the_regenerated_platform_move_binds_the_digest_and_all_eight_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, behind: str | None
) -> None:
    """Car C10 on top of yaadgaar 250: a platform move regenerates `chart_tag`, `chart_digest` and `platform_version`
    together and the five operator files; a head that left one operator file behind is named. Only the K3 writer,
    the registry and the target's platform are stubbed."""
    base_pin = (REPOSITORY / pb.CHART_PIN).read_text()
    old_platform, new_platform, digest = json.loads(base_pin)["platform_version"], "9.9.9", "sha256:" + "1" * 64
    operators = {path: (REPOSITORY / path).read_text() for path in pb.OPERATOR_APPLICATIONS}
    head_files = {
        pb.APPLICATION: moved(),
        pb.CHART_PIN: pb.rewrite_chart_pin(base_pin, TARGET, digest=digest, platform=new_platform),
        pb.TABLE: "table@target\n",
    }
    for path, text in operators.items():
        moved_text = text.replace(f"targetRevision: {old_platform}\n", f"targetRevision: {new_platform}\n")
        head_files[path] = text if path == behind else moved_text
    for path, text in head_files.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(text)
    for writer in pe.WRITERS:
        (tmp_path / "scripts" / "gates" / writer).write_text(f"# {writer}")

    class Finished:
        returncode = 0
        stderr = ""

    def writer(command, **_):
        Path(command[1]).parents[2].joinpath(pb.TABLE).write_text("table@target\n")
        return Finished()

    monkeypatch.setattr(pb, "writer_helm", lambda: "/bin/true")
    monkeypatch.setattr(pb.subprocess, "run", writer)
    monkeypatch.setattr(pb, "chart_digest", lambda version: digest)
    monkeypatch.setattr(pb, "target_platform", lambda version: new_platform)
    base = {pb.APPLICATION: BASE, pb.CHART_PIN: base_pin, pb.TABLE: "table@base\n", **operators}
    assert pe.regenerated_differences(tmp_path, TARGET, base_of=lambda path: base[path].encode()) == ([behind] if behind else [])


def test_other_contexts_are_not_the_e2_status() -> None:
    other = {**green_status(), "context": "ci / passed"}
    outcome, _ = judge("event-auto.json", moved(), statuses=[other], required=True)
    assert not outcome.passed


def test_an_unreadable_status_api_warns_in_report_mode_and_fails_when_required() -> None:
    def broken(sha):
        raise OSError("HTTP 502")

    for required, passed in ((False, True), (True, False)):
        outcome = pe.judge(
            pe.pr_of(event("event-human.json")),
            BASE,
            moved(),
            evidence_of=lambda *a: copy.deepcopy(ACCEPT),
            statuses_of=broken,
            helms=HELMS,
            e2_required=required,
            changed=list(pb.MECHANICAL_FILES),
            regenerated_of=lambda target: [],
        )
        assert outcome.passed is passed, (required, outcome.failures)


# ── NEEDS-HUMAN, automatic against human ────────────────────────────────────

NEEDS_HUMAN = {**ACCEPT, "accept": False, "reasons": ["task-db: migration(s) 7 added; interim block"]}


def test_a_needs_human_verdict_fails_an_automatic_pin_pr() -> None:
    outcome, _ = judge("event-auto.json", moved(), evidence=NEEDS_HUMAN, statuses=[green_status()])
    assert not outcome.passed
    assert any("migration(s) 7" in f for f in outcome.failures), outcome.failures


def test_an_unacknowledged_needs_human_verdict_fails_a_human_pin_pr() -> None:
    """Max, 2026-10-30: with K3 gone (argocd#193), a hand pin PR's NEEDS-HUMAN had no hard red. It fails now."""
    outcome, _ = judge("event-human.json", moved(), evidence=NEEDS_HUMAN, statuses=[green_status()])
    assert not outcome.passed
    assert any("NEEDS-HUMAN: task-db: migration(s) 7 added" in f for f in outcome.failures), outcome.failures
    told = "\n".join(outcome.failures)
    assert pe.ACK_FILE in told and "NEEDS-HUMAN-UNACKNOWLEDGED" in told, told
    # The failure carries the exact entry to commit: this target, these reasons.
    entry = json.loads(told[told.index("{"):told.rindex("}") + 1])
    assert entry["target"] == TARGET and entry["reasons"] == NEEDS_HUMAN["reasons"], entry


def ack(target: str = TARGET, reasons=None, why: str = "task-db migration 7 read by hand; additive") -> dict:
    return {target: {"target": target, "reasons": list(NEEDS_HUMAN["reasons"] if reasons is None else reasons), "why": why}}


def test_a_committed_acknowledgement_of_every_reason_clears_a_human_pin_pr() -> None:
    """argocd#139's shape: a human pin PR that commits the reviewed ack for this target passes; the reasons stay visible."""
    outcome, _ = judge("event-human.json", moved(), evidence=NEEDS_HUMAN, statuses=[green_status()], acks=ack())
    assert outcome.passed, outcome.failures
    assert any("migration(s) 7" in w for w in outcome.warnings), outcome.warnings


@pytest.mark.parametrize(
    "acks",
    [
        pytest.param(ack(target="98.0.0"), id="another-target"),
        pytest.param(ack(reasons=[]), id="no-reason"),
        pytest.param(ack(reasons=["task-db: migration(s) 8 added; interim block"]), id="another-reason"),
    ],
)
def test_an_acknowledgement_that_misses_a_reason_or_the_target_fails(acks: dict) -> None:
    two = {**NEEDS_HUMAN, "reasons": [*NEEDS_HUMAN["reasons"], "gateway: field spec.x changed"]}
    for evidence in (NEEDS_HUMAN, two):
        outcome, _ = judge("event-human.json", moved(), evidence=evidence, statuses=[green_status()], acks=acks)
        assert not outcome.passed, (acks, evidence)
        assert any("NEEDS-HUMAN-UNACKNOWLEDGED" in f for f in outcome.failures), outcome.failures


def test_an_acknowledgement_of_one_reason_of_two_fails_on_the_other() -> None:
    two = {**NEEDS_HUMAN, "reasons": [*NEEDS_HUMAN["reasons"], "gateway: field spec.x changed"]}
    outcome, _ = judge("event-human.json", moved(), evidence=two, statuses=[green_status()], acks=ack())
    assert not outcome.passed
    unacked = [f for f in outcome.failures if f.startswith("NEEDS-HUMAN: ")]
    assert unacked == ["NEEDS-HUMAN: gateway: field spec.x changed"], outcome.failures


def test_an_acknowledgement_never_clears_an_automatic_pin_pr() -> None:
    outcome, _ = judge("event-auto.json", moved(), evidence=NEEDS_HUMAN, statuses=[green_status()], acks=ack())
    assert not outcome.passed
    assert any("migration(s) 7" in f for f in outcome.failures), outcome.failures


def test_an_acknowledgement_already_on_the_base_does_not_clear_a_later_pin_pr() -> None:
    """Review of argocd#200: an ack keyed by target alone, already on main, would clear a re-pin to the same
    target (after a revert, or after an ack-only PR merged first). The clearing entry must be new in this PR."""
    outcome, _ = judge(
        "event-human.json", moved(), evidence=NEEDS_HUMAN, statuses=[green_status()], acks=ack(), base_acks=ack()
    )
    assert not outcome.passed
    told = "\n".join(outcome.failures)
    assert "NEEDS-HUMAN-UNACKNOWLEDGED" in told and "already on the base" in told, told
    # ADR-1094: E2 (`pin_dryrun`) refuses that rewrite, so the failure says where the dry run goes.
    assert "E2 (pin / kind-dryrun) refuses the rewrite" in told and "run the hand dry run" in told, told


def test_an_entry_this_pr_rewrites_from_the_base_clears_it() -> None:
    outcome, _ = judge(
        "event-human.json", moved(), evidence=NEEDS_HUMAN, statuses=[green_status()],
        acks=ack(why="re-read for this re-pin"), base_acks=ack(),
    )
    assert outcome.passed, outcome.failures


def test_a_partial_entry_for_the_target_says_to_replace_it() -> None:
    two = {**NEEDS_HUMAN, "reasons": [*NEEDS_HUMAN["reasons"], "gateway: field spec.x changed"]}
    outcome, _ = judge("event-human.json", moved(), evidence=two, statuses=[green_status()], acks=ack())
    told = "\n".join(outcome.failures)
    assert "replace the existing entry" in told, told


def test_the_base_copy_of_the_acknowledgement_file_is_read_from_the_first_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []

    def run_with(tree: str):
        def run(command, **kwargs):
            calls.append(command[3:])
            out = tree if command[3] == "ls-tree" else "[]\n"
            return type("Finished", (), {"stdout": out})()

        return run

    monkeypatch.setattr(pe.subprocess, "run", run_with(""))
    assert pe.base_ack_text(Path("/repo")) is None, "a base without the file has no acknowledgement"
    assert calls == [["ls-tree", "HEAD^1", "--", pe.ACK_FILE]]
    calls.clear()
    monkeypatch.setattr(pe.subprocess, "run", run_with(f"100644 blob {'d' * 40}\t{pe.ACK_FILE}\n"))
    assert pe.base_ack_text(Path("/repo")) == "[]\n"
    assert calls == [["ls-tree", "HEAD^1", "--", pe.ACK_FILE], ["show", f"HEAD^1:{pe.ACK_FILE}"]]


def test_the_acknowledgement_file_is_read_by_target(tmp_path: Path) -> None:
    path = tmp_path / "ack.json"
    assert pe.acknowledgements(path) == {}, "no file is no acknowledgement"
    entry = ack()[TARGET]
    path.write_text(json.dumps([entry]))
    assert pe.acknowledgements(path) == {TARGET: entry}


@pytest.mark.parametrize(
    "entries",
    [
        pytest.param("not json", id="not-json"),
        pytest.param({"target": TARGET}, id="not-a-list"),
        pytest.param([{**ack()[TARGET], "why": ""}], id="empty-why"),
        pytest.param([{**ack()[TARGET], "why": pe.ACK_WHY_PLACEHOLDER}], id="placeholder-why"),
        pytest.param([{**ack()[TARGET], "why": ["not", "text"]}], id="why-not-a-string"),
        pytest.param([{**ack()[TARGET], "reasons": []}], id="empty-reasons"),
        pytest.param([{**ack()[TARGET], "reasons": "one"}], id="reasons-not-a-list"),
        pytest.param([{k: v for k, v in ack()[TARGET].items() if k != "target"}], id="no-target"),
        pytest.param([ack()[TARGET], ack()[TARGET]], id="duplicate-target"),
    ],
)
def test_a_malformed_acknowledgement_file_is_refused(tmp_path: Path, entries) -> None:
    path = tmp_path / "ack.json"
    path.write_text(entries if isinstance(entries, str) else json.dumps(entries))
    with pytest.raises(pe.Refused):
        pe.acknowledgements(path)


def test_an_unreadable_acknowledgement_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "ack.json"
    path.mkdir()
    with pytest.raises(pe.Refused):
        pe.acknowledgements(path)


def test_the_committed_acknowledgement_file_parses() -> None:
    pe.acknowledgements(REPOSITORY / pe.ACK_FILE)


def test_an_auto_branch_from_a_fork_is_not_automatic() -> None:
    forked = event("event-auto.json")
    forked["pull_request"]["head"]["repo"]["full_name"] = "someone/argocd"
    assert pe.pr_of(forked).automatic is False


def test_a_downgrade_is_refused_on_an_automatic_head_and_warned_on_a_human_one() -> None:
    lower = "0.0.1"
    for name, passed in (("event-auto.json", False), ("event-human.json", True)):
        outcome, calls = judge(name, moved(target=lower))
        assert outcome.passed is passed, (name, outcome.failures)
        assert calls == [], "a downgrade is not judged by the forward classifier"
        assert any("lower" in m for m in outcome.failures + outcome.warnings)


def test_a_prerelease_target_fails_on_any_head() -> None:
    for name in ("event-auto.json", "event-human.json"):
        outcome, _ = judge(name, moved(target="99.0.0-rc.1"))
        assert not outcome.passed


# ── the helms ───────────────────────────────────────────────────────────────


def test_both_helms_must_be_present() -> None:
    for helms in ({"v3.18.4": "/helm3"}, {"v4.3.0": "/helm4"}, {"v3.18.4": "/a", "v3.19.0": "/b"}):
        outcome, calls = judge("event-human.json", moved(), helms=helms)
        assert not outcome.passed, helms
        assert calls == []
        assert any("v4.3.0" in f or "v3.18.4" in f for f in outcome.failures), outcome.failures


def test_only_the_two_pinned_helms_render() -> None:
    _, calls = judge("event-human.json", moved(), helms={**HELMS, "v3.19.0": "/other"})
    assert calls == [(PIN, TARGET, ["v3.18.4", "v4.3.0"])]


def test_the_helms_are_pinned_by_version_and_sha256() -> None:
    assert pe.HELMS == {
        "v3.18.4": "f8180838c23d7c7d797b208861fecb591d9ce1690d8704ed1e4cb8e2add966c1",
        "v4.3.0": "86584a54def73570558f66f5111cc53dfed56689637ae32c1201205d494f54fb",
    }


def fake_tarball(version: str) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, body in (("linux-amd64/helm", b"#!/bin/sh\necho " + version.encode() + b"\n"), ("linux-amd64/LICENSE", b"x")):
            info = tarfile.TarInfo(name)
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
    return buffer.getvalue()


def test_install_extracts_each_helm_when_its_checksum_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blobs = {v: fake_tarball(v) for v in pe.HELMS}
    monkeypatch.setattr(pe, "HELMS", {v: hashlib.sha256(b).hexdigest() for v, b in blobs.items()})
    installed = pe.install_helms(tmp_path, fetch=lambda version: blobs[version])
    assert sorted(installed) == sorted(pe.HELMS)
    for version, binary in installed.items():
        assert Path(binary).read_bytes().endswith(version.encode() + b"\n")
        assert Path(binary).stat().st_mode & 0o111


def test_install_refuses_a_tarball_whose_checksum_differs(tmp_path: Path) -> None:
    with pytest.raises(pe.Refused, match="sha256"):
        pe.install_helms(tmp_path, fetch=lambda version: fake_tarball(version))
    assert not any(tmp_path.rglob("helm")), "nothing is extracted from a tarball that failed its checksum"


# ── E1, E3, E4 through pin_bump.check, with the fail-closed rules ───────────

GATEWAY_DIGEST = {"0.12.10": "sha256:" + "1" * 64, "0.12.11": "sha256:" + "2" * 64}


def chart(gateway: str, task_db: str = "0.9.8") -> str:
    dependencies = [
        {"name": "gateway", "version": gateway},
        {"name": "task-db", "version": task_db},
        {"name": "platform", "version": "0.2.7"},
    ]
    return yaml.safe_dump({"apiVersion": "v2", "name": "yadgar", "dependencies": dependencies})


def tree(name: str) -> dict[str, str]:
    root = FIXTURES / "pin_bump" / name
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*.rs"))}


def with_cargo(source: dict, lifecycle: str, store: str | None = None) -> dict:
    lines = ["[package]", 'name = "m"', "", "[dependencies]"]
    lines.append(f'yadgar-lifecycle = {{ git = "https://github.com/yadgarhq/lifecycle.git", tag = "{lifecycle}" }}')
    lock = ["version = 4", "", "[[package]]", 'name = "yadgar-lifecycle"', 'version = "0.2.0"']
    lock += [f'source = "git+https://github.com/yadgarhq/lifecycle.git?tag={lifecycle}#{"0" * 40}"', ""]
    if store:
        lines.append(f'yadgar-store = {{ git = "https://github.com/yadgarhq/store.git", tag = "{store}" }}')
        lock += ["[[package]]", 'name = "yadgar-store"', 'version = "0.4.0"']
        lock += [f'source = "git+https://github.com/yadgarhq/store.git?tag={store}#{"0" * 40}"', ""]
    return {**source, "Cargo.toml": "\n".join(lines) + "\n", "Cargo.lock": "\n".join(lock)}


class FakeNet:
    def __init__(self, sources: dict, charts: dict) -> None:
        self._sources, self._charts = sources, charts

    def chart_yaml(self, version: str) -> str:
        return self._charts[version]

    def commits(self, old: str, new: str) -> list:
        return [{"sha": "abc1234", "subject": "fix: a module"}]

    def ghcr_digest(self, module: str, tag: str) -> str:
        return GATEWAY_DIGEST[tag]

    def source(self, module: str, version: str) -> dict:
        return copy.deepcopy(self._sources[(module, version)])


def gateway_render(gateway: str, env: tuple = ("LISTEN", "DB_HOST")) -> list:
    container = {"name": "gateway", "image": f"ghcr.io/yadgarhq/gateway@{GATEWAY_DIGEST[gateway]}"}
    container["env"] = [{"name": name, "value": "x"} for name in env]
    return [
        {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "gateway"},
            "spec": {"template": {"spec": {"containers": [container]}}},
        }
    ]


def through_check(
    monkeypatch, sources: dict, head_env: tuple = ("LISTEN", "DB_HOST"), task_db: str = "0.9.8", repo: Path | None = None, base_of=None
) -> dict:
    """`pe.evidence` (the default `evidence_of`) over pin_bump.check, renders and network stubbed. The base has no
    seed allowlist unless `base_of` (the base reader `evidence` takes, by path) says otherwise."""
    charts = {PIN: chart("0.12.10"), TARGET: chart("0.12.11", task_db)}

    def render(application_text, version, binary):
        # pin_bump.check reads the pin from the tree, and `evidence` wrote the HEAD values at the BASE pin.
        assert pb.parsed_revision(application_text) == PIN
        assert application_text == moved(target=PIN)
        return gateway_render("0.12.10") if version == PIN else gateway_render("0.12.11", head_env)

    monkeypatch.setattr(pb, "render", render)
    base_of = base_of or (lambda path: None)
    return pe.evidence(moved(), PIN, TARGET, HELMS, net=FakeNet(sources, charts), repo=repo, base_of=base_of)


def gateway_sources(old: dict, new: dict) -> dict:
    return {("gateway", "0.12.10"): old, ("gateway", "0.12.11"): new}


def test_the_default_evidence_is_pin_bump_check_and_accepts_the_107_shape(monkeypatch) -> None:
    sources = gateway_sources(with_cargo(tree("e3-old"), "v0.2.17"), with_cargo(tree("e3-old"), "v0.2.17"))
    found = through_check(monkeypatch, sources)
    assert found["reasons"] == [] and found["accept"], found["reasons"]
    assert found["pin"] == PIN and found["target"] == TARGET


def test_an_env_entry_added_to_the_render_fails_an_automatic_pr(monkeypatch) -> None:
    sources = gateway_sources(with_cargo(tree("e3-old"), "v0.2.17"), with_cargo(tree("e3-old"), "v0.2.17"))
    found = through_check(monkeypatch, sources, head_env=("LISTEN", "DB_HOST", "EXTRA"))
    outcome, _ = judge("event-auto.json", moved(), evidence=found, statuses=[green_status()])
    assert not outcome.passed
    assert any("env" in f and "apps/Deployment//gateway" in f for f in outcome.failures), outcome.failures


def test_a_moved_yadgar_crate_tag_fails_closed(monkeypatch) -> None:
    sources = gateway_sources(with_cargo(tree("e3-old"), "v0.2.17"), with_cargo(tree("e3-old"), "v0.2.18"))
    found = through_check(monkeypatch, sources)
    outcome, _ = judge("event-auto.json", moved(), evidence=found, statuses=[green_status()])
    assert not outcome.passed
    assert any("yadgar-lifecycle" in f for f in outcome.failures), outcome.failures


def test_an_unresolved_env_read_site_fails_closed(monkeypatch) -> None:
    new = with_cargo(tree("e3-old"), "v0.2.17")
    new["src/boot.rs"] = 'const prefix: &str = "X";\n' + new["src/boot.rs"].replace(
        "    Ok(())\n}", '    let _t = std::env::var(&format!("{prefix}_NEW_TIMEOUT")).ok();\n    Ok(())\n}', 1
    )
    found = through_check(monkeypatch, gateway_sources(with_cargo(tree("e3-old"), "v0.2.17"), new))
    outcome, _ = judge("event-auto.json", moved(), evidence=found, statuses=[green_status()])
    assert not outcome.passed
    assert any("cannot resolve" in f for f in outcome.failures), outcome.failures


def test_a_moved_yadgar_store_tag_is_red(monkeypatch) -> None:
    sources = gateway_sources(with_cargo(tree("e3-old"), "v0.2.17"), with_cargo(tree("e3-old"), "v0.2.17"))
    sources[("task-db", "0.9.8")] = with_cargo(tree("e4-old"), "v0.2.20", "v0.4.0")
    sources[("task-db", "0.9.9")] = with_cargo(tree("e4-old"), "v0.2.20", "v0.4.1")
    found = through_check(monkeypatch, sources, task_db="0.9.9")
    assert found["migrations"]["task-db"]["status"] == "red"
    outcome, _ = judge("event-auto.json", moved(), evidence=found, statuses=[green_status()])
    assert not outcome.passed
    assert any("yadgar-store" in f for f in outcome.failures), outcome.failures


def test_an_admitted_migration_passes_an_automatic_head(monkeypatch) -> None:
    """ADR-1099: a task-db release that adds one CREATE TABLE in its own fn and list entry is green in E4, so the
    judge (unchanged) passes an automatic head; the record carries the migration's checksum and forward-only version."""
    sources = gateway_sources(with_cargo(tree("e3-old"), "v0.2.17"), with_cargo(tree("e3-old"), "v0.2.17"))
    sources[("task-db", "0.9.8")] = with_cargo(tree("e4-old"), "v0.2.20", "v0.4.0")
    sources[("task-db", "0.9.9")] = with_cargo(tree("e4-new"), "v0.2.20", "v0.4.0")
    found = through_check(monkeypatch, sources, task_db="0.9.9")
    task_db = found["migrations"]["task-db"]
    assert task_db["status"] == "green" and found["accept"], found["reasons"]
    assert [m["version"] for m in task_db["admitted"]] == [7] and task_db["forward_only"] == [7]
    outcome, _ = judge("event-auto.json", moved(), evidence=found, statuses=[green_status()])
    assert outcome.passed, outcome.failures


def test_the_evidence_tree_holds_the_whole_estate(monkeypatch) -> None:
    """ADR-1095: a platform move's E1 reads root and every Application it selects, with the directories they source,
    so `evidence` hands `pin_bump.check` the merge checkout's whole tree, the `yadgar` file alone moved to the base
    pin. A tree of the pin files only would make every platform move NEEDS-HUMAN ("`projects/root.yaml` is absent")."""
    seen = {}

    def check(tree: Path, target: str, net, helms) -> dict:
        seen["files"] = {p.relative_to(tree).as_posix() for p in tree.rglob("*") if p.is_file()}
        seen["yadgar"] = (tree / pb.APPLICATION).read_text()
        return {}

    monkeypatch.setattr(pb, "check", check)
    pe.evidence(moved(), PIN, TARGET, HELMS, net=object(), base_of=lambda path: b"[]\n")
    wanted = {pb.ROOT_APPLICATION, *(p.relative_to(REPOSITORY).as_posix() for p in (REPOSITORY / "applications").glob("*.yaml"))}
    wanted |= {p.relative_to(REPOSITORY).as_posix() for d in ("manifests", "verifier", "install") for p in (REPOSITORY / d).rglob("*") if p.is_file()}
    assert sorted(wanted - seen["files"]) == []
    assert not any(f.startswith(".git/") or f == ".git" for f in seen["files"])
    assert seen["yadgar"] == moved(target=PIN)


# ── the workflow ────────────────────────────────────────────────────────────

WORKFLOW = yaml.safe_load((REPOSITORY / ".github" / "workflows" / "ci.yaml").read_text())


def test_pin_evidence_is_part_of_ci_passed() -> None:
    assert "pin-evidence" in WORKFLOW["jobs"]["ci"]["needs"]


def test_the_job_is_pull_request_only_with_read_permissions_and_no_secret() -> None:
    job = WORKFLOW["jobs"]["pin-evidence"]
    assert job["if"] == "github.event_name == 'pull_request'"
    assert job["permissions"] == {"contents": "read", "statuses": "read"}
    # A bound on the whole job: the gates' `helm pull` has no timeout of its own (yaadgaar #164).
    assert 10 <= job["timeout-minutes"] <= 30
    text = json.dumps(job)
    assert "secrets." not in text.replace("secrets.GITHUB_TOKEN", "")


def test_the_heavy_steps_run_only_on_a_pin_move() -> None:
    steps = WORKFLOW["jobs"]["pin-evidence"]["steps"]
    detect = next(i for i, s in enumerate(steps) if s.get("id") == "detect")
    for step in steps[detect + 1 :]:
        assert step.get("if") == "steps.detect.outputs.moved == 'true'", step.get("name")
    assert any("install-helms" in s.get("run", "") for s in steps[detect + 1 :])
    assert any("judge" in s.get("run", "") for s in steps[detect + 1 :])
    # The gates' modules that pin_bump.check imports start with `import pytest`.
    assert any('"pytest==' in s.get("run", "") for s in steps[detect + 1 :])


def test_every_pin_move_changes_all_three_mechanical_files() -> None:
    """Why an automatic head must change EXACTLY the three files, never a subset: the K3 table's header names
    the pin and `chart_pin.json` names the tag, so a pin move that leaves either untouched is a broken pin."""
    table = (REPOSITORY / pb.TABLE).read_text()
    assert f"targetRevision  {PIN}\n" in table
    assert json.loads((REPOSITORY / pb.CHART_PIN).read_text())["chart_tag"] == f"v{PIN}"


# ── the seed allowlist is the base's (ADR-1099, car 3) ──────────────────────

SEED_SQL = '"INSERT INTO user (id) VALUES (1)".into()'
SEED_ENTRY = {"module": "task-db", "table": "user", "columns": ["id"], "reason": "the first user", "adr": "ADR-1099"}


def seed_sources() -> dict:
    """A task-db release whose one added migration is a seed INSERT that `SEED_ENTRY` admits."""
    sources = gateway_sources(with_cargo(tree("e3-old"), "v0.2.17"), with_cargo(tree("e3-old"), "v0.2.17"))
    new = tree("e4-new")
    new["src/schema.rs"] = new["src/schema.rs"].replace('"CREATE TABLE audit (id BIGINT PRIMARY KEY)".into()', SEED_SQL)
    assert SEED_SQL in new["src/schema.rs"]
    sources[("task-db", "0.9.8")] = with_cargo(tree("e4-old"), "v0.2.20", "v0.4.0")
    sources[("task-db", "0.9.9")] = with_cargo(new, "v0.2.20", "v0.4.0")
    return sources


def head_checkout(tmp_path: Path, allowlist: str | None) -> Path:
    """The merge checkout: this repository less `.git`, its seed allowlist the HEAD's (`None`: no file)."""
    import shutil

    root = tmp_path / "checkout"
    shutil.copytree(REPOSITORY, root, ignore=shutil.ignore_patterns(".git"), symlinks=True)
    (root / pb.SEED_ALLOWLIST_FILE).unlink(missing_ok=True)
    if allowlist is not None:
        (root / pb.SEED_ALLOWLIST_FILE).write_text(allowlist)
    return root


@pytest.mark.parametrize("base", [b"[]\n", None], ids=["base-empty", "base-without-the-file"])
def test_the_allowlist_is_read_from_the_base_not_the_head(monkeypatch, tmp_path: Path, base) -> None:
    """A head that adds an entry and a migration that needs it stays red: E4 reads the base (HEAD^1) copy, never the
    merge checkout's."""
    asked: list = []

    def base_of(path: str):
        asked.append(path)
        return base

    head = head_checkout(tmp_path, json.dumps([SEED_ENTRY]))
    found = through_check(monkeypatch, seed_sources(), task_db="0.9.9", repo=head, base_of=base_of)
    task_db = found["migrations"]["task-db"]
    assert task_db["status"] == "red" and not found["accept"], task_db
    assert task_db["reasons"] == ["task-db: migration 7 (create_audit) not admitted: seed INSERT into user (id) is not on the seed allowlist for task-db"]
    assert asked == [pb.SEED_ALLOWLIST_FILE]
    outcome, _ = judge("event-auto.json", moved(), evidence=found, statuses=[green_status()])
    assert not outcome.passed


def test_a_base_entry_admits_the_seed_whatever_the_head_holds(monkeypatch, tmp_path: Path) -> None:
    head = head_checkout(tmp_path, "[]\n")
    base = json.dumps([SEED_ENTRY]).encode()
    found = through_check(monkeypatch, seed_sources(), task_db="0.9.9", repo=head, base_of=lambda path: base)
    task_db = found["migrations"]["task-db"]
    assert task_db["status"] == "green" and found["accept"], found["reasons"]
    assert task_db["admitted"][0]["seed"] == {"module": "task-db", "table": "user", "adr": "ADR-1099"}
    outcome, _ = judge("event-auto.json", moved(), evidence=found, statuses=[green_status()])
    assert outcome.passed, outcome.failures


def test_a_malformed_base_allowlist_is_red_with_its_reason_not_an_exception(monkeypatch, tmp_path: Path) -> None:
    head = head_checkout(tmp_path, json.dumps([SEED_ENTRY]))
    found = through_check(monkeypatch, seed_sources(), task_db="0.9.9", repo=head, base_of=lambda path: b"[{]")
    reasons = found["migrations"]["task-db"]["reasons"]
    assert len(reasons) == 1 and "the seed allowlist is unusable" in reasons[0] and pb.SEED_ALLOWLIST_FILE in reasons[0], reasons


def test_the_base_copy_of_the_seed_allowlist_is_read_from_the_first_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list = []

    def run_with(tree: str):
        def run(command, **kwargs):
            calls.append(command[3:])
            out = tree if command[3] == "ls-tree" else b"[]\n"
            return type("Finished", (), {"stdout": out})()

        return run

    monkeypatch.setattr(pe.subprocess, "run", run_with(b""))
    assert pe.base_file_if_any(Path("/repo"), pb.SEED_ALLOWLIST_FILE) is None, "a base without the file"
    assert calls == [["ls-tree", "HEAD^1", "--", pb.SEED_ALLOWLIST_FILE]]
    calls.clear()
    monkeypatch.setattr(pe.subprocess, "run", run_with(f"100644 blob {'d' * 40}\t{pb.SEED_ALLOWLIST_FILE}\n".encode()))
    assert pe.base_file_if_any(Path("/repo"), pb.SEED_ALLOWLIST_FILE) == b"[]\n"
    assert calls == [["ls-tree", "HEAD^1", "--", pb.SEED_ALLOWLIST_FILE], ["show", f"HEAD^1:{pb.SEED_ALLOWLIST_FILE}"]]


# ── yaadgaar 479 item 2: the base copy is the merge commit's FIRST parent, in real git ──


def real_git(repo: Path, *args: str) -> str:
    """No `git config` write, and no inherited `GIT_*` (a hook's GIT_DIR overrides `-C`; yaadgaar 498)."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    command = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org", "-c", "commit.gpgsign=false", *args]
    return subprocess.run(command, capture_output=True, text=True, check=True, env=env).stdout.strip()


def test_base_ack_text_reads_the_first_parent_of_a_real_merge_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """refs/pull/N/merge: parent 1 is main's tip, parent 2 the PR head. The PR's own ack must not be read as base."""
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    ack = tmp_path / pe.ACK_FILE
    ack.parent.mkdir(parents=True)
    real_git(tmp_path, "init", "-q", "-b", "main")
    ack.write_text("main\n")
    real_git(tmp_path, "add", "-A")
    real_git(tmp_path, "commit", "-q", "-m", "main")
    real_git(tmp_path, "switch", "-q", "-c", "pr")
    ack.write_text("pr\n")
    real_git(tmp_path, "commit", "-q", "-am", "pr")
    real_git(tmp_path, "switch", "-q", "main")
    (tmp_path / "other").write_text("x\n")
    real_git(tmp_path, "add", "-A")
    real_git(tmp_path, "commit", "-q", "-m", "main moves on")
    real_git(tmp_path, "merge", "-q", "--no-ff", "-X", "theirs", "-m", "merge", "pr")
    assert ack.read_text() == "pr\n", "the merge result carries the PR's copy"
    assert pe.base_ack_text(tmp_path) == "main\n"


def test_base_ack_text_is_none_when_only_the_pr_adds_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The base has no ack file, the PR adds one: no base entry, read through the first parent's tree."""
    for key in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(key)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    real_git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "other").write_text("x\n")
    real_git(tmp_path, "add", "-A")
    real_git(tmp_path, "commit", "-q", "-m", "main")
    real_git(tmp_path, "switch", "-q", "-c", "pr")
    ack = tmp_path / pe.ACK_FILE
    ack.parent.mkdir(parents=True)
    ack.write_text("pr\n")
    real_git(tmp_path, "add", "-A")
    real_git(tmp_path, "commit", "-q", "-m", "pr")
    real_git(tmp_path, "switch", "-q", "main")
    (tmp_path / "other").write_text("y\n")
    real_git(tmp_path, "commit", "-q", "-am", "main moves on")
    real_git(tmp_path, "merge", "-q", "--no-ff", "-m", "merge", "pr")
    assert pe.base_ack_text(tmp_path) is None


# ── a platform-only move is judged (yaadgaar #514 option (a), Max 2026-11-02) ─
#
# A PR that leaves `targetRevision` alone but moves the platform (the five
# operator Applications and `chart_pin.json`'s `platform_version`) is a move:
# `detect` writes moved=true, `judge` runs the operator E1 (`classify_platform`
# through `pin_bump.platform_evidence`), and a NEEDS-HUMAN verdict FAILS a human
# head unless the PR commits an ack whose target is `platform:<to>` (ADR-1080's
# rule, keyed by the platform version).

PLATFORM_FILES = (*pb.OPERATOR_APPLICATIONS, pb.CHART_PIN)
PLATFORM_BASE = {path: (REPOSITORY / path).read_text() for path in PLATFORM_FILES}
PLATFORM_FROM = json.loads(PLATFORM_BASE[pb.CHART_PIN])["platform_version"]
PLATFORM_TO = "0.99.0"
PLATFORM_KEY = f"platform:{PLATFORM_TO}"


def platform_moved(to: str = PLATFORM_TO, only=PLATFORM_FILES, base: dict | None = None) -> dict:
    """The base platform files with `to` written into `only`, each by its one line edit."""
    files = dict(PLATFORM_BASE if base is None else base)
    pin = json.loads(files[pb.CHART_PIN])
    for path in only:
        if path == pb.CHART_PIN:
            files[path] = pb.rewrite_chart_pin(
                files[path], pin["chart_tag"].removeprefix("v"), digest=pin["chart_digest"], platform=to
            )
        else:
            files[path] = pb.rewrite_target_revision(files[path], to, path)[0]
    return files


PLATFORM_ACCEPT = {**ACCEPT, "platform": {"from": PLATFORM_FROM, "to": PLATFORM_TO, "reasons": []}}
PLATFORM_NEEDS_HUMAN = {
    **PLATFORM_ACCEPT,
    "accept": False,
    "reasons": ["keda: apps/Deployment/keda/keda-operator: field spec.template.spec.containers[0].args changed; outside the platform-move set"],
}


def platform_judge(
    pr_event: str = "event-human.json", head_files: dict | None = None, *, evidence=None, acks=None, base_acks=None,
    head: str = BASE, helms=None, wired: bool = True, statuses=None,
):
    calls: list = []
    files = platform_moved() if head_files is None else head_files

    def platform_evidence_of(head_text, old, new, found):
        calls.append((old, new, sorted(found)))
        return copy.deepcopy(evidence if evidence is not None else PLATFORM_ACCEPT)

    def no_pin_evidence(*_):
        raise AssertionError("a platform-only move renders no yadgar pin move")

    outcome = pe.judge(
        pe.pr_of(event(pr_event)),
        BASE,
        head,
        evidence_of=no_pin_evidence,
        statuses_of=lambda sha: [] if statuses is None else statuses,
        helms=HELMS if helms is None else helms,
        changed=sorted(p for p in PLATFORM_FILES if files[p] != PLATFORM_BASE[p]) + ([pb.APPLICATION] if head != BASE else []),
        regenerated_of=lambda target: [],
        acks={} if acks is None else acks,
        base_acks={} if base_acks is None else base_acks,
        platform_texts=(dict(PLATFORM_BASE), files),
        platform_evidence_of=platform_evidence_of if wired else None,
    )
    return outcome, calls


def platform_ack(target: str = PLATFORM_KEY, reasons=None, why: str = "keda args read by hand") -> dict:
    return {target: {"target": target, "reasons": list(PLATFORM_NEEDS_HUMAN["reasons"] if reasons is None else reasons), "why": why}}


def test_the_repository_platform_files_agree() -> None:
    assert pe.platform_move_of(PLATFORM_BASE, PLATFORM_BASE) is None


def test_a_platform_only_move_is_a_move() -> None:
    assert pe.platform_move_of(PLATFORM_BASE, platform_moved()) == (PLATFORM_FROM, PLATFORM_TO)


def test_a_platform_only_move_is_judged_by_the_operator_e1() -> None:
    outcome, calls = platform_judge()
    assert calls == [(PLATFORM_FROM, PLATFORM_TO, sorted(HELMS))], "the operator E1 runs on a platform-only move"
    assert outcome.passed, outcome.failures
    assert any(f"platform {PLATFORM_FROM} -> {PLATFORM_TO}" in n for n in outcome.notes), outcome.notes
    assert not any("no pin move" in n for n in outcome.notes), outcome.notes


def test_a_platform_only_needs_human_fails_a_human_head_without_an_ack() -> None:
    outcome, _ = platform_judge(evidence=PLATFORM_NEEDS_HUMAN)
    assert not outcome.passed
    assert f"NEEDS-HUMAN: {PLATFORM_NEEDS_HUMAN['reasons'][0]}" in outcome.failures, outcome.failures
    told = "\n".join(outcome.failures)
    assert "NEEDS-HUMAN-UNACKNOWLEDGED" in told and pe.ACK_FILE in told, told
    entry = json.loads(told[told.index("{"):told.rindex("}") + 1])
    assert entry["target"] == PLATFORM_KEY and entry["reasons"] == PLATFORM_NEEDS_HUMAN["reasons"], entry


def test_a_platform_ack_for_the_moved_version_clears_a_human_head() -> None:
    outcome, _ = platform_judge(evidence=PLATFORM_NEEDS_HUMAN, acks=platform_ack())
    assert outcome.passed, outcome.failures
    assert any(PLATFORM_NEEDS_HUMAN["reasons"][0] in w for w in outcome.warnings), outcome.warnings


@pytest.mark.parametrize(
    "acks",
    [
        pytest.param(platform_ack("platform:0.98.0"), id="another-platform-version"),
        pytest.param(platform_ack(PLATFORM_TO), id="bare-version-no-prefix"),
        pytest.param(platform_ack(PIN), id="the-yadgar-pin"),
        pytest.param(platform_ack(reasons=["keda: something else"]), id="another-reason"),
    ],
)
def test_a_wrong_platform_ack_is_refused(acks: dict) -> None:
    outcome, _ = platform_judge(evidence=PLATFORM_NEEDS_HUMAN, acks=acks)
    assert not outcome.passed
    assert any("NEEDS-HUMAN-UNACKNOWLEDGED" in f for f in outcome.failures), outcome.failures


def test_a_platform_ack_already_on_the_base_does_not_clear() -> None:
    outcome, _ = platform_judge(evidence=PLATFORM_NEEDS_HUMAN, acks=platform_ack(), base_acks=platform_ack())
    assert not outcome.passed
    assert any("already on the base" in f for f in outcome.failures), outcome.failures


def test_an_automatic_head_that_moves_only_the_platform_fails_and_renders_nothing() -> None:
    outcome, calls = platform_judge("event-auto.json", acks=platform_ack())
    assert not outcome.passed and calls == []
    assert any("must move targetRevision" in f for f in outcome.failures), outcome.failures


def test_a_no_op_edit_of_the_platform_files_is_not_judged() -> None:
    files = dict(PLATFORM_BASE)
    path = pb.OPERATOR_APPLICATIONS[0]
    files[path] = "# a comment only\n" + files[path]
    assert pe.platform_move_of(PLATFORM_BASE, files) is None
    outcome, calls = platform_judge(head_files=files)
    assert outcome.passed and calls == [], outcome.failures
    assert any("no pin move" in n for n in outcome.notes), outcome.notes


@pytest.mark.parametrize(
    "only",
    [
        pytest.param((pb.CHART_PIN,), id="platform_version-alone"),
        pytest.param(pb.OPERATOR_APPLICATIONS, id="operators-without-platform_version"),
        pytest.param((pb.OPERATOR_APPLICATIONS[0], pb.CHART_PIN), id="one-operator-and-platform_version"),
    ],
)
def test_a_partial_platform_move_fails_and_renders_nothing(only: tuple) -> None:
    files = platform_moved(only=only)
    with pytest.raises(pe.Refused):
        pe.platform_move_of(PLATFORM_BASE, files)
    outcome, calls = platform_judge(head_files=files)
    assert not outcome.passed and calls == []
    assert any("move together" in f for f in outcome.failures), outcome.failures


def test_a_base_whose_platform_files_disagree_is_refused() -> None:
    base = platform_moved(only=(pb.CHART_PIN,))
    with pytest.raises(pe.Refused):
        pe.platform_move_of(base, platform_moved(base=base))


def test_a_missing_platform_file_is_refused() -> None:
    files = platform_moved()
    files[pb.OPERATOR_APPLICATIONS[3]] = None
    with pytest.raises(pe.Refused):
        pe.platform_move_of(PLATFORM_BASE, files)


def test_a_duplicate_operator_target_revision_is_refused() -> None:
    files = platform_moved()
    path = pb.OPERATOR_APPLICATIONS[1]
    line = f"    targetRevision: {PLATFORM_TO}\n"
    assert files[path].count(line) == 1
    files[path] = files[path].replace(line, line + '    "targetRevision": 0.0.1\n', 1)
    with pytest.raises(pe.Refused, match="duplicate key"):
        pe.platform_move_of(PLATFORM_BASE, files)


def test_a_values_change_beside_a_platform_move_fails_before_any_render() -> None:
    files = platform_moved()
    path = pb.OPERATOR_APPLICATIONS[2]
    files[path] = files[path].replace("    targetRevision: ", "    # moved\n    targetRevision: ", 1)
    assert pe.platform_move_of(PLATFORM_BASE, files) == (PLATFORM_FROM, PLATFORM_TO)
    outcome, calls = platform_judge(head_files=files)
    assert not outcome.passed and calls == []
    assert any("not a platform-only change" in f and path in f for f in outcome.failures), outcome.failures


def test_a_chart_pin_change_beside_a_platform_move_fails_before_any_render() -> None:
    files = platform_moved()
    files[pb.CHART_PIN] = files[pb.CHART_PIN] + "\n"
    outcome, calls = platform_judge(head_files=files)
    assert not outcome.passed and calls == []
    assert any("not a platform-only change" in f and pb.CHART_PIN in f for f in outcome.failures), outcome.failures


def test_a_yadgar_values_change_beside_a_platform_move_fails_before_any_render() -> None:
    outcome, calls = platform_judge(head=BASE + "# a values change\n")
    assert not outcome.passed and calls == []
    assert any("not a platform-only change" in f and pb.APPLICATION in f for f in outcome.failures), outcome.failures


def test_a_platform_prerelease_fails() -> None:
    outcome, calls = platform_judge(head_files=platform_moved(to="0.99.0-rc.1"))
    assert not outcome.passed and calls == []


def test_a_platform_downgrade_is_still_judged() -> None:
    """Fail-closed: E1 compares both directions, so a lower platform is judged, never skipped."""
    outcome, calls = platform_judge(head_files=platform_moved(to="0.0.1"), evidence=PLATFORM_NEEDS_HUMAN)
    assert calls and calls[0][:2] == (PLATFORM_FROM, "0.0.1")
    assert not outcome.passed


def test_a_platform_move_needs_both_helms() -> None:
    outcome, calls = platform_judge(helms={"v3.18.4": "/helm3"})
    assert not outcome.passed and calls == []


def test_a_platform_move_with_no_operator_e1_wired_fails() -> None:
    outcome, _ = platform_judge(wired=False)
    assert not outcome.passed


def test_platform_e2_is_reported_on_a_human_head() -> None:
    outcome, _ = platform_judge()
    assert any("E2 (report mode on a human head)" in w for w in outcome.warnings), outcome.warnings


def detect_with(monkeypatch, tmp_path: Path, head_files: dict, changed: list) -> Path:
    monkeypatch.setattr(pe, "texts", lambda repo, sha: (BASE, BASE))
    monkeypatch.setattr(pe, "changed_files", lambda repo: list(changed))
    monkeypatch.setattr(pe, "merge_parent", lambda repo: "c" * 40)
    monkeypatch.setattr(pe, "platform_texts", lambda repo: (dict(PLATFORM_BASE), head_files))
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps(event("event-human.json")))
    return event_file


def test_detect_writes_moved_true_for_a_platform_only_move(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    event_file = detect_with(monkeypatch, tmp_path, platform_moved(), list(PLATFORM_FILES))
    output = tmp_path / "output"
    assert pe.main(["detect", "--event", str(event_file), "--output", str(output)]) == 0
    assert output.read_text().splitlines() == ["moved=true", f"platform={PLATFORM_FROM}", f"platform_target={PLATFORM_TO}"]


def test_detect_writes_moved_false_for_a_no_op_platform_edit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    event_file = detect_with(monkeypatch, tmp_path, dict(PLATFORM_BASE), [pb.CHART_PIN])
    output = tmp_path / "output"
    assert pe.main(["detect", "--event", str(event_file), "--output", str(output)]) == 0
    assert output.read_text().splitlines() == ["moved=false"]


def test_detect_fails_a_partial_platform_move(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    event_file = detect_with(monkeypatch, tmp_path, platform_moved(only=(pb.CHART_PIN,)), [pb.CHART_PIN])
    output = tmp_path / "output"
    assert pe.main(["detect", "--event", str(event_file), "--output", str(output)]) == 1
    assert "move together" in capsys.readouterr().out


def test_main_judges_with_the_platform_texts_and_the_operator_e1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []
    files = platform_moved()
    event_file = detect_with(monkeypatch, tmp_path, files, list(PLATFORM_FILES))
    monkeypatch.setattr(
        pe, "judge", lambda pr, *a, **k: seen.append((k.get("platform_texts"), callable(k.get("platform_evidence_of")))) or pe.Outcome()
    )
    assert pe.main(["judge", "--event", str(event_file)]) == 0
    assert seen == [((PLATFORM_BASE, files), True)]


def test_platform_texts_read_the_base_from_the_first_parent_and_the_head_from_the_checkout(tmp_path: Path, monkeypatch) -> None:
    for path in PLATFORM_FILES:
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_text(f"head {path}\n")
    monkeypatch.setattr(pe, "base_file_if_any", lambda repo, path: None if path == pb.CHART_PIN else f"base {path}\n".encode())
    base, head = READ_PLATFORM_TEXTS(tmp_path)
    assert sorted(base) == sorted(head) == sorted(PLATFORM_FILES)
    assert base[pb.CHART_PIN] is None and base[pb.OPERATOR_APPLICATIONS[0]] == f"base {pb.OPERATOR_APPLICATIONS[0]}\n"
    assert head[pb.CHART_PIN] == f"head {pb.CHART_PIN}\n"


def test_the_default_platform_evidence_gives_yadgars_render_to_the_selector_set(monkeypatch, tmp_path: Path) -> None:
    """ADR-1095: yadgar's selectors join the operators' even when the yadgar pin does not move."""
    seen: dict = {}
    yadgar_doc = {"kind": "Service", "metadata": {"name": "y"}}

    def render(application_text, version, binary):
        seen.setdefault("renders", []).append((version, binary))
        assert application_text == BASE
        return [yadgar_doc]

    def platform_evidence(repo, old, new, helms, net, others):
        seen.update(old=old, new=new, others=others, files={p.name for p in repo.iterdir()})
        return {"from": old, "to": new, "objects": {}, "changed": [], "applications": [], "selector_keys": {}, "reasons": ["r"]}

    (tmp_path / "applications").mkdir()
    (tmp_path / "applications" / "x.yaml").write_text("x\n")
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(pb, "render", render)
    monkeypatch.setattr(pb, "platform_evidence", platform_evidence)
    found = pe.platform_evidence(BASE, PLATFORM_FROM, PLATFORM_TO, HELMS, net=object(), repo=tmp_path)
    assert sorted(seen["renders"]) == sorted((PIN, binary) for binary in HELMS.values())
    assert seen["others"] == [yadgar_doc, yadgar_doc]
    assert (seen["old"], seen["new"]) == (PLATFORM_FROM, PLATFORM_TO)
    assert ".git" not in seen["files"] and "applications" in seen["files"]
    assert found["accept"] is False and found["reasons"] == ["r"] and found["platform"]["to"] == PLATFORM_TO
    assert set(found) >= {"helms", "changed", "boot_audit", "migrations"}, "summary_lines reads these"
    assert pe.summary_lines(pe.Outcome(evidence=found)), "the summary renders a platform evidence"


def test_an_unreadable_operator_application_is_a_failure_not_a_traceback() -> None:
    def refuse(*_):
        raise pb.Refused("operator Application keda: source keys are not the platform shape")

    outcome = pe.judge(
        pe.pr_of(event("event-human.json")), BASE, BASE,
        evidence_of=lambda *_: ACCEPT, statuses_of=lambda sha: [], helms=HELMS, changed=list(PLATFORM_FILES),
        regenerated_of=lambda target: [], platform_texts=(dict(PLATFORM_BASE), platform_moved()), platform_evidence_of=refuse,
    )
    assert any("platform shape" in f for f in outcome.failures), outcome.failures


@pytest.mark.parametrize("target", [PLATFORM_KEY, "0.50.0"])
def test_the_acknowledgement_file_accepts_a_platform_key(tmp_path: Path, target: str) -> None:
    path = tmp_path / "ack.json"
    entry = platform_ack(target)[target]
    path.write_text(json.dumps([entry]))
    assert pe.acknowledgements(path) == {target: entry}


@pytest.mark.parametrize("target", ["platform:", "platform:0.2", "platform:v0.2.18", "latest", "v0.50.0", "platform: 0.2.18"])
def test_the_acknowledgement_file_refuses_a_target_of_another_shape(tmp_path: Path, target: str) -> None:
    path = tmp_path / "ack.json"
    path.write_text(json.dumps([platform_ack(target)[target]]))
    with pytest.raises(pe.Refused):
        pe.acknowledgements(path)


# ── argocd#230 review: a pin move beside a platform move (F1) ───────────────
#
# On a pin move E1 judges the platform move the TARGET CHART names
# (`found["platform"]`). What deploys is the version the six platform files
# name. They must be one move: files that move while the chart's platform does
# not, or that move to another version than the chart's, FAIL on any head.


def pin_and_platform(pr_event: str, chart_platform: dict | None, files: dict) -> pe.Outcome:
    evidence = {**ACCEPT, "platform": chart_platform}
    outcome, _ = judge(
        pr_event, moved(), evidence=evidence, statuses=[green_status()], required=True,
        changed=pb.pin_files(platform_moved=True), platform_texts=(dict(PLATFORM_BASE), files),
    )
    return outcome


@pytest.mark.parametrize("pr_event", ["event-human.json", "event-auto.json"])
def test_operator_files_that_move_while_the_charts_platform_stays_fail(pr_event: str) -> None:
    """Review repro (A): the pin moves, the target chart keeps its platform, the six files move. E1 judged no
    platform move at all."""
    outcome = pin_and_platform(pr_event, None, platform_moved())
    assert not outcome.passed
    assert any("the target chart's platform does not move" in f for f in outcome.failures), outcome.failures


@pytest.mark.parametrize("pr_event", ["event-human.json", "event-auto.json"])
def test_operator_files_that_move_to_another_version_than_the_charts_platform_fail(pr_event: str) -> None:
    """Review repro (B): the chart moves platform to 0.3.0, the files to 0.99.0. E1 judged 0.3.0; 0.99.0 deploys."""
    outcome = pin_and_platform(pr_event, {"from": PLATFORM_FROM, "to": "0.3.0", "reasons": []}, platform_moved())
    assert not outcome.passed
    assert any("0.3.0" in f and PLATFORM_TO in f and "E1 judged" in f for f in outcome.failures), outcome.failures


def test_operator_files_that_move_with_the_charts_platform_pass() -> None:
    outcome = pin_and_platform("event-auto.json", {"from": PLATFORM_FROM, "to": PLATFORM_TO, "reasons": []}, platform_moved())
    assert outcome.passed, outcome.failures


def test_a_pin_move_with_unmoved_platform_files_and_no_chart_platform_move_passes() -> None:
    outcome = pin_and_platform("event-auto.json", None, dict(PLATFORM_BASE))
    assert outcome.passed, outcome.failures


def test_a_pin_move_with_partially_moved_platform_files_fails() -> None:
    outcome = pin_and_platform("event-human.json", None, platform_moved(only=(pb.CHART_PIN,)))
    assert not outcome.passed
    assert any("move together" in f for f in outcome.failures), outcome.failures


# ── argocd#230 review: applications/ is an explicit allowlist (F2) ──────────
#
# The heuristic (`chart: platform` anywhere) missed an OCI repoURL with a path,
# a git repoURL, a templated ApplicationSet and an app-of-apps. The guard is
# now fail-closed: every file under applications/ must be one of
# KNOWN_APPLICATIONS, no symlink, no ApplicationSet, and no Application document
# anywhere else in the tree (outside scripts/ and projects/root.yaml).


def known_tree(tmp_path: Path) -> Path:
    """A tree holding this repository's applications/ and projects/ as they are."""
    tree = tmp_path / "tree"
    shutil.copytree(REPOSITORY / "applications", tree / "applications")
    shutil.copytree(REPOSITORY / "projects", tree / "projects")
    return tree


def test_the_repository_has_no_unknown_application() -> None:
    assert pe.unknown_applications(REPOSITORY) == []


def test_known_applications_is_the_tree() -> None:
    on_disk = sorted(p.relative_to(REPOSITORY).as_posix() for p in (REPOSITORY / "applications").rglob("*") if p.is_file())
    assert sorted(pe.KNOWN_APPLICATIONS) == on_disk
    assert len(pe.KNOWN_APPLICATIONS) == 13
    assert set(pb.OPERATOR_APPLICATIONS) <= set(pe.KNOWN_APPLICATIONS)


def test_known_applications_is_the_union_of_the_other_gates_lists() -> None:
    """A new Application is a visible edit in two places: here and in the gate that pins its shape."""
    for path in (REPOSITORY / "scripts" / "tests",):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    import test_infra_children
    import test_operator_applications
    import test_pin_estate_selectors
    import test_runner_image_pinned

    union = {f"applications/{name}.yaml" for name in test_operator_applications.ADOPTED}
    union |= {f"applications/{name}.yaml" for name in test_infra_children.CHILDREN}
    union |= {p.as_posix() for p in (test_runner_image_pinned.VERIFIER_RUNNER, test_runner_image_pinned.ESTATE_RUNNER)}
    # argocd and post-merge-verifier are named in the estate-selectors gate's own list (root is projects/root.yaml).
    union |= {f"applications/{name}.yaml" for name in test_pin_estate_selectors.ESTATE if name != "root"}
    assert sorted(union) == sorted(pe.KNOWN_APPLICATIONS)


OCI_PATH_APPLICATION = """\
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata: {name: sneaky, namespace: argocd}
spec:
  project: default
  source: {repoURL: "oci://ghcr.io/yadgarhq/charts/platform", targetRevision: 0.2.17, path: .}
  destination: {server: https://kubernetes.default.svc, namespace: sneaky}
"""
GIT_PATH_APPLICATION = """\
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata: {name: sneaky, namespace: argocd}
spec:
  project: default
  source: {repoURL: "https://github.com/yadgarhq/platform", targetRevision: main, path: chart}
  destination: {server: https://kubernetes.default.svc, namespace: sneaky}
"""
APPLICATION_SET = """\
apiVersion: argoproj.io/v1alpha1
kind: ApplicationSet
metadata: {name: sneaky, namespace: argocd}
spec:
  generators: [{list: {elements: [{n: platform}]}}]
  template:
    metadata: {name: "{{n}}"}
    spec:
      project: default
      source: {repoURL: ghcr.io/yadgarhq/charts, chart: "{{n}}", targetRevision: 0.2.17}
      destination: {server: https://kubernetes.default.svc, namespace: sneaky}
"""
WRAPPER = """\
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata: {name: wrapper, namespace: argocd}
spec:
  project: default
  source: {repoURL: https://github.com/yadgarhq/argocd, targetRevision: main, path: extra}
  destination: {server: https://kubernetes.default.svc, namespace: argocd}
"""


@pytest.mark.parametrize(
    "path, text",
    [
        pytest.param("applications/sneaky.yaml", OCI_PATH_APPLICATION, id="oci-repourl-with-path"),
        pytest.param("applications/sneaky.yaml", GIT_PATH_APPLICATION, id="git-repourl-with-path"),
        pytest.param("applications/sneaky.yaml", APPLICATION_SET, id="templated-applicationset"),
        pytest.param("applications/wrapper.yaml", WRAPPER, id="app-of-apps-wrapper"),
        pytest.param("applications/nested/deeper/x.yaml", OCI_PATH_APPLICATION, id="nested"),
        pytest.param("applications/notes.txt", "not yaml at all\n", id="any-suffix"),
    ],
)
def test_a_file_under_applications_outside_the_allowlist_is_refused(tmp_path: Path, path: str, text: str) -> None:
    tree = known_tree(tmp_path)
    (tree / path).parent.mkdir(parents=True, exist_ok=True)
    (tree / path).write_text(text)
    problems = pe.unknown_applications(tree)
    assert any(path in p and "not one of the" in p for p in problems), problems


def test_the_app_of_apps_child_outside_applications_is_refused(tmp_path: Path) -> None:
    """The child of a wrapper, wherever it sits: an Application document outside applications/ is refused."""
    tree = known_tree(tmp_path)
    (tree / "extra").mkdir()
    (tree / "extra" / "child.yaml").write_text(OCI_PATH_APPLICATION)
    problems = pe.unknown_applications(tree)
    assert any("extra/child.yaml" in p and "outside applications/" in p for p in problems), problems


def test_an_applicationset_in_a_known_file_is_refused(tmp_path: Path) -> None:
    tree = known_tree(tmp_path)
    (tree / "applications" / "tls.yaml").write_text(APPLICATION_SET)
    problems = pe.unknown_applications(tree)
    assert any("applications/tls.yaml" in p and "ApplicationSet" in p for p in problems), problems


@pytest.mark.parametrize("text", [OCI_PATH_APPLICATION, GIT_PATH_APPLICATION], ids=["oci", "git"])
def test_a_known_non_operator_file_that_sources_the_platform_is_refused(tmp_path: Path, text: str) -> None:
    tree = known_tree(tmp_path)
    (tree / "applications" / "tls.yaml").write_text(text)
    problems = pe.unknown_applications(tree)
    assert any("applications/tls.yaml" in p and "platform" in p for p in problems), problems


def test_a_symlink_under_applications_is_refused(tmp_path: Path) -> None:
    tree = known_tree(tmp_path)
    (tree / "applications" / "tls.yaml").unlink()
    (tree / "applications" / "tls.yaml").symlink_to(tree / "applications" / "keda.yaml")
    problems = pe.unknown_applications(tree)
    assert any("applications/tls.yaml" in p and "symlink" in p for p in problems), problems


def test_a_symlinked_directory_under_applications_is_refused(tmp_path: Path) -> None:
    tree = known_tree(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    (tree / "applications" / "linked").symlink_to(tmp_path / "elsewhere", target_is_directory=True)
    problems = pe.unknown_applications(tree)
    assert any("applications/linked" in p and "symlink" in p for p in problems), problems


def test_an_unparseable_known_file_is_refused(tmp_path: Path) -> None:
    tree = known_tree(tmp_path)
    (tree / "applications" / "tls.yaml").write_text("a: [\n")
    problems = pe.unknown_applications(tree)
    assert any("applications/tls.yaml" in p and "could not be read" in p for p in problems), problems


def test_the_five_operator_files_may_source_the_platform(tmp_path: Path) -> None:
    assert pe.unknown_applications(known_tree(tmp_path)) == []


def test_detect_refuses_an_unknown_application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    tree = known_tree(tmp_path)
    event_file = detect_with(monkeypatch, tmp_path, dict(PLATFORM_BASE), ["applications/sneaky.yaml"])
    (tree / "applications" / "sneaky.yaml").write_text(OCI_PATH_APPLICATION)
    output = tmp_path / "output"
    assert pe.main(["detect", "--event", str(event_file), "--output", str(output), "--repo", str(tree)]) == 1
    assert "applications/sneaky.yaml" in capsys.readouterr().out


def test_judge_refuses_an_unknown_application(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    tree = known_tree(tmp_path)
    event_file = detect_with(monkeypatch, tmp_path, dict(PLATFORM_BASE), ["applications/sneaky.yaml"])
    monkeypatch.setattr(pe, "base_ack_text", lambda repo: None)
    (tree / "applications" / "sneaky.yaml").write_text(OCI_PATH_APPLICATION)
    assert pe.main(["judge", "--event", str(event_file), "--repo", str(tree)]) == 1
    assert "applications/sneaky.yaml" in capsys.readouterr().out


# ── argocd#230 review: the chart moves the platform, the files stay (F1, third branch) ─


@pytest.mark.parametrize("pr_event", ["event-human.json", "event-auto.json"])
def test_a_chart_platform_move_with_the_operator_files_left_on_the_old_platform_fails(pr_event: str) -> None:
    """The chart moves platform P -> Q, the operator files stay on P yet are in `changed` (a comment edit, say)."""
    files = dict(PLATFORM_BASE)
    for path in pb.OPERATOR_APPLICATIONS:
        files[path] = "# touched\n" + files[path]
    outcome = pin_and_platform(pr_event, {"from": PLATFORM_FROM, "to": PLATFORM_TO, "reasons": []}, files)
    assert not outcome.passed
    assert any("stay on platform" in f and PLATFORM_TO in f for f in outcome.failures), outcome.failures


# ── argocd#230 review: one parse of chart_pin.json (F4), ASCII digits (F5) ──


def test_a_duplicate_key_in_chart_pin_is_refused() -> None:
    files = dict(PLATFORM_BASE)
    files[pb.CHART_PIN] = files[pb.CHART_PIN].replace(
        f'"platform_version": "{PLATFORM_FROM}"', f'"platform_version": "{PLATFORM_FROM}",\n  "platform_version": "{PLATFORM_TO}"', 1
    )
    assert json.loads(files[pb.CHART_PIN])["platform_version"] == PLATFORM_TO
    with pytest.raises(pe.Refused, match="duplicate"):
        pe.platform_move_of(PLATFORM_BASE, files)


def test_an_ack_target_of_non_ascii_digits_is_refused(tmp_path: Path) -> None:
    target = "platform:١.٢.٣"
    path = tmp_path / "ack.json"
    path.write_text(json.dumps([platform_ack(target)[target]]))
    with pytest.raises(pe.Refused):
        pe.acknowledgements(path)


def test_a_release_of_non_ascii_digits_is_no_release() -> None:
    assert pb.release("١.٢.٣") is None
    assert pb.release("1.2.3") == (1, 2, 3) and pb.release("v0.2.17") == (0, 2, 17)
