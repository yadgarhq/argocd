#!/usr/bin/env python3
"""Dispatch the settled-state gate on every argocd main merge until it has judged the epoch (ledger 1435).

    settled_dispatch.py nudge --repo DIR --sha SHA --out FILE [--slice-seconds N] [--interval N] [--grace N]

WHY. The gate (`yadgarhq/argocd-verify` `settled.yaml`, `settled_gate.py`
here) runs on a 10-minute cron, but GitHub starts scheduled runs only every
2-5 h on that repository (measured 2026-10-10). The deadline of an epoch
(K, A) is A's committer date + 3900 s, and estate's `verdict.py` refuses at
deadline + 30 min (its DEADLINE_MARGIN) without a verdict. One poll per 2-5 h
cannot meet that. The operator ruled: dispatch the gate on every argocd main
merge, and keep the cron as backup.

HOW. This loop runs on `ubuntu-latest` in argocd (`settled-dispatch.yaml`),
never on the gate's one runner. Each pass:

  1. `find_verdict` for (K, A). Found, green or red: done, exit 0. The red is
     the gate's finding, recorded in argocd-verify, not this loop's failure.
  2. The window is closed (deadline + grace): done with no verdict, exit 1.
  3. The time slice is over: exit 0 with done false. The workflow mints a new
     App token (it lives 60 minutes) and runs the next slice.
  4. No settled.yaml run that is not `completed`: send a plain
     `workflow_dispatch` with body `{"ref": "main"}`. Never `rejudge`, never
     `argocd_sha`. A run that is queued or running holds the gate's one
     concurrency slot; a new dispatch would evict a pending one, which can be
     a person's trial or rejudge.
  5. Sleep `interval`, then go to 1.

EACH GATE RUN STAYS ONE STATELESS POLL (ADR-0844 (2)). The loop lives here,
not in `settled.yaml`, so the runner is free for `verify.yaml` and
`project-probe.yaml` between polls. A dispatch before Argo CD has rolled finds
the cluster not settled, and `judge` answers PENDING before the deadline:
nothing is written, so an early poll can never write a red. After the deadline
`judge` answers only green or red, which is the ADR-0840 budget; the loop goes
on past the deadline exactly so that verdict exists before estate refuses.

PUBLIC LOGS. argocd is public, and the minted token can read every
argocd-verify artifact. This prints only the result and the run id, never a
verdict's clause.

A transient GitHub failure (5xx, 429, dropped connection) costs one pass;
any other API failure ends the run at once with exit 3.

Exit codes: 0 found, no K, or slice over; 1 window closed with no verdict;
2 usage; 3 infrastructure.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import settled_gate

REPOSITORY = "yadgarhq/argocd-verify"
WORKFLOW_FILE = "settled.yaml"
WORKFLOW_PATH = f".github/workflows/{WORKFLOW_FILE}"
RUNS = f"/repos/{REPOSITORY}/actions/workflows/{WORKFLOW_FILE}/runs?per_page=20"
DISPATCH = f"/repos/{REPOSITORY}/actions/workflows/{WORKFLOW_FILE}/dispatches"

# Under the App token's 60 minutes, with room for the find and the dispatch of the last pass.
SLICE_SECONDS = 3300
# Argo CD syncs and rolls in about two to five minutes after a merge (ledger 1435's measurement).
INTERVAL_SECONDS = 240
# Estate refuses at deadline + 30 min; the last dispatch must still be judged and uploaded before that.
GRACE_SECONDS = 1200


def epoch(repo: Path, sha: str, runner: Callable = subprocess.run) -> dict | None:
    """K, A and the deadline at `sha`, exactly as `settled_gate.derive` computes them, or None when there is no K.

    Git and PyYAML only: K and A need no render, so this job needs no helm and no chart pull.
    """
    table = settled_gate.read_at(repo, sha, settled_gate.TABLE, runner)
    application = settled_gate.read_at(repo, sha, settled_gate.APPLICATION, runner)
    try:
        key = settled_gate.render_key(table, application)
    except settled_gate.KeyRefusal:
        return None
    if key is None:
        return None
    anchor, committed, _problems = settled_gate.find_anchor(repo, sha, key, runner)
    deadline = settled_gate.utc(committed) + dt.timedelta(seconds=settled_gate.BUDGET_SECONDS)
    return {"key": key, "anchor": anchor, "deadline": settled_gate.zulu(deadline)}


def gate_busy(github) -> bool:
    """True when any recent settled.yaml run is not `completed` (queued, waiting, pending, requested, in progress)."""
    runs = github.get(RUNS).get("workflow_runs", [])
    return any(run.get("status") != "completed" for run in runs)


def nudge(
    github,
    epoch_: dict,
    *,
    now: Callable[[], dt.datetime],
    sleep: Callable[[float], None],
    slice_seconds: int = SLICE_SECONDS,
    interval: int = INTERVAL_SECONDS,
    grace: int = GRACE_SECONDS,
) -> dict:
    """Dispatch until a verdict for `epoch_` exists, the window closes, or the slice ends.

    A transient GitHub failure (a 5xx, a 429, a dropped connection) costs one
    pass, not the merge: it is logged and the next pass retries. Anything else
    raises InfrastructureError at once, so a 401/403/404 (the credential or its
    permission) stays loud rather than quietly running out the window.
    """
    close = settled_gate.utc(epoch_["deadline"]) + dt.timedelta(seconds=grace)
    stop = now() + dt.timedelta(seconds=slice_seconds)
    end = min(stop, close)
    dispatches = 0

    def ended(moment: dt.datetime) -> dict | None:
        if moment >= close:
            return {"done": True, "result": None, "run_id": None, "dispatches": dispatches}
        if moment >= stop:
            return {"done": False, "result": None, "run_id": None, "dispatches": dispatches}
        return None

    while True:
        try:
            found = settled_gate.find_verdict(github, REPOSITORY, WORKFLOW_PATH, epoch_)
            if found:
                return {"done": True, "result": found["verdict"].get("result"), "run_id": found["run_id"], "dispatches": dispatches}
            if over := ended(now()):
                return over
            if not gate_busy(github):
                github.post(DISPATCH, {"ref": "main"})
                dispatches += 1
        except settled_gate.InfrastructureError as error:
            if not transient(error):
                raise
            print(f"transient GitHub failure, the next pass retries: {error}")
            if over := ended(now()):
                return over
        sleep(max(0.0, min(interval, (end - now()).total_seconds())))


def transient(error: Exception) -> bool:
    """A dropped connection, a 429 or a 5xx. Not a 4xx otherwise, and not an unreadable verdict."""
    if isinstance(error, settled_gate.GitHubError):
        return error.status == 429 or error.status >= 500
    return isinstance(error, settled_gate.GitHubUnavailable)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    nudged = sub.add_parser("nudge", help="dispatch settled.yaml until the epoch at SHA has a verdict")
    nudged.add_argument("--repo", type=Path, required=True, help="the argocd checkout holding the sha")
    nudged.add_argument("--sha", type=settled_gate._full_sha, required=True, help="the argocd main commit just pushed")
    nudged.add_argument("--out", type=Path, required=True, help='{"done", "result", "run_id", "dispatches"}')
    nudged.add_argument("--slice-seconds", type=int, default=SLICE_SECONDS)
    nudged.add_argument("--interval", type=int, default=INTERVAL_SECONDS)
    nudged.add_argument("--grace", type=int, default=GRACE_SECONDS)
    return parser


def main(
    argv: list[str] | None = None,
    *,
    github=None,
    now: Callable[[], dt.datetime] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exit_:
        return 2 if exit_.code else 0
    now = now or (lambda: dt.datetime.now(dt.timezone.utc))
    try:
        epoch_ = epoch(args.repo, args.sha)
        if epoch_ is None:
            outcome = {"done": True, "result": None, "run_id": None, "dispatches": 0}
            print(f"no render key at {args.sha}: nothing for the gate to judge; estate refuses that input itself")
        else:
            print(f"epoch K {epoch_['key']} A {epoch_['anchor']} deadline {epoch_['deadline']}")
            outcome = nudge(
                github or settled_gate.GitHub(),
                epoch_,
                now=now,
                sleep=sleep,
                slice_seconds=args.slice_seconds,
                interval=args.interval,
                grace=args.grace,
            )
    except settled_gate.InfrastructureError as error:
        print(f"INFRASTRUCTURE FAILURE: {error}", file=sys.stderr)
        return 3
    args.out.write_text(json.dumps(outcome, sort_keys=True) + "\n")
    if outcome["result"]:
        print(f"verdict {outcome['result']}, run {outcome['run_id']}; {outcome['dispatches']} dispatch(es) this slice")
        return 0
    if not outcome["done"]:
        print(f"no verdict yet; {outcome['dispatches']} dispatch(es) this slice; the next slice continues")
        return 0
    if epoch_ is None:
        return 0
    print(f"NO VERDICT by deadline + {args.grace} s; {outcome['dispatches']} dispatch(es) this slice", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
