#!/usr/bin/env python3
"""The pre-flip refusal gate: may gateway project validation move from counting to enforcing?

Ledgers 1608 and 1618; yadgarhq/docs `plans/project-validation.md` stage 5 and
`plans/the-d35-seeder.md` errata E2. The operator's condition is a clean
observation window: 0 refusals. It runs on the argocd-verify runner (ADR-0840),
from `preflip.yaml` there, which executes argocd main's copy (ADR-0844).

    preflip_gate.py --start ISO [--end ISO] [--min-resolutions N]

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
  4. No scrape gap: across all gateway pods, `registry_loaded` samples cover
     [start, end] with no gap longer than `--max-gap`. An empty answer is a
     dead scrape, never zero refusals.
  5. `yadgar_gateway_project_refusal_total`, summed over EVERY reason
     (including `PROJECT_REGISTRY_UNAVAILABLE`) and every pod, rose by 0.
  6. `yadgar_gateway_project_resolution_total` rose by at least
     `--min-resolutions` (default 1): zero traffic yields zero refusals for
     free, so a silent window proves nothing.

PER SERIES, FROM RAW SAMPLES, NEVER A BARE `increase()` (ADR-0620). The gateway
registers its counters lazily, so a pod's first refusal makes the series appear
at 1, which `increase()` reads as 0. The gate reads the raw matrix over
[start - lookback, end] and walks each series (one per pod and reason):
  - the baseline is the last sample at or before `start`;
  - a decrease is a reset (a container restart under the same pod name), and
    the value after it counts in full;
  - a series with no sample at or before `start` has an unknown baseline. For
    refusals that counts the first value in full (fail closed). For
    resolutions it counts the first value only when the same pod's
    `registry_loaded` gauge was sampled BEFORE it (the gauge is published from
    boot, so the counter's absence then was a real 0); otherwise it counts
    nothing (fail closed in the other direction).

KNOWN BLIND SPOT, stated rather than hidden: a refusal in the last scrape
interval (15 s) of a pod that then died is never scraped. Leg 4 bounds how
long nobody was looking; it cannot see inside a single interval.

THE WINDOW LENGTH IS THE OPERATOR'S (plan open question 3). This gate takes it
as input. Prometheus on kind-yadgar keeps 1d (measured 2026-10-18), so a
longer window refuses at leg 3 until retention changes or the window is read
in slices.

Exit 0 PASS, 1 FAIL. Any read that fails is a FAIL. Never printed: the token.

Standard library only: the pre-commit hook that runs the tests installs pytest
and PyYAML and nothing else.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import math
import os
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
SELECTOR = f'{{__name__=~"{REFUSALS}|{RESOLUTIONS}|{LOADED}",namespace="yadgar",app="gateway"}}'

PROBE_REPO = "yadgarhq/argocd-verify"
PROBE_WORKFLOW = "project-probe.yaml"
PROBE_JOB_TIMEOUT = 20 * 60  # project-probe.yaml `timeout-minutes: 20`

EXIT_PASS, EXIT_FAIL = 0, 1


class GateError(Exception):
    """A read that could not be made, or an answer that cannot be trusted. Always a FAIL."""


def iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str | None) -> float | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


# --- the samples ----------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Series:
    name: str
    pod: str
    label: str  # `reason` for refusals, `outcome` for resolutions, "" for the gauge
    instance: str
    samples: tuple[tuple[float, float], ...]

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
        out.append(Series(name, pod, label, metric.get("instance", ""), tuple(sorted(samples))))
    return out


@dataclasses.dataclass
class Count:
    total: float = 0
    events: list[str] = dataclasses.field(default_factory=list)


def count(series: Series, start: float, unknown_baseline_counts: bool, born_after: float | None = None) -> Count:
    """The series' rise over (start, end], per the module docstring."""
    c = Count()
    before = [v for t, v in series.samples if t <= start]
    inside = [(t, v) for t, v in series.samples if t > start]
    if not inside:
        return c
    if before:
        prev = before[-1]
    else:
        t1, v1 = inside[0]
        if unknown_baseline_counts or (born_after is not None and born_after < t1):
            prev = 0.0  # born in the window (or no baseline, read as born: fail closed)
        else:
            prev = v1
            c.events.append(f"{iso(t1)} {series.key()} first sample {v1:g} has no baseline; not counted")
    for t, v in inside:
        if v >= prev:
            if v > prev:
                c.events.append(f"{iso(t)} {series.key()} {prev:g} -> {v:g}")
            c.total += v - prev
        else:
            c.events.append(f"{iso(t)} {series.key()} reset {prev:g} -> {v:g}, counted {v:g}")
            c.total += v
        prev = v
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
    return gaps


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


# --- the network ------------------------------------------------------------------


class Transport(Protocol):
    def prom_query(self, params: dict) -> dict: ...
    def github_get(self, path: str) -> dict: ...


class Network:
    def __init__(self, prometheus_url: str, github_token: str):
        self.prometheus_url = prometheus_url
        self.github_token = github_token

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
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise GateError(f"github {path.split('?')[0]}: {e}") from e


# --- the run ----------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--start", required=True, help="window start, ISO 8601 UTC (when the probe was already off)")
    p.add_argument("--end", help="window end, ISO 8601 UTC; default now")
    p.add_argument("--min-resolutions", type=float, default=1, help="the traffic leg's floor")
    p.add_argument("--lookback", type=float, default=300.0, help="seconds before start read for each baseline")
    p.add_argument("--max-gap", type=float, default=120.0, help="longest tolerated scrape gap, seconds")
    p.add_argument("--probe-slack", type=float, default=300.0, help="a probe job ending this close before start overlaps")
    p.add_argument("--prometheus-url", default="http://prometheus-server.observability.svc:80")
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

    # Leg 1: the switch.
    enabled = env.get("PROJECT_PROBE_ENABLED")
    if enabled != "false":
        failures.append(f"PROJECT_PROBE_ENABLED is {enabled!r}, not 'false': the probe is not serialized out of the window")
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

    refusals = Count()
    resolutions = Count()
    try:
        # Leg 3: retention.
        lowest_body = transport.prom_query({"query": LOWEST_QUERY, "time": f"{end:.3f}"})
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

        first_gauge: dict[str, float] = {}
        for g in gauges:
            if g.samples:
                first_gauge[g.pod] = min(first_gauge.get(g.pod, math.inf), g.samples[0][0])

        # Legs 5 and 6.
        for s in series:
            if s.name == REFUSALS:
                c = count(s, start, unknown_baseline_counts=True)
                refusals.total += c.total
                refusals.events += c.events
            elif s.name == RESOLUTIONS:
                c = count(s, start, unknown_baseline_counts=False, born_after=first_gauge.get(s.pod))
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

    for f in failures:
        say(f"FAIL: {f}")
    verdict = "PASS" if not failures else "FAIL"
    say(f"{verdict}: refusals {refusals.total:g}, resolutions {resolutions.total:g}, window {iso(start)} .. {iso(end)}")
    return EXIT_PASS if not failures else EXIT_FAIL


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ)
    lines: list[str] = []

    def out(line: str) -> None:
        print(line, flush=True)
        lines.append(line)

    code = run(args, env, Network(args.prometheus_url, env.get("GITHUB_TOKEN", "")), out=out)
    summary = env.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("## preflip-gate\n\n```\n" + "\n".join(lines) + "\n```\n")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
