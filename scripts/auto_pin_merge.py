#!/usr/bin/env python3
"""Merge an automatic argocd pin PR once every check holds, read fresh at merge time (ledger 1632 C6, ADR-0946, ADR-0948, ADR-1004).

    auto_pin_merge.py --repo DIR --verdicts OUT.json [--run-head SHA --run-branch REF]   # judge pass
    auto_pin_merge.py --repo DIR --merge OUT.json                                        # merge pass
    auto_pin_merge.py --repo DIR [--run-head SHA --run-branch REF]                       # both, one process

TWO PASSES, SO THE RELEASE APP TOKEN IS MINTED ONLY FOR A MERGE (yaadgaar #424
item 2). The judge pass reads with GITHUB_TOKEN and VERIFY_TOKEN only, carries
out every regenerate, and writes the merge verdicts to `--verdicts`, plus the
step outputs `merge=true|false` and `refused=true|false`. It exits 0 on a
refusal, so a refused PR never stops another PR's merge; the workflow's last
step turns the job red on `refused=true`. Only on `merge=true` does the
workflow mint the release App token and run the merge pass, which reads
PIN_TOKEN and carries out each merge verdict through `act` (holds, main and
the head read once more right before the merge).

THE PLAN IS `yadgarhq/docs` `plans/auto-deploy-parent-chart.md`, section 4
("Auto-merge") and section 7 ("Identity"). `.github/workflows/auto-pin-merge.yaml`
runs this at argocd `main`, gated on `vars.AUTO_PIN_MERGE == 'true'`, on a
schedule and by hand (no `workflow_run`: zizmor's dangerous-triggers audit
refuses it). It checks out nothing from a PR. `--run-head`/`--run-branch`
stay for a caller that knows the ci run it follows; the workflow passes none.

THE BOT MERGES, NOT GITHUB AUTO-MERGE: auto-merge cannot re-check the hold or
the settled verdict at the moment it merges. For each open automatic PR
(`auto_pin.pin_prs`), `judge_pr` reads everything again and answers merge,
regenerate, wait or refuse. It MERGES only when all of these hold:

  1. No open `auto-pin-hold` issue (any kind, ADR-1015 included).
  2. The PR is open, based on `main`, from this repository, authored by the
     release App (`yadgarhq-bot[bot]`), on a branch `auto/pin-<v>-<sha12>`
     whose `<sha12>` is the head's.
  3. The hidden marker is present, names the head sha and `<v>`, and records
     E1 `accept`, E2 `green` and the E2 run. A LABEL NEVER PERMITS a merge:
     anyone with triage sets one, so the marker and the statuses decide. A
     label can only STOP one (plan section 4): `auto-pin-needs-human` on the
     PR, or no `auto-pin` label, is a human's brake, and the PR is skipped.
  4. Exactly one commit: the head, authored by the release App, verified
     (GitHub signs an App's API commit), whose only parent is the marker's main.
  5. The changed files equal `pin_bump.MECHANICAL_FILES` as MAIN's checkout
     defines it (`--repo`), never anything the PR carries, and each one is a
     plain `modified` entry: a rename onto a mechanical path deletes a file
     outside the set under a matching name, and a removal or an addition is
     no pin.
  6. Main is still the marker's main. Otherwise the base moved: REGENERATE.
     Never update-branch: it makes a new head with two commits and no
     `pin / kind-dryrun`, which no later pass could merge.
  7. Check runs named `ci / passed` on the head: exactly one, by GitHub
     Actions (app id 15368, what the ruleset requires), completed, success.
     None or still running: wait. Concluded otherwise: the sha is spent
     (ADR-0692): REGENERATE. Two or more: refuse.
  8. Every `pin / e1` and every `pin / kind-dryrun` status on the head is
     `success`, by `github-actions[bot]`, parses as `auto_pin.DESCRIPTION`
     (accept / green, `sha=` the head, `main=` main), and every E2 names the
     marker's run. A forged green beside the real one cannot win.
  9. YAAGAAR #420, the identity check: the E2 run, read on argocd-verify with
     the settled-dispatch token (`e2_run_problem`), is a `pin-dryrun.yaml`
     `workflow_dispatch` started by `yadgarhq-settled-dispatch[bot]`, titled
     `pin-dryrun <head>`, completed success, on argocd-verify's default
     branch, with a `pin-dryrun` artifact whose result is green for this sha,
     this main and main's pin. Any argocd workflow can post a status as
     `github-actions[bot]`; only that App can start that run.
 10. Main's settled epoch is green (`auto_pin.epoch_state`).
 11. `mergeable_state` is `clean`, polled a bounded number of times while
     GitHub still computes it (`unknown`). `behind`: REGENERATE. Anything
     else: wait.
 12. In `act`, right before the merge: the hold issues, main and the PR
     head are read once more, then `PUT /pulls/{n}/merge` with `merge_method: squash` and
     `sha: <head>`, so a push in between fails the merge. With the release
     App token: a merge by `GITHUB_TOKEN` starts no workflow, and
     settled-dispatch must run on the merge. A refusal (HTTP 405, for
     example `require_extra_approval_for_unattributed_changes`, unmeasured)
     is raised once, never retried.

REGENERATE dispatches `auto-pin.yaml` with `regenerate: <n>` (this job's
GITHUB_TOKEN, `actions: write`), unless an `auto-pin` run is already queued
or running. A pending dispatch can be evicted by the cron (`auto-pin.yaml`,
CONCURRENCY); the schedule here re-checks and dispatches again, so a lost
regenerate costs one pass. It is BOUNDED: once MAX_REGENERATIONS automatic
PRs for the same version were closed unmerged on the same main, it stops and
says so, so a deterministic CI failure cannot loop the private runner.

SKIP is a PR that is a human's to decide, a normal state: a head the bot did
not push, or a marker that records NEEDS-HUMAN. REFUSE is an anomaly (a
forged or mismatched status, the wrong files, the wrong run identity, two
`ci / passed`), and turns the job red.

Exit codes: 0 done (merged, waiting, skipping or regenerating are decisions);
1 a PR was refused or a merge was refused; 3 infrastructure. The judge pass
(`--verdicts`) exits 0 on a refused PR and says `refused=true` instead.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
import auto_pin as ap  # noqa: E402  (scripts/ is not a package)
import pin_dryrun as pd  # noqa: E402

CI_CHECK = "ci / passed"
ACTIONS_APP_ID = 15368
STRICT_BRANCH = re.compile(r"^auto/pin-(\d+\.\d+\.\d+)-([0-9a-f]{12})$")
AUTO_PIN_WORKFLOW = "auto-pin.yaml"
MAX_REGENERATIONS = 2
MERGEABLE_POLLS = 6
MERGEABLE_INTERVAL_SECONDS = 10
R = f"/repos/{ap.REPOSITORY}"
V = f"/repos/{ap.VERIFY_REPOSITORY}"


@dataclass(frozen=True, kw_only=True)
class Verdict:
    number: int
    action: str  # "merge", "regenerate", "wait", "skip" or "refuse"
    reason: str
    head: str | None = None
    version: str | None = None
    main: str | None = None


@dataclass(frozen=True, kw_only=True)
class MergeReads:
    """What `judge_pr` reads besides argocd's API: injected so every branch has a test."""

    epoch: Callable[[str], ap.EpochState]
    e2_run: Callable[[int, str, str], str | None]
    mechanical: tuple[str, ...]
    sleep: Callable[[float], None]


def _statuses_problem(statuses: list[dict], *, head: str, main: str, run: int) -> str | None:
    """Every `pin / e1` and `pin / kind-dryrun` on the head agrees: success, by the Actions bot, this head, this main, the marker's run."""
    expected = {ap.STATUS_E1: ("e1", "accept"), ap.STATUS_E2: ("e2", "green")}
    for context, (check, result) in expected.items():
        mine = [s for s in statuses if s.get("context") == context]
        if not mine:
            return f"no `{context}` status on {head}"
        for status in mine:
            if status.get("state") != "success":
                return f"`{context}` on {head} is {status.get('state')}"
            creator = (status.get("creator") or {}).get("login")
            if creator != ap.STATUS_LOGIN:
                return f"`{context}` on {head} was created by {creator}, not {ap.STATUS_LOGIN}"
            parsed = ap.parse_status_description(str(status.get("description") or ""))
            if not parsed or parsed["check"] != check or parsed["result"] != result:
                return f"`{context}` on {head}: description {str(status.get('description'))[:160]!r} is not `{check} {result} sha=… main=…`"
            if parsed["sha"] != head:
                return f"`{context}` on {head} names judged sha {parsed['sha']}, not the head"
            if parsed["main"] != main:
                return f"`{context}` on {head} was judged against main {parsed['main']}, not main {main}"
            if check == "e2" and parsed.get("run") != run:
                return f"`{context}` on {head} names run {parsed.get('run')}, not the marker's E2 run {run}"
    return None


def _ci_passed(api: ap.Client, head: str) -> tuple[str, str]:
    """`(state, reason)`: state is green, pending, spent or refuse."""
    query = urllib.parse.urlencode({"check_name": CI_CHECK, "filter": "all", "per_page": 100}, quote_via=urllib.parse.quote, safe="")
    runs = [r for r in ((api.get(f"{R}/commits/{head}/check-runs?{query}") or {}).get("check_runs") or []) if r.get("name") == CI_CHECK]
    if not runs:
        return "pending", f"no `{CI_CHECK}` check run on {head} yet"
    if len(runs) > 1:
        return "refuse", f"{len(runs)} `{CI_CHECK}` check runs on {head}; exactly one may exist"
    (run,) = runs
    if (run.get("app") or {}).get("id") != ACTIONS_APP_ID:
        return "refuse", f"`{CI_CHECK}` on {head} comes from app {(run.get('app') or {}).get('id')}, not GitHub Actions ({ACTIONS_APP_ID})"
    if run.get("status") != "completed":
        return "pending", f"`{CI_CHECK}` on {head} is {run.get('status')}"
    if run.get("conclusion") != "success":
        return "spent", f"`{CI_CHECK}` on {head} concluded {run.get('conclusion')}: the sha is spent (ADR-0692)"
    return "green", f"`{CI_CHECK}` success on {head}"


def judge_pr(api: ap.Client, number: int, reads: MergeReads, *, run_head: str | None = None, run_branch: str | None = None) -> Verdict:
    """The merge decision for automatic PR #`number`, every input read fresh (see the module docstring for the order)."""

    def verdict(action: str, reason: str, **extra: str | None) -> Verdict:
        return Verdict(number=number, action=action, reason=reason, **extra)

    holds = ap.open_holds(api)
    if holds:
        return verdict("wait", f"hold issue {', '.join(f'#{n}' for n in holds)} is open")
    pull = api.get(f"{R}/pulls/{number}") or {}
    head = pull.get("head") or {}
    sha = str(head.get("sha") or "")
    branch = str(head.get("ref") or "")
    if pull.get("state") != "open":
        return verdict("wait", f"#{number} is {pull.get('state')}")
    if (pull.get("user") or {}).get("login") != ap.PIN_LOGIN:
        return verdict("refuse", f"#{number}'s author is {(pull.get('user') or {}).get('login')}, not {ap.PIN_LOGIN}")
    if (pull.get("base") or {}).get("ref") != "main" or (head.get("repo") or {}).get("full_name") != ap.REPOSITORY:
        return verdict("refuse", f"#{number} is not a same-repository PR into main")
    match = STRICT_BRANCH.match(branch)
    if not match or not ap.FULL_SHA.match(sha) or match.group(2) != sha[:12]:
        return verdict("refuse", f"#{number}'s branch {branch!r} is not auto/pin-<version>-<sha12> of its head {sha}")
    version = match.group(1)
    if run_head and run_branch == branch and run_head != sha:
        return verdict("wait", f"the ci run judged {run_head}, but #{number}'s head is now {sha}")
    marker = ap.parse_marker(pull.get("body"))
    if marker is None:
        return verdict("refuse", f"#{number} carries no auto-pin marker")
    if marker.target != version:
        return verdict("refuse", f"#{number}'s marker names target {marker.target}, not its branch's {version}")
    # SKIP, not refuse: these PRs are a human's to decide, a normal state, so the job stays green.
    if marker.sha != sha:
        return verdict("skip", f"#{number}'s head {sha} is not the commit auto-pin pushed ({marker.sha}): a human's PR now")
    if marker.e1 != "accept" or marker.e2 != "green" or marker.e2_run is None:
        return verdict("skip", f"#{number}'s marker records e1={marker.e1} e2={marker.e2} e2_run={marker.e2_run}: NEEDS-HUMAN, never merged by the bot")
    labels = {str(label.get("name")) for label in pull.get("labels") or []}
    if ap.LABEL_NEEDS_HUMAN in labels:
        return verdict("skip", f"#{number} is labelled {ap.LABEL_NEEDS_HUMAN}: a human stopped it, never merged by the bot")
    if ap.LABEL_PIN not in labels:
        return verdict("skip", f"#{number} is not labelled {ap.LABEL_PIN}: a human removed it, or the label write failed; never merged by the bot")
    extra = {"head": sha, "version": version, "main": marker.main}

    commits = api.get(f"{R}/pulls/{number}/commits") or []
    if len(commits) != 1:
        return verdict("refuse", f"#{number} has {len(commits)} commits; exactly one commit, the bot's, may merge")
    (commit,) = commits
    parents = [str(p.get("sha")) for p in commit.get("parents") or []]
    verified = ((commit.get("commit") or {}).get("verification") or {}).get("verified") is True
    if commit.get("sha") != sha or (commit.get("author") or {}).get("login") != ap.PIN_LOGIN or not verified or parents != [marker.main]:
        return verdict("refuse", f"#{number}'s commit is not the release App's verified commit on main {marker.main}")

    entries = api.paginate(f"{R}/pulls/{number}/files")
    files = sorted(str(f.get("filename")) for f in entries)
    if files != sorted(reads.mechanical):
        return verdict("refuse", f"#{number} changes files {files}, not main's MECHANICAL_FILES {sorted(reads.mechanical)}")
    odd = [f"{f.get('filename')} ({f.get('status')}{', from ' + str(f['previous_filename']) if f.get('previous_filename') else ''})" for f in entries if f.get("status") != "modified" or f.get("previous_filename")]
    if odd:
        return verdict("refuse", f"#{number} does not only modify its mechanical files in place: {', '.join(odd)}")

    main = ap.main_head(api)
    if main != marker.main:
        return verdict("regenerate", f"#{number} was built on main {marker.main}; main is {main}", **extra)

    ci, why = _ci_passed(api, sha)
    if ci == "pending":
        return verdict("wait", why)
    if ci == "refuse":
        return verdict("refuse", why)
    if ci == "spent":
        return verdict("regenerate", why, **extra)

    problem = _statuses_problem(api.paginate(f"{R}/commits/{sha}/statuses"), head=sha, main=main, run=marker.e2_run)
    if problem:
        return verdict("refuse", problem)
    problem = reads.e2_run(marker.e2_run, sha, main)
    if problem:
        return verdict("refuse", f"E2 run {marker.e2_run}: {problem}")

    epoch = reads.epoch(main)
    if epoch.kind != "green":
        return verdict("wait", f"main's settled epoch is {epoch.kind}: {epoch.reason}")

    state = str(pull.get("mergeable_state") or "unknown")
    for _ in range(MERGEABLE_POLLS):
        if state != "unknown":
            break
        reads.sleep(MERGEABLE_INTERVAL_SECONDS)
        state = str((api.get(f"{R}/pulls/{number}") or {}).get("mergeable_state") or "unknown")
    if state == "behind":
        return verdict("regenerate", f"#{number} is behind main", **extra)
    if state != "clean":
        return verdict("wait", f"#{number}'s mergeable_state is {state}, not clean")
    return verdict("merge", f"#{number} ({version}) is green on {sha}", **extra)


def e2_run_problem(verify: Any, run_id: int, sha: str, main: str, pin: str) -> str | None:
    """None when run `run_id` is the dispatch App's green pin-dryrun of `sha` against `main` and `pin`; else why not."""
    run = verify.get(f"{V}/actions/runs/{run_id}") or {}
    for actor_field in ("actor", "triggering_actor"):
        login = (run.get(actor_field) or {}).get("login")
        if login != ap.DISPATCH_LOGIN:
            return f"run {run_id} was started by {login}, not {ap.DISPATCH_LOGIN}"
    if run.get("path") != ap.DRYRUN_WORKFLOW_PATH:
        return f"run {run_id} is workflow path {run.get('path')}, not {ap.DRYRUN_WORKFLOW_PATH}"
    if run.get("event") != "workflow_dispatch":
        return f"run {run_id} event is {run.get('event')}, not workflow_dispatch"
    if run.get("head_branch") != "main":
        return f"run {run_id} ran on branch {run.get('head_branch')}, not main"
    for repository_field in ("repository", "head_repository"):
        if (run.get(repository_field) or {}).get("full_name") != ap.VERIFY_REPOSITORY:
            return f"run {run_id} {repository_field} is {(run.get(repository_field) or {}).get('full_name')}, not {ap.VERIFY_REPOSITORY}"
    if run.get("display_title") != f"pin-dryrun {sha}":
        return f"run {run_id} display title is {run.get('display_title')!r}, not 'pin-dryrun {sha}'"
    if run.get("status") != "completed" or run.get("conclusion") != "success":
        return f"run {run_id} is {run.get('status')} / {run.get('conclusion')}, not completed / success"
    # A tag named `main` reports head_branch=main (argocd#111 S1): compare against the branch's head COMMIT.
    default_head = pd.branch_head(verify, ap.VERIFY_REPOSITORY, "main")
    if not pd.on_default_branch(verify, ap.VERIFY_REPOSITORY, run.get("head_sha"), default_head):
        return f"run {run_id} ran workflow code {run.get('head_sha')}, not on the default branch at {default_head}"
    artifacts = [a for a in ((verify.get(f"{V}/actions/runs/{run_id}/artifacts") or {}).get("artifacts") or []) if a.get("name") == pd.ARTIFACT and not a.get("expired")]
    if len(artifacts) != 1:
        return f"run {run_id} holds {len(artifacts)} unexpired {pd.ARTIFACT} artifacts, not one"
    result = pd._result_of(verify.download(artifacts[0]["archive_download_url"]))
    if result.get("result") != "green":
        return f"run {run_id} result is {result.get('result')}, not green"
    if result.get("sha") != sha:
        return f"run {run_id} judged sha {result.get('sha')}, not {sha}"
    if result.get("main_sha") != main:
        return f"run {run_id} judged against main {result.get('main_sha')}, not {main}"
    if result.get("pin") != pin:
        return f"run {run_id} judged against pin {result.get('pin')}, not main's {pin}"
    return None


def regenerations_left(api: ap.Client, version: str, main: str) -> bool:
    """False once MAX_REGENERATIONS automatic PRs for `version` were closed unmerged on this same main."""
    spent = [p for p in ap.pin_prs(api) if p.version == version and p.state == "closed" and not p.merged and p.marker is not None and p.marker.main == main]
    return len(spent) < MAX_REGENERATIONS


def _auto_pin_busy(actions: ap.Client) -> bool:
    for state in ("in_progress", "queued"):
        if (actions.get(f"{R}/actions/workflows/{AUTO_PIN_WORKFLOW}/runs?status={state}") or {}).get("workflow_runs"):
            return True
    return False


def act(pin: ap.Client, actions: ap.Client, verdict: Verdict, *, regenerations_left: bool) -> None:
    """Carry out a verdict: merge with the release App (`pin`), or dispatch a regenerate with GITHUB_TOKEN (`actions`)."""
    print(f"#{verdict.number}: {verdict.action}: {verdict.reason}")
    if verdict.action == "regenerate":
        if not regenerations_left:
            print(f"::warning::#{verdict.number}: {MAX_REGENERATIONS} automatic PRs for {verdict.version} already closed on main {verdict.main}; no regenerate, a human looks")
            return
        if _auto_pin_busy(actions):
            print(f"#{verdict.number}: an auto-pin run is queued or running; the next pass re-checks")
            return
        actions.post(f"{R}/actions/workflows/{AUTO_PIN_WORKFLOW}/dispatches", {"ref": "main", "inputs": {"regenerate": str(verdict.number)}})
        print(f"#{verdict.number}: dispatched auto-pin.yaml regenerate={verdict.number}")
        return
    if verdict.action != "merge":
        return
    # GITHUB_TOKEN (`issues: read`) reads the holds; the release App token is not asked for an issues scope.
    holds = ap.open_holds(actions)
    if holds:
        print(f"#{verdict.number}: hold issue {', '.join(f'#{n}' for n in holds)} opened after the judgement; no merge")
        return
    if ap.main_head(pin) != verdict.main:
        print(f"#{verdict.number}: main moved after the judgement; no merge, the next pass re-judges")
        return
    live = ((pin.get(f"{R}/pulls/{verdict.number}") or {}).get("head") or {}).get("sha")
    if live != verdict.head:
        print(f"#{verdict.number}: its head moved to {live} after the judgement of {verdict.head}; no merge")
        return
    merged = pin.put(f"{R}/pulls/{verdict.number}/merge", {"merge_method": "squash", "sha": verdict.head})
    print(f"#{verdict.number}: merged as {(merged or {}).get('sha')}")


def _e2_reader(pin: str) -> Callable[[int, str, str], str | None]:
    client = pd.GitHub(os.environ.get("VERIFY_TOKEN"))

    def read(run_id: int, sha: str, main: str) -> str | None:
        try:
            return e2_run_problem(client, run_id, sha, main, pin)
        except pd.InfrastructureError as error:
            raise ap.InfrastructureError(str(error)) from error

    return read


def judge_pass(reader: ap.Client, args: argparse.Namespace) -> tuple[int, list[Verdict]]:
    """Judge every open automatic PR with GITHUB_TOKEN alone, regenerate where due, and return the merge verdicts."""
    pb = ap._import("pin_bump")
    reads = MergeReads(epoch=ap._epoch_reader(args.repo), e2_run=_e2_reader(ap.current_pin(args.repo)), mechanical=tuple(pb.MECHANICAL_FILES), sleep=time.sleep)
    code = ap.EXIT_OK
    merges: list[Verdict] = []
    candidates = [p for p in ap.pin_prs(reader) if p.state == "open"]
    if not candidates:
        print("no open automatic pin PR")
    for pr in candidates:
        verdict = judge_pr(reader, pr.number, reads, run_head=args.run_head or None, run_branch=args.run_branch or None)
        if verdict.action == "refuse":
            print(f"::warning::#{pr.number} refused: {verdict.reason}")
            code = ap.EXIT_REFUSED
            continue
        if verdict.action == "merge":
            print(f"#{verdict.number}: merge: {verdict.reason}")
            merges.append(verdict)
            continue
        left = regenerations_left(reader, verdict.version or "", verdict.main or "") if verdict.action == "regenerate" else True
        # A regenerate needs GITHUB_TOKEN only; `act` touches its first argument only for a merge.
        try:
            act(reader, reader, verdict, regenerations_left=left)
        except ap.ApiError as error:
            print(f"::error::#{pr.number}: the {verdict.action} failed ({error}); not retried")
            code = ap.EXIT_REFUSED
    return code, merges


def merge_pass(pin_api: ap.Client, reader: ap.Client, merges: list[Verdict]) -> int:
    """Carry out each merge verdict with the release App token; `act` re-reads holds, main and the head first."""
    code = ap.EXIT_OK
    for verdict in merges:
        try:
            act(pin_api, reader, verdict, regenerations_left=True)
        except ap.ApiError as error:
            print(f"::error::#{verdict.number}: the merge was refused ({error}); not retried")
            code = ap.EXIT_REFUSED
    return code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, required=True, help="main's checkout: MECHANICAL_FILES, the pin and the epoch come from here")
    parser.add_argument("--run-head", default="", help="the head sha of the ci run that triggered this pass, if any")
    parser.add_argument("--run-branch", default="", help="that run's head branch")
    passes = parser.add_mutually_exclusive_group()
    passes.add_argument("--verdicts", type=Path, default=None, help="judge pass: write the merge verdicts here; PIN_TOKEN is not read")
    passes.add_argument("--merge", type=Path, default=None, help="merge pass: carry out the merge verdicts the judge pass wrote, with PIN_TOKEN")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_:
        return ap.EXIT_USAGE if exit_.code else ap.EXIT_OK
    try:
        reader = ap.Api(os.environ.get("GITHUB_TOKEN"))
        main_sha = ap.main_head(reader)
        if ap.checkout_sha(args.repo) != main_sha:
            print(f"the checkout is not main's head {main_sha}; the next pass reads it")
            if args.verdicts:
                ap._output(merge="false", refused="false")
            return ap.EXIT_OK
        if args.merge:
            pin_api = ap.Api(os.environ.get("PIN_TOKEN"))
            return merge_pass(pin_api, reader, [Verdict(**v) for v in json.loads(args.merge.read_text())["merge"]])
        code, merges = judge_pass(reader, args)
        if args.verdicts:
            args.verdicts.write_text(json.dumps({"merge": [asdict(v) for v in merges]}, indent=2, sort_keys=True) + "\n")
            ap._output(merge="true" if merges else "false", refused="true" if code != ap.EXIT_OK else "false")
            return ap.EXIT_OK
        return max(code, merge_pass(ap.Api(os.environ.get("PIN_TOKEN")), reader, merges))
    except ap.Refused as refused:
        print(f"::error::refused: {refused}")
        return ap.EXIT_REFUSED
    except ap.InfrastructureError as error:
        print(f"::error::INFRASTRUCTURE FAILURE: {error}")
        return ap.EXIT_INFRA


if __name__ == "__main__":
    sys.exit(main())
