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
base and the head `applications/yadgar.yaml`, and, when that does not move,
the platform version the six `PLATFORM_FILES` name (the five operator
Applications' `targetRevision` and `chart_pin.json`'s `platform_version`). It
writes `moved=false` for a PR that moves neither. Every later step of the job
is gated on `moved`, so such a PR installs no helm and renders nothing.

A PLATFORM-ONLY MOVE IS JUDGED TOO (yaadgaar #514 option (a), Max 2026-11-02).
A PR that leaves the yadgar pin alone and moves the platform writes
`moved=true` with `platform=` and `platform_target=`. A move is read from the
versions, never the bytes: an edit that leaves all six versions alone is no
move. AN OPERATOR VALUES EDIT THAT MOVES NO VERSION IS `moved=false` HERE, and
pin-evidence does not judge it: only the D4 clauses of the `operator-applications`
pre-commit hook (`scripts/tests/test_operator_applications.py`,
`platform_clause_errors`) bind those values. The six move together: on either side they must name one version, so a
partial move (`platform_version` alone, the operators alone, some of them) FAILS
in `detect` already, as does a missing or duplicate-keyed file (`chart_pin.json`
is read by `strict_json`, one parse). A pin move beside them is bound too: on
a pin move the files must move exactly as the target chart's `platform` does,
the move E1 judged (no move, the same move, never one without the other), or
the PR FAILS on any head (argocd#230 review). And on every PR, `detect` and
`judge` refuse Applications that are not the known ones
(`unknown_applications`, an allowlist, `KNOWN_APPLICATIONS`): any file under
`applications/` (recursive, as root's include, any suffix) not on the list, a
symlink there, an ApplicationSet, a known non-operator file that sources the
platform, and an Application document anywhere else in the tree outside
scripts/ (an app-of-apps child). The six files are a fixed list, so a sixth
platform Application would move unjudged. `judge` then
(`judge_platform`): the six files and `yadgar.yaml` must be the base with only
the version lines moved, byte for byte (`platform_evidence` renders the HEAD
values at both versions, so a values change beside the move would go unjudged):
FAIL otherwise, before any render. Both versions must be releases; a lower one
is judged like any other (E1 compares both directions), never skipped. Both
helms must be installed. E1 is `pin_bump.platform_evidence`, the operator
classifier `classify_platform`, with `yadgar` rendered at its one pin joining
the selector set (ADR-1095). A NEEDS-HUMAN verdict FAILS, as on a pin (step 4),
and on a human head it clears only through a NEW `ACK_FILE` entry whose
`target` is `platform:<to>`. An automatic head never gets here: it must move
`targetRevision` (step 0); automatic platform moves go through auto-pin with
the pin. E2 is read as on a pin, reported on a human head.

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
   two-owners and review cover those (K3 is gone, argocd#193). The pin
   move's own NEEDS-HUMAN verdict is enforced on a human head (step 4).
   ONE EXCEPTION, ON EVERY PR (ADR-1103): a merge commit that changes
   `scripts/gates/migration_seed_allowlist.json` FAILS, in `detect` already,
   as a hard failure that no acknowledgement clears, unless it moves no pin
   and changes no other file. An allowlist change lands only in its own
   non-pin PR, never beside a gate edit, a platform move or a chart pin.
1. Pin-only. The head `yadgar.yaml` must be the base one with the one
   `targetRevision:` line rewritten (`pin_bump.rewrite_target_revision`), byte
   for byte. A values change in the same PR is not a pin-only PR: FAIL, before
   any render (plan, E1). After the helm check (3.), all three mechanical files
   are bound too (yaadgaar #424 item 3): `regenerated_differences` seeds a tree
   with the base's three files and the checkout's two writers, runs
   `pin_bump.write_files` for the target, and compares each file with the head
   byte for byte. A difference, or a regeneration that fails, FAILS an
   automatic head and WARNS on a human one.
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
   `yadgar-store` tag on a `-db` module is red. E4 admits a seed INSERT only
   by the BASE's `scripts/gates/migration_seed_allowlist.json` (`evidence`
   writes the HEAD^1 copy into its tree, ADR-1099): an entry a PR adds admits
   nothing in that PR.

   MEASURED 2026-10-24, NOT ASSUMED: the two helms' raw `helm template`
   output is NOT byte-identical at 0.34.8. Helm v4.3.0 prints one blank line
   before each of the 96 `---` separators that v3.18.4 does not, and the 94
   parsed objects are equal. So the comparison is per object, over the
   canonical JSON digests `classify` already takes (the K3 table's own
   digests), and never over raw bytes.

   A NEEDS-HUMAN verdict FAILS an automatic PR (step 0: a same-repository
   head `auto/pin-X.Y.Z` or `auto/pin-X.Y.Z-<sha12>`, by branch alone),
   always. On a HUMAN head it FAILS too, as `NEEDS-HUMAN-UNACKNOWLEDGED`,
   unless the PR commits the human's acknowledgement (Max, 2026-10-30: with
   K3 dropped, argocd#193 and ADR-1075, a hand pin PR that changes non-image
   fields had no hard red at all). The acknowledgement is an entry in
   `ACK_FILE`, `scripts/pin_needs_human_ack.json`:
   `{"target": X.Y.Z, "reasons": [...], "why": "..."}` (`platform:X.Y.Z` on a
   platform-only move; any other target shape is refused). It clears the verdict
   only when its `target` is this PR's target AND its `reasons` hold every
   reason of the verdict, string for string; any reason it does not name
   stays a failure. The failure prints the exact entry to commit. Nothing
   else clears it: a label is set by anyone with triage, and a review
   approval does not re-run this job (`ci.yaml` runs on `opened`,
   `synchronize`, `reopened` and `edited` only). The ack is a commit in the
   reviewed diff, and a commit re-runs this check; it also leaves the
   acknowledged reasons in main's history. The file is read from the merge
   checkout (`main`), never from the event.

   THE CLEARING ENTRY MUST BE NEW IN THIS PR. An entry already on the base,
   equal after parsing (same target, same reasons in the same order, same
   why), clears nothing (`base_ack_text` reads the base's
   copy from the merge commit's first parent; no file there is no entry).
   Otherwise an ack merged earlier for the same target, by a revert and a
   re-pin or by an ack-only PR merged first, would clear a later PR nobody
   acknowledged. To re-acknowledge, the PR rewrites the existing entry (a
   new `why` at least). That passes this job, but `pin_dryrun` step 1 (ADR-1094)
   takes the file as an append only, so E2 (`pin / kind-dryrun`) stays refused
   for a re-ack of a target already on main: run the hand dry run. A malformed file on either side FAILS
   (`acknowledgements`): a half-filled acknowledgement reads as reviewed when
   it was not (the `crd_removals_ack.json` rule).

   This keeps argocd#139's route open: that PR crossed task-db migration 7,
   C1's verdict was NEEDS-HUMAN, and Max reviewed it by hand and merged it.
   Under this rule the same PR commits one ack entry naming that reason. A
   migration, or a moved `yadgar-store`, stays mergeable by hand.
5. E2: the commit status `pin / kind-dryrun` on the PR head sha (never the
   merge commit, which carries no status). It counts only when EVERY status
   of that context on the sha is `success`, created by `yadgarhq-settled-dispatch[bot]`,
   and its description parses as `auto_pin.DESCRIPTION` with check `e2`,
   result `green`, `sha=` the head, `main=` the merge commit's first parent
   and a run id (yaadgaar #420: no substring match, and a forged green after
   a red cannot win). The creator is the settled-dispatch App, whose token
   auto-pin mints for argocd with `statuses: write` only (yaadgaar #422): a
   job's GITHUB_TOKEN posts as `github-actions[bot]`, so such a status is
   refused. The App's key is an argocd repository secret, so an argocd
   workflow that names it can still post as the App; the dispatch App's
   identity is checked on the argocd-verify run itself by
   `auto_pin_merge.py`, which holds the token this job must not.
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
import shutil
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
import yaml  # noqa: E402  (pin_bump needs it too; the job installs PyYAML before `detect`)

# Human heads only: report, do not require. An automatic head always requires E2.
E2_REQUIRED = False
# A human head's acknowledgement of a NEEDS-HUMAN verdict, by target (module docstring, step 4).
ACK_FILE = "scripts/pin_needs_human_ack.json"
ACK_FIELDS = ("target", "reasons", "why")
# An ack's target: a yadgar pin `X.Y.Z`, or `platform:X.Y.Z` for a platform-only move (yaadgaar #514).
ACK_PLATFORM_PREFIX = "platform:"
ACK_TARGET = re.compile(r"^(platform:)?[0-9]+\.[0-9]+\.[0-9]+$")
# The six files that name the platform version: the five operator Applications and `chart_pin.json` (car C10).
PLATFORM_FILES = (*pb.OPERATOR_APPLICATIONS, pb.CHART_PIN)
# The `why` the failure prints. Committed unchanged, it says nobody wrote what was checked: refused.
ACK_WHY_PLACEHOLDER = "<what the reviewer checked, per reason>"
E2_CONTEXT = "pin / kind-dryrun"
# auto-pin posts E2 with a settled-dispatch App token; any argocd workflow's GITHUB_TOKEN posts as github-actions[bot] (yaadgaar #422).
E2_CREATOR = "yadgarhq-settled-dispatch[bot]"

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
# The two writers `pin_bump.write_files` runs for the K3 table, copied (never linked: both resolve their
# repository from `Path(__file__).resolve()`) into the regeneration tree, as `test_pin_bump_replay` does.
WRITERS = ("test_yadgar_application.py", "test_no_two_owners.py")

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


def platform_texts(repo: Path) -> tuple[dict[str, str | None], dict[str, str | None]]:
    """`(base, head)` texts of `PLATFORM_FILES`: the base from the merge commit's first parent, the head from the
    checkout. A file a side does not have is None."""

    def text(raw: bytes | None) -> str | None:
        return None if raw is None else raw.decode()

    base = {path: text(base_file_if_any(repo, path)) for path in PLATFORM_FILES}
    head = {path: (repo / path).read_text() if (repo / path).exists() else None for path in PLATFORM_FILES}
    return base, head


def strict_json(text: str):
    """`json.loads` that refuses a duplicate key instead of keeping the last (as `pin_dryrun.strict_json`)."""

    def pairs(items: list) -> dict:
        found: dict = {}
        for key, value in items:
            if key in found:
                raise Refused(f"duplicate key {key!r}: two readers may read it apart")
            found[key] = value
        return found

    return json.loads(text, object_pairs_hook=pairs)


def platform_versions(files: dict[str, str | None], side: str) -> dict[str, str]:
    """`{path: platform version}` of one side: each operator Application's `targetRevision` (line regex and YAML
    parse must agree, as `pin_of`) and `chart_pin.json`'s `platform_version`. A missing or unreadable file is refused."""
    found: dict[str, str] = {}
    for path in PLATFORM_FILES:
        text = files.get(path)
        if text is None:
            raise Refused(f"the {side} has no {path}; a platform move moves all five operator Applications and {pb.CHART_PIN}")
        try:
            if path == pb.CHART_PIN:
                version = strict_json(text).get("platform_version")
                if not isinstance(version, str) or not version:
                    raise Refused(f"the {side} {path} has no platform_version string")
            else:
                version = pb.current_pin(text, path)
                parsed = pb.parsed_revision(text)
                if parsed != version:
                    raise Refused(
                        f"the {side} {path}: the targetRevision line reads {version} but YAML reads "
                        f"spec.source.targetRevision as {parsed} (a duplicate key?)"
                    )
        except (ValueError, AttributeError, KeyError, TypeError, yaml.YAMLError, pb.Refused) as error:
            raise Refused(f"the {side} {path} could not be read for its platform version: {error}") from error
        found[path] = version
    return found


# THE APPLICATIONS ROOT SYNCS (argocd#230 review F2). Root's `include: applications/*.yaml` lets `*` cross `/`, so
# any file under applications/ becomes an Application, and `PLATFORM_FILES` is a fixed list: a sixth Application that
# deploys the platform (by `chart: platform`, an OCI or git `repoURL` with a `path`, a templated ApplicationSet, or an
# app-of-apps child) would move with no platform move detected and no E1. So the guard is an allowlist, not a
# heuristic: a new Application is an edit here AND in the gate that pins its shape (test_operator_applications'
# ADOPTED, test_infra_children's CHILDREN, test_runner_image_pinned, test_pin_estate_selectors' ESTATE), and
# `test_known_applications_is_the_union_of_the_other_gates_lists` holds the two equal.
KNOWN_APPLICATIONS = tuple(
    f"applications/{name}.yaml"
    for name in (
        "arc", "argocd", "cert-manager", "envoy-gateway", "estate-front", "estate-front-runner", "keda",
        "mariadb-operator", "post-merge-verifier", "post-merge-verifier-runner", "prometheus", "tls", "yadgar",
    )
)
# Where an Application document may sit: the allowlist and root itself. scripts/ holds test fixtures only; nothing
# syncs it (root includes applications/ alone, and no known Application sources scripts/).
APPLICATION_HOMES = ("applications/", "projects/root.yaml")
APPLICATION_KINDS = ("Application", "ApplicationSet")


def sources_platform(document) -> bool:
    """True when any mapping inside `document` names the `platform` chart (`chart: platform`) or a `repoURL` whose
    last segment is `platform` (an OCI or git source with a `path`), at any depth. An extra check on the known
    non-operator files; the guard is `KNOWN_APPLICATIONS`."""
    if isinstance(document, dict):
        url = str(document.get("repoURL") or "").rstrip("/").removesuffix(".git")
        if document.get("chart") == pb.PLATFORM or url.rsplit("/", 1)[-1] == pb.PLATFORM:
            return True
        return any(sources_platform(v) for v in document.values())
    if isinstance(document, list):
        return any(sources_platform(v) for v in document)
    return False


def documents_of(path: Path) -> list:
    return [d for d in yaml.safe_load_all(path.read_text()) if d is not None]


def unknown_applications(repo: Path) -> list[str]:
    """Why `repo`'s Applications are not the known ones, one line per problem; empty when they are (F2).

    Under applications/ (recursive, any suffix): a symlink, a path not in `KNOWN_APPLICATIONS`, an unparseable known
    file, an ApplicationSet, and a known file other than the five operator Applications that sources the platform.
    Elsewhere in `repo` (less `.git` and scripts/): an Application or ApplicationSet document outside
    `APPLICATION_HOMES`, the child an app-of-apps wrapper would sync, and an unparseable YAML or JSON file.
    `main` refuses every one, on every PR, in `detect` and `judge`.
    """
    problems: list[str] = []
    applications = repo / "applications"
    for path in sorted(applications.rglob("*")) if applications.is_dir() else []:
        relative = path.relative_to(repo).as_posix()
        if path.is_symlink():
            problems.append(f"{relative}: a symlink under applications/; refused, Argo CD and this check may read it apart")
            continue
        if path.is_dir():
            continue
        if relative not in KNOWN_APPLICATIONS:
            problems.append(f"{relative}: not one of the {len(KNOWN_APPLICATIONS)} known Applications (KNOWN_APPLICATIONS)")
            continue
        try:
            documents = documents_of(path)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            problems.append(f"{relative}: could not be read: {error}")
            continue
        if any(isinstance(d, dict) and d.get("kind") == "ApplicationSet" for d in documents):
            problems.append(f"{relative}: holds an ApplicationSet; its generated Applications are not known ones")
        if relative not in pb.OPERATOR_APPLICATIONS and any(sources_platform(d) for d in documents):
            problems.append(f"{relative}: sources the {pb.PLATFORM} chart but is not one of the five operator Applications")
    for path in sorted(repo.rglob("*")):
        relative = path.relative_to(repo).as_posix()
        top = relative.split("/", 1)[0]
        if top in (".git", "scripts", "applications") or relative in APPLICATION_HOMES:
            continue
        if not path.is_file() or path.suffix not in (".yaml", ".yml", ".json"):
            continue
        try:
            documents = documents_of(path)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as error:
            problems.append(f"{relative}: could not be read for an Application document: {error}")
            continue
        if any(isinstance(d, dict) and d.get("kind") in APPLICATION_KINDS for d in documents):
            problems.append(f"{relative}: an Application or ApplicationSet outside applications/; an app-of-apps child is refused")
    return problems


def platform_move_of(base_files: dict[str, str | None], head_files: dict[str, str | None]) -> tuple[str, str] | None:
    """`(base platform, head platform)`, or None when no platform version moved (yaadgaar #514).

    A move is judged by the VERSIONS, never the bytes: an edit that leaves all six versions (`PLATFORM_FILES`) as
    they are is no move. On each side the six must agree; a move of some but not all of them is refused, because
    the operators and `platform_version` move together or not at all.
    """
    base, head = platform_versions(base_files, "base"), platform_versions(head_files, "head")
    if base == head:
        return None
    for side, versions in (("base", base), ("head", head)):
        if len(set(versions.values())) != 1:
            named = ", ".join(f"{path} {version}" for path, version in versions.items())
            raise Refused(
                f"a platform move: the {side}'s five operator Applications and {pb.CHART_PIN} platform_version "
                f"must move together and name one platform version; they name {named}"
            )
    return next(iter(base.values())), next(iter(head.values()))


def platform_only_problem(
    base_text: str, head_text: str, base_files: dict[str, str | None], head_files: dict[str, str | None], to: str
) -> str | None:
    """Why a platform-only move is more than the version lines, or None (the pin-only rule of step 1, for platform).

    `platform_evidence` renders each operator Application's HEAD values at both versions, so a values change beside
    the version move would be on both sides of E1 and never judged. Each operator head must be its base with the one
    `targetRevision` line moved, `chart_pin.json` its base with `platform_version` alone moved, and
    `applications/yadgar.yaml` unchanged, byte for byte.
    """
    differs = [] if head_text == base_text else [pb.APPLICATION]
    for path in PLATFORM_FILES:
        base = base_files[path] or ""
        try:
            if path == pb.CHART_PIN:
                pin = json.loads(base)
                expected = pb.rewrite_chart_pin(
                    base, str(pin.get("chart_tag", "")).removeprefix("v"), digest=str(pin.get("chart_digest", "")), platform=to
                )
            else:
                expected = pb.rewrite_target_revision(base, to, path)[0]
        except (ValueError, pb.Refused) as error:
            return f"not a platform-only change: {path} could not be rewritten from the base to platform {to}: {error}"
        if head_files[path] != expected:
            differs.append(path)
    if differs:
        return (
            f"not a platform-only change: {', '.join(differs)} differ(s) from the base beyond the platform version lines "
            f"moved to {to} (a values change beside a platform move is a separate PR)"
        )
    return None


def automatic_problem(pr: Pr, move: tuple[str, str] | None, changed: list[str]) -> str | None:
    """Why an automatic PR is not a pure pin PR, or None. A human head is not judged here."""
    if not pr.automatic:
        return None
    if move is None:
        return f"an automatic pin PR ({pr.head_ref}) must move targetRevision, and this one does not"
    three, eight = pb.pin_files(platform_moved=False), pb.pin_files(platform_moved=True)
    if sorted(changed) not in (sorted(three), sorted(eight)):
        return (
            f"an automatic pin PR must change exactly {', '.join(three)}, or those and all of "
            f"{', '.join(pb.OPERATOR_APPLICATIONS)} on a platform move; it changes {', '.join(changed) or 'nothing'}"
        )
    return None


def seed_allowlist_problem(move: tuple[str, str] | None, changed: list[str]) -> str | None:
    """Why a PR that changes the seed allowlist may not merge (ADR-1103), or None.

    Allowlist changes land ONLY in their own non-pin PR: a PR that changes the allowlist moves no pin (human or
    automatic) and changes no other file, not a gate, a platform move, a chart pin or a note. A HARD failure, never
    a NEEDS-HUMAN reason, so no acknowledgement clears it. A PR that leaves the allowlist alone is not judged here.
    """
    if pb.SEED_ALLOWLIST_FILE not in changed:
        return None
    if move is not None:
        return f"a pin PR may not change {pb.SEED_ALLOWLIST_FILE}: allowlist changes land in their own non-pin PR (ADR-1103)"
    if others := sorted(set(changed) - {pb.SEED_ALLOWLIST_FILE}):
        return f"a PR that changes {pb.SEED_ALLOWLIST_FILE} changes nothing else; it also changes {', '.join(others)} (ADR-1103)"
    return None


def base_file(repo: Path, path: str) -> bytes:
    """`path` at the merge commit's first parent, the base."""
    return subprocess.run(["git", "-C", str(repo), "show", f"HEAD^1:{path}"], capture_output=True, check=True).stdout


def base_file_if_any(repo: Path, path: str) -> bytes | None:
    """`path` at the merge commit's first parent, the base; None when the base has no such file."""

    def git(*arguments: str) -> bytes:
        return subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, check=True).stdout

    if not git("ls-tree", "HEAD^1", "--", path).strip():
        return None
    return git("show", f"HEAD^1:{path}")


def regenerated_differences(
    repo: Path, target: str, *, base_of: Callable[[str], bytes] | None = None, write: Callable[[Path, str], None] | None = None
) -> list[str]:
    """The pin files of the checkout `repo` that differ from what `pin_bump.write_files` makes for `target`.

    The tree holds the BASE's eight pin files (the three and the five operator Applications) and the checkout's
    two writers, nothing else, as `test_pin_bump_replay` builds it; `write_files` then moves it to `target` (helm
    v3.18.4 from `PIN_BUMP_HELMS`), the operator files only on a platform move (car C10). All eight are compared.
    """
    base_of = base_of or (lambda path: base_file(repo, path))
    write = write or pb.write_files
    paths = pb.pin_files(platform_moved=True)
    with tempfile.TemporaryDirectory() as scratch:
        tree = Path(scratch)
        for path in paths:
            (tree / path).parent.mkdir(parents=True, exist_ok=True)
            (tree / path).write_bytes(base_of(path))
        for writer in WRITERS:
            shutil.copyfile(repo / "scripts" / "gates" / writer, tree / "scripts" / "gates" / writer)
        write(tree, target)

        def read(root: Path, path: str) -> bytes | None:
            return (root / path).read_bytes() if (root / path).exists() else None

        return [path for path in paths if read(tree, path) != read(repo, path)]


def acknowledgements(path: Path) -> dict[str, dict]:
    """`ACK_FILE`'s entries keyed by target; no file is no acknowledgement. Anything unreadable or malformed is refused."""
    if not path.exists():
        return {}
    try:
        text = path.read_text()
    except OSError as error:
        raise Refused(f"{ACK_FILE} could not be read: {error}") from error
    return parse_acknowledgements(text)


def base_ack_text(repo: Path) -> str | None:
    """`ACK_FILE` at the merge commit's first parent, the base; None when the base has no such file."""

    def git(*arguments: str) -> str:
        return subprocess.run(["git", "-C", str(repo), *arguments], capture_output=True, text=True, check=True).stdout

    if not git("ls-tree", "HEAD^1", "--", ACK_FILE).strip():
        return None
    return git("show", f"HEAD^1:{ACK_FILE}")


def parse_acknowledgements(text: str | None) -> dict[str, dict]:
    """`ACK_FILE`'s text parsed into entries keyed by target; None (no file) is none. Malformed is refused."""
    if text is None:
        return {}
    try:
        entries = json.loads(text)
    except ValueError as error:
        raise Refused(f"{ACK_FILE} is not JSON: {error}") from error
    if not isinstance(entries, list):
        raise Refused(f"{ACK_FILE} must be a JSON list of entries, not {type(entries).__name__}")
    found: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict) or any(not entry.get(name) for name in ACK_FIELDS):
            raise Refused(f"{ACK_FILE} entry {entry!r} needs a non-empty {', '.join(ACK_FIELDS)}")
        reasons = entry["reasons"]
        typed = isinstance(entry["target"], str) and isinstance(entry["why"], str) and isinstance(reasons, list)
        if not typed or not all(isinstance(r, str) for r in reasons):
            raise Refused(f"{ACK_FILE} entry {entry!r}: target and why are strings and reasons a list of strings")
        if not ACK_TARGET.fullmatch(entry["target"]):
            raise Refused(f"{ACK_FILE} entry target {entry['target']!r} is neither a pin X.Y.Z nor {ACK_PLATFORM_PREFIX}X.Y.Z")
        if entry["why"] == ACK_WHY_PLACEHOLDER:
            raise Refused(f"{ACK_FILE} entry for {entry['target']}: replace the placeholder why with what the reviewer checked")
        if entry["target"] in found:
            raise Refused(f"{ACK_FILE} names target {entry['target']} twice")
        found[entry["target"]] = entry
    return found


def unacknowledged(target: str, reasons: list[str], acks: dict[str, dict], base_acks: dict[str, dict]) -> list[str]:
    """The NEEDS-HUMAN reasons this PR's own `ACK_FILE` entry for `target` does not name, string for string.

    An entry equal to the base's entry for `target` is not this PR's: it names nothing.
    """
    entry = acks.get(target)
    if entry is None or base_acks.get(target) == entry:
        return list(reasons)
    return [reason for reason in reasons if reason not in set(entry["reasons"])]


def evidence(
    head_text: str,
    pin: str,
    target: str,
    helms: dict[str, str],
    net=None,
    repo: Path | None = None,
    base_of: Callable[[str], bytes | None] | None = None,
) -> dict:
    """E1, E3 and E4 by `pin_bump.check`: both pins rendered with the HEAD `valuesObject`.

    The tree is the merge checkout `repo` as it is, less `.git`, with `applications/yadgar.yaml` alone moved to the
    base pin, and the seed allowlist replaced by the BASE's copy (`base_of`, default `base_file_if_any`; removed when
    the base has none): an entry this PR adds admits nothing in this PR (ADR-1099). A platform move renders each
    operator Application at both platform versions with its HEAD values (car C10), whatever its own `targetRevision`
    reads, and reads selectors from root and every Application it selects, with the directories they source (ADR-1095).
    """
    repo = repo or pb.REPOSITORY
    base_of = base_of or (lambda path: base_file_if_any(repo, path))
    seeds = base_of(pb.SEED_ALLOWLIST_FILE)
    with tempfile.TemporaryDirectory() as scratch:
        tree = Path(scratch) / "tree"
        shutil.copytree(repo, tree, ignore=shutil.ignore_patterns(".git"), symlinks=True)
        (tree / pb.APPLICATION).write_text(pb.rewrite_target_revision(head_text, pin)[0])
        allowlist = tree / pb.SEED_ALLOWLIST_FILE
        allowlist.unlink(missing_ok=True)
        if seeds is not None:
            allowlist.parent.mkdir(parents=True, exist_ok=True)
            allowlist.write_bytes(seeds)
        return pb.check(tree, target, net or pb.Net(), helms)


def platform_evidence(
    head_text: str, old: str, new: str, helms: dict[str, str], net=None, repo: Path | None = None
) -> dict:
    """The operator E1 of a platform-only move (yaadgaar #514): `pin_bump.platform_evidence` on the merge checkout.

    The tree is `repo` less `.git`. `yadgar` does not move, so it is rendered once per helm at its one pin with the
    HEAD values, and those documents join the selector set (`others`, ADR-1095) as `check` passes them on a pin move.
    The result has `check`'s shape (`accept`, `reasons`, `helms`, `changed`, the audits empty) so `judge` and
    `summary_lines` read it the same way, with the platform verdict under `platform`.
    """
    repo = repo or pb.REPOSITORY
    net = net or pb.Net()
    pin = pb.current_pin(head_text)
    with tempfile.TemporaryDirectory() as scratch:
        tree = Path(scratch) / "tree"
        shutil.copytree(repo, tree, ignore=shutil.ignore_patterns(".git"), symlinks=True)
        yadgar = [d for _, binary in sorted(helms.items()) for d in pb.render(head_text, pin, binary)]
        platform = pb.platform_evidence(tree, old, new, helms, net, yadgar)
    return {
        "pin": pin,
        "target": pin,
        "helms": sorted(helms),
        "objects": sum(platform["objects"].values()),
        "changed": platform["changed"],
        "boot_audit": {},
        "migrations": {},
        "platform": platform,
        "accept": not platform["reasons"],
        "reasons": list(platform["reasons"]),
    }


def statuses(repository: str, sha: str) -> list[dict]:
    """The commit statuses of `sha`, newest first, with `GITHUB_TOKEN` when set (never printed)."""
    url = f"https://api.github.com/repos/{repository}/commits/{sha}/statuses?per_page=100"
    with pb.Net().get(url, {"Accept": "application/vnd.github+json"}) as response:
        return json.load(response)


def e2_problem(found: list[dict], sha: str, base: str) -> str | None:
    """None when EVERY `pin / kind-dryrun` on `sha` is a green bot status for this head and this base; else why not.

    YAAGAAR #420, #422. The creator must be E2_CREATOR, the settled-dispatch App:
    any argocd workflow's GITHUB_TOKEN posts as `github-actions[bot]`. Still
    neither the creator nor the newest status decides alone. Each one must parse as `auto_pin.DESCRIPTION` exactly (no
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
    regenerated_of: Callable[[str], list[str]],
    acks: dict[str, dict] | None = None,
    base_acks: dict[str, dict] | None = None,
    e2_required: bool = E2_REQUIRED,
    platform_texts: tuple[dict[str, str | None], dict[str, str | None]] | None = None,
    platform_evidence_of: Callable[[str, str, str, dict], dict] | None = None,
) -> Outcome:
    """The verdict of `pin-evidence` on one PR (see the module docstring for the order).

    `platform_texts` is `(base, head)` of `PLATFORM_FILES` (`main` always passes it); None reads as no platform move.
    `platform_evidence_of(head_text, from, to, helms)` is the operator E1 of a platform-only move; a platform move
    with none wired FAILS.
    """
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
    # Defence in depth: `detect` already refuses this, on every PR (ADR-1103).
    if problem := seed_allowlist_problem(move, changed):
        outcome.failures.append(problem)
        return outcome
    if move is None:
        try:
            platform_move = platform_move_of(*platform_texts) if platform_texts is not None else None
        except Refused as refused:
            outcome.failures.append(str(refused))
            return outcome
        if platform_move is None:
            outcome.notes.append(f"no pin move: targetRevision stays {pb.current_pin(base_text)}")
            return outcome
        return judge_platform(
            outcome, pr, base_text, head_text, platform_move, platform_texts,
            platform_evidence_of=platform_evidence_of, statuses_of=statuses_of, helms=helms,
            acks=acks or {}, base_acks=base_acks or {}, e2_required=e2_required,
        )
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
    # Pin-only binds all three mechanical files, not `yadgar.yaml` alone: each must be what `pin_bump.write_files`
    # makes from the base for this target (yaadgaar #424 item 3). FAIL on an automatic head, WARN on a human one.
    try:
        differs = regenerated_of(target)
        problem = (
            f"not the regenerated pin: {', '.join(differs)} differ(s) from what `pin_bump.write_files` makes from the base for {target}"
            if differs
            else None
        )
    except (pb.Refused, subprocess.CalledProcessError, OSError) as error:
        problem = f"the three mechanical files could not be regenerated for {target}: {error}"
    if problem:
        (outcome.failures if pr.automatic else outcome.warnings).append(problem)
    else:
        outcome.notes.append(f"{', '.join(pb.MECHANICAL_FILES)} are the regenerated ones for {target}, byte for byte")
    found = evidence_of(head_text, pin, target, {version: helms[version] for version in HELMS})
    outcome.evidence = found
    # Car C10: a target whose chart moved `platform` moves all five operator Applications with the pin, on any head.
    # A FAILURE, not a NEEDS-HUMAN reason: no acknowledgement may leave an operator on the old platform.
    if platform := found.get("platform"):
        behind = [path for path in pb.OPERATOR_APPLICATIONS if path not in changed]
        if behind:
            outcome.failures.append(
                f"platform move {platform['from']} -> {platform['to']}: {', '.join(behind)} left behind on the old platform; "
                f"a platform move changes all eight pin files ({', '.join(pb.pin_files(platform_moved=True))})"
            )
    # argocd#230 review F1: E1 judged the platform move the TARGET CHART names; what deploys is the version the six
    # platform files name. They are one move or the PR FAILS, on any head, never a warning.
    if platform_texts is not None:
        try:
            files_move = platform_move_of(*platform_texts)
        except Refused as refused:
            outcome.failures.append(str(refused))
        else:
            judged = (platform["from"], platform["to"]) if platform else None
            if files_move is not None and judged is None:
                outcome.failures.append(
                    f"the platform files move platform {files_move[0]} -> {files_move[1]}, but the target chart's platform "
                    f"does not move; E1 judged no platform move, so the operators would deploy a version nobody judged"
                )
            elif judged is not None and files_move is not None and files_move != judged:
                outcome.failures.append(
                    f"the platform files move platform {files_move[0]} -> {files_move[1]}, but E1 judged the target chart's "
                    f"platform {judged[0]} -> {judged[1]}; the operators would deploy a version nobody judged"
                )
            elif judged is not None and files_move is None:
                outcome.failures.append(
                    f"E1 judged the target chart's platform {judged[0]} -> {judged[1]}, but the platform files stay on "
                    f"platform {judged[0]}; the operators would stay on a version the chart no longer ships"
                )
    if not found["accept"]:
        needs_human(outcome, pr, target, found["reasons"], acks or {}, base_acks or {}, f"a human pin PR to {target}")
    e2(outcome, pr, statuses_of, e2_required)
    return outcome


def needs_human(
    outcome: Outcome, pr: Pr, key: str, reasons: list[str], acks: dict[str, dict], base_acks: dict[str, dict], subject: str
) -> None:
    """Step 4's NEEDS-HUMAN rule: every reason FAILS, on a human head unless this PR's `ACK_FILE` entry for `key` names it.

    `key` is the pin target, or `platform:<to>` on a platform-only move; `subject` names the PR in the failure.
    """
    # An acknowledgement never clears an automatic head: it may change only the three mechanical files anyway.
    open_reasons = reasons if pr.automatic else unacknowledged(key, reasons, acks, base_acks)
    outcome.failures += [f"NEEDS-HUMAN: {reason}" for reason in open_reasons]
    outcome.warnings += [f"NEEDS-HUMAN (acknowledged in {ACK_FILE}): {r}" for r in reasons if r not in open_reasons]
    if open_reasons and not pr.automatic:
        entry = json.dumps({"target": key, "reasons": reasons, "why": ACK_WHY_PLACEHOLDER})
        if key not in acks:
            how = f"commits this entry to {ACK_FILE} in this PR"
        elif base_acks.get(key) == acks[key]:
            how = (
                f"rewrites {ACK_FILE}'s entry for {key}: the one there is already on the base (it acknowledged an "
                f"earlier PR), so it clears nothing here; replace the existing entry in this PR "
                f"(E2 (pin / kind-dryrun) refuses the rewrite, as {ACK_FILE} is append-only there (ADR-1094): run the "
                f"hand dry run) with"
            )
        else:
            how = f"edits {ACK_FILE} in this PR to replace the existing entry for {key}, which misses a reason, with"
        outcome.failures.append(
            f"NEEDS-HUMAN-UNACKNOWLEDGED: {subject} merges only after a human reads each NEEDS-HUMAN "
            f"reason above and {how}: {entry} (a commit in the reviewed diff re-runs this check; a label or an "
            f"approval does not)"
        )
    elif not pr.automatic:
        outcome.notes.append(f"{subject}: every NEEDS-HUMAN reason is acknowledged in {ACK_FILE} for {key}")


def e2(outcome: Outcome, pr: Pr, statuses_of: Callable[[str], list[dict]], e2_required: bool) -> None:
    """Step 5: E2 read as the head sha's `pin / kind-dryrun` status; required on an automatic head, else reported."""
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


def judge_platform(
    outcome: Outcome,
    pr: Pr,
    base_text: str,
    head_text: str,
    move: tuple[str, str],
    texts_of_platform: tuple[dict[str, str | None], dict[str, str | None]],
    *,
    platform_evidence_of: Callable[[str, str, str, dict], dict] | None,
    statuses_of: Callable[[str], list[dict]],
    helms: dict[str, str],
    acks: dict[str, dict],
    base_acks: dict[str, dict],
    e2_required: bool,
) -> Outcome:
    """A platform-only move (yaadgaar #514 option (a)): the operator E1 (`classify_platform`), its NEEDS-HUMAN rule
    keyed `platform:<to>`, and E2 as on a pin. An automatic head never reaches here (`automatic_problem`)."""
    old, new = move
    kind = "automatic" if pr.automatic else "human"
    outcome.notes.append(f"{kind} platform PR: platform {old} -> {new}, targetRevision stays {pb.current_pin(base_text)}")
    if problem := platform_only_problem(base_text, head_text, *texts_of_platform, new):
        outcome.failures.append(problem)
        return outcome
    # Fail-closed: a lower platform is judged as any other move (E1 compares both directions), never skipped.
    if pb.release(new) is None or pb.release(old) is None:
        outcome.failures.append(f"platform {old} -> {new}: both must be release versions X.Y.Z; a prerelease is never pinned")
        return outcome
    missing = [version for version in HELMS if version not in helms]
    if missing:
        outcome.failures.append(f"helm {', '.join(missing)} not installed; E1 renders on exactly {', '.join(HELMS)}")
        return outcome
    if platform_evidence_of is None:
        outcome.failures.append(f"platform move {old} -> {new}: no operator E1 is wired; a platform move is never left unjudged")
        return outcome
    try:
        found = platform_evidence_of(head_text, old, new, {version: helms[version] for version in HELMS})
    except pb.Refused as refused:
        outcome.failures.append(f"platform move {old} -> {new}: the operator E1 refused: {refused}")
        return outcome
    outcome.evidence = found
    if not found["accept"]:
        key = f"{ACK_PLATFORM_PREFIX}{new}"
        needs_human(outcome, pr, key, found["reasons"], acks, base_acks, f"a human platform PR to {key}")
    else:
        outcome.notes.append(f"platform {old} -> {new}: the operator E1 found no reason")
    e2(outcome, pr, statuses_of, e2_required)
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
        if problems := unknown_applications(arguments.repo):
            raise Refused(
                "the Applications are not the known ones (argocd#230 review F2): " + "; ".join(problems)
                + f". pin-evidence judges a platform move over {', '.join(pb.OPERATOR_APPLICATIONS)} only; a new Application "
                "is an edit to KNOWN_APPLICATIONS and to the gate that pins its shape"
            )
        if arguments.command == "detect":
            move = move_of(base_text, head_text)
            if problem := automatic_problem(pr, move, changed) or seed_allowlist_problem(move, changed):
                raise Refused(problem)
            # A platform-only move is a move too (yaadgaar #514); a partial one is refused here already.
            platform_move = platform_move_of(*platform_texts(arguments.repo)) if move is None else None
            if move is not None:
                lines, said = ["moved=true", f"pin={move[0]}", f"target={move[1]}"], f"pin move {move[0]} -> {move[1]}"
            elif platform_move is not None:
                lines = ["moved=true", f"platform={platform_move[0]}", f"platform_target={platform_move[1]}"]
                said = f"platform-only move {platform_move[0]} -> {platform_move[1]}"
            else:
                lines, said = ["moved=false"], "no pin move"
            with arguments.output.open("a") as output:
                output.write("\n".join(lines) + "\n")
            print(said)
            return 0
        outcome = judge(
            pr,
            base_text,
            head_text,
            evidence_of=lambda head_text, pin, target, found: evidence(head_text, pin, target, found, repo=arguments.repo),
            statuses_of=lambda sha: statuses(pr.repository, sha),
            helms=pb.discover_helms(),
            changed=changed,
            regenerated_of=lambda target: regenerated_differences(arguments.repo, target),
            acks=acknowledgements(arguments.repo / ACK_FILE),
            base_acks=parse_acknowledgements(base_ack_text(arguments.repo)),
            platform_texts=platform_texts(arguments.repo),
            platform_evidence_of=lambda head_text, old, new, found: platform_evidence(
                head_text, old, new, found, repo=arguments.repo
            ),
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
