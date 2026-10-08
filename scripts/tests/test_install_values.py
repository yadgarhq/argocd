"""`install/values.yaml` sets Argo CD's sync timeout (ledger 1208).

THE DEFECT: Argo CD v3.1.8 ships `controller.sync.timeout.seconds: "0"`, which
means no timeout. A PreSync hook Job with no `activeDeadlineSeconds` (the
envoy-gateway certgen hook) whose image cannot be pulled holds its operation
Running for ever. `retry` never fires, because retry runs only after an attempt
FAILS.

WHAT THE TIMEOUT DOES, read in argo-cd v3.1.8 (becb020)
`controller/appcontroller.go`:
- `:1420-1424`: an operation still in progress `syncTimeout` after its
  `StartedAt` goes to Terminating.
- `controller/sync.go:412-413` then calls gitops-engine `Terminate()`
  (`pkg/sync/sync_context.go:1278-1313` at e48120133eec) in the same call. It
  deletes every RUNNING hook and sets the phase Failed, "Operation terminated".
- `:1476-1490`: the local `terminating` was read at `:1397`, before the timeout
  set the phase, so it is false, and a Failed operation with retries left is
  retried.
- `StartedAt` is set only when an operation starts (`:1429`); the retry path
  (`:1400-1419`) keeps it. So the clock covers the WHOLE operation, every attempt
  and every backoff wait. Once it has run out, each retry gets one pass and is
  terminated on the next one.

SO THE VALUE MUST OUTLAST THE LONGEST LEGITIMATE RETRY CHAIN ON THIS CLUSTER.
Every AUTOMATED Application root syncs is read: `applications/*.yaml` and
`projects/*.yaml` (root itself). `ARGO_FILES` also carried
`applicationsets/*.yaml`, the template of each ApplicationSet under that
directory, until ledger 1270b retired the `yadgar-modules` ApplicationSet and
the directory with it — nothing generates an ApplicationSet-keyed row today.
For each file: its backoff waits, plus its per-attempt allowance
(ATTEMPT_ALLOWANCE below) for each of its `limit + 1` attempts. The floor is the
largest of those. An automated Application with no `retry` block gets Argo's
implicit `RetryStrategy{Limit: 5}` (`:2148`) with the default backoff, 5s x2,
max 3m (`pkg/apis/application/v1alpha1/application_defaults.go:6-8`). A `limit`
of 0 or less is refused: Argo retries for ever when it is below 0 (`:1477`).

THE RETRY WAITS. Retry k of `limit` waits `min(maxDuration, duration *
factor^k)` for k = 1..limit, NOT k = 0..limit-1. `:1485` increments
`RetryCount` before the operation is stored, and the wait that gates the retry
is `NextRetryAt(FinishedAt, RetryCount)` at `:1401`, with the incremented count
(`pkg/apis/application/v1alpha1/types.go:1483-1510`). The message at `:1486`
uses the count before the increment and understates the wait. MEASURED on the
v0.3.15 VM proof run (yadgar, 15s x2): attempt 0 Failed 09:55:31, retry #1
09:56:01 (30 s); retry #1 Failed 09:56:04, retry #2 09:57:04 (60 s).

AN UNBOUNDED HOOK MUST NOT RAISE THE FLOOR WITHOUT A NUMBER. The timeout exists
to END an unbounded hang, so a hook with no bound is allowed only what it was
measured to take, times MEASURED_MARGIN, and is labelled "measured, not
bounded". A hook with a real bound (`activeDeadlineSeconds`, or a command's
own wait) is allowed that bound, labelled "bounded". A hook whose chart
documents a ceiling that does not cover every part of it (a loop bound, with
request time left open) is allowed that ceiling, labelled "documented ceiling".

A KNOWN LIMITATION: the labels and the seconds in ATTEMPT_ALLOWANCE are written
by hand from the renders and measurements cited beside them. This test checks
that every automated Application has a row and that the value clears the floor
the rows give. It does NOT check that a row is true: a hook added to a chart, or
a measurement that no longer holds, changes nothing here until a person updates
the row.

Run: python3 -m pytest scripts/tests/test_install_values.py -q
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
VALUES = REPOSITORY / "install" / "values.yaml"
ARGO_FILES = ("applications/*.yaml", "projects/*.yaml")

SYNC_TIMEOUT_KEY = "controller.sync.timeout.seconds"

# THE VALUE MUST FIT A GO INT32. The controller reads the env var with
# `env.ParseNumFromEnv(..., 0, 0, math.MaxInt32)` (v3.1.8
# `cmd/argocd-application-controller/commands/argocd_application_controller.go:278`),
# and a value above the maximum logs a warning and falls back to the default, 0
# (`util/env/env.go:36-39`): no timeout at all, which is ledger 1208 again.
MAX_INT32 = 2_147_483_647

# Argo's retry for an automated sync whose Application has no `retry` block.
IMPLICIT_RETRY = {"limit": 5, "backoff": {"duration": "5s", "factor": 2, "maxDuration": "3m"}}

# THE MARGIN ON A MEASURED, UNBOUNDED DURATION.
MEASURED_MARGIN = 3

# ─── yadgar: chart 0.13.13, which embeds `platform` 0.1.36 ────────────────────
#
# WRITTEN FORWARD-LOOKING (ledger 1224, ADR-0830), NOW THE PIN. The numbers
# below were hand-typed from `yadgarhq/platform#24` @2d86082
# (`chart/templates/_preflight.tpl`, `preflight.yaml`,
# `envoy-gateway-probe.yaml`, `bootstrap-secrets.yaml`) before #24 merged.
# #24 merged as `platform` cf2dd99 and reached this cluster's pin with parent
# 0.3.38 (ledger 1266). Measured 2026-10-03 on that render at
# `applications/yadgar.yaml`'s `valuesObject`: `activeDeadlineSeconds` is
# preflight 1260, envoy-gateway-probe 1200, bootstrap-secrets 450 and
# admin-bootstrap-token 350; `terminationGracePeriodSeconds` is preflight 60
# and envoy-gateway-probe 45. Each equals the arithmetic below. Nothing
# re-reads them. Re-measured 2026-10-08 at parent 0.13.13 (`platform` 0.1.36):
# the same six numbers, and both `backoffLimit`s (bootstrap 4, the probes 0).
#
# Rendered with `applications/yadgar.yaml`'s `valuesObject` (`iamKeys.create:
# false` in this org), every attempt runs four hook Jobs from the platform
# subchart.
#
#   preflight (PreSync, hook-weight -7). #24 bounds the Job itself:
#     `activeDeadlineSeconds` = bounded loops x `timeoutSeconds` (120) + a 300 s
#     margin. This cluster runs all four operators: cert-manager (4 loops),
#     keda (3), mariadb-operator (0), prometheus (1) = 8 loops, 1260 s.
#     `terminationGracePeriodSeconds` lets the TERM trap finish the request in
#     flight (35 s) and one 5 s cleanup DELETE per object the probes left
#     (cert-manager 3, keda 2 = 5 objects): 35 + 5*5 = 60 s. INFERRED: a Job
#     reads Failed only after its pod terminates (Kubernetes >= 1.31; kind is
#     1.36), so the grace period counts toward the attempt.
PREFLIGHT_DEADLINE = 8 * 120 + 300
PREFLIGHT_GRACE = 35 + 5 * 5
PREFLIGHT_SECONDS = PREFLIGHT_DEADLINE + PREFLIGHT_GRACE
#   envoy-gateway-probe (PostSync, hook-weight -7). Same shape: `activeDeadlineSeconds`
#     = loops (3) x `timeoutSeconds` (300) + 300 s margin = 1200 s.
#     `terminationGracePeriodSeconds` = 35 s request-in-flight + 2 objects
#     (Gateway, EnvoyProxy) x 5 s cleanup = 45 s.
PROBE_DEADLINE = 3 * 300 + 300
PROBE_GRACE = 35 + 2 * 5
PROBE_SECONDS = PROBE_DEADLINE + PROBE_GRACE
#   bootstrap-secrets and admin-bootstrap-token (PreSync, both hook-weight -5,
#     run IN PARALLEL). #24 gives each its own `activeDeadlineSeconds`: the Job
#     controller's own pod backoff (10 + 20 + 40 + 80 = 150 s, `backoffLimit: 4`)
#     plus 5 attempts of (a 30 s scheduling/start allowance + its requests x a
#     10 s `--max-time` each). bootstrap-secrets makes 3 requests with
#     `iamKeys.create: false` (valkey-password, nats-auth, nats-auth-gateway),
#     so its attempt is 30 + 3*10 = 60 s and its deadline 150 + 5*60 = 450 s;
#     admin-bootstrap-token makes 1 and is smaller. The two Jobs share the
#     larger deadline. Neither sets `terminationGracePeriodSeconds`, so
#     Kubernetes' pod default, 30 s, applies.
BOOTSTRAP_DEADLINE = (10 + 20 + 40 + 80) + 5 * (30 + 3 * 10)
BOOTSTRAP_GRACE = 30
BOOTSTRAP_JOB_SECONDS = BOOTSTRAP_DEADLINE + BOOTSTRAP_GRACE
#   SYNC-PHASE HEALTH. yadgar's sync is multi-step (it has Pre- and PostSync
#     hooks; gitops-engine `pkg/sync/sync_tasks.go:274`), so each attempt also
#     waits for its Sync-phase resources to be Healthy before PostSync runs
#     (`pkg/sync/sync_context.go:494-501`, at e48120133eec). Nothing bounds that wait: no
#     Deployment sets `progressDeadlineSeconds` (Kubernetes' default is 600 s),
#     and the nats StatefulSet and the MariaDBs have no deadline at all.
#     MEASURED, NOT BOUNDED: the v0.3.15 VM run's attempt 0 finished its
#     preflight at 09:53:55 and failed at 09:55:31 still "waiting for healthy
#     state of apps/Deployment/iam-db", so at least 90 s. #24 does not touch this.
SYNC_HEALTH_SECONDS = 90 * MEASURED_MARGIN
YADGAR_ATTEMPT_SECONDS = PREFLIGHT_SECONDS + PROBE_SECONDS + BOOTSTRAP_JOB_SECONDS + SYNC_HEALTH_SECONDS

# PER-ATTEMPT ALLOWANCE, one row per automated Application, by name: (seconds,
# label). ApplicationSets are keyed `applicationset/<name>`.
#
# MEASURED on kind-yadgar 2026-10-01, read-only. The hook Jobs of envoy-gateway
# and cert-manager are deleted after they succeed, so no Job start/completion is
# left to read. Each operation's `status.history` deployStartedAt -> deployedAt
# holds the hook with the rest of the sync, so it is an UPPER bound on the hook.
# The largest of each Application's entries; the 2026-09-05 rows are the first
# install, with cold image pulls. History has second resolution, so 0 s counts
# as 1 s.
ATTEMPT_ALLOWANCE = {
    "yadgar": (
        YADGAR_ATTEMPT_SECONDS,
        "bounded: activeDeadlineSeconds + grace, platform#24 (forward-looking): "
        "preflight 1260+60, probe 1200+45, bootstrap deadline 450+30 (iamKeys off "
        "in this org); "
        "measured, not bounded: Sync-phase health, 90 s",
    ),
    # The CA preflight Job's `activeDeadlineSeconds: 300` (`manifests/tls/ca-preflight.yaml`).
    "tls": (300, "bounded: preflight activeDeadlineSeconds"),
    "envoy-gateway": (38 * MEASURED_MARGIN, "measured, not bounded: certgen, sync 38 s"),
    # startupapicheck runs `check api --wait=1m` with `backoffLimit: 4` and no
    # deadline (platform 0.1.21 render): 5 attempts of 60 s plus the pod backoff
    # 10 + 20 + 40 + 80 s. The command's own wait bounds it; request time aside.
    "cert-manager": (5 * 60 + 150, "bounded: startupapicheck --wait=1m x5 + pod backoff"),
    "keda": (2 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 2 s"),
    "mariadb-operator": (1 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 1 s"),
    "prometheus": (1 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 1 s"),
    # gha-runner-scale-set-controller 0.14.2 templates no Job.
    "arc": (2 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 2 s"),
    # `manifests/estate-front` is a Service and a NetworkPolicy.
    "estate-front": (11 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 11 s"),
    # gha-runner-scale-set 0.14.2 templates no Job.
    "estate-front-runner": (1 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 1 s"),
    # `verifier/manifests`: a Namespace, a ServiceAccount, a ClusterRole, a binding
    # and (ledger 785) an egress NetworkPolicy. The 0 s was measured before the policy.
    "post-merge-verifier": (1 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 0 s"),
    # A directory of Application manifests; no hook.
    "root": (12 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 12 s"),
    # The `applicationset/yadgar-modules` row that used to sit here (it
    # generated no Application: all 7 `yadgar-deployable` repositories were in
    # its NotIn list, 2026-10-01) is gone — ledger 1270b retired the
    # ApplicationSet and `applicationsets/` with it.
}

GO_UNIT_SECONDS = {"h": 3600.0, "m": 60.0, "s": 1.0}
GO_DURATION = re.compile(r"(\d+)([hms])")


def go_seconds(value: object) -> float:
    """Argo's `parseStringToDuration`: a bare integer is seconds, else a Go duration. PURE."""
    text = str(value)
    if re.fullmatch(r"\d+", text):
        return float(text)
    parts = GO_DURATION.findall(text)
    assert parts and "".join(f"{n}{u}" for n, u in parts) == text, f"not a Go duration: {text!r}"
    return sum(float(number) * GO_UNIT_SECONDS[unit] for number, unit in parts)


def retry_waits(retry: dict) -> list[float]:
    """The wait before each retry, as Argo v3.1.8 applies it: k = 1..limit. PURE."""
    backoff = retry["backoff"]
    duration, factor = go_seconds(backoff["duration"]), int(backoff["factor"])
    ceiling = go_seconds(backoff["maxDuration"])
    waits = []
    for k in range(1, int(retry["limit"]) + 1):
        wait = duration * factor**k
        waits.append(min(ceiling, wait) if ceiling > 0 else wait)
    return waits


def chain_seconds(retry: dict, allowance: float) -> float:
    """The longest legitimate operation under one retry block. PURE."""
    return sum(retry_waits(retry)) + (int(retry["limit"]) + 1) * allowance


def automated_applications(root: Path = REPOSITORY) -> dict[str, dict | None]:
    """Every automated Application root syncs, by name, with its `retry` (None if absent)."""
    found: dict[str, dict | None] = {}
    for pattern in ARGO_FILES:
        for path in sorted(root.glob(pattern)):
            for document in yaml.safe_load_all(path.read_text()):
                if not isinstance(document, dict):
                    continue
                kind = document.get("kind")
                if kind == "Application":
                    name, spec = document["metadata"]["name"], document.get("spec") or {}
                elif kind == "ApplicationSet":
                    name = f"applicationset/{document['metadata']['name']}"
                    spec = ((document.get("spec") or {}).get("template") or {}).get("spec") or {}
                else:
                    continue
                policy = spec.get("syncPolicy") or {}
                if "automated" in policy:
                    found[name] = policy.get("retry")
    return found


def effective_retry(retry: dict | None) -> dict:
    """The retry Argo runs: the block, or the implicit one. Refuses a limit of 0 or less."""
    if retry is None:
        return IMPLICIT_RETRY
    limit = retry.get("limit")
    assert isinstance(limit, int) and not isinstance(limit, bool) and limit >= 1, (
        f"retry.limit {limit!r} is not a positive count (below 0 retries for ever)"
    )
    return retry


def sync_timeout_floor(root: Path = REPOSITORY) -> float:
    applications = automated_applications(root)
    missing = sorted(set(applications) - set(ATTEMPT_ALLOWANCE))
    assert not missing, f"no ATTEMPT_ALLOWANCE row for {missing}: measure or bound it first"
    return max(
        chain_seconds(effective_retry(retry), ATTEMPT_ALLOWANCE[name][0])
        for name, retry in applications.items()
    )


def sync_timeout_failures(values: dict) -> list[str]:
    """What is wrong with the sync timeout in one values document. PURE-ish."""
    params = (values.get("configs") or {}).get("params") or {}
    if SYNC_TIMEOUT_KEY not in params:
        return [f"configs.params has no {SYNC_TIMEOUT_KEY!r}: v3.1.8 defaults to 0, no timeout"]
    raw = params[SYNC_TIMEOUT_KEY]
    # Argo reads it as an integer env var; a YAML string keeps it one in the
    # rendered ConfigMap.
    if not isinstance(raw, str) or not raw.isdigit():
        return [f"{SYNC_TIMEOUT_KEY} is {raw!r}, not a quoted whole number of seconds"]
    seconds = int(raw)
    if seconds == 0:
        return [f"{SYNC_TIMEOUT_KEY} is 0, which disables the timeout"]
    if seconds > MAX_INT32:
        return [f"{SYNC_TIMEOUT_KEY} is {seconds}, above MaxInt32: Argo falls back to 0, no timeout"]
    floor = sync_timeout_floor()
    if seconds < floor:
        return [f"{SYNC_TIMEOUT_KEY} is {seconds} s, below the {floor:.0f} s a retried sync may legitimately take"]
    return []


def values() -> dict:
    return yaml.safe_load(VALUES.read_text())


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    # No `applicationsets/` here: ledger 1270b retired it, and ARGO_FILES no
    # longer globs that directory either.
    for directory in ("applications", "projects"):
        shutil.copytree(REPOSITORY / directory, tmp_path / directory)
    return tmp_path


def test_the_sync_timeout_outlasts_every_retry_chain_on_the_cluster() -> None:
    assert sync_timeout_failures(values()) == []


def test_the_retry_waits_are_the_measured_sequence() -> None:
    # An independent table: the waits MEASURED on the v0.3.15 VM proof run were
    # 30 s and 60 s for retries 1 and 2 (15s x2); the rest follow and cap at 5m.
    operator = {"limit": 6, "backoff": {"duration": "15s", "factor": 2, "maxDuration": "5m"}}
    assert retry_waits(operator) == [30, 60, 120, 240, 300, 300]
    assert sum(retry_waits(operator)) == 1050
    # Argo's implicit retry for an automated sync: 5s x2, max 3m, limit 5.
    assert retry_waits(IMPLICIT_RETRY) == [10, 20, 40, 80, 160]


def test_yadgar_per_attempt_is_the_documented_bounds() -> None:
    assert (PREFLIGHT_DEADLINE, PREFLIGHT_GRACE) == (1260, 60)
    assert (PROBE_DEADLINE, PROBE_GRACE) == (1200, 45)
    assert (BOOTSTRAP_DEADLINE, BOOTSTRAP_GRACE) == (450, 30)
    assert (PREFLIGHT_SECONDS, PROBE_SECONDS, BOOTSTRAP_JOB_SECONDS, SYNC_HEALTH_SECONDS) == (1320, 1245, 480, 270)
    assert YADGAR_ATTEMPT_SECONDS == 3315


def test_the_floor_is_yadgars_chain() -> None:
    # 1050 s of backoff plus 7 attempts of 3315 s.
    assert sync_timeout_floor() == 1050 + 7 * 3315 == 24255


def test_every_automated_application_is_read() -> None:
    # The denominator: argocd is manual, so it is not here. No
    # `applicationset/yadgar-modules` row either — ledger 1270b retired it.
    assert sorted(automated_applications()) == sorted(
        [
            "arc",
            "cert-manager",
            "envoy-gateway",
            "estate-front",
            "estate-front-runner",
            "keda",
            "mariadb-operator",
            "post-merge-verifier",
            "prometheus",
            "root",
            "tls",
            "yadgar",
        ]
    )


def test_root_uses_the_implicit_retry() -> None:
    applications = automated_applications()
    assert applications["root"] is None


def test_every_allowance_is_labelled() -> None:
    for name, (seconds, label) in ATTEMPT_ALLOWANCE.items():
        assert seconds > 0, name
        assert label.startswith(("bounded: ", "measured, not bounded: ", "documented ceiling ")), (name, label)


def test_a_new_automated_application_without_a_row_reddens(copy: Path) -> None:
    (copy / "applications" / "new.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "Application",
                "metadata": {"name": "new", "namespace": "argocd"},
                "spec": {"syncPolicy": {"automated": {}}},
            }
        )
    )
    with pytest.raises(AssertionError, match=r"no ATTEMPT_ALLOWANCE row for \['new'\]"):
        sync_timeout_floor(copy)


def test_a_new_manual_application_needs_no_row(copy: Path) -> None:
    (copy / "applications" / "manual.yaml").write_text(
        yaml.safe_dump(
            {
                "apiVersion": "argoproj.io/v1alpha1",
                "kind": "Application",
                "metadata": {"name": "manual", "namespace": "argocd"},
                "spec": {"syncPolicy": {}},
            }
        )
    )
    assert sync_timeout_floor(copy) == 24255


@pytest.mark.parametrize("limit", [0, -1], ids=["zero", "forever"])
def test_a_retry_limit_below_one_reddens(copy: Path, limit: int) -> None:
    path = copy / "applications" / "keda.yaml"
    document = yaml.safe_load(path.read_text())
    document["spec"]["syncPolicy"]["retry"]["limit"] = limit
    path.write_text(yaml.safe_dump(document))
    with pytest.raises(AssertionError, match="not a positive count"):
        sync_timeout_floor(copy)


def test_a_dropped_retry_block_falls_back_to_the_implicit_one(copy: Path) -> None:
    path = copy / "applications" / "yadgar.yaml"
    document = yaml.safe_load(path.read_text())
    del document["spec"]["syncPolicy"]["retry"]
    path.write_text(yaml.safe_dump(document))
    # 310 s of implicit backoff plus 6 attempts of 3315 s.
    assert sync_timeout_floor(copy) == 310 + 6 * 3315


@pytest.mark.parametrize(
    ("value", "named"),
    [
        (None, "has no"),
        ("0", "disables"),
        ("900", "below"),
        ("4200", "below"),
        ("22200", "below"),
        ("24000", "below"),
        ("24254", "below"),
        ("2147483648", "MaxInt32"),
        ("3000000000", "MaxInt32"),
        (25200, "not a quoted"),
        ("6h", "not a quoted"),
    ],
    ids=[
        "missing",
        "zero",
        "900",
        "old-4200",
        "old-22200",
        "old-24000",
        "one-under-floor",
        "maxint32-plus-1",
        "3e9",
        "unquoted",
        "go-duration",
    ],
)
def test_a_bad_sync_timeout_reddens(value: object, named: str) -> None:
    document = yaml.safe_load(yaml.safe_dump(values()))
    params = document["configs"]["params"]
    params.pop(SYNC_TIMEOUT_KEY, None)
    if value is not None:
        params[SYNC_TIMEOUT_KEY] = value
    failures = sync_timeout_failures(document)
    assert len(failures) == 1 and named in failures[0], failures


def test_maxint32_itself_is_accepted() -> None:
    # The boundary: `ParseNumFromEnv` rejects only a value ABOVE the maximum.
    document = yaml.safe_load(yaml.safe_dump(values()))
    document["configs"]["params"][SYNC_TIMEOUT_KEY] = str(MAX_INT32)
    assert sync_timeout_failures(document) == []
