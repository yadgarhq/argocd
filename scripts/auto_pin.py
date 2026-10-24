#!/usr/bin/env python3
"""Open, supersede and relay evidence for automatic argocd pin PRs (ledger 1632 C5, ledger 1670, ADR-0946, ADR-0948).

    auto_pin.py gate     --repo DIR --out STATE [--regenerate PR] [--oci-wait SECONDS]
    auto_pin.py tidy     --state STATE
    auto_pin.py push     --repo DIR --state STATE
    auto_pin.py dryrun   --state STATE --evidence E1.json --out DRYRUN.json
    auto_pin.py open     --state STATE --evidence E1.json --dryrun DRYRUN.json --body BODY.md --checker PR_BODY_PY
    auto_pin.py evidence --repo DIR --pr N

THE PLAN IS `yadgarhq/docs` `plans/auto-deploy-parent-chart.md`, sections 1
("Trigger", "Supersede", "Serialize and refuse"), 3 ("Check E2", steps 1-6) and
7 ("Identity"). `.github/workflows/auto-pin.yaml` runs the subcommands in that
order, one step each, so each step mints its own short-lived App token.

`gate` (read-only; GITHUB_TOKEN reads argocd, VERIFY_TOKEN reads settled
verdicts on argocd-verify, ADR-0948 R4). It writes the state file and decides,
in this order, cheapest first:

  0. The checkout is not main's head → no PR (the next run reads the new main).
  1. Open `auto-pin-hold` issue → no PR.
  2. Main's current settled epoch (K, A), read as `settled_dispatch.epoch` and
     `settled_gate.find_verdict` read it: green → go on; red → no PR; no verdict
     before close (A + 3900 s + 1200 s) → pending, no PR; no verdict past close
     → counts as red, no PR. No K → no PR. A merged pin is main's epoch, so
     "the last merged pin's verdict is pending" is the pending case. Red and
     past-close are what C6's hold writer acts on (`EpochState.holds`).
  3. The target: the highest release `v*` tag of `yadgarhq/chart` (prereleases
     ignored) whose chart `oci://ghcr.io/yadgarhq/charts/yadgar:<v>` resolves
     anonymously. When the newest does not resolve yet (the publish and the
     nudge race), it re-reads the registry for up to OCI_WAIT_SECONDS, then
     takes the highest version that does. Highest tag below the pin → REFUSED
     (exit 1). Nothing above the pin → no PR. The `repository_dispatch` payload
     is a nudge, never an input: nothing here reads it.
  4. The PRs: every PR on an `auto/pin-<X.Y.Z>[-<sha12>]` head authored by
     PIN_LOGIN. `decide` (pure; all of its cases are tests) then opens, leaves
     alone, tidies or supersedes. A version a human closed unmerged is not
     reopened. Of several open PRs for the target the newest is kept and the
     others are tidied. A PR whose head the bot did not push (`bot_head`) is a
     human's: it is never superseded, tidied or regenerated, whatever its
     version. Only a race remains: a human who pushes after the gate read the
     PR gets it closed by `open` with a comment, and its branch is kept
     (`close_superseded` re-reads the head; the plan's C5 red case).
  5. Back-off: an orphan `auto/pin-<target>-*` branch (no open PR) whose head
     carries a `pin / e1` status by `github-actions[bot]`, on this same main,
     younger than BACKOFF_HOURS, is a recent attempt that failed after its push
     (E2 never usable, or the open refused): no PR until it ages out or main
     moves. Without it the cron would re-dispatch the private runner every 15
     minutes for the same failure.

`tidy` (release App token): the target's PR is in flight but an older automatic
PR is still open (an earlier `open` failed between creating the new PR and
closing the old one). Closes the older ones, naming the one in flight.

`push` (release App token, contents + pull-requests write, R3-a). Reads main
again (moved → stop). Deletes stale orphan branches of the target that are
provably the bot's (author PIN_LOGIN, verified, one parent); anything else is
kept. Then it makes ONE commit through the Git Data API (blobs, a tree on main's
tree, a commit whose only parent is main) and points a NEW branch
`auto/pin-<v>-<sha12>` at it: GitHub signs a commit an App makes through the
API, and `pin-dryrun` needs exactly one merge base with main and a
`main...sha` of the three pin files only. A change outside those three files
is refused. Each attempt gets its own branch, so a regenerate never has to
move or delete the branch of a PR that is still open. `push` closes nothing.

`dryrun` (VERIFY_TOKEN, plus GITHUB_TOKEN with `statuses: write`). Posts
`pin / e1` (pin_bump's offline verdict), then dispatches
`yadgarhq/argocd-verify` `pin-dryrun.yaml` for the pushed sha and BINDS to the
run it started (run-name `pin-dryrun <sha>`, event `workflow_dispatch`, actor
DISPATCH_LOGIN, created after the dispatch, not a run bound before). It waits
for that run for at most DRYRUN_WAIT_SECONDS (pending past that is an
infrastructure failure: no PR). It then reads the result with
`pin_dryrun.find` and accepts it only when `find`'s run id is the bound run:
any other run's artifact, a stale verdict, a cancelled (evicted) run or a run
that uploaded nothing dispatches again, at most MAX_DISPATCHES runs in all,
and the whole relay ends within RELAY_SECONDS, inside its token's 60 minutes.
Main moving meanwhile stops the relay. Then it posts `pin / kind-dryrun` (E2).
Both statuses are `success` or `failure`, with a parseable description
(`status_description`), E2's with the argocd-verify run as `target_url`.

`open` (release App token; GITHUB_TOKEN reads the holds). Reads main again
(moved → stop, no PR) and the holds again. The body is `pin_bump.py`'s, with
the E2 line and a hidden marker added; it is final here and never edited after
(a body edit starts a second `ci` run on the same sha, ADR-0692). It must pass
`pr_body.py` from `yadgarhq/actions` (`--checker`); the PR is opened only if it
does. Labels: `auto-pin`, plus `auto-pin-needs-human` unless E1 accepted AND
E2 is green. ONLY THEN are the superseded PRs closed, each with a comment
naming the new one, its branch deleted only when its head is still the sha the
bot pushed. So no failure path leaves zero automatic PRs open. NOTHING HERE
MERGES. Merging is C6 (`auto-pin-merge.yaml`).

`evidence` (VERIFY_TOKEN, GITHUB_TOKEN `statuses: write`): the same E2 relay
for a human pin PR, refused unless the PR is open, based on `main`, from this
repository (no fork) and moves `targetRevision`. It posts `pin / kind-dryrun`
and opens nothing.

THE C6 SEAM, what `auto-pin-merge.yaml` and the hold writer read:

  - `EpochState` / `epoch_state`: `.blocks` (no automatic pin), `.holds` (red
    or past close: ensure one `auto-pin-hold` issue for K).
  - the PR body marker (`parse_marker`): the sha the bot pushed, the main it
    was built on, the target, E1, E2 and the E2 run.
  - the statuses `pin / e1` and `pin / kind-dryrun` on the head sha, created
    by `github-actions[bot]`, parsed by `parse_status_description`: C6 merges
    only when both are `success`, their `sha=` is the head and their `main=`
    is main's current head (else it dispatches this workflow with
    `regenerate: <pr>`).
  - `workflow_dispatch` input `regenerate`: close that bot PR and open a fresh
    one on current main (a spent sha, ADR-0692, or a base that moved). A
    regenerate dispatch shares the `auto-pin` concurrency group with the cron,
    and GitHub cancels a PENDING run when a newer one queues in its group, so
    a regenerate can be lost. C6 must tolerate that: on its next pass it
    re-checks the PR and dispatches again. A moved base needs no regenerate at
    all (`decide` regenerates a PR built on an older main by itself).

Exit codes: 0 done (including "no PR", which is a decision, not a failure);
1 refused; 2 usage; 3 infrastructure.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

HERE = Path(__file__).resolve().parent

REPOSITORY = "yadgarhq/argocd"
VERIFY_REPOSITORY = "yadgarhq/argocd-verify"
CHART_REPOSITORY = "https://github.com/yadgarhq/chart"
CHART_OCI = "yadgarhq/charts/yadgar"
DRYRUN_WORKFLOW = "pin-dryrun.yaml"
DRYRUN_WORKFLOW_PATH = f".github/workflows/{DRYRUN_WORKFLOW}"
SETTLED_WORKFLOW_PATH = ".github/workflows/settled.yaml"
GITHUB_API = "https://api.github.com"

APPLICATION = "applications/yadgar.yaml"
PIN_FILES = ("applications/yadgar.yaml", "scripts/chart_pin.json", "scripts/gates/yadgar_render.sha256")

BRANCH_PREFIX = "auto/pin-"
BRANCH = re.compile(r"^auto/pin-(\d+\.\d+\.\d+)(?:-[0-9a-f]{12})?$")
PIN_LOGIN = "yadgarhq-bot[bot]"
DISPATCH_LOGIN = "yadgarhq-settled-dispatch[bot]"
STATUS_LOGIN = "github-actions[bot]"
LABEL_PIN = "auto-pin"
LABEL_NEEDS_HUMAN = "auto-pin-needs-human"
LABEL_HOLD = "auto-pin-hold"
STATUS_E1 = "pin / e1"
STATUS_E2 = "pin / kind-dryrun"

# The publish and the nudge race: wait this long for the newest chart, then fall back.
OCI_WAIT_SECONDS = 900
OCI_INTERVAL_SECONDS = 60
# settled_dispatch.GRACE_SECONDS: an epoch closes at its deadline plus this.
CLOSE_GRACE_SECONDS = 1200
# One dispatched run, then at most two more for a stale, evicted or empty result.
MAX_DISPATCHES = 3
# The run a dispatch starts must appear in the listing within this.
BIND_SECONDS = 180
BIND_SKEW_SECONDS = 120
BIND_INTERVAL_SECONDS = 10
# The private runner is shared (maxRunners 1): a pin-dryrun run may queue behind a settled poll.
DRYRUN_WAIT_SECONDS = 1800
DRYRUN_INTERVAL_SECONDS = 30
# The whole relay, every dispatch included, ends inside the 60-minute settled-dispatch token it runs on.
RELAY_SECONDS = 3000
# A failed attempt on the same main waits this long before the cron tries the same version again.
BACKOFF_HOURS = 6
STATUS_LIMIT = 140

RELEASE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
MARKER = re.compile(r"<!-- auto-pin (?P<fields>[^>]*?) -->")
DESCRIPTION = re.compile(r"^(?P<check>e1|e2) (?P<result>\S+) sha=(?P<sha>[0-9a-f]{40}) main=(?P<main>[0-9a-f]{40})(?: run=(?P<run>\d+))?$")
REVISION_LINE = re.compile(r"^([-+])[ \t]+targetRevision:[ \t]*(\S+)[ \t]*$", re.M)

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_USAGE = 2
EXIT_INFRA = 3


class Refused(Exception):
    """The request cannot be carried out as asked."""


class MainMoved(Exception):
    """argocd main is no longer the commit this run built on: the next run starts again."""


class InfrastructureError(Exception):
    """GitHub, the registry or the runner did not answer usefully. Not a verdict."""


class ApiError(InfrastructureError):
    def __init__(self, status: int, method: str, path: str) -> None:
        super().__init__(f"{method} {path}: HTTP {status}")
        self.status = status


class Client(Protocol):
    def get(self, path: str) -> Any: ...
    def post(self, path: str, body: object = None) -> Any: ...
    def patch(self, path: str, body: object = None) -> Any: ...
    def delete(self, path: str) -> Any: ...
    def paginate(self, path: str) -> list: ...


class Api:
    """GitHub REST. The token is never printed and never follows a redirect."""

    def __init__(self, token: str | None) -> None:
        self.token = token or ""

    def _request(self, method: str, path: str, body: object = None) -> Any:
        data = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(GITHUB_API + path, data=data, method=method, headers={"Accept": "application/vnd.github+json"})
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_unredirected_header("Authorization", f"Bearer {self.token}")
        where = path.split("?")[0]
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # GITHUB_API only
                raw = response.read()
        except urllib.error.HTTPError as error:
            raise ApiError(error.code, method, where) from error
        except (urllib.error.URLError, OSError) as error:
            raise InfrastructureError(f"{method} {where}: {error!r}") from error
        return json.loads(raw) if raw.strip() else None

    def get(self, path: str) -> Any:
        return self._request("GET", path)

    def post(self, path: str, body: object = None) -> Any:
        return self._request("POST", path, body if body is not None else {})

    def patch(self, path: str, body: object = None) -> Any:
        return self._request("PATCH", path, body if body is not None else {})

    def delete(self, path: str) -> Any:
        return self._request("DELETE", path)

    def paginate(self, path: str) -> list:
        items: list = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 11):
            batch = self.get(f"{path}{separator}per_page=100&page={page}")
            items += batch
            if len(batch) < 100:
                return items
        return items


def _utc(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def _zulu(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── the epoch ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class EpochState:
    """Main's settled epoch: green, red, pending (no verdict before close), past_close (counts as red) or no_key."""

    kind: str
    reason: str
    key: str | None = None
    anchor: str | None = None
    close: str | None = None
    run_id: int | None = None

    @property
    def blocks(self) -> bool:
        return self.kind != "green"

    @property
    def holds(self) -> bool:
        """C6's hold writer ensures one `auto-pin-hold` issue for K when this is true."""
        return self.kind in ("red", "past_close")


def epoch_state(epoch: dict | None, found: dict | None, now: dt.datetime, grace: int = CLOSE_GRACE_SECONDS) -> EpochState:
    if epoch is None:
        return EpochState(kind="no_key", reason="main has no render key, so there is no epoch to judge")
    close = _utc(str(epoch["deadline"])) + dt.timedelta(seconds=grace)
    common = {"key": str(epoch["key"]), "anchor": str(epoch["anchor"]), "close": _zulu(close)}
    if found:
        result = (found.get("verdict") or {}).get("result")
        run_id = int(found["run_id"]) if found.get("run_id") is not None else None
        kind = "green" if result == "green" else "red"
        return EpochState(kind=kind, reason=f"settled verdict {result} for epoch {epoch['anchor'][:12]}, run {run_id}", run_id=run_id, **common)
    if now >= close:
        return EpochState(kind="past_close", reason=f"no settled verdict for epoch {epoch['anchor'][:12]} by close {_zulu(close)}: counts as red", **common)
    return EpochState(kind="pending", reason=f"settled verdict pending for epoch {epoch['anchor'][:12]} until close {_zulu(close)}", **common)


# ── the PRs ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class Marker:
    """The hidden line in every automatic pin PR body. Written once, at creation."""

    sha: str
    main: str
    target: str
    e1: str
    e2: str
    e2_run: int | None


def marker_line(marker: Marker) -> str:
    run = "" if marker.e2_run is None else str(marker.e2_run)
    return f"<!-- auto-pin sha={marker.sha} main={marker.main} target={marker.target} e1={marker.e1} e2={marker.e2} e2_run={run} -->"


def parse_marker(body: str | None) -> Marker | None:
    match = MARKER.search(body or "")
    if not match:
        return None
    fields = dict(part.split("=", 1) for part in match.group("fields").split() if "=" in part)
    try:
        if not (FULL_SHA.match(fields["sha"]) and FULL_SHA.match(fields["main"]) and RELEASE.match(fields["target"])):
            return None
        run = fields.get("e2_run") or ""
        return Marker(sha=fields["sha"], main=fields["main"], target=fields["target"], e1=fields["e1"], e2=fields["e2"], e2_run=int(run) if run.isdigit() else None)
    except KeyError:
        return None


@dataclass(frozen=True, kw_only=True)
class PinPR:
    number: int
    branch: str
    version: str | None
    head_sha: str
    state: str
    merged: bool
    labels: frozenset[str]
    marker: Marker | None


def pin_prs(api: Client) -> list[PinPR]:
    """Every PR on an `auto/pin-<X.Y.Z>[-<sha12>]` head authored by the pin App, newest first. By head and author, not label."""
    found = []
    for item in api.paginate(f"/repos/{REPOSITORY}/pulls?state=all&sort=created&direction=desc"):
        branch = str((item.get("head") or {}).get("ref") or "")
        match = BRANCH.match(branch)
        if not match or (item.get("user") or {}).get("login") != PIN_LOGIN:
            continue
        found.append(
            PinPR(
                number=int(item["number"]),
                branch=branch,
                version=match.group(1),
                head_sha=str((item.get("head") or {}).get("sha") or ""),
                state=str(item.get("state")),
                merged=bool(item.get("merged_at")),
                labels=frozenset(str(label.get("name")) for label in item.get("labels") or []),
                marker=parse_marker(item.get("body")),
            )
        )
    return found


def human_closed(api: Client, prs: Iterable[PinPR], target: str) -> frozenset[str]:
    """`{target}` when someone other than the bot closed an automatic PR for it unmerged: that version is not reopened.

    Only the target's closed PRs are read, one GET each; older versions are never targets again.
    """
    for pr in prs:
        if pr.version != target or pr.state != "closed" or pr.merged:
            continue
        closer = ((api.get(f"/repos/{REPOSITORY}/issues/{pr.number}") or {}).get("closed_by") or {}).get("login")
        if closer != PIN_LOGIN:
            return frozenset({target})
    return frozenset()


def open_holds(api: Client) -> list[int]:
    issues = api.get(f"/repos/{REPOSITORY}/issues?labels={LABEL_HOLD}&state=open") or []
    return [int(issue["number"]) for issue in issues if "pull_request" not in issue]


def main_head(api: Client) -> str:
    sha = ((api.get(f"/repos/{REPOSITORY}/branches/main") or {}).get("commit") or {}).get("sha")
    if not isinstance(sha, str) or not FULL_SHA.match(sha):
        raise InfrastructureError(f"/repos/{REPOSITORY}/branches/main carries no commit sha")
    return sha


def target_refs(api: Client, target: str) -> list[tuple[str, str]]:
    """`(ref, sha)` of every `auto/pin-<target>` / `auto/pin-<target>-<sha12>` branch. matching-refs is a prefix match."""
    refs = []
    for item in api.get(f"/repos/{REPOSITORY}/git/matching-refs/heads/{BRANCH_PREFIX}{target}") or []:
        ref = str(item.get("ref") or "")
        match = BRANCH.match(ref.removeprefix("refs/heads/"))
        if match and match.group(1) == target:
            refs.append((ref, str((item.get("object") or {}).get("sha") or "")))
    return refs


def backoff(api: Client, target: str, main_sha: str, now: dt.datetime, *, open_heads: frozenset[str]) -> str | None:
    """Why to wait, or None: an orphan branch of `target` whose `pin / e1` on this main is younger than BACKOFF_HOURS."""
    for ref, sha in target_refs(api, target):
        if sha in open_heads:
            continue
        for status in api.get(f"/repos/{REPOSITORY}/commits/{sha}/statuses") or []:
            if status.get("context") != STATUS_E1 or (status.get("creator") or {}).get("login") != STATUS_LOGIN:
                continue
            if parse_status_description(str(status.get("description") or "")).get("main") != main_sha:
                continue
            age = now - _utc(str(status.get("created_at")))
            if dt.timedelta(0) <= age < dt.timedelta(hours=BACKOFF_HOURS):
                return f"{ref.removeprefix('refs/heads/')} was tried on this main {int(age.total_seconds() // 60)} min ago and opened no PR"
    return None


# ── the target ──────────────────────────────────────────────────────────────


def release(version: str) -> tuple[int, int, int] | None:
    match = RELEASE.match(version.strip())
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))) if match else None


def _bare(parts: tuple[int, int, int]) -> str:
    return ".".join(str(part) for part in parts)


def resolvable_target(
    pin: str,
    tags: Iterable[str],
    resolves: Callable[[str], bool],
    *,
    now: Callable[[], dt.datetime],
    sleep: Callable[[float], None],
    wait: int = OCI_WAIT_SECONDS,
    interval: int = OCI_INTERVAL_SECONDS,
) -> str | None:
    """The version to pin, or None when nothing above the pin resolves. Raises Refused when every release is below it."""
    current = release(pin)
    if current is None:
        raise Refused(f"the current pin {pin!r} is not a release X.Y.Z")
    releases = sorted({found for found in (release(tag) for tag in tags) if found}, reverse=True)
    if not releases:
        raise Refused("yadgarhq/chart carries no release tag")
    if releases[0] < current:
        raise Refused(f"the highest chart release {_bare(releases[0])} is lower than the pin {pin}; a downgrade is a human revert")
    newer = [_bare(parts) for parts in releases if parts > current]
    if not newer:
        return None
    until = now() + dt.timedelta(seconds=wait)
    while True:
        if resolves(newer[0]):
            return newer[0]
        if now() >= until:
            break
        print(f"chart {newer[0]} does not resolve in the registry yet; waiting")
        sleep(min(interval, max(1.0, (until - now()).total_seconds())))
    for version in newer[1:]:
        if resolves(version):
            print(f"chart {newer[0]} still does not resolve after {wait} s; taking {version}, the next run picks the newer one")
            return version
    return None


def chart_tags() -> list[str]:
    finished = subprocess.run(["git", "ls-remote", "--tags", "--refs", CHART_REPOSITORY], capture_output=True, text=True, check=False, timeout=60)
    if finished.returncode != 0:
        raise InfrastructureError(f"git ls-remote {CHART_REPOSITORY} exited {finished.returncode}")
    return [line.rsplit("refs/tags/", 1)[-1] for line in finished.stdout.splitlines()]


def oci_resolves(version: str) -> bool:
    """Whether `oci://ghcr.io/yadgarhq/charts/yadgar:<version>` resolves anonymously."""
    try:
        with urllib.request.urlopen(f"https://ghcr.io/token?scope=repository:{CHART_OCI}:pull", timeout=30) as response:  # fixed host
            token = json.load(response)["token"]
        request = urllib.request.Request(
            f"https://ghcr.io/v2/{CHART_OCI}/manifests/{version}",
            method="HEAD",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.oci.image.manifest.v1+json"},
        )
        with urllib.request.urlopen(request, timeout=30):  # fixed host
            return True
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise InfrastructureError(f"ghcr.io {CHART_OCI}:{version}: HTTP {error.code}") from error
    except (urllib.error.URLError, OSError, KeyError, ValueError) as error:
        raise InfrastructureError(f"ghcr.io {CHART_OCI}:{version}: {error!r}") from error


# ── decide ──────────────────────────────────────────────────────────────────


def bot_head(pr: PinPR) -> bool:
    """The PR's head is still the commit auto-pin pushed (its marker's sha); otherwise a human touched it."""
    return pr.marker is not None and pr.head_sha == pr.marker.sha


@dataclass(frozen=True, kw_only=True)
class Decision:
    action: str  # "open", "tidy" or "none"
    reason: str
    target: str | None = None
    supersede: tuple[PinPR, ...] = ()
    keep: int | None = None


def decide(
    *,
    pin: str,
    main_sha: str,
    holds: list[int],
    epoch: EpochState,
    prs: list[PinPR],
    target: str | None,
    human_closed: frozenset[str],
    regenerate: int | None,
    backoff: str | None = None,
) -> Decision:
    if holds:
        return Decision(action="none", reason=f"hold issue {', '.join(f'#{n}' for n in holds)} is open; a human closes it")
    if epoch.blocks:
        return Decision(action="none", reason=f"main's epoch is {epoch.kind}: {epoch.reason}")
    if target is None:
        return Decision(action="none", reason=f"no chart release above the pin {pin} resolves")
    if target in human_closed:
        return Decision(action="none", reason=f"a human closed the automatic PR for {target}; a newer version or a human resumes")
    wanted = release(target)
    opened = [(release(p.version or ""), p) for p in prs if p.state == "open" and p.version]
    for version, pr in opened:
        if version is not None and wanted is not None and version > wanted:
            return Decision(action="none", reason=f"#{pr.number} already pins {pr.version}, above {target}")
    # The head guard covers EVERY PR auto-pin would close: one whose head the bot did not push is a human's.
    older = tuple(pr for version, pr in opened if version is not None and wanted is not None and version < wanted and bot_head(pr))
    same = [pr for version, pr in opened if version == wanted]
    if same:
        # Newest first (pin_prs lists by creation, descending): keep the newest, the others are duplicates.
        current = same[0]
        older = (*older, *(pr for pr in same[1:] if bot_head(pr)))

        def leave(reason: str) -> Decision:
            if older:
                numbers = ", ".join(f"#{p.number}" for p in older)
                return Decision(action="tidy", reason=f"{reason}; close the older {numbers}", target=target, supersede=older, keep=current.number)
            return Decision(action="none", reason=reason)

        # The head guard comes first: a PR a human pushed to is never closed, not even by `regenerate`.
        if not bot_head(current):
            return leave(f"#{current.number} for {target} has a head the bot did not push; it is a human's now")
        if regenerate != current.number:
            if LABEL_NEEDS_HUMAN in current.labels:
                return leave(f"#{current.number} for {target} waits for a human ({LABEL_NEEDS_HUMAN})")
            if current.marker.main == main_sha:
                return leave(f"#{current.number} for {target} is in flight on main {main_sha[:12]}")
        older = (*older, current)
    if backoff:
        return Decision(action="none", reason=f"back off: {backoff} (BACKOFF_HOURS {BACKOFF_HOURS})")
    return Decision(action="open", reason=f"open a pin PR {pin} -> {target}", target=target, supersede=older)


# ── the state file ──────────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class State:
    main_sha: str
    pin: str
    target: str
    title: str
    sha: str | None = None
    superseded: tuple[int, ...] = field(default=())
    keep: int | None = None


def save_state(path: Path, state: State) -> None:
    path.write_text(json.dumps(asdict(state), indent=2, sort_keys=True) + "\n")


def load_state(path: Path) -> State:
    raw = json.loads(path.read_text())
    return State(
        main_sha=str(raw["main_sha"]),
        pin=str(raw["pin"]),
        target=str(raw["target"]),
        title=str(raw["title"]),
        sha=str(raw["sha"]) if raw.get("sha") else None,
        superseded=tuple(int(n) for n in raw.get("superseded") or ()),
        keep=int(raw["keep"]) if raw.get("keep") is not None else None,
    )


def branch_for(target: str, sha: str) -> str:
    return f"{BRANCH_PREFIX}{target}-{sha[:12]}"


# ── closing ─────────────────────────────────────────────────────────────────


def close_superseded(api: Client, pr: PinPR, target: str, *, by: int) -> None:
    """Close `pr` with a comment naming #`by`; delete its branch only when its head is still the sha the bot pushed."""
    live = api.get(f"/repos/{REPOSITORY}/pulls/{pr.number}") or {}
    if live.get("state") != "open":
        return
    head = str((live.get("head") or {}).get("sha") or "")
    ours = pr.marker is not None and head == pr.marker.sha
    if ours:
        branch_note = f"Its branch `{pr.branch}` is deleted: its head is still the commit auto-pin pushed."
    else:
        pushed = pr.marker.sha if pr.marker else "unknown"
        branch_note = f"Its branch `{pr.branch}` is kept: its head `{head}` is not the commit auto-pin pushed (`{pushed}`)."
    body = f"Superseded by #{by} (parent chart {target}). {branch_note}"
    api.post(f"/repos/{REPOSITORY}/issues/{pr.number}/comments", {"body": body})
    api.patch(f"/repos/{REPOSITORY}/pulls/{pr.number}", {"state": "closed"})
    if ours:
        api.delete(f"/repos/{REPOSITORY}/git/refs/heads/{pr.branch}")
    print(f"closed #{pr.number} ({pr.version}), superseded by #{by}; branch {'deleted' if ours else 'kept'}")


def tidy(api: Client, prs: Sequence[PinPR], target: str, *, keep: int) -> None:
    """Close older automatic PRs left open beside the one in flight (#`keep`)."""
    for pr in prs:
        close_superseded(api, pr, target, by=keep)


# ── push ────────────────────────────────────────────────────────────────────


def bot_owned(api: Client, sha: str) -> bool:
    commit = api.get(f"/repos/{REPOSITORY}/commits/{sha}") or {}
    verified = ((commit.get("commit") or {}).get("verification") or {}).get("verified") is True
    return (commit.get("author") or {}).get("login") == PIN_LOGIN and verified and len(commit.get("parents") or []) == 1


def push(api: Client, tree: Path, state: State, *, changed: list[str], open_heads: frozenset[str]) -> str:
    """Make the pin commit on main through the Git Data API and point a new `auto/pin-<v>-<sha12>` at it. Closes nothing."""
    outside = sorted(set(changed) - set(PIN_FILES))
    if outside:
        raise Refused(f"pin_bump changed files outside the three pin files: {', '.join(outside)}")
    # C3's rule: a non-empty subset of the three pin files that includes the Application.
    if APPLICATION not in changed:
        raise Refused(f"pin_bump's change set {sorted(changed)} does not include {APPLICATION}: not a pin")
    if main_head(api) != state.main_sha:
        raise MainMoved(f"main moved since the gate read {state.main_sha}")
    for ref, sha in target_refs(api, state.target):
        if sha in open_heads:
            continue
        if bot_owned(api, sha):
            api.delete(f"/repos/{REPOSITORY}/git/{ref}")
            print(f"deleted the stale orphan {ref} (the bot's commit {sha})")
        else:
            print(f"kept {ref}: its head {sha} is not provably the bot's commit")
    base_tree = ((api.get(f"/repos/{REPOSITORY}/git/commits/{state.main_sha}") or {}).get("tree") or {}).get("sha")
    entries = []
    for path in PIN_FILES:
        blob = api.post(f"/repos/{REPOSITORY}/git/blobs", {"content": (tree / path).read_text(), "encoding": "utf-8"})
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    made_tree = api.post(f"/repos/{REPOSITORY}/git/trees", {"base_tree": base_tree, "tree": entries})
    # No author or committer: GitHub then signs the App's commit, and it is the App's.
    commit = api.post(f"/repos/{REPOSITORY}/git/commits", {"message": state.title, "tree": made_tree["sha"], "parents": [state.main_sha]})
    sha = str(commit["sha"])
    branch = branch_for(state.target, sha)
    api.post(f"/repos/{REPOSITORY}/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})
    print(f"pushed {branch} at {sha} on main {state.main_sha}")
    return sha


def changed_files(tree: Path) -> list[str]:
    finished = subprocess.run(["git", "-C", str(tree), "status", "--porcelain", "--untracked-files=all"], capture_output=True, text=True, check=True)
    return sorted(line[3:].strip() for line in finished.stdout.splitlines() if line.strip())


# ── E2: dispatch, bind, wait, find ──────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class DryRun:
    result: str  # "green" or "red"
    run_id: int | None
    reason: str


RUNS = f"/repos/{VERIFY_REPOSITORY}/actions/workflows/{DRYRUN_WORKFLOW}/runs?event=workflow_dispatch&per_page=30"
DISPATCH = f"/repos/{VERIFY_REPOSITORY}/actions/workflows/{DRYRUN_WORKFLOW}/dispatches"


def _bind(verify: Client, sha: str, since: dt.datetime, seen: set[int], *, now: Callable[[], dt.datetime], sleep: Callable[[float], None], actor: str) -> int:
    """The id of the run our dispatch started: its run-name, event, actor, a creation time after the dispatch, not bound before."""
    until = now() + dt.timedelta(seconds=BIND_SECONDS)
    earliest = since - dt.timedelta(seconds=BIND_SKEW_SECONDS)
    while True:
        runs = (verify.get(RUNS) or {}).get("workflow_runs") or []
        mine = [
            run
            for run in runs
            if run.get("display_title") == f"pin-dryrun {sha}"
            and run.get("event") == "workflow_dispatch"
            and (run.get("actor") or {}).get("login") == actor
            and int(run["id"]) not in seen
            and _utc(str(run.get("created_at"))) >= earliest
        ]
        if mine:
            return int(min(mine, key=lambda run: _utc(str(run["created_at"])))["id"])
        if now() >= until:
            raise InfrastructureError(f"no pin-dryrun run for {sha} by {actor} appeared within {BIND_SECONDS} s of the dispatch")
        sleep(BIND_INTERVAL_SECONDS)


def _wait(verify: Client, run_id: int, deadline: dt.datetime, *, now: Callable[[], dt.datetime], sleep: Callable[[float], None]) -> dict:
    until = min(now() + dt.timedelta(seconds=DRYRUN_WAIT_SECONDS), deadline)
    while True:
        run = verify.get(f"/repos/{VERIFY_REPOSITORY}/actions/runs/{run_id}") or {}
        if run.get("status") == "completed":
            return run
        if now() >= until:
            if until == deadline:
                raise InfrastructureError(f"pin-dryrun run {run_id} still {run.get('status')} at the relay deadline ({RELAY_SECONDS} s)")
            raise InfrastructureError(f"pin-dryrun run {run_id} still pending ({run.get('status')}) after {DRYRUN_WAIT_SECONDS} s")
        sleep(DRYRUN_INTERVAL_SECONDS)


def relay_dryrun(
    verify: Client,
    repo: Client,
    *,
    sha: str,
    main_sha: str,
    pin: str,
    find: Callable[[str, str, str], dict | None],
    now: Callable[[], dt.datetime],
    sleep: Callable[[float], None],
    actor: str = DISPATCH_LOGIN,
) -> DryRun:
    """E2 for `sha`, judged by a pin-dryrun run THIS call dispatched, against main `main_sha` and pin `pin`."""
    seen: set[int] = set()
    why = "no attempt"
    deadline = now() + dt.timedelta(seconds=RELAY_SECONDS)
    for attempt in range(1, MAX_DISPATCHES + 1):
        if main_head(repo) != main_sha:
            raise MainMoved(f"main moved from {main_sha} during the dry run")
        if now() >= deadline:
            raise InfrastructureError(f"the relay deadline ({RELAY_SECONDS} s) passed before dispatch {attempt}: {why}")
        since = now()
        verify.post(DISPATCH, {"ref": "main", "inputs": {"argocd_sha": sha}})
        run_id = _bind(verify, sha, since, seen, now=now, sleep=sleep, actor=actor)
        seen.add(run_id)
        print(f"dispatched pin-dryrun for {sha}: run {run_id} (attempt {attempt} of {MAX_DISPATCHES})")
        run = _wait(verify, run_id, deadline, now=now, sleep=sleep)
        found = find(sha, main_sha, pin)
        if found and found.get("run_id") == run_id and found.get("verdict") in ("green", "red"):
            print(f"pin-dryrun run {run_id}: {found['verdict']}")
            return DryRun(result=str(found["verdict"]), run_id=run_id, reason=str(found.get("reason") or ""))
        if found and found.get("run_id") == run_id:
            why = f"run {run_id} result is {found.get('verdict')} ({found.get('reason')})"
        elif found:
            why = f"the newest result for {sha} is run {found.get('run_id')}'s, not run {run_id}'s (stale for this relay)"
        else:
            why = f"run {run_id} concluded {run.get('conclusion')} and uploaded no result"
        print(f"not usable: {why}; dispatching again" if attempt < MAX_DISPATCHES else f"not usable: {why}")
    if main_head(repo) != main_sha:
        raise MainMoved(f"main moved from {main_sha} during the dry run")
    raise InfrastructureError(f"no usable pin-dryrun result after {MAX_DISPATCHES} dispatches: {why}")


# ── statuses ────────────────────────────────────────────────────────────────


def status_description(check: str, result: str, *, sha: str, main: str, run_id: int | None) -> str:
    text = f"{check} {result} sha={sha} main={main}" + (f" run={run_id}" if run_id is not None else "")
    if len(text) > STATUS_LIMIT:
        raise ValueError(f"status description over {STATUS_LIMIT} characters: {text}")
    return text


def parse_status_description(text: str) -> dict:
    match = DESCRIPTION.match(text or "")
    if not match:
        return {}
    parsed: dict = {"check": match.group("check"), "result": match.group("result"), "sha": match.group("sha"), "main": match.group("main")}
    if match.group("run"):
        parsed["run"] = int(match.group("run"))
    return parsed


def post_status(api: Client, sha: str, context: str, ok: bool, description: str, url: str | None) -> None:
    body = {"state": "success" if ok else "failure", "context": context, "description": description}
    if url:
        body["target_url"] = url
    api.post(f"/repos/{REPOSITORY}/statuses/{sha}", body)


def run_url(run_id: int | None) -> str | None:
    return None if run_id is None else f"https://github.com/{VERIFY_REPOSITORY}/actions/runs/{run_id}"


def post_e1(api: Client, sha: str, main: str, accept: bool) -> None:
    word = "accept" if accept else "needs-human"
    post_status(api, sha, STATUS_E1, accept, status_description("e1", word, sha=sha, main=main, run_id=None), None)


def post_e2(api: Client, sha: str, main: str, dryrun: DryRun) -> None:
    description = status_description("e2", dryrun.result, sha=sha, main=main, run_id=dryrun.run_id)
    post_status(api, sha, STATUS_E2, dryrun.result == "green", description, run_url(dryrun.run_id))


# ── open ────────────────────────────────────────────────────────────────────

E2_PLACEHOLDER = "- **Not covered here:** E2, the kind-yadgar dry run, is a separate check."


def compose_body(pin_body: str, state: State, *, e1_accept: bool, dryrun: DryRun) -> str:
    """pin_bump's body with the E2 line, the supersede note and the hidden marker. Final at creation."""
    run = f"[argocd-verify run {dryrun.run_id}]({run_url(dryrun.run_id)})" if dryrun.run_id is not None else "no run"
    e2 = f"- **E2 kind-yadgar dry run (interim, read-only).** {dryrun.result}, {run}, judged against main `{state.main_sha}`."
    if dryrun.result != "green":
        e2 += f" Not green, so this PR is `{LABEL_NEEDS_HUMAN}`: {dryrun.reason}"
    if state.superseded:
        e2 += "\n- **Supersedes** " + ", ".join(f"#{n}" for n in state.superseded) + "; each closes once this PR exists."
    e2 += f"\n- **Opened by** `auto-pin.yaml`; head commit `{state.sha}`, built on main `{state.main_sha}`. Nothing here merges it."
    body = pin_body.replace(E2_PLACEHOLDER, e2) if E2_PLACEHOLDER in pin_body else pin_body.replace("\n## Risk", f"\n{e2}\n\n## Risk", 1)
    mark = Marker(
        sha=str(state.sha), main=state.main_sha, target=state.target, e1="accept" if e1_accept else "needs-human", e2=dryrun.result, e2_run=dryrun.run_id
    )
    return marker_line(mark) + "\n\n" + body


def changelog_title(body: str) -> str | None:
    """pin_bump's one Changelog bullet without its dash: the title the hand pins carry, and the squash subject."""
    section = body.split("## Changelog\n", 1)[1].split("\n## ", 1)[0] if "## Changelog\n" in body else ""
    bullets = [line.strip()[2:].strip() for line in section.splitlines() if line.strip().startswith("- ")]
    return bullets[0] if len(bullets) == 1 else None


def open_pr(
    api: Client,
    state: State,
    *,
    e1_accept: bool,
    dryrun: DryRun,
    pin_body: str,
    check_body: Callable[[str], list[str]],
    reader: Client | None = None,
    superseded: Sequence[PinPR] = (),
) -> int | None:
    """Open the pin PR for `state`, THEN close `superseded`; None when a hold opened meanwhile. MainMoved when main moved.

    `reader` (the job's GITHUB_TOKEN, `issues: read`) reads the holds; the release App token holds no issues scope.
    """
    if main_head(api) != state.main_sha:
        raise MainMoved(f"main moved since {state.main_sha}: the evidence is for an older main")
    holds = open_holds(reader or api)
    if holds:
        print(f"hold issue {', '.join(f'#{n}' for n in holds)} opened during this run: no PR")
        return None
    body = compose_body(pin_body, state, e1_accept=e1_accept, dryrun=dryrun)
    problems = check_body(body)
    if problems:
        raise Refused("the generated body fails the template check: " + "; ".join(problems))
    title = changelog_title(pin_body) or state.title
    created = api.post(f"/repos/{REPOSITORY}/pulls", {"title": title, "head": branch_for(state.target, str(state.sha)), "base": "main", "body": body})
    number = int(created["number"])
    labels = [LABEL_PIN] if (e1_accept and dryrun.result == "green") else [LABEL_PIN, LABEL_NEEDS_HUMAN]
    api.post(f"/repos/{REPOSITORY}/issues/{number}/labels", {"labels": labels})
    print(f"opened #{number} for {state.target} with {', '.join(labels)}")
    for pr in superseded:
        close_superseded(api, pr, state.target, by=number)
    return number


def pr_body_checker(script: Path) -> Callable[[str], list[str]]:
    """`yadgarhq/actions` `scripts/pr_body.py` on an unwrapped body with no author (no bot exemption)."""

    def check(body: str) -> list[str]:
        environment = {key: value for key, value in os.environ.items() if key not in ("AUTHOR", "WRAPPED", "GITHUB_STEP_SUMMARY")}
        environment["BODY"] = body
        finished = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, check=False, env=environment)
        print(finished.stdout.strip())
        return [] if finished.returncode == 0 else [finished.stdout.strip() or finished.stderr.strip() or f"exit {finished.returncode}"]

    return check


# ── evidence for a human pin PR ─────────────────────────────────────────────


def evidence_target(api: Client, number: int) -> str:
    """The head sha of human pin PR #`number`; refused unless open, based on main, from this repository and moving the pin."""
    pull = api.get(f"/repos/{REPOSITORY}/pulls/{number}") or {}
    if pull.get("state") != "open":
        raise Refused(f"#{number} is not open")
    if (pull.get("base") or {}).get("ref") != "main":
        raise Refused(f"#{number}'s base is {(pull.get('base') or {}).get('ref')!r}, not main")
    head = pull.get("head") or {}
    if ((head.get("repo") or {}).get("full_name")) != REPOSITORY:
        raise Refused(f"#{number} comes from a fork ({(head.get('repo') or {}).get('full_name')}); its data never reaches the runner")
    files = api.paginate(f"/repos/{REPOSITORY}/pulls/{number}/files")
    entry = next((f for f in files if f.get("filename") == APPLICATION), None)
    if entry is None:
        raise Refused(f"#{number} does not move targetRevision in {APPLICATION}: the file is not changed")
    if not isinstance(entry.get("patch"), str):
        raise Refused(f"#{number}: the files API gave no patch for {APPLICATION} (a large diff); fail closed")
    lines = REVISION_LINE.findall(entry["patch"])
    removed = [value for sign, value in lines if sign == "-"]
    added = [value for sign, value in lines if sign == "+"]
    if len(removed) != 1 or len(added) != 1 or removed[0] == added[0]:
        raise Refused(f"#{number} does not move targetRevision in {APPLICATION} (removed {removed}, added {added})")
    return str(head.get("sha") or "")


# ── the gate ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, kw_only=True)
class GateReads:
    """What the gate reads besides the argocd API: injected so every branch of `run_gate` has a test."""

    epoch: Callable[[str], EpochState]
    tags: Callable[[], list[str]]
    resolves: Callable[[str], bool]
    now: Callable[[], dt.datetime]
    sleep: Callable[[float], None]


def current_pin(tree: Path) -> str:
    match = re.search(r"^[ \t]+targetRevision:[ \t]*(\S+)[ \t]*$", (tree / APPLICATION).read_text(), re.M)
    if not match:
        raise Refused(f"no targetRevision in {APPLICATION}")
    return match.group(1)


def checkout_sha(tree: Path) -> str:
    return subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()


def title_for(target: str) -> str:
    return f"fix: move the yadgar pin to parent chart {target}"


def run_gate(
    api: Client,
    tree: Path,
    reads: GateReads,
    *,
    regenerate: int | None,
    oci_wait: int = OCI_WAIT_SECONDS,
    checkout: Callable[[Path], str] = checkout_sha,
) -> dict:
    """The gate's decision as the state file's JSON: `action` (open, tidy, none), `reason`, and the State fields."""
    main_sha = main_head(api)
    if checkout(tree) != main_sha:
        return {"action": "none", "reason": f"the checkout is not main's head {main_sha}; the next run reads it"}
    pin = current_pin(tree)
    holds = open_holds(api)
    epoch = EpochState(kind="unread", reason="not read: a hold is open") if holds else reads.epoch(main_sha)
    if epoch.holds:
        print(f"::warning::main's epoch is {epoch.kind} ({epoch.reason}); the hold writer (C6) opens the hold issue")
    prs = pin_prs(api)
    target = None
    if not holds and not epoch.blocks:
        target = resolvable_target(pin, reads.tags(), reads.resolves, now=reads.now, sleep=reads.sleep, wait=oci_wait)
    open_heads = frozenset(p.head_sha for p in prs if p.state == "open")
    decision = decide(
        pin=pin,
        main_sha=main_sha,
        holds=holds,
        epoch=epoch,
        prs=prs,
        target=target,
        human_closed=human_closed(api, prs, target) if target else frozenset(),
        regenerate=regenerate,
        backoff=backoff(api, target, main_sha, reads.now(), open_heads=open_heads) if target else None,
    )
    out: dict = {"action": decision.action, "reason": decision.reason}
    if decision.action in ("open", "tidy") and decision.target:
        state = State(
            main_sha=main_sha,
            pin=pin,
            target=decision.target,
            title=title_for(decision.target),
            superseded=tuple(p.number for p in decision.supersede),
            keep=decision.keep,
        )
        out.update(asdict(state))
    return out


# ── CLI ─────────────────────────────────────────────────────────────────────


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def _import(name: str) -> Any:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    return __import__(name)


def _output(**values: str) -> None:
    """Step outputs: the workflow runs the next step only on `done=true`."""
    if path := os.environ.get("GITHUB_OUTPUT"):
        with open(path, "a") as handle:
            for key, value in values.items():
                handle.write(f"{key}={value}\n")


def _epoch_reader(tree: Path) -> Callable[[str], EpochState]:
    def read(main_sha: str) -> EpochState:
        settled_dispatch, settled_gate = _import("settled_dispatch"), _import("settled_gate")
        derived = settled_dispatch.epoch(tree, main_sha)
        found = None
        if derived is not None:
            found = settled_gate.find_verdict(settled_gate.GitHub(os.environ.get("VERIFY_TOKEN")), VERIFY_REPOSITORY, SETTLED_WORKFLOW_PATH, derived)
        return epoch_state(derived, found, _now())

    return read


def command_gate(args: argparse.Namespace) -> int:
    reads = GateReads(epoch=_epoch_reader(args.repo), tags=chart_tags, resolves=oci_resolves, now=_now, sleep=_sleep)
    out = run_gate(Api(os.environ.get("GITHUB_TOKEN")), args.repo, reads, regenerate=args.regenerate, oci_wait=args.oci_wait)
    print(f"{out['action']}: {out['reason']}")
    args.out.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n")
    _output(action=str(out["action"]), **({"target": str(out["target"])} if out.get("target") else {}))
    return EXIT_OK


def _superseded(api: Client, state: State) -> list[PinPR]:
    return [pr for pr in pin_prs(api) if pr.number in state.superseded]


def command_tidy(args: argparse.Namespace) -> int:
    api = Api(os.environ.get("PIN_TOKEN"))
    state = load_state(args.state)
    if state.keep is None:
        raise Refused("the state file names no PR in flight to keep")
    tidy(api, _superseded(api, state), state.target, keep=state.keep)
    return EXIT_OK


def command_push(args: argparse.Namespace) -> int:
    api = Api(os.environ.get("PIN_TOKEN"))
    state = load_state(args.state)
    open_heads = frozenset(pr.head_sha for pr in pin_prs(api) if pr.state == "open")
    sha = push(api, args.repo, state, changed=changed_files(args.repo), open_heads=open_heads)
    save_state(args.state, State(**{**asdict(state), "sha": sha}))
    _output(done="true", sha=sha)
    return EXIT_OK


def _finder() -> Callable[[str, str, str], dict | None]:
    pin_dryrun = _import("pin_dryrun")
    client = pin_dryrun.GitHub(os.environ.get("VERIFY_TOKEN"))

    def find(sha: str, main: str, current: str) -> dict | None:
        try:
            return pin_dryrun.find(client, VERIFY_REPOSITORY, DRYRUN_WORKFLOW_PATH, sha, main_sha=main, pin=current)
        except pin_dryrun.InfrastructureError as error:
            raise InfrastructureError(str(error)) from error

    return find


def command_dryrun(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    if not state.sha:
        raise Refused("the state file names no pushed sha")
    repo = Api(os.environ.get("GITHUB_TOKEN"))
    evidence = json.loads(args.evidence.read_text())
    post_e1(repo, state.sha, state.main_sha, bool(evidence.get("accept")))
    verify = Api(os.environ.get("VERIFY_TOKEN"))
    result = relay_dryrun(verify, repo, sha=state.sha, main_sha=state.main_sha, pin=state.pin, find=_finder(), now=_now, sleep=_sleep)
    post_e2(repo, state.sha, state.main_sha, result)
    args.out.write_text(json.dumps(asdict(result), indent=2, sort_keys=True) + "\n")
    _output(done="true", result=result.result)
    return EXIT_OK


def command_open(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    dryrun = DryRun(**json.loads(args.dryrun.read_text()))
    evidence = json.loads(args.evidence.read_text())
    if evidence.get("target") != state.target or evidence.get("pin") != state.pin:
        raise Refused(f"the evidence is for {evidence.get('pin')} -> {evidence.get('target')}, not {state.pin} -> {state.target}")
    api = Api(os.environ.get("PIN_TOKEN"))
    open_pr(
        api,
        state,
        e1_accept=bool(evidence.get("accept")),
        dryrun=dryrun,
        pin_body=args.body.read_text(),
        check_body=pr_body_checker(args.checker),
        reader=Api(os.environ.get("GITHUB_TOKEN")),
        superseded=_superseded(api, state),
    )
    return EXIT_OK


def command_evidence(args: argparse.Namespace) -> int:
    repo = Api(os.environ.get("GITHUB_TOKEN"))
    sha = evidence_target(repo, args.pr)
    main_sha = main_head(repo)
    if checkout_sha(args.repo) != main_sha:
        raise MainMoved(f"the checkout is not main's head {main_sha}")
    pin = current_pin(args.repo)
    verify = Api(os.environ.get("VERIFY_TOKEN"))
    result = relay_dryrun(verify, repo, sha=sha, main_sha=main_sha, pin=pin, find=_finder(), now=_now, sleep=_sleep)
    post_e2(repo, sha, main_sha, result)
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    gate = sub.add_parser("gate", help="read-only: decide whether to open a pin PR")
    gate.add_argument("--repo", type=Path, required=True)
    gate.add_argument("--out", type=Path, required=True)
    gate.add_argument("--regenerate", type=int, default=None)
    gate.add_argument("--oci-wait", type=int, default=OCI_WAIT_SECONDS)
    tidied = sub.add_parser("tidy", help="close older automatic PRs beside the one in flight")
    tidied.add_argument("--state", type=Path, required=True)
    pushed = sub.add_parser("push", help="make the pin commit and its branch")
    pushed.add_argument("--repo", type=Path, required=True)
    pushed.add_argument("--state", type=Path, required=True)
    dry = sub.add_parser("dryrun", help="E2 relay for the pushed sha; posts pin / e1 and pin / kind-dryrun")
    dry.add_argument("--state", type=Path, required=True)
    dry.add_argument("--evidence", type=Path, required=True)
    dry.add_argument("--out", type=Path, required=True)
    opened = sub.add_parser("open", help="open the pin PR, then close the superseded ones")
    opened.add_argument("--state", type=Path, required=True)
    opened.add_argument("--evidence", type=Path, required=True)
    opened.add_argument("--dryrun", type=Path, required=True)
    opened.add_argument("--body", type=Path, required=True)
    opened.add_argument("--checker", type=Path, required=True)
    evidence = sub.add_parser("evidence", help="E2 relay for a human pin PR; opens nothing")
    evidence.add_argument("--repo", type=Path, required=True)
    evidence.add_argument("--pr", type=int, required=True)
    return parser


COMMANDS = {
    "gate": command_gate,
    "tidy": command_tidy,
    "push": command_push,
    "dryrun": command_dryrun,
    "open": command_open,
    "evidence": command_evidence,
}


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exit_:
        return EXIT_USAGE if exit_.code else EXIT_OK
    try:
        return COMMANDS[args.command](args)
    except MainMoved as moved:
        print(f"no PR: {moved}; the next run starts again on the new main")
        return EXIT_OK
    except Refused as refused:
        print(f"::error::refused: {refused}")
        return EXIT_REFUSED
    except InfrastructureError as error:
        print(f"::error::INFRASTRUCTURE FAILURE: {error}")
        return EXIT_INFRA


if __name__ == "__main__":
    sys.exit(main())
