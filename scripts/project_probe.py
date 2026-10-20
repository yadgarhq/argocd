#!/usr/bin/env python3
"""The 785 refusal-counter probe: front-door coverage of gateway -> project -> project-db.

ADR-0841 keeps `gateway.projectValidation.mode` at `counting` and covers the
project path with this probe instead. The routine run sends one authenticated,
read-only MCP call carrying a project claim nobody registered, then checks that
the matching `yadgar_gateway_project_refusal_total{reason}` rose in Prometheus
(`--leg-c` sends one call per reason class; see below). The plan is
yadgarhq/docs `plans/settled-state-smoke-gate.md`, stage 6. It runs on the
argocd-verify runner (ADR-0840), from `project-probe.yaml` there.

    project_probe.py --edge-ca ROOT_PEM [--trial unregistered-personal | --trial no-token | --leg-c]

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
     `X-Yadgar-Project: probe-unregistered/never-registered`. (The personal
     trial and leg (c) first read the account's own segment; see OWN SEGMENT.)
     The answer must be the expected one for either mode (THE ANSWER below).
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

THE ANSWER, in every mode that sends a token (`answer_ok`). Under `counting`
the call is SERVED (2xx, no JSON-RPC error). Under `enforcing` (argocd#129) the
gateway refuses the claim instead: a 4xx JSON-RPC error whose `data.reason` is
the class the claim earns (`PROJECT_UNRESOLVABLE` here, the trial's
`PROJECT_UNREGISTERED_PERSONAL`, each leg (c) arm's own). Either passes, and
either still needs the counter to rise. A refusal with any other reason, or
none, and any 5xx are FAIL.

THE VERDICT:
  PASS          the answer is as expected and `PROJECT_UNRESOLVABLE`
                rose by at least 1.                                   exit 0
  INCONCLUSIVE  an estate `smoke` job overlapped, or no rise and a
                reset or vanished pod could have hidden it.           exit 0
  FAIL          no rise; a 401, a 5xx or any other non-2xx that is not
                the expected refusal; a JSON-RPC error that is not the
                expected refusal; Prometheus or the GitHub API
                unreachable; no pod with `registry_loaded == 1`; a pod
                whose value is unknown.                               exit 1

THE TRIALS (stage 6 acceptance 2 and 3):
  --trial unregistered-personal sends `local/<own>/probe-never-registered`,
      where `<own>` is the probe account's own segment (see OWN SEGMENT below).
      It classifies as `PROJECT_UNREGISTERED_PERSONAL`. The probe's assertion
      on `PROJECT_UNRESOLVABLE` must then fail. The trial is VALID only when
      `PROJECT_UNREGISTERED_PERSONAL` rose and neither `PROJECT_UNRESOLVABLE`
      nor `PROJECT_NOT_MEMBER` did: exit 3 (red, as designed). Anything else
      is exit 2 (the trial proved nothing). A trial where neither series moved
      proves nothing. A whoami that fails is exit 1 and sends no call. An
      answer that is neither served nor the PERSONAL refusal is exit 1.
  --trial no-token sends the call with no bearer token. The trial is VALID
      only when the call answered 401 AND every reason's rise is 0 with no
      reset, unknown or vanished pod: exit 3 (red, as designed). Any other
      status, or any counter movement, is exit 2. A read that fails is exit 1.

LEG (C), `--leg-c` (ledger 1624; yadgarhq/docs `plans/project-validation.md`
stage 5). The flip's gate needs one deliberate probe per reason class, observed
to count, before its window opens. This mode sends one call per class that a
probe can reach, in one login, then polls until every class's counter rose:

  PROJECT_UNRESOLVABLE           find_tasks   probe-unregistered/never-registered
  PROJECT_UNREGISTERED_PERSONAL  find_tasks   local/<own>/probe-never-registered
  PROJECT_UNREGISTERED_ORG       find_tasks   yadgarhq/probe-never-registered
  PROJECT_INVALID                find_tasks   probe-invalid/
  PROJECT_ARCHIVED               create_task  yadgarhq/probe-archived
  PROJECT_NOT_MEMBER             find_tasks   local/<FOREIGN_SEGMENT>/probe

  - PERSONAL needs the caller's OWN segment: gateway v0.12.16 counts any
    well-formed `local/<seg>/...` claim whose `<seg>` is not the caller's as
    `PROJECT_NOT_MEMBER`, before the registry is read (`project.rs`,
    `member::is_foreign_private`, ADR-0954). It also needs no registered
    ancestor (`local/<own>` or the claim itself). The probe cannot read
    private rows (its egress never reaches namespace `yadgar`), so a row
    there shows as PERSONAL not rising, and the failure says so.
  - NOT_MEMBER uses a fixed, canonical-shaped segment no account is minted
    with (a v7 uuid with a zero timestamp). The run refuses before sending
    anything if it equals the own segment, compared ASCII-case-insensitively
    as the gateway compares it.
  - ORG needs the root `yadgarhq` declared and the claim itself undeclared:
    the walk then lands on the anchor (`classify.rs`, ADR-0673).
  - INVALID: a trailing `/` fails the grammar before any resolution.
  - ARCHIVED needs `yadgarhq/probe-archived` to be an ARCHIVED row, which the
    seeder makes by declaring the path once and then removing it (ADR-0934).
    Until both argocd changes have synced, this arm counts ORG (never
    declared) or resolves (declared and active), and leg (c) is red.
  - ARCHIVED is the only class decided by the call (ADR-0941): only a WRITE
    counts it. The call is `create_task` WITHOUT `title`. The gateway counts
    the claim during attestation, before any tool work (`http/dispatch.rs`),
    and in `counting` mode then runs the tool, which refuses the missing
    title (`tools.rs`, `ToolError::Invalid`) before `task` is dialled. So the
    call writes nothing and is safe to repeat. A SERVED success on this arm
    means a task WAS written; the run fails and prints the task's id.
  - `PROJECT_REGISTRY_UNAVAILABLE` is not probed: it needs the registry down.

An arm's answer passes when it is the expected one for `counting` (served, or
for ARCHIVED the title refusal) or, under `enforcing`, a 4xx JSON-RPC error
whose `data.reason` is that arm's class (THE ANSWER above). Exit 0 only when every arm answered as
expected AND every class's counter rose per pod. Anything else is exit 1,
INCONCLUSIVE included (a smoke overlap, a reset or a vanished pod): leg (c)
needs a clean proof, so dispatch again. `project-probe.yaml` names these runs
`project-probe leg-c`, and `preflip_gate.py` reads the newest one.

OWN SEGMENT (ADR-0959). `--leg-c` and `--trial unregistered-personal` read
`GET /auth/whoami` with the login's bearer token right after login. The answer
is `{"account_segment": "<uuid>", "user_id": "yadgar:user:<uuid>"}` (gateway
#124). The run fails closed (exit 1, no MCP call sent) on any answer but 200, a
missing or null `account_segment`, a segment that is not a canonical v7 uuid as
gateway `project/account.rs` spells one, or a `user_id` that is not
`yadgar:user:` + that segment. It never falls back to a guessed claim. The
routine probe and `--trial no-token` do not call whoami.

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
UNREGISTERED_ORG = "PROJECT_UNREGISTERED_ORG"
INVALID = "PROJECT_INVALID"
ARCHIVED = "PROJECT_ARCHIVED"
NOT_MEMBER = "PROJECT_NOT_MEMBER"  # gateway `src/project.rs` Reason::NotMember
REASONS = (UNRESOLVABLE, PERSONAL, UNREGISTERED_ORG, INVALID, ARCHIVED, NOT_MEMBER)

# No ancestor of this claim is registered: `classify` (gateway
# `src/project/classify.rs`) returns `Unresolvable`.
CLAIM = "probe-unregistered/never-registered"
# The personal claim is `local/<own segment>/<PERSONAL_SUFFIX>`, built at run
# time from `GET /auth/whoami` (ADR-0959); see `personal_claim`. Under the
# caller's own segment with no registered ancestor it is `UnregisteredPersonal`.
PERSONAL_SUFFIX = "probe-never-registered"
# A canonical-shaped v7 segment with a zero timestamp, which no real account is
# minted with. Never the caller's own, so the claim counts NOT_MEMBER before the
# registry is read (gateway `member::is_foreign_private`). Asserted at run time.
FOREIGN_SEGMENT = "00000000-0000-7000-8000-000000000000"
NOT_MEMBER_CLAIM = f"local/{FOREIGN_SEGMENT}/probe"
USER_ID_PREFIX = "yadgar:user:"  # gateway `src/project/account.rs`
WHOAMI = "/auth/whoami"
# Leg (c)'s three further claims; see the docstring for why each reaches its
# class. `scripts/gates/test_probe_claims.py` binds the first to the declared
# registry. The last is bound by argocd#113 (the step that archives the row):
# its `ARCHIVED_ON_PURPOSE` in `test_yadgar_application.py` is imported from
# this `ARCHIVED_CLAIM`, so the declaration can never re-declare it.
ORG_CLAIM = "yadgarhq/probe-never-registered"
INVALID_CLAIM = "probe-invalid/"
ARCHIVED_CLAIM = "yadgarhq/probe-archived"

QUERY = f'{{__name__=~"{REFUSALS}|{LOADED}",namespace="yadgar",app="gateway"}}'

PROTOCOL_VERSION = "2026-07-28"  # gateway `src/mcp.rs` PROTOCOL_VERSION
TOOL = "find_tasks"
WRITE_TOOL = "create_task"
# The gateway's exact refusal for a missing title (gateway `src/tools.rs`,
# `ToolError::Invalid`). Matched whole: another failure that merely mentions a
# title is not proof that nothing was written.
TITLE_REQUIRED = "`title` is required"


@dataclasses.dataclass(frozen=True)
class Arm:
    reason: str
    claim: str
    tool: str
    arguments: dict


def is_canonical_segment(segment: object) -> bool:
    """Gateway `account.rs` `is_canonical_user_id`, less the prefix: a lower-case v7 uuid."""
    if not isinstance(segment, str) or len(segment) != 36:
        return False
    for i, c in enumerate(segment):
        if i in (8, 13, 18, 23):
            if c != "-":
                return False
        elif c not in "0123456789abcdef":
            return False
    return segment[14] == "7" and segment[19] in "89ab"


def personal_claim(own_segment: str) -> str:
    return f"local/{own_segment}/{PERSONAL_SUFFIX}"


def same_segment(a: str, b: str) -> bool:
    """Whether two `local/` segments name one account: ASCII-case-insensitive, as the gateway compares."""
    return a.lower() == b.lower()


def leg_c_arms(own_segment: str) -> tuple[Arm, ...]:
    """Leg (c)'s arms, one per probed reason. The PERSONAL claim is the caller's own."""
    if same_segment(FOREIGN_SEGMENT, own_segment):
        raise ProbeError(
            f"the NOT_MEMBER arm's segment {FOREIGN_SEGMENT} is this account's own; "
            "that claim would not count PROJECT_NOT_MEMBER"
        )
    return (
        Arm(UNRESOLVABLE, CLAIM, TOOL, {"page_size": 1}),
        Arm(PERSONAL, personal_claim(own_segment), TOOL, {"page_size": 1}),
        Arm(UNREGISTERED_ORG, ORG_CLAIM, TOOL, {"page_size": 1}),
        Arm(INVALID, INVALID_CLAIM, TOOL, {"page_size": 1}),
        # NO `title`: the one argument whose absence makes the write a no-op.
        Arm(ARCHIVED, ARCHIVED_CLAIM, WRITE_TOOL, {}),
        Arm(NOT_MEMBER, NOT_MEMBER_CLAIM, TOOL, {"page_size": 1}),
    )

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
    u, p, n = deltas[UNRESOLVABLE], deltas[PERSONAL], deltas[NOT_MEMBER]
    return p.rise >= 1 and u.rise == 0 and n.rise == 0 and not (u.resets or u.unknown or u.missing)


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
    def edge_get(self, path: str, headers: dict[str, str]) -> tuple[int, bytes]: ...


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
        return self._edge("POST", path, {"content-type": "application/json", **headers}, body)

    def edge_get(self, path: str, headers: dict[str, str]) -> tuple[int, bytes]:
        return self._edge("GET", path, headers, None)

    def _edge(self, method: str, path: str, headers: dict[str, str], body: bytes | None) -> tuple[int, bytes]:
        context = ssl.create_default_context(cafile=self.args.edge_ca)
        conn = _PinnedHTTPS(self.args.edge_address, self.args.edge_host, self.args.edge_port, context, 30)
        try:
            conn.request(method, path, body=body, headers=headers)
            response = conn.getresponse()
            return response.status, response.read()
        except (OSError, http.client.HTTPException) as e:
            raise ProbeError(f"edge {self.args.edge_address}:{self.args.edge_port} {path}: {e}") from e
        finally:
            conn.close()


def own_segment(transport: Transport, token: str) -> str:
    """The caller's own `local/` segment from `GET /auth/whoami` (ADR-0959). Fails closed.

    Raises `ProbeError` on anything but a 200 whose `account_segment` is a
    canonical v7 uuid and whose `user_id` is exactly `yadgar:user:` + it. The
    caller then sends nothing: a guessed segment would make the PERSONAL arm
    count NOT_MEMBER and prove nothing.
    """
    status, raw = transport.edge_get(WHOAMI, {"authorization": f"Bearer {token}"})
    say(f"whoami: HTTP {status}")
    if status != 200:
        raise ProbeError(f"whoami answered HTTP {status}; the own segment is unknown, so no personal claim is built")
    answer = _json(raw)
    if "account_segment" not in answer:
        raise ProbeError("whoami answered 200 without `account_segment`; no personal claim is built")
    segment = answer["account_segment"]
    if segment is None:
        raise ProbeError("whoami answered a null `account_segment` (a non-canonical user_id); no personal claim is built")
    if not is_canonical_segment(segment):
        raise ProbeError(f"whoami answered a malformed `account_segment` {str(segment)[:64]!r}; no personal claim is built")
    if answer.get("user_id") != USER_ID_PREFIX + segment:
        raise ProbeError("whoami's `user_id` is not `yadgar:user:` + its `account_segment`; no personal claim is built")
    say(f"whoami: own segment {segment}")
    return segment


def mcp_call(token: str | None, claim: str, tool: str = TOOL, arguments: dict | None = None) -> tuple[dict[str, str], bytes]:
    # `mcp-name` and the body's `name` are ONE value: the gateway cross-checks
    # the mirror header against the body (`http/gate.rs`).
    headers = {
        "mcp-protocol-version": PROTOCOL_VERSION,
        "mcp-method": "tools/call",
        "mcp-name": tool,
        "x-yadgar-project": claim,
    }
    if token is not None:
        headers["authorization"] = f"Bearer {token}"
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": tool,
            "arguments": {"page_size": 1} if arguments is None else arguments,
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
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--trial", choices=["unregistered-personal", "no-token"])
    mode.add_argument("--leg-c", action="store_true", help="one call per reason class (leg (c), dispatch only)")
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
    if args.leg_c:
        return run_leg_c(args, transport, env, now, sleep)
    # The routine probe and the trials judge their one answer as leg (c) judges
    # an arm (`answer_ok`): served under `counting`, or under `enforcing` a 4xx
    # refusal naming this claim's class. The counter must rise either way.
    arm = Arm(UNRESOLVABLE, CLAIM, TOOL, {"page_size": 1})
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
            if args.trial == "unregistered-personal":
                arm = Arm(PERSONAL, personal_claim(own_segment(transport, token)), TOOL, {"page_size": 1})

        headers, body = mcp_call(token, arm.claim, arm.tool, arm.arguments)
        status, raw = transport.edge_post("/", headers, body)
        call_ok, detail = answer_ok(arm, status, _json(raw))
        say(f"call: tools/call {arm.tool} claim={arm.claim} -> {detail}")

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
            say(f"FAIL: the call answered {detail}; moved: {moved(deltas)}")
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
            say(
                f"trial: VALID — the assertion is red as designed; {PERSONAL} moved and neither "
                f"{UNRESOLVABLE} nor {NOT_MEMBER} did"
            )
            return EXIT_TRIAL_RED
        say(
            f"trial: INVALID — needs {PERSONAL} to rise and neither {UNRESOLVABLE} nor {NOT_MEMBER}; "
            f"moved: {moved(deltas)}"
        )
        if deltas[PERSONAL].rise < 1:
            say(f"trial: a registered row at local/<own> or {arm.claim} would resolve the claim and count nothing")
        return EXIT_TRIAL_INVALID
    return EXIT_FAIL if verdict.kind == FAIL else EXIT_PASS


def answer_ok(arm: Arm, status: int, answer: dict) -> tuple[bool, str]:
    """Whether one probe call answered as its arm expects, and what it said.

    Expected: served under `counting` (for ARCHIVED, the title refusal), or
    under `enforcing` a JSON-RPC error whose `data.reason` is the arm's class
    on a 4xx. The gateway refuses a caller's claim with 400, or 403 for
    NOT_MEMBER (`http/answer.rs` `project_answer`); its one 5xx refusal is
    PROJECT_REGISTRY_UNAVAILABLE, which no arm probes. A 5xx is never the
    expected answer, whatever reason it carries.

    Never echoes a served payload except the ARCHIVED arm's written task id,
    which is what someone needs to find and remove the row.
    """
    error = answer.get("error")
    if isinstance(error, dict):
        reason = (error.get("data") or {}).get("reason") if isinstance(error.get("data"), dict) else None
        if reason == arm.reason and 400 <= status < 500:
            return True, f"HTTP {status} refused {reason} (enforcing)"
        return False, (
            f"HTTP {status} error {error.get('code')} reason {reason!r}, "
            f"expected a 4xx refusing {arm.reason} or a served call"
        )
    if not 200 <= status < 300:
        return False, f"HTTP {status}"
    result = answer.get("result") if isinstance(answer.get("result"), dict) else {}
    if arm.tool != WRITE_TOOL:
        return True, f"HTTP {status} ({'isError' if result.get('isError') else 'served'})"
    text = " ".join(str(c.get("text", "")) for c in result.get("content", []) if isinstance(c, dict))
    if result.get("isError") and text.strip() == TITLE_REQUIRED:
        return True, f"HTTP {status} tool refused the missing title; nothing written"
    if not result.get("isError"):
        written = result.get("structuredContent") or {}
        return False, (
            f"HTTP {status} SERVED: a task was WRITTEN under {arm.claim} "
            f"(id {written.get('id')!r}, number {written.get('number')!r}); remove it"
        )
    return False, f"HTTP {status} isError, but not the missing-title refusal: {text[:200]!r}"


def run_leg_c(
    args: argparse.Namespace,
    transport: Transport,
    env: dict[str, str],
    now: Callable[[], float],
    sleep: Callable[[float], None],
) -> int:
    """Leg (c): one call per reason class; exit 0 only on a clean, complete proof."""
    password = env.get("ESTATE_PROBE_PASSWORD", "")
    if not password:
        say("FAIL: ESTATE_PROBE_PASSWORD is not set")
        return EXIT_FAIL
    failures: list[str] = []
    try:
        start = now()
        overlap = smoke_overlaps(transport, args.estate_repo, args.smoke_workflow, start, start)
        if overlap:
            say(f"FAIL: inconclusive: estate smoke run {overlap[0]} overlapped (already running); nothing sent; dispatch again")
            return EXIT_FAIL
        before = read(transport, "before")

        status, raw = transport.edge_post(
            "/auth/login", {}, json.dumps({"username": args.username, "password": password}).encode()
        )
        say(f"login: HTTP {status}")
        token = _json(raw).get("token") if status == 200 else None
        if not token:
            say(f"FAIL: login as {args.username} answered HTTP {status} with no token")
            return EXIT_FAIL
        own = own_segment(transport, token)
        arms = leg_c_arms(own)

        for arm in arms:
            headers, body = mcp_call(token, arm.claim, arm.tool, arm.arguments)
            status, raw = transport.edge_post("/", headers, body)
            ok, detail = answer_ok(arm, status, _json(raw))
            say(f"call {arm.reason}: tools/call {arm.tool} claim={arm.claim} -> {detail}")
            if not ok:
                failures.append(f"{arm.reason} arm: {detail}")

        after, deltas = before, {r: Delta() for r in REASONS}
        for attempt in range(1, math.ceil(args.poll_timeout / args.poll_interval) + 1):
            sleep(args.poll_interval)
            after = read(transport, f"poll {attempt}")
            deltas = {r: compare(before, after, r) for r in REASONS}
            if all(d.rise >= 1 for d in deltas.values()):
                break
        end = now()
        say("deltas: " + "; ".join(f"{r} +{d.rise:g} resets={d.resets} unknown={d.unknown} missing={d.missing}" for r, d in deltas.items()))

        overlap = smoke_overlaps(transport, args.estate_repo, args.smoke_workflow, start, end)
        if overlap:
            failures.append(f"inconclusive: estate smoke run {overlap[0]} overlapped; dispatch again")
    except ProbeError as e:
        say(f"FAIL: {e}")
        return EXIT_FAIL

    for reason, d in deltas.items():
        verdict = evaluate(reason, deltas)
        if verdict.kind == PASS:
            continue
        if verdict.kind == INCONCLUSIVE:
            failures.append(f"{verdict.message}; leg (c) needs a clean proof, dispatch again")
        else:
            failures.append(f"{reason} did not rise: {verdict.message}")
            if reason == PERSONAL:
                failures.append(
                    f"{PERSONAL} did not rise: a registered private row at local/{own} or "
                    f"{personal_claim(own)} would resolve the claim; the probe cannot read private rows"
                )

    for f in failures:
        say(f"FAIL: {f}")
    if failures:
        say(f"FAIL: leg (c) not proved; moved: {moved(deltas)}")
        return EXIT_FAIL
    say(f"PASS: leg (c): every probed reason class counted ({moved(deltas)})")
    return EXIT_PASS


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
