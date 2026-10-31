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
    changed=pb.MECHANICAL_FILES, differs=(), acks=None, base_acks=None,
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
    ],
    ids=["an-extra-file", "a-missing-file", "a-swapped-file"],
)
def test_an_automatic_pin_pr_must_change_exactly_the_three_pin_files(changed: list) -> None:
    outcome, calls = judge("event-auto.json", moved(), statuses=[green_status()], changed=changed)
    assert not outcome.passed
    assert any("exactly" in f for f in outcome.failures), outcome.failures
    assert calls == []


def test_a_human_pin_pr_may_change_other_files() -> None:
    """C3 does not judge non-pin edits on a human head: K3, two-owners and review do."""
    outcome, _ = judge("event-human.json", moved(), statuses=[green_status()], changed=[*pb.MECHANICAL_FILES, "MIGRATION_NOTES.md"])
    assert outcome.passed, outcome.failures


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
        pb.CHART_PIN: pb.rewrite_chart_pin(base_pin, TARGET, "sha256:" + "1" * 64),
        pb.TABLE: "table@target\n",
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
    base = {pb.APPLICATION: BASE, pb.CHART_PIN: base_pin, pb.TABLE: "table@base\n"}
    assert pe.regenerated_differences(head, TARGET, base_of=lambda path: base[path].encode()) == differs


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


def through_check(monkeypatch, sources: dict, head_env: tuple = ("LISTEN", "DB_HOST"), task_db: str = "0.9.8") -> dict:
    """`pe.evidence` (the default `evidence_of`) over pin_bump.check, renders and network stubbed."""
    charts = {PIN: chart("0.12.10"), TARGET: chart("0.12.11", task_db)}

    def render(application_text, version, binary):
        # pin_bump.check reads the pin from the tree, and `evidence` wrote the HEAD values at the BASE pin.
        assert pb.parsed_revision(application_text) == PIN
        assert application_text == moved(target=PIN)
        return gateway_render("0.12.10") if version == PIN else gateway_render("0.12.11", head_env)

    monkeypatch.setattr(pb, "render", render)
    return pe.evidence(moved(), PIN, TARGET, HELMS, net=FakeNet(sources, charts))


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
