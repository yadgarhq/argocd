#!/usr/bin/env python3
"""CI's `pin-evidence` job: ADR-0881's evidence on a pull request that moves the `yadgar` pin (ADR-0946, ledger 1632 C3).

    pin_evidence.py detect --event "$GITHUB_EVENT_PATH" --output "$GITHUB_OUTPUT"
    pin_evidence.py install-helms --dir DIR --env "$GITHUB_ENV"
    pin_evidence.py judge  --event "$GITHUB_EVENT_PATH" [--summary "$GITHUB_STEP_SUMMARY"]

THE PLAN IS `yadgarhq/docs` `plans/auto-deploy-parent-chart.md`, section 3
(E1, E3, E4 run here; E2 is read as a status) and car C3. The job is in
`.github/workflows/ci.yaml` and joins `ci`'s `needs:`, so it is part of
`ci / passed`. Pull request only, no secret, `contents: read` and
`statuses: read`, public registry and public module repositories only.

`detect` IS THE FAST PATH. It compares `spec.source.targetRevision` of the
base and the head `applications/yadgar.yaml` and writes `moved=false` for a
PR that does not move it. Every later step of the job is gated on `moved`, so
such a PR installs no helm and renders nothing.

BASE AND HEAD COME FROM THE MERGE COMMIT. On `pull_request`, the checkout is
`refs/pull/<n>/merge`: its first parent is the base the merge was made on and
its second parent is the PR head. `texts` reads the base through
`git show HEAD^1:` (the job checks out with `fetch-depth: 2`) and refuses
unless `HEAD^2` is the event's head sha. The head text is the merge result,
which is what merging the PR makes main.

`judge`, in order:

0. One pin per side. On both texts the `targetRevision:` line regex and the
   YAML parse must read the same value. Otherwise a duplicate key the regex
   misses (`targetRevision : X`, `"targetRevision": X`) wins the parse, and
   the job would say "no pin move" for a PR that moves the pin: FAIL.
   An AUTOMATIC PR is a same-repository head named `auto/pin-X.Y.Z` or
   `auto/pin-X.Y.Z-<sha12>` (C5's form). The branch alone decides, never the
   author (the release App also opens PRs that are no pin, argocd#135) and
   never a label. It must move the pin, and the merge commit must change exactly the three
   mechanical files (`pin_bump.MECHANICAL_FILES`): FAIL otherwise, in
   `detect` already. C3 does NOT judge non-pin edits on a HUMAN head;
   K3, two-owners and review cover those.
1. Pin-only. The head `yadgar.yaml` must be the base one with the one
   `targetRevision:` line rewritten (`pin_bump.rewrite_target_revision`), byte
   for byte. A values change in the same PR is not a pin-only PR: FAIL, before
   any render (plan, E1).
2. The direction. A prerelease or unreadable target: FAIL. A target lower than
   the base pin is a downgrade (a revert, plan section 6). `pin_bump.check` and
   `pin_dryrun.py` judge forward moves only, so a downgrade is not judged:
   FAIL on an automatic head, WARN on a human one.
3. The helms: exactly v3.18.4 and v4.3.0 (`HELMS`), installed by
   `install-helms` from get.helm.sh and checked against the sha256 below. A
   missing one: FAIL.
4. E1, E3 and E4: `pin_bump.check`, the C1 classifier, unchanged. It renders
   the base pin and the head pin with the HEAD `valuesObject` on both helms,
   and its reasons carry the fail-closed rules of the C1 review: the two
   helms' per-object digests must agree at both pins, an env read whose key
   cannot be resolved is red, a moved `yadgar-*` crate tag is red, and a moved
   `yadgar-store` tag on a `-db` module is red.

   MEASURED 2026-10-24, NOT ASSUMED: the two helms' raw `helm template`
   output is NOT byte-identical at 0.34.8. Helm v4.3.0 prints one blank line
   before each of the 96 `---` separators that v3.18.4 does not, and the 94
   parsed objects are equal. So the comparison is per object, over the
   canonical JSON digests `classify` already takes (the K3 table's own
   digests), and never over raw bytes.

   A NEEDS-HUMAN verdict FAILS an automatic PR (step 0: a same-repository
   head `auto/pin-X.Y.Z` or `auto/pin-X.Y.Z-<sha12>`, by branch alone) and
   WARNS on a human one. A human pin PR is the human
   review that NEEDS-HUMAN asks for: argocd#139 crossed task-db migration 7,
   C1's verdict was NEEDS-HUMAN, and Max reviewed it by hand and merged it.
   Failing that verdict on every PR would make a migration unmergeable until
   C10, and a moved `yadgar-store` unmergeable for good (C10 does not lift it).
5. E2: the commit status `pin / kind-dryrun` on the PR head sha (never the
   merge commit, which carries no status). It counts only when EVERY status
   of that context on the sha is `success`, created by `github-actions[bot]`,
   and its description parses as `auto_pin.DESCRIPTION` with check `e2`,
   result `green`, `sha=` the head, `main=` the merge commit's first parent
   and a run id (yaadgaar #420: no substring match, and a forged green after
   a red cannot win). The creator check cannot tell one argocd workflow from
   another; the dispatch App's identity is checked on the argocd-verify run
   itself by `auto_pin_merge.py`, which holds the token this job must not.
   A downgrade is exempt (`pin_dryrun.py` refuses one, so its status can
   never exist).

   ON AN AUTOMATIC HEAD E2 IS ALWAYS REQUIRED: a missing or non-green E2
   FAILS. C5's `auto-pin.yaml` posts `pin / kind-dryrun` on the branch head
   BEFORE it opens the PR, so the status exists on this job's first run.

   ON A HUMAN HEAD E2 IS REPORTED, NOT REQUIRED (`E2_REQUIRED = False`): a
   missing or non-green E2 is a WARN. A human pin PR gets the status only
   when someone runs `auto-pin.yaml`'s `evidence-only` dispatch for it, and
   until that is the routine, requiring it would block every hand pin
   (argocd#139's head `4548255` carries no commit status at all, measured
   2026-10-24). Setting `E2_REQUIRED = True` later requires it on human heads
   too; that is a separate decision, not part of any car yet.

Exit codes: 0 pass, 1 fail.

Tests: `scripts/tests/test_pin_evidence.py` (offline, in the
`operator-applications` hook).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import auto_pin  # noqa: E402  (stdlib only: the one status description form, `auto_pin.DESCRIPTION`)
import pin_bump as pb  # after the path insert: scripts/ is not a package

# Human heads only: report, do not require. An automatic head always requires E2.
E2_REQUIRED = False
E2_CONTEXT = "pin / kind-dryrun"
E2_CREATOR = "github-actions[bot]"

# Pinned by version AND by the sha256 of get.helm.sh's linux-amd64 tarball,
# checked on 2026-10-24 against both get.helm.sh's `.sha256sum` and the
# helm/helm GitHub release notes. v3.18.4 is the helm Argo CD v3.1.8 bundles.
HELMS = {
    "v3.18.4": "f8180838c23d7c7d797b208861fecb591d9ce1690d8704ed1e4cb8e2add966c1",
    "v4.3.0": "86584a54def73570558f66f5111cc53dfed56689637ae32c1201205d494f54fb",
}
HELM_URL = "https://get.helm.sh/helm-{version}-linux-amd64.tar.gz"
HELM_MEMBER = "linux-amd64/helm"

# The branch alone marks a PR automatic. `auto/pin-<v>` or C5's
# `auto/pin-<v>-<sha12>` (argocd#141). Never the author: the release App
# `yadgarhq-bot[bot]` also opens PRs that are no pin (argocd#135,
# `actions-adopt`, a hooks bump). Never a label: anyone with triage sets one.
AUTO_BRANCH = re.compile(r"^auto/pin-\d+\.\d+\.\d+(-[0-9a-f]{12})?$")
SHA = re.compile(r"^[0-9a-f]{40}$")


class Refused(Exception):
    """The job cannot judge as asked."""


@dataclass
class Pr:
    head_sha: str
    head_ref: str
    automatic: bool
    repository: str
    # The base E2 must name (`main=`). `pr_of` reads the event's; `main` replaces it with
    # the merge commit's first parent (`merge_parent`), the base this job actually renders.
    base_sha: str = ""


@dataclass
class Outcome:
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    evidence: dict | None = None

    @property
    def passed(self) -> bool:
        return not self.failures


def pr_of(event: dict) -> Pr:
    """The fields of a `pull_request` event this job reads."""
    pull = event["pull_request"]
    head, base = pull["head"], pull["base"]
    repository = base["repo"]["full_name"]
    same_repository = (head.get("repo") or {}).get("full_name") == repository
    sha = str(head["sha"])
    if not SHA.match(sha):
        raise Refused(f"the event's head sha {sha!r} is not a full commit sha")
    automatic = same_repository and bool(AUTO_BRANCH.fullmatch(str(head["ref"])))
    return Pr(sha, str(head["ref"]), automatic, repository, str(base.get("sha") or ""))


def merge_parent(repo: Path) -> str:
    """The merge commit's first parent: the base the PR is merged onto and rendered against."""
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD^1"], capture_output=True, text=True, check=True).stdout.strip()


def texts(repo: Path, head_sha: str) -> tuple[str, str]:
    """`(base yadgar.yaml, head yadgar.yaml)` of the merge commit checked out at `repo`."""

    def git(*arguments: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, text=True, check=True).stdout

    parents = git("rev-list", "--parents", "-n", "1", "HEAD").split()
    if len(parents) != 3 or parents[2] != head_sha:
        raise Refused(f"HEAD is not the merge commit of the PR head {head_sha} (parents: {parents[1:]})")
    return git("show", f"{parents[1]}:{pb.APPLICATION}"), (repo / pb.APPLICATION).read_text()


def changed_files(repo: Path) -> list[str]:
    """The paths the PR changes: the merge commit against its first parent, the base."""
    finished = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-only", "--no-renames", "HEAD^1", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return sorted(finished.stdout.split())


def pin_of(text: str, side: str) -> str:
    """The pin the line regex reads, refused unless the YAML parse reads the same value.

    A second key the regex misses (`targetRevision : X`, `"targetRevision": X`)
    wins in PyYAML, as in any last-key reader, so the line edit and the parse
    would name different pins: a false "no pin move".
    """
    lined, parsed = pb.current_pin(text), pb.parsed_revision(text)
    if lined != parsed:
        raise Refused(
            f"the {side} {pb.APPLICATION}: the targetRevision line reads {lined} but YAML reads "
            f"spec.source.targetRevision as {parsed} (a duplicate key?)"
        )
    return lined


def move_of(base_text: str, head_text: str) -> tuple[str, str] | None:
    """`(base pin, head pin)`, or None when `targetRevision` did not move."""
    pin, target = pin_of(base_text, "base"), pin_of(head_text, "head")
    return None if pin == target else (pin, target)


def automatic_problem(pr: Pr, move: tuple[str, str] | None, changed: list[str]) -> str | None:
    """Why an automatic PR is not a pure pin PR, or None. A human head is not judged here."""
    if not pr.automatic:
        return None
    if move is None:
        return f"an automatic pin PR ({pr.head_ref}) must move targetRevision, and this one does not"
    if sorted(changed) != sorted(pb.MECHANICAL_FILES):
        return f"an automatic pin PR must change exactly {', '.join(pb.MECHANICAL_FILES)}; it changes {', '.join(changed) or 'nothing'}"
    return None


def evidence(head_text: str, pin: str, target: str, helms: dict[str, str], net=None) -> dict:
    """E1, E3 and E4 by `pin_bump.check`: both pins rendered with the HEAD `valuesObject`."""
    with tempfile.TemporaryDirectory() as scratch:
        tree = Path(scratch)
        (tree / "applications").mkdir()
        (tree / pb.APPLICATION).write_text(pb.rewrite_target_revision(head_text, pin)[0])
        return pb.check(tree, target, net or pb.Net(), helms)


def statuses(repository: str, sha: str) -> list[dict]:
    """The commit statuses of `sha`, newest first, with `GITHUB_TOKEN` when set (never printed)."""
    url = f"https://api.github.com/repos/{repository}/commits/{sha}/statuses?per_page=100"
    with pb.Net().get(url, {"Accept": "application/vnd.github+json"}) as response:
        return json.load(response)


def e2_problem(found: list[dict], sha: str, base: str) -> str | None:
    """None when EVERY `pin / kind-dryrun` on `sha` is a green bot status for this head and this base; else why not.

    YAAGAAR #420. Any workflow in argocd can post a status as
    `github-actions[bot]`, so neither the creator nor the newest status can
    decide alone. Each one must parse as `auto_pin.DESCRIPTION` exactly (no
    substring match) with check `e2`, result `green`, `sha` the head, `main`
    the base and a run id. A forged green therefore cannot outvote the bot's
    red; it can only make E2 red. What binds E2 to the dispatch App is the
    argocd-verify run itself, which `auto_pin_merge.py` reads with the
    settled-dispatch token before it merges; this job holds no secret.
    """
    mine = [status for status in found if status.get("context") == E2_CONTEXT]
    if not mine:
        return f"no `{E2_CONTEXT}` status on {sha}"
    for status in mine:
        state = status.get("state")
        creator = (status.get("creator") or {}).get("login")
        if state != "success":
            return f"`{E2_CONTEXT}` on {sha} is {state}"
        if creator != E2_CREATOR:
            return f"`{E2_CONTEXT}` on {sha} was created by {creator}, not {E2_CREATOR}"
        text = str(status.get("description") or "")
        parsed = auto_pin.parse_status_description(text)
        if not parsed:
            return f"`{E2_CONTEXT}` on {sha}: description {text[:160]!r} is not the form `e2 <result> sha=<40 hex> main=<40 hex> run=<id>`"
        if parsed["check"] != "e2":
            return f"`{E2_CONTEXT}` on {sha} carries check {parsed['check']}, not e2"
        if parsed["result"] != "green":
            return f"`{E2_CONTEXT}` on {sha} records result {parsed['result']}, not green"
        if parsed["sha"] != sha:
            return f"`{E2_CONTEXT}` on {sha} names judged sha {parsed['sha']}, not the head"
        if parsed["main"] != base:
            return f"`{E2_CONTEXT}` on {sha} was judged against main {parsed['main']}, not the base {base or 'unknown'}"
        if "run" not in parsed:
            return f"`{E2_CONTEXT}` on {sha} names no run"
    return None


def judge(
    pr: Pr,
    base_text: str,
    head_text: str,
    *,
    evidence_of: Callable[[str, str, str, dict], dict],
    statuses_of: Callable[[str], list[dict]],
    helms: dict[str, str],
    changed: list[str],
    e2_required: bool = E2_REQUIRED,
) -> Outcome:
    """The verdict of `pin-evidence` on one PR (see the module docstring for the order)."""
    outcome = Outcome()
    kind = "automatic" if pr.automatic else "human"
    try:
        move = move_of(base_text, head_text)
    except Refused as refused:
        outcome.failures.append(str(refused))
        return outcome
    if problem := automatic_problem(pr, move, changed):
        outcome.failures.append(problem)
        return outcome
    if move is None:
        outcome.notes.append(f"no pin move: targetRevision stays {pb.current_pin(base_text)}")
        return outcome
    pin, target = move
    outcome.notes.append(f"{kind} pin PR: targetRevision {pin} -> {target}")

    if head_text != pb.rewrite_target_revision(base_text, target)[0]:
        outcome.failures.append(
            f"not a pin-only change: {pb.APPLICATION} differs from the base beyond the one targetRevision line "
            "(a values change beside a pin move is a separate PR)"
        )
        return outcome
    try:
        pb.decide(pin, target)
    except pb.Refused as refused:
        if pb.release(target) is not None and pb.release(pin) is not None:
            lower = f"target {target} is lower than the pin {pin}: a downgrade (a revert) is not judged by E1-E4"
            (outcome.failures if pr.automatic else outcome.warnings).append(lower)
        else:
            outcome.failures.append(str(refused))
        return outcome

    missing = [version for version in HELMS if version not in helms]
    if missing:
        outcome.failures.append(f"helm {', '.join(missing)} not installed; E1 renders on exactly {', '.join(HELMS)}")
        return outcome
    found = evidence_of(head_text, pin, target, {version: helms[version] for version in HELMS})
    outcome.evidence = found
    if not found["accept"]:
        block = outcome.failures if pr.automatic else outcome.warnings
        block += [f"NEEDS-HUMAN: {reason}" for reason in found["reasons"]]
        if not pr.automatic:
            outcome.notes.append("a human pin PR: NEEDS-HUMAN reasons are reported for the human reviewer, not enforced")

    try:
        problem = e2_problem(statuses_of(pr.head_sha), pr.head_sha, pr.base_sha)
    except Exception as error:  # noqa: BLE001  (an unreadable status is a missing status)
        problem = f"`{E2_CONTEXT}` on {pr.head_sha} could not be read ({type(error).__name__}: {error})"
    # An automatic head always requires E2: C5 posts the status before it opens the PR.
    required = e2_required or pr.automatic
    if problem:
        mode = "required" if required else "report mode on a human head"
        (outcome.failures if required else outcome.warnings).append(f"E2 ({mode}): {problem}")
    else:
        outcome.notes.append(f"E2: `{E2_CONTEXT}` is success on {pr.head_sha}")
    return outcome


# ── helm install ────────────────────────────────────────────────────────────


def download(version: str) -> bytes:
    with pb.Net().get(HELM_URL.format(version=version)) as response:
        return response.read()


def install_helms(directory: Path, fetch: Callable[[str], bytes] = download) -> dict[str, str]:
    """`{version: binary}`, every tarball checked against `HELMS` before anything is extracted."""
    blobs = {}
    for version, expected in HELMS.items():
        blob = fetch(version)
        actual = hashlib.sha256(blob).hexdigest()
        if actual != expected:
            raise Refused(f"helm {version} tarball sha256 {actual} is not the pinned {expected}")
        blobs[version] = blob
    installed = {}
    for version, blob in blobs.items():
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
            member = archive.extractfile(HELM_MEMBER)
            if member is None:
                raise Refused(f"helm {version} tarball has no {HELM_MEMBER}")
            binary = directory / version / "helm"
            binary.parent.mkdir(parents=True, exist_ok=True)
            binary.write_bytes(member.read())
        binary.chmod(binary.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        installed[version] = str(binary)
    return installed


# ── CLI ─────────────────────────────────────────────────────────────────────


def summary_lines(outcome: Outcome) -> list[str]:
    lines = [f"## pin-evidence: {'pass' if outcome.passed else 'FAIL'}", ""]
    lines += [f"- {note}" for note in outcome.notes]
    lines += [f"- **warning:** {warning}" for warning in outcome.warnings]
    lines += [f"- **failure:** {failure}" for failure in outcome.failures]
    if outcome.evidence:
        found = outcome.evidence
        lines += ["", f"Helms: {', '.join(found['helms'])}. Changed: {len(found['changed'])} image(s)."]
        for name in ("boot_audit", "migrations"):
            for module, result in sorted(found[name].items()):
                lines.append(f"- {name} {module}: {result['status']}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    commands = parser.add_subparsers(dest="command", required=True)
    detect = commands.add_parser("detect")
    detect.add_argument("--event", type=Path, required=True)
    detect.add_argument("--output", type=Path, required=True)
    install = commands.add_parser("install-helms")
    install.add_argument("--dir", type=Path, required=True)
    install.add_argument("--env", type=Path, required=True)
    run = commands.add_parser("judge")
    run.add_argument("--event", type=Path, required=True)
    run.add_argument("--summary", type=Path)
    for sub in (detect, run):
        sub.add_argument("--repo", type=Path, default=pb.REPOSITORY, help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)

    try:
        if arguments.command == "install-helms":
            installed = install_helms(arguments.dir)
            with arguments.env.open("a") as env:
                env.write(f"PIN_BUMP_HELMS={os.pathsep.join(installed.values())}\n")
            print(f"installed helm {', '.join(installed)}, each matching its pinned sha256")
            return 0
        pr = pr_of(json.loads(arguments.event.read_text()))
        base_text, head_text = texts(arguments.repo, pr.head_sha)
        pr.base_sha = merge_parent(arguments.repo)
        changed = changed_files(arguments.repo)
        if arguments.command == "detect":
            move = move_of(base_text, head_text)
            if problem := automatic_problem(pr, move, changed):
                raise Refused(problem)
            lines = ["moved=false"] if move is None else ["moved=true", f"pin={move[0]}", f"target={move[1]}"]
            with arguments.output.open("a") as output:
                output.write("\n".join(lines) + "\n")
            print("no pin move" if move is None else f"pin move {move[0]} -> {move[1]}")
            return 0
        outcome = judge(
            pr,
            base_text,
            head_text,
            evidence_of=evidence,
            statuses_of=lambda sha: statuses(pr.repository, sha),
            helms=pb.discover_helms(),
            changed=changed,
        )
    except (Refused, pb.Refused) as refused:
        print(f"::error::pin-evidence refused: {refused}")
        return 1
    except subprocess.CalledProcessError as failed:
        detail = (failed.stderr or "").strip().replace("\n", " ")
        print(f"::error::pin-evidence: `{' '.join(map(str, failed.cmd))}` exited {failed.returncode}: {detail}")
        return 1

    for note in outcome.notes:
        print(note)
    for warning in outcome.warnings:
        print(f"::warning::{warning}")
    for failure in outcome.failures:
        print(f"::error::{failure}")
    if arguments.summary:
        with arguments.summary.open("a") as summary:
            summary.write("\n".join(summary_lines(outcome)) + "\n")
    return 0 if outcome.passed else 1


if __name__ == "__main__":
    sys.exit(main())
