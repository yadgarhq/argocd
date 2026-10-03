#!/usr/bin/env python3
"""The 785 refusal-counter probe: front-door coverage of gateway -> project -> project-db.

ADR-0841 keeps `gateway.projectValidation.mode` at `counting` and covers the
project path with this probe instead: one authenticated, read-only MCP call
carrying a project claim nobody registered, then a check that the matching
`yadgar_gateway_project_refusal_total{reason}` rose in Prometheus. The plan is
yadgarhq/docs `plans/settled-state-smoke-gate.md`, stage 6. It runs on the
argocd-verify runner (ADR-0840), from `project-probe.yaml` there.

    project_probe.py --edge-ca ROOT_PEM [--trial unregistered-personal | --trial no-token]

THE STEPS, in order:
  1. Read estate's `smoke.yaml` runs. A `smoke` job already running makes the
     run INCONCLUSIVE and nothing is sent.
  2. Read, per pod and in ONE instant query, the refusal counter and
     `yadgar_gateway_project_registry_loaded` from Prometheus directly
     (`prometheus-server.observability.svc:80`), never through the API
     server's Service proxy: the verifier role has no `services/proxy` and
     must not gain it (plan M10).
  3. Log in at the edge (`/auth/login`, user `estate-probe`, password from
     `ESTATE_PROBE_PASSWORD`) and send `tools/call find_tasks` with
     `X-Yadgar-Project: probe-unregistered/never-registered`.
  4. Poll Prometheus every 15 s for up to 120 s until the per-pod sum rises.
  5. Read estate's runs again; a `smoke` job that ran inside the window makes
     the run INCONCLUSIVE.

WHY `find_tasks`. The gateway serves five tools (`tools.rs` `SERVED`); three
write (`is_write`: create_task, edit_task, transition_task) and `read_task`
needs an id. `tools_call` (`http/dispatch.rs`) runs `attest::attest` — and so
`Validator::check`, which counts the claim — before any tool work, and in
`counting` mode the call is then SERVED. A write would land under the bogus
project. `find_tasks` with `page_size: 1` lists and creates nothing.

WHY ONE QUERY. "No series" counts as 0 only when `registry_loaded == 1` is
present for the same pod IN THE SAME READ: the gateway registers the refusal
counter lazily, so today's pods carry the gauge and no counter series at all
(measured 2026-10-03). Reading both names in one instant query makes "same
read" literal: one evaluation timestamp, one scrape state.

PER POD, NEVER A BARE `increase()` (ADR-0620). A pod present in both reads must
not decrease; a decrease is a reset, contributes nothing and is reported. A pod
new since the first read counts from 0. A pod gone by the last read is reported.

THE VERDICT:
  PASS          `PROJECT_UNRESOLVABLE` rose by at least 1.            exit 0
  INCONCLUSIVE  an estate `smoke` job overlapped, or no rise and a
                reset or vanished pod could have hidden it.           exit 0
  FAIL          no rise; a 401 or any non-2xx; a JSON-RPC error;
                Prometheus or the GitHub API unreachable; no pod with
                `registry_loaded == 1`; a pod whose value is unknown. exit 1

THE TRIALS (stage 6 acceptance 2 and 3):
  --trial unregistered-personal sends `local/probe-never-registered`, which
      classifies as `PROJECT_UNREGISTERED_PERSONAL`. The probe's assertion on
      `PROJECT_UNRESOLVABLE` must then fail. The trial is VALID only when
      `PROJECT_UNREGISTERED_PERSONAL` rose and `PROJECT_UNRESOLVABLE` did not:
      exit 3 (red, as designed). Anything else is exit 2 (the trial proved
      nothing). A trial where neither series moved proves nothing.
  --trial no-token sends the call with no bearer token. The trial is VALID
      only when the call answered 401 AND every reason's rise is 0 with no
      reset, unknown or vanished pod: exit 3 (red, as designed). Any other
      status, or any counter movement, is exit 2. A read that fails is exit 1.

NEVER PRINTED: the password and the bearer token.

Standard library only: the pre-commit hook that runs the tests installs pytest
and PyYAML and nothing else.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import http.client
import json
import math
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Protocol

REFUSALS = "yadgar_gateway_project_refusal_total"
LOADED = "yadgar_gateway_project_registry_loaded"
UNRESOLVABLE = "PROJECT_UNRESOLVABLE"
PERSONAL = "PROJECT_UNREGISTERED_PERSONAL"
REASONS = (UNRESOLVABLE, PERSONAL)

# No ancestor of either claim is registered. `classify` (gateway
# `src/project/classify.rs`) returns `Unresolvable` for the first and
# `UnregisteredPersonal` for the second, whose root is the reserved `local`.
CLAIM = "probe-unregistered/never-registered"
TRIAL_CLAIM = "local/probe-never-registered"

QUERY = f'{{__name__=~"{REFUSALS}|{LOADED}",namespace="yadgar",app="gateway"}}'

PROTOCOL_VERSION = "2026-07-28"  # gateway `src/mcp.rs` PROTOCOL_VERSION
TOOL = "find_tasks"

PASS, FAIL, INCONCLUSIVE = "PASS", "FAIL", "INCONCLUSIVE"
EXIT_PASS, EXIT_FAIL, EXIT_TRIAL_INVALID, EXIT_TRIAL_RED = 0, 1, 2, 3


class ProbeError(Exception):
    """A read or a call that could not be made. Always a FAIL."""


def say(line: str) -> None:
    print(f"project-probe: {line}", flush=True)


# --- Prometheus ---------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Reading:
    loaded: dict[str, float]
    refusals: dict[tuple[str, str], float]

    def pods(self) -> set[str]:
        return set(self.loaded) | {pod for pod, _ in self.refusals}

    def value(self, pod: str, reason: str) -> float | None:
        """The counter, 0 for no series beside a live gauge, else unknown."""
        if (pod, reason) in self.refusals:
            return self.refusals[(pod, reason)]
        if self.loaded.get(pod) == 1:
            return 0.0
        return None

    def render(self) -> str:
        rows = []
        for pod in sorted(self.pods()):
            values = " ".join(f"{r}={self.refusals.get((pod, r), '-')}" for r in REASONS)
            rows.append(f"{pod}[loaded={self.loaded.get(pod, '-')} {values}]")
        return " ".join(rows)


def parse_reading(body: dict) -> Reading:
    if body.get("status") != "success" or body.get("data", {}).get("resultType") != "vector":
        raise ProbeError(f"prometheus answered {body.get('status')!r}: {body.get('error', 'not a vector')}")
    loaded: dict[str, float] = {}
    refusals: dict[tuple[str, str], float] = {}
    for sample in body["data"]["result"]:
        metric, value = sample["metric"], float(sample["value"][1])
        pod = metric.get("pod")
        if not pod:
            continue
        if metric.get("__name__") == LOADED:
            loaded[pod] = value
        elif metric.get("__name__") == REFUSALS and metric.get("reason"):
            refusals[(pod, metric["reason"])] = value
    if not any(v == 1 for v in loaded.values()):
        raise ProbeError(
            f"no gateway pod reports {LOADED} == 1: the scrape is dead or no registry has loaded, "
            "so an absent counter cannot be read as 0"
        )
    return Reading(loaded, refusals)


@dataclasses.dataclass
class Delta:
    rise: float = 0
    resets: list[str] = dataclasses.field(default_factory=list)
    unknown: list[str] = dataclasses.field(default_factory=list)
    missing: list[str] = dataclasses.field(default_factory=list)


def compare(before: Reading, after: Reading, reason: str) -> Delta:
    delta = Delta()
    for pod in sorted(after.pods()):
        now = after.value(pod, reason)
        then = before.value(pod, reason) if pod in before.pods() else 0.0
        if now is None or then is None:
            delta.unknown.append(pod)
        elif now < then:
            delta.resets.append(pod)
        else:
            delta.rise += now - then
    delta.missing = sorted(before.pods() - after.pods())
    return delta


@dataclasses.dataclass(frozen=True)
class Verdict:
    kind: str
    message: str


def moved(deltas: dict[str, Delta]) -> str:
    rose = [f"{r} +{d.rise:g}" for r, d in deltas.items() if d.rise > 0]
    return ", ".join(rose) if rose else "no series moved"


def evaluate(expected: str, deltas: dict[str, Delta]) -> Verdict:
    d = deltas[expected]
    if d.rise >= 1:
        return Verdict(PASS, f"{expected} rose by {d.rise:g}")
    if d.unknown:
        return Verdict(FAIL, f"{expected} did not rise and pods {d.unknown} have no counter and no live gauge")
    if d.resets or d.missing:
        return Verdict(
            INCONCLUSIVE,
            f"inconclusive: {expected} did not rise, and a reset {d.resets} or a vanished pod {d.missing} "
            "could have hidden the probe's increment",
        )
    return Verdict(FAIL, f"expected {expected} to rise and it did not; moved: {moved(deltas)}")


def trial_valid(deltas: dict[str, Delta]) -> bool:
    u, p = deltas[UNRESOLVABLE], deltas[PERSONAL]
    return p.rise >= 1 and u.rise == 0 and not (u.resets or u.unknown or u.missing)


# --- estate's smoke runs ------------------------------------------------------


def parse_time(value: str | None) -> float | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def job_overlaps(job: dict, start: float, end: float) -> bool:
    """A `smoke` job (the rows, on `estate-front`) that ran inside [start, end]."""
    if job.get("name") != "smoke" or job.get("conclusion") == "skipped":
        return False
    if job.get("status") not in ("in_progress", "completed"):
        return False
    started = parse_time(job.get("started_at"))
    if started is None or started > end:
        return False
    completed = parse_time(job.get("completed_at"))
    return completed is None or completed >= start


def smoke_overlaps(transport: "Transport", repo: str, workflow: str, start: float, end: float) -> list[int]:
    runs = transport.github_get(f"/repos/{repo}/actions/workflows/{workflow}/runs?per_page=20")
    listed = runs.get("workflow_runs", [])
    say(f"estate runs read: {len(listed)} recent {workflow} runs")
    hits = []
    for run in listed:
        updated = parse_time(run.get("updated_at"))
        if run.get("status") == "completed" and updated is not None and updated < start:
            continue
        jobs = transport.github_get(f"/repos/{repo}/actions/runs/{run['id']}/jobs?per_page=100")
        if any(job_overlaps(j, start, end) for j in jobs.get("jobs", [])):
            hits.append(run["id"])
    return hits


# --- the network --------------------------------------------------------------


class Transport(Protocol):
    def prom_query(self, query: str) -> dict: ...
    def github_get(self, path: str) -> dict: ...
    def edge_post(self, path: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]: ...


class _PinnedHTTPS(http.client.HTTPSConnection):
    """HTTPS to a fixed address, with SNI and Host set to the edge's name.

    The verifier runner has no `hostAliases` entry for `gateway.yadgar.internal`,
    so the name is never resolved: the socket dials the `yadgar-edge` Service
    address and the TLS session still verifies the name against estate's root.
    """

    def __init__(self, address: str, host: str, port: int, context: ssl.SSLContext, timeout: float):
        super().__init__(host, port, context=context, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class Network:
    def __init__(self, args: argparse.Namespace, env: dict[str, str]):
        self.args = args
        self.github_token = env.get("GITHUB_TOKEN", "")

    def prom_query(self, query: str) -> dict:
        url = f"{self.args.prometheus_url}/api/v1/query?{urllib.parse.urlencode({'query': query})}"
        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return json.load(response)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ProbeError(f"prometheus unreachable at {self.args.prometheus_url}: {e}") from e

    def github_get(self, path: str) -> dict:
        request = urllib.request.Request(f"https://api.github.com{path}", headers={"Accept": "application/vnd.github+json"})
        if self.github_token:
            request.add_header("Authorization", f"Bearer {self.github_token}")
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)
        except (urllib.error.URLError, OSError, ValueError) as e:
            raise ProbeError(f"github {path}: {e}") from e

    def edge_post(self, path: str, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        context = ssl.create_default_context(cafile=self.args.edge_ca)
        conn = _PinnedHTTPS(self.args.edge_address, self.args.edge_host, self.args.edge_port, context, 30)
        try:
            conn.request("POST", path, body=body, headers={"content-type": "application/json", **headers})
            response = conn.getresponse()
            return response.status, response.read()
        except (OSError, http.client.HTTPException) as e:
            raise ProbeError(f"edge {self.args.edge_address}:{self.args.edge_port} {path}: {e}") from e
        finally:
            conn.close()


def mcp_call(token: str | None, claim: str) -> tuple[dict[str, str], bytes]:
    headers = {
        "mcp-protocol-version": PROTOCOL_VERSION,
        "mcp-method": "tools/call",
        "mcp-name": TOOL,
        "x-yadgar-project": claim,
    }
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": TOOL,
            "arguments": {"page_size": 1},
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    return headers, json.dumps(body).encode()


# --- the run ------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--edge-ca", required=True, help="estate's committed edge root (ca/root.pem)")
    p.add_argument("--edge-address", default="10.96.0.100", help="Service yadgar-edge, pinned in manifests/estate-front/edge-service.yaml")
    p.add_argument("--edge-port", type=int, default=443)
    p.add_argument("--edge-host", default="gateway.yadgar.internal")
    p.add_argument("--prometheus-url", default="http://prometheus-server.observability.svc:80")
    p.add_argument("--username", default="estate-probe")
    p.add_argument("--estate-repo", default="yadgarhq/estate")
    p.add_argument("--smoke-workflow", default="smoke.yaml")
    p.add_argument("--poll-interval", type=float, default=15.0)
    p.add_argument("--poll-timeout", type=float, default=120.0)
    p.add_argument("--trial", choices=["unregistered-personal", "no-token"])
    return p.parse_args(argv)


def read(transport: Transport, label: str) -> Reading:
    reading = parse_reading(transport.prom_query(QUERY))
    say(f"read {label}: {reading.render()}")
    return reading


def run(
    args: argparse.Namespace,
    transport: Transport,
    env: dict[str, str],
    now: Callable[[], float] = time.time,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    claim = TRIAL_CLAIM if args.trial == "unregistered-personal" else CLAIM
    send_token = args.trial != "no-token"
    password = env.get("ESTATE_PROBE_PASSWORD", "")
    if send_token and not password:
        say("FAIL: ESTATE_PROBE_PASSWORD is not set")
        return EXIT_FAIL

    try:
        start = now()
        overlap = smoke_overlaps(transport, args.estate_repo, args.smoke_workflow, start, start)
        if overlap:
            say(f"inconclusive: estate smoke run {overlap[0]} overlapped (already running); nothing sent")
            return EXIT_PASS
        before = read(transport, "before")

        token = None
        if send_token:
            status, raw = transport.edge_post(
                "/auth/login", {}, json.dumps({"username": args.username, "password": password}).encode()
            )
            say(f"login: HTTP {status}")
            token = _json(raw).get("token") if status == 200 else None
            if not token:
                say(f"FAIL: login as {args.username} answered HTTP {status} with no token")
                return EXIT_FAIL

        headers, body = mcp_call(token, claim)
        status, raw = transport.edge_post("/", headers, body)
        answer = _json(raw)
        call_ok = 200 <= status < 300 and "error" not in answer
        detail = f"error {answer['error'].get('code')}" if isinstance(answer.get("error"), dict) else (
            "isError" if answer.get("result", {}).get("isError") else "served"
        )
        say(f"call: tools/call {TOOL} claim={claim} -> HTTP {status} ({detail})")

        after, deltas = before, {r: Delta() for r in REASONS}
        for attempt in range(1, math.ceil(args.poll_timeout / args.poll_interval) + 1):
            sleep(args.poll_interval)
            after = read(transport, f"poll {attempt}")
            deltas = {r: compare(before, after, r) for r in REASONS}
            # Wait for the series the verdict asserts. A trial may stop on
            # either: it needs to see whichever one the call moved.
            watched = REASONS if args.trial else (UNRESOLVABLE,)
            if any(deltas[r].rise >= 1 for r in watched):
                break
        end = now()
        say("deltas: " + "; ".join(f"{r} +{d.rise:g} resets={d.resets} unknown={d.unknown} missing={d.missing}" for r, d in deltas.items()))

        if args.trial == "no-token":
            still = all(d.rise == 0 and not (d.resets or d.unknown or d.missing) for d in deltas.values())
            if status == 401 and still:
                say("trial: VALID — no token got HTTP 401 and no counter moved")
                return EXIT_TRIAL_RED
            say(f"trial: INVALID — needs HTTP 401 and no counter movement; got HTTP {status}, moved: {moved(deltas)}")
            return EXIT_TRIAL_INVALID

        if not call_ok:
            say(f"FAIL: the call answered HTTP {status} ({detail}); moved: {moved(deltas)}")
            return EXIT_FAIL

        overlap = smoke_overlaps(transport, args.estate_repo, args.smoke_workflow, start, end)
        if overlap:
            say(f"inconclusive: estate smoke run {overlap[0]} overlapped")
            return EXIT_PASS
    except ProbeError as e:
        say(f"FAIL: {e}")
        return EXIT_FAIL

    verdict = evaluate(UNRESOLVABLE, deltas)
    say(f"{verdict.kind}: {verdict.message}")
    if verdict.kind == INCONCLUSIVE:
        # A reset or a vanished pod: green, but visible on the run's summary.
        print(f"::warning::project-probe {verdict.message}", flush=True)
    if args.trial == "unregistered-personal":
        if trial_valid(deltas):
            say(f"trial: VALID — the assertion is red as designed; {PERSONAL} moved and {UNRESOLVABLE} did not")
            return EXIT_TRIAL_RED
        say(f"trial: INVALID — needs {PERSONAL} to rise and {UNRESOLVABLE} not to; moved: {moved(deltas)}")
        return EXIT_TRIAL_INVALID
    return EXIT_FAIL if verdict.kind == FAIL else EXIT_PASS


def _json(raw: bytes) -> dict:
    try:
        value = json.loads(raw)
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    env = dict(os.environ)
    return run(args, Network(args, env), env)


if __name__ == "__main__":
    raise SystemExit(main())
