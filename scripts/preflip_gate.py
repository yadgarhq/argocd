#!/usr/bin/env python3
"""The pre-flip refusal gate: the plan's legs (a), (b) and (c) for the counting -> enforcing flip.

Ledgers 1608 and 1618; yadgarhq/docs `plans/project-validation.md` stage 5 and
`plans/the-d35-seeder.md` errata E2. The operator's condition is a clean
observation window: 0 refusals. It runs on the argocd-verify runner (ADR-0840),
from `preflip.yaml` there, which executes argocd main's copy (ADR-0844).

    preflip_gate.py --start ISO [--end ISO] [--min-resolutions N] [--leg-c-max-age S]

WHAT A PASS MEANS. The plan's stage 5 gate has three legs, in order: (c) one
deliberate probe per reason class, observed to increment its counter BEFORE
the window opens; then, over the window, (a) the refusal counter rose by 0 and
(b) the resolution counter rose. Legs 5 and 6 below are (a) and (b), with the
guards 1-4 that make them readable; leg 7 is (c).

LEG (C) IS READ FROM THE PROBE'S OWN VERDICT, NOT FROM PROMETHEUS AGAIN
(ledger 1624). `project_probe.py --leg-c` sends one call per probeable reason
class (`PROJECT_UNRESOLVABLE`, `PROJECT_UNREGISTERED_PERSONAL`,
`PROJECT_UNREGISTERED_ORG`, `PROJECT_INVALID`, `PROJECT_ARCHIVED`,
`PROJECT_NOT_MEMBER`) and exits 0 only when every one of their counters rose
per pod; it is red when inconclusive. Its PERSONAL claim is under the probe
account's own segment, read from the gateway's `GET /auth/whoami` (ADR-0959),
so leg (c) cannot pass on a gateway without that route. argocd-verify's
`project-probe.yaml` names such a run `project-probe leg-c` (`run-name`; the
runs listing carries no dispatch inputs). Only a `workflow_dispatch` of that workflow's path on this
repository's default branch, with this repository as head and a `head_sha`
the default branch contains, counts: a branch's or a tag's copy of the
workflow could set the same name and exit 0. Only the probe job's own
conclusion is read. `--leg-c-max-age` is finite, above 0, at most 7 days. Leg 7
passes when the NEWEST such run whose job completed at or
before `start - probe_slack`, and no earlier than `start - leg_c_max_age`,
concluded `success`. A newer red run is not masked by an older green one.
Re-reading the counter here instead would need the run, the slack and the
window inside Prometheus' 3d retention together, which caps the window.
`PROJECT_REGISTRY_UNAVAILABLE` has no probe (it needs the registry down); legs
(a) and 4 cover it inside the window.

THE PROBE IS SERIALIZED OUT OF THE WINDOW, NOT SUBTRACTED. The refusal counter
has one label, `reason`, and no claim label, so the ADR-0841 probe's
increments look exactly like real traffic. Subtracting "one per probe run" is
foolable: a probe run that finds an estate `smoke` job running sends NOTHING
and still exits 0, so its credit would hide one real refusal. The plan
therefore switches the probe off (`PROJECT_PROBE_ENABLED=false`) before the
window opens, and this gate CHECKS that rather than trusting it: any
non-skipped `project-probe` job that ran inside the window, or within
`--probe-slack` seconds before it, fails the gate.

THE LEGS, all read, all reported, any one failing fails the gate:
  1. `PROJECT_PROBE_ENABLED` (passed in from the workflow's `vars`) is exactly
     `false`.
  2. No non-skipped `project-probe` job overlaps [start - slack, end].
  3. The window starts inside Prometheus' retention
     (`prometheus_tsdb_lowest_timestamp_seconds` <= start - lookback).
  4. No scrape gap, read three ways, because one healthy pod must not hide a
     blind one:
     - across all gateway pods, `registry_loaded` samples cover [start, end]
       with no gap longer than `--max-gap`. An empty answer is a dead scrape,
       never zero refusals;
     - no single pod's own gauge has a gap longer than `--max-gap` between its
       own first and last sample;
     - `up{namespace="yadgar",app="gateway"}` exists and is never 0 in
       [start, end].
  5. `yadgar_gateway_project_refusal_total`, summed over EVERY reason
     (including `PROJECT_REGISTRY_UNAVAILABLE`) and every pod, rose by 0.
  6. `yadgar_gateway_project_resolution_total` rose by at least
     `--min-resolutions` (default 1): zero traffic yields zero refusals for
     free, so a silent window proves nothing.
  7. Leg (c), as above.
  8. AT GATE TIME, `registry_loaded` reads 1 on EVERY gateway replica (ledger
     1674, ADR-0674's gauge). Argo calls the gateway Healthy once its pods are
     Ready, which can precede the registry load; a flip then refuses every
     call such a replica serves. Read at `now`, not over the window:
     - the expected count is the live Deployment `yadgar/gateway`'s
       `spec.replicas` (an integer >= 1), never a constant;
     - the live pods are those its own `matchLabels` selector lists, minus
       the terminal phases (Failed, Succeeded); a selector with
       `matchExpressions`, or none, is refused rather than approximated;
     - the live pod count must equal `spec.replicas`, every live pod must
       have the gauge in the instant read (`LOADED{namespace="yadgar"}`, no
       pod or instance filter) and every one of its series must read 1, and
       no series may name a pod that is not live. A missing series is a FAIL
       (unscraped, or a boot that never published), never a pass.
     The Kubernetes reads are two GETs with the runner pod's own
     ServiceAccount token (`post-merge-verifier`, get/list on pods and
     deployments), straight to the API server.

PER SERIES, FROM RAW SAMPLES, NEVER A BARE `increase()` (ADR-0620). The gateway
registers its counters lazily, so a pod's first refusal makes the series appear
at 1, which `increase()` reads as 0. The gate reads the raw matrix over
[start - lookback, end] and walks each series (one per pod and reason):
  - the baseline is the last sample at or before `start`;
  - a reset (a container restart under the same pod name) counts the value
    after it in full. A reset is any of: a decrease; the series ABSENT at a
    scrape where the same target's `registry_loaded` gauge was sampled (one
    scrape stamps all of a target's series alike; target = pod, instance,
    job), then back
    (a counter never unregisters, so absence means a new process, and the
    value it returns at can EQUAL the old one: 1, restart, one refusal, 1);
    or that target's gauge dropping from 1 to 0 (a fresh process that has
    not loaded the registry yet), which resets every series of the target;
  - a series with no sample at or before `start` has an unknown baseline. For
    refusals that counts the first value in full (fail closed). For
    resolutions it counts the first value only when the same pod's
    `registry_loaded` gauge was sampled BEFORE it (the gauge is published from
    boot, so the counter's absence then was a real 0); otherwise it counts
    nothing (fail closed in the other direction).

KNOWN BLIND SPOTS, stated rather than hidden. Leg 4 bounds how long nobody was
looking; it cannot see inside a single scrape interval (15 s) or outside
Prometheus' discovery:
  - a refusal in the last scrape interval of a pod that then died is never
    scraped;
  - a container restart AND a climb back to the same value inside one scrape
    interval leaves no absent scrape and no gauge 0, so it reads as no change;
  - a pod Prometheus never discovers (never scraped, so no `up` series) is not
    covered at all.

THE WINDOW LENGTH IS THE OPERATOR'S (plan open question 3). This gate takes it
as input. Prometheus on kind-yadgar keeps 3d (set by ledger 1678), so a
longer window refuses at leg 3 until retention changes or the window is read
in slices.

Exit 0 PASS, 1 FAIL. Any read that fails is a FAIL. Never printed: either token.

Standard library only: the pre-commit hook that runs the tests installs pytest
and PyYAML and nothing else.
"""

from __future__ import annotations

import argparse
import bisect
import dataclasses
import datetime as dt
import json
import math
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Protocol

REFUSALS = "yadgar_gateway_project_refusal_total"
RESOLUTIONS = "yadgar_gateway_project_resolution_total"
LOADED = "yadgar_gateway_project_registry_loaded"
LOWEST_QUERY = 'min(prometheus_tsdb_lowest_timestamp_seconds{job="prometheus"})'
# The metric names are the gateway's own, so the counters need no `app` label:
# a relabelled pod still counts. `up` carries no gateway-specific name and does.
SELECTOR = f'{{__name__=~"{REFUSALS}|{RESOLUTIONS}|{LOADED}",namespace="yadgar"}}'
UP_SELECTOR = 'up{namespace="yadgar",app="gateway"}'
# Leg 8's instant read: every pod's gauge, with no pod, instance or app filter.
LOADED_NOW_QUERY = f'{LOADED}{{namespace="yadgar"}}'
GATEWAY_NAMESPACE = "yadgar"
GATEWAY_DEPLOYMENT = "gateway"
TERMINAL_POD_PHASES = frozenset({"Failed", "Succeeded"})
SERVICE_ACCOUNT_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"

PROBE_REPO = "yadgarhq/argocd-verify"
PROBE_WORKFLOW = "project-probe.yaml"
PROBE_JOB_TIMEOUT = 20 * 60  # project-probe.yaml `timeout-minutes: 20`
LEG_C_TITLE = "project-probe leg-c"  # project-probe.yaml `run-name` with `leg_c: true`
PROBE_PATH = f".github/workflows/{PROBE_WORKFLOW}"
PROBE_JOB_NAME = "785 refusal-counter probe"  # project-probe.yaml `jobs.probe.name`
FULL_SHA = re.compile(r"[0-9a-f]{40}")
LEG_C_MAX_AGE_CAP = 7 * 86400.0

EXIT_PASS, EXIT_FAIL = 0, 1


class GateError(Exception):
    """A read that could not be made, or an answer that cannot be trusted. Always a FAIL."""


class GitHubNotFound(GateError):
    """A GitHub 404. Only `on_default_branch` reads it as an answer ("not on it")."""


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str | None) -> float | None:
    if not value:
        return None
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"{value!r} has no timezone; give UTC as a trailing Z")
    return parsed.timestamp()


# --- the samples ----------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Series:
    name: str
    pod: str
    label: str  # `reason` for refusals, `outcome` for resolutions, "" for the gauge
    instance: str
    samples: tuple[tuple[float, float], ...]
    job: str = ""

    def target(self) -> tuple[str, str, str]:
        """The scrape target: one scrape stamps all of its series with one timestamp."""
        return (self.pod, self.instance, self.job)

    def key(self) -> str:
        return f"{self.pod}{{{self.label}}}" if self.label else self.pod


def parse_matrix(body: dict) -> list[Series]:
    if body.get("status") != "success" or body.get("data", {}).get("resultType") != "matrix":
        raise GateError(f"prometheus answered {body.get('status')!r}: {body.get('error', 'not a matrix')}")
    out = []
    for row in body["data"]["result"]:
        metric = row["metric"]
        name, pod = metric.get("__name__"), metric.get("pod")
        if not name or not pod:
            raise GateError(f"a gateway series without __name__ or pod: {metric}")
        samples = []
        for t, v in row["values"]:
            value = float(v)
            if math.isnan(value):
                raise GateError(f"{name} {pod}: NaN sample at {iso(float(t))}")
            samples.append((float(t), value))
        label = metric.get("reason") or metric.get("outcome") or ""
        if name == REFUSALS and not metric.get("reason"):
            raise GateError(f"a refusal series without a reason: {metric}")
        out.append(Series(name, pod, label, metric.get("instance", ""), tuple(sorted(samples)), metric.get("job", "")))
    return out


@dataclasses.dataclass
class Count:
    total: float = 0
    events: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass(frozen=True)
class Restarts:
    """One pod's restart evidence: its gauge's scrape times, and the times it fell 1 -> 0."""

    scrapes: tuple[float, ...] = ()
    drops: tuple[float, ...] = ()

    def between(self, a: float, b: float) -> bool:
        """A restart between two consecutive samples of a series at `a` and `b`."""
        i = bisect.bisect_right(self.scrapes, a)
        if i < len(self.scrapes) and self.scrapes[i] < b:
            return True  # the gauge was scraped and the series was absent
        j = bisect.bisect_right(self.drops, a)
        return j < len(self.drops) and self.drops[j] <= b


def restarts_by_target(gauges: list[Series]) -> dict[tuple[str, str, str], Restarts]:
    """Keyed by scrape target, so a second job scraping the same pod on its own
    clock can never be read as the counter's absence."""
    scrapes: dict[tuple[str, str, str], set[float]] = {}
    drops: dict[tuple[str, str, str], set[float]] = {}
    for g in gauges:
        scrapes.setdefault(g.target(), set()).update(t for t, _ in g.samples)
        for (_, v0), (t1, v1) in zip(g.samples, g.samples[1:]):
            if v0 == 1 and v1 == 0:
                drops.setdefault(g.target(), set()).add(t1)
    return {
        target: Restarts(tuple(sorted(scrapes[target])), tuple(sorted(drops.get(target, ()))))
        for target in scrapes
    }


def count(
    series: Series,
    start: float,
    unknown_baseline_counts: bool,
    born_after: float | None = None,
    restarts: Restarts = Restarts(),
) -> Count:
    """The series' rise over (start, end], per the module docstring."""
    c = Count()
    before = [(t, v) for t, v in series.samples if t <= start]
    inside = [(t, v) for t, v in series.samples if t > start]
    if not inside:
        return c
    prev_t: float | None
    if before:
        prev_t, prev = before[-1]
    else:
        t1, v1 = inside[0]
        if unknown_baseline_counts or (born_after is not None and born_after < t1):
            prev_t, prev = None, 0.0  # born in the window (or no baseline, read as born: fail closed)
        else:
            prev_t, prev = t1, v1
            c.events.append(f"{iso(t1)} {series.key()} first sample {v1:g} has no baseline; not counted")
    for t, v in inside:
        if v < prev or (prev_t is not None and restarts.between(prev_t, t)):
            c.events.append(f"{iso(t)} {series.key()} reset {prev:g} -> {v:g}, counted {v:g}")
            c.total += v
        else:
            if v > prev:
                c.events.append(f"{iso(t)} {series.key()} {prev:g} -> {v:g}")
            c.total += v - prev
        prev_t, prev = t, v
    return c


def scrape_gaps(gauges: list[Series], start: float, end: float, max_gap: float) -> list[str]:
    times = sorted({t for s in gauges for t, _ in s.samples if start - max_gap <= t <= end + max_gap})
    if not times:
        return [f"no {LOADED} sample in [{iso(start)}, {iso(end)}]: the scrape is dead, so no series is not zero"]
    gaps = []
    edges = [start] + times + [end]
    for a, b in zip(edges, edges[1:]):
        if b - a > max_gap:
            gaps.append(f"scrape gap {iso(a)} .. {iso(b)} ({b - a:.0f}s > {max_gap:.0f}s)")
    # Per pod, between its own first and last sample: one healthy pod must not
    # hide a blind one in the merged timeline above.
    per_pod: dict[str, set[float]] = {}
    for g in gauges:
        per_pod.setdefault(g.pod, set()).update(t for t, _ in g.samples)
    for pod in sorted(per_pod):
        own = sorted(per_pod[pod])
        for a, b in zip(own, own[1:]):
            if b - a > max_gap and b > start and a < end:
                gaps.append(f"scrape gap on pod {pod} {iso(a)} .. {iso(b)} ({b - a:.0f}s > {max_gap:.0f}s)")
    return gaps


def up_failures(ups: list[Series], start: float, end: float) -> list[str]:
    if not ups:
        return [f"no {UP_SELECTOR} series: the gateway's scrape targets are not discovered, so nothing is covered"]
    out = []
    for u in sorted(ups, key=lambda u: (u.pod, u.instance)):
        down = [t for t, v in u.samples if start <= t <= end and v == 0]
        if down:
            out.append(f"up == 0 on pod {u.pod} ({len(down)} scrape(s) from {iso(down[0])} to {iso(down[-1])}): that pod was blind")
    return out


def parse_vector(body: dict) -> dict[str, list[float]]:
    """An instant read of the gauge, as {pod: [value per series]}."""
    if body.get("status") != "success" or body.get("data", {}).get("resultType") != "vector":
        raise GateError(f"prometheus answered {body.get('status')!r} to the gate-time read: {body.get('error', 'not a vector')}")
    out: dict[str, list[float]] = {}
    for row in body["data"]["result"]:
        pod = row["metric"].get("pod")
        if not pod:
            raise GateError(f"a gate-time {LOADED} series without a pod: {row['metric']}")
        value = float(row["value"][1])
        if math.isnan(value):
            raise GateError(f"{LOADED} {pod}: NaN at gate time")
        out.setdefault(pod, []).append(value)
    return out


def registry_loaded_now(transport: "Transport", now: float) -> tuple[list[str], str]:
    """Leg 8: `(failures, summary)`. Every live gateway replica reads 1 now, and
    the live replicas are exactly as many as the Deployment wants."""
    where = f"Deployment {GATEWAY_NAMESPACE}/{GATEWAY_DEPLOYMENT}"
    deployment = transport.kube_get(f"/apis/apps/v1/namespaces/{GATEWAY_NAMESPACE}/deployments/{GATEWAY_DEPLOYMENT}")
    spec = deployment.get("spec") or {}
    replicas = spec.get("replicas")
    if not isinstance(replicas, int) or isinstance(replicas, bool) or replicas < 1:
        raise GateError(f"{where} spec.replicas is {replicas!r}: the gate needs an integer >= 1 to compare against")
    selector = spec.get("selector") or {}
    labels = selector.get("matchLabels") or {}
    if not labels or selector.get("matchExpressions"):
        raise GateError(f"{where} selector {selector!r}: only a non-empty matchLabels selector is read exactly")
    query = urllib.parse.urlencode({"labelSelector": ",".join(f"{k}={v}" for k, v in sorted(labels.items()))})
    pods = transport.kube_get(f"/api/v1/namespaces/{GATEWAY_NAMESPACE}/pods?{query}").get("items")
    if not isinstance(pods, list):
        raise GateError(f"kubernetes: the pod list for {where} carries no items")
    live = sorted(
        p["metadata"]["name"] for p in pods if (p.get("status") or {}).get("phase") not in TERMINAL_POD_PHASES
    )
    loaded = parse_vector(transport.prom_query({"query": LOADED_NOW_QUERY, "time": f"{now:.3f}"}))
    failures = []
    if len(live) != replicas:
        failures.append(f"{where} wants {replicas} replica(s), {len(live)} gateway pod(s) are live: {live}")
    for pod in live:
        values = loaded.get(pod)
        if not values:
            failures.append(
                f"pod {pod} has no {LOADED} series at gate time: unscraped or never published, so not loaded"
            )
        elif any(v != 1 for v in values):
            failures.append(f"pod {pod}: {LOADED} is {min(values):g}, not 1: its project registry has not loaded")
    for pod in sorted(loaded.keys() - set(live)):
        failures.append(f"pod {pod} reports {LOADED} but is not a live gateway pod of {where}")
    ok = sum(1 for pod in live if loaded.get(pod) and all(v == 1 for v in loaded[pod]))
    return failures, f"registry loaded on {ok}/{replicas} gateway replica(s) at {iso(now)}"


# --- the probe's runs -------------------------------------------------------------


def job_overlaps(job: dict, start: float, end: float) -> bool:
    if job.get("conclusion") == "skipped":
        return False
    started = parse_time(job.get("started_at"))
    if started is None or started > end:
        return False
    completed = parse_time(job.get("completed_at"))
    return completed is None or completed >= start


def probe_overlaps(transport: "Transport", start: float, end: float) -> list[str]:
    since = iso(start - PROBE_JOB_TIMEOUT - 600)
    hits, page = [], 1
    while True:
        q = urllib.parse.urlencode({"per_page": 100, "page": page, "created": f">={since}"})
        body = transport.github_get(f"/repos/{PROBE_REPO}/actions/workflows/{PROBE_WORKFLOW}/runs?{q}")
        runs = body.get("workflow_runs")
        if runs is None:
            raise GateError(f"github: no workflow_runs in the {PROBE_WORKFLOW} listing")
        for run in runs:
            updated = parse_time(run.get("updated_at"))
            if run.get("status") == "completed" and updated is not None and updated < start:
                continue
            jobs = transport.github_get(f"/repos/{PROBE_REPO}/actions/runs/{run['id']}/jobs?per_page=100")
            for job in jobs.get("jobs", []):
                if job_overlaps(job, start, end):
                    hits.append(
                        f"project-probe run {run['id']} job {job.get('name')!r} "
                        f"{job.get('started_at')} .. {job.get('completed_at') or 'running'} ({job.get('conclusion')})"
                    )
        if len(runs) < 100:
            return hits
        page += 1


def on_default_branch(transport: "Transport", sha: object, branch: str) -> bool:
    """Whether `sha` is the default branch's head or an ancestor of it.

    `head_branch` is not enough: a TAG run reports its bare tag name there
    (argocd run 39155132593, head_branch=v0.25.27), so a dispatch on a tag
    named `main` reads head_branch "main" while running the tag's copy of the
    workflow. `compare/{branch}...{sha}` reads `behind` or `identical` exactly
    when `sha` is on the branch, and 404s for a sha it does not know.

    THE SAME TEST AS `settled_gate.reachable`, restricted to the one branch, and
    written here rather than imported: settled_gate imports PyYAML at module
    level, and `preflip.yaml` installs nothing, so the import would break the
    job while every test (the hook installs PyYAML) stayed green. Never
    `commits/{sha}`: GitHub serves the whole fork network's commits there.
    """
    if not isinstance(sha, str) or not FULL_SHA.fullmatch(sha):
        return False
    try:
        status = transport.github_get(
            f"/repos/{PROBE_REPO}/compare/{urllib.parse.quote(branch, safe='/')}...{sha}"
        ).get("status")
    except GitHubNotFound:
        return False
    return status in {"behind", "identical"}


def leg_c(transport: "Transport", start: float, slack: float, max_age: float) -> tuple[bool, str]:
    """Leg (c): the newest finished `project-probe leg-c` run before the window, and its verdict."""
    since = iso(start - max_age - PROBE_JOB_TIMEOUT - 600)
    finished: list[tuple[float, int, str]] = []
    # THE TITLE ALONE IS SPOOFABLE. A dispatch can name any branch, and that
    # branch's copy of the workflow can set the same `run-name` and exit 0.
    # So only a `workflow_dispatch` of this workflow's path, on this
    # repository's default branch, with this repository as head, is evidence:
    # `settled_gate.find_verdict`'s filters for a verdict.
    own = transport.github_get(f"/repos/{PROBE_REPO}")
    if "id" not in own or not own.get("default_branch"):
        raise GateError(f"github: /repos/{PROBE_REPO} carries no id or default_branch")
    page = 1
    while True:
        q = urllib.parse.urlencode({"per_page": 100, "page": page, "created": f">={since}"})
        body = transport.github_get(f"/repos/{PROBE_REPO}/actions/workflows/{PROBE_WORKFLOW}/runs?{q}")
        runs = body.get("workflow_runs")
        if runs is None:
            raise GateError(f"github: no workflow_runs in the {PROBE_WORKFLOW} listing")
        for run in runs:
            if (
                run.get("display_title") != LEG_C_TITLE
                or run.get("status") != "completed"
                or run.get("event") != "workflow_dispatch"
                or run.get("head_branch") != own["default_branch"]
                or run.get("path") != PROBE_PATH
                or (run.get("repository") or {}).get("id") != own["id"]
                or (run.get("head_repository") or {}).get("id") != own["id"]
                or not on_default_branch(transport, run.get("head_sha"), own["default_branch"])
            ):
                continue
            jobs = transport.github_get(f"/repos/{PROBE_REPO}/actions/runs/{run['id']}/jobs?per_page=100")
            # The PROBE job by name: another job in the workflow that ends green
            # later must not stand in for a red probe job.
            for job in jobs.get("jobs", []):
                if job.get("name") != PROBE_JOB_NAME:
                    continue
                completed = parse_time(job.get("completed_at"))
                if completed is not None and completed <= start - slack:
                    finished.append((completed, run["id"], str(job.get("conclusion"))))
        if len(runs) < 100:
            break
        page += 1
    if not finished:
        return False, (
            f"no project-probe leg-c run finished before {iso(start - slack)}: dispatch project-probe.yaml "
            "with leg_c: true while PROJECT_PROBE_ENABLED is true, then switch the probe off"
        )
    completed, run_id, conclusion = max(finished)
    where = f"project-probe run {run_id} (job ended {iso(completed)}, max age {max_age:.0f}s)"
    if completed < start - max_age:
        return False, f"leg (c): the newest leg-c run, {where}, is older than {max_age:.0f}s before the start"
    if conclusion != "success":
        return False, f"leg (c): the newest leg-c run, {where}, concluded {conclusion}"
    return True, f"leg (c): {where} concluded success"


# --- the network ------------------------------------------------------------------


class Transport(Protocol):
    def prom_query(self, params: dict) -> dict: ...
    def github_get(self, path: str) -> dict: ...
    def kube_get(self, path: str) -> dict: ...


class Network:
    def __init__(self, prometheus_url: str, github_token: str, kube_url: str = "https://kubernetes.default.svc"):
        self.prometheus_url = prometheus_url
        self.github_token = github_token
        self.kube_url = kube_url

    def kube_get(self, path: str) -> dict:
        """One GET to the API server with the runner pod's own ServiceAccount token."""
        try:
            with open(f"{SERVICE_ACCOUNT_DIR}/token") as f:
                token = f.read().strip()
            context = ssl.create_default_context(cafile=f"{SERVICE_ACCOUNT_DIR}/ca.crt")
        except (OSError, ssl.SSLError) as e:
            raise GateError(
                f"no in-cluster ServiceAccount token or CA under {SERVICE_ACCOUNT_DIR} ({type(e).__name__}): "
                "is the runner pod's serviceAccountName post-merge-verifier?"
            ) from None
        if not token:
            raise GateError(f"the ServiceAccount token under {SERVICE_ACCOUNT_DIR} is empty")
        request = urllib.request.Request(f"{self.kube_url}{path}", headers={"Accept": "application/json"})
        request.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(request, timeout=30, context=context) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            raise GateError(f"kubernetes {path.split('?')[0]}: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise GateError(f"kubernetes {path.split('?')[0]}: {e}") from e

    def prom_query(self, params: dict) -> dict:
        url = f"{self.prometheus_url}/api/v1/query?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=60) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            # Prometheus answers 4xx/5xx with a JSON error body; keep its message.
            try:
                return json.load(e)
            except ValueError:
                raise GateError(f"prometheus at {self.prometheus_url}: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise GateError(f"prometheus unreachable at {self.prometheus_url}: {e}") from e

    def github_get(self, path: str) -> dict:
        request = urllib.request.Request(f"https://api.github.com{path}", headers={"Accept": "application/vnd.github+json"})
        request.add_header("Authorization", f"Bearer {self.github_token}")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise GitHubNotFound(f"github {path.split('?')[0]}: HTTP 404") from e
            raise GateError(f"github {path.split('?')[0]}: HTTP {e.code}") from e
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise GateError(f"github {path.split('?')[0]}: {e}") from e


# --- the run ----------------------------------------------------------------------


def max_age(value: str) -> float:
    """`--leg-c-max-age`: finite, above 0, at most 7 days. `inf` would accept a
    leg-c run of any age and `nan` compares false to everything."""
    try:
        seconds = float(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{value!r} is not a number of seconds") from None
    if not math.isfinite(seconds) or not 0 < seconds <= LEG_C_MAX_AGE_CAP:
        raise argparse.ArgumentTypeError(
            f"{value!r} must be finite, above 0 and at most {LEG_C_MAX_AGE_CAP:.0f} (7 days)"
        )
    return seconds


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--start", required=True, help="window start, ISO 8601 UTC (when the probe was already off)")
    p.add_argument("--end", help="window end, ISO 8601 UTC; default now")
    p.add_argument("--min-resolutions", type=int, default=1, help="the traffic leg's floor, an integer >= 1")
    p.add_argument("--lookback", type=float, default=300.0, help="seconds before start read for each baseline")
    p.add_argument("--max-gap", type=float, default=120.0, help="longest tolerated scrape gap, seconds")
    p.add_argument("--probe-slack", type=float, default=300.0, help="a probe job ending this close before start overlaps")
    p.add_argument(
        "--leg-c-max-age",
        type=max_age,
        default=86400.0,
        help="the leg-c run must end within this many seconds before start: finite, > 0, at most 7 days",
    )
    p.add_argument("--prometheus-url", default="http://prometheus-server.observability.svc:80")
    p.add_argument("--kube-url", default="https://kubernetes.default.svc", help="the API server, read with the pod's own token")
    return p.parse_args(argv)


def run(
    args: argparse.Namespace,
    env: dict[str, str],
    transport: Transport,
    now: Callable[[], float] = time.time,
    out: Callable[[str], None] = print,
) -> int:
    failures: list[str] = []

    def say(line: str) -> None:
        out(f"preflip-gate: {line}")

    try:
        start = parse_time(args.start)
        end = parse_time(args.end) if args.end else now()
    except ValueError as e:
        say(f"FAIL: bad window: {e}")
        return EXIT_FAIL
    if start is None or end is None or end <= start or end > now():
        say(f"FAIL: the window must satisfy start < end <= now; got {args.start} .. {args.end or 'now'}")
        return EXIT_FAIL
    say(f"window {iso(start)} .. {iso(end)} ({(end - start) / 3600:.2f}h)")
    if args.min_resolutions < 1:
        say(f"FAIL: --min-resolutions must be at least 1, got {args.min_resolutions}: 0 lets a window with no traffic pass")
        return EXIT_FAIL

    # Leg 1: the switch.
    enabled = env.get("PROJECT_PROBE_ENABLED")
    if enabled != "false":
        failures.append(f"PROJECT_PROBE_ENABLED is {enabled!r}, not 'false': the probe is not serialized out of the window")
    leg_c_line = "leg (c): not read"
    if not env.get("GITHUB_TOKEN"):
        failures.append("GITHUB_TOKEN is empty: the probe's run history cannot be read")
    else:
        # Leg 2: the probe's runs.
        try:
            hits = probe_overlaps(transport, start - args.probe_slack, end)
            for hit in hits:
                failures.append(f"{hit} overlaps the window (or its {args.probe_slack:.0f}s slack)")
            say(f"project-probe jobs overlapping: {len(hits)}")
        except GateError as e:
            failures.append(str(e))
        # Leg 7: leg (c).
        try:
            ok, leg_c_line = leg_c(transport, start, args.probe_slack, args.leg_c_max_age)
            say(leg_c_line)
            if not ok:
                failures.append(leg_c_line)
        except GateError as e:
            failures.append(str(e))

    refusals = Count()
    resolutions = Count()
    try:
        # Leg 3: retention.
        lowest_body = transport.prom_query({"query": LOWEST_QUERY, "time": f"{now():.3f}"})
        if lowest_body.get("status") != "success" or not lowest_body.get("data", {}).get("result"):
            raise GateError(f"prometheus answered {lowest_body.get('status')!r} to the retention read: {lowest_body.get('error', 'empty')}")
        lowest = float(lowest_body["data"]["result"][0]["value"][1])
        if lowest > start - args.lookback:
            failures.append(
                f"the window starts before the retention: oldest sample {iso(lowest)}, "
                f"window needs {iso(start - args.lookback)}"
            )

        span = int(math.ceil(end - start + args.lookback))
        series = parse_matrix(transport.prom_query({"query": f"{SELECTOR}[{span}s]", "time": f"{end:.3f}"}))
        gauges = [s for s in series if s.name == LOADED]

        # Leg 4: coverage.
        failures.extend(scrape_gaps(gauges, start, end, args.max_gap))
        ups = parse_matrix(transport.prom_query({"query": f"{UP_SELECTOR}[{span}s]", "time": f"{end:.3f}"}))
        failures.extend(up_failures(ups, start, end))
        restarts = restarts_by_target(gauges)

        first_gauge: dict[str, float] = {}
        for g in gauges:
            if g.samples:
                first_gauge[g.pod] = min(first_gauge.get(g.pod, math.inf), g.samples[0][0])

        # Legs 5 and 6.
        for s in series:
            if s.name == REFUSALS:
                c = count(s, start, unknown_baseline_counts=True, restarts=restarts.get(s.target(), Restarts()))
                refusals.total += c.total
                refusals.events += c.events
            elif s.name == RESOLUTIONS:
                c = count(
                    s,
                    start,
                    unknown_baseline_counts=False,
                    born_after=first_gauge.get(s.pod),
                    restarts=restarts.get(s.target(), Restarts()),
                )
                resolutions.total += c.total
                resolutions.events += c.events
        say(f"gateway pods seen: {sorted({s.pod for s in series})}")
        say(f"refusals in window: {refusals.total:g} (all reasons)")
        for e in sorted(refusals.events):
            say(f"  refusal {e}")
        say(f"resolutions in window: {resolutions.total:g}")
        if refusals.total != 0:
            failures.append(f"{refusals.total:g} refusal(s) in the window; the condition is 0")
        if resolutions.total < args.min_resolutions:
            failures.append(
                f"{resolutions.total:g} resolution(s) in the window, below {args.min_resolutions:g}: "
                "a window with no traffic proves nothing"
            )
    except GateError as e:
        failures.append(str(e))
    except (KeyError, IndexError, TypeError, ValueError) as e:
        failures.append(f"prometheus answer not understood: {e!r}")

    # Leg 8: the registry loaded on every replica, at gate time.
    loaded_line = "registry loaded: not read"
    try:
        loaded_failures, loaded_line = registry_loaded_now(transport, now())
        say(loaded_line)
        failures.extend(loaded_failures)
    except GateError as e:
        failures.append(str(e))
    except (KeyError, IndexError, TypeError, ValueError) as e:
        failures.append(f"gate-time registry read not understood: {e!r}")

    for f in failures:
        say(f"FAIL: {f}")
    tally = f"refusals {refusals.total:g}, resolutions {resolutions.total:g}, window {iso(start)} .. {iso(end)}"
    if failures:
        say(f"FAIL: {tally}")
    else:
        say(f"PASS: legs (a)+(b)+(c) passed ({tally}); {leg_c_line}; {loaded_line}")
    return EXIT_PASS if not failures else EXIT_FAIL


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ)
    lines: list[str] = []

    def out(line: str) -> None:
        print(line, flush=True)
        lines.append(line)

    code = run(args, env, Network(args.prometheus_url, env.get("GITHUB_TOKEN", ""), args.kube_url), out=out)
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## preflip-gate\n\n```\n" + "\n".join(lines) + "\n```\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
