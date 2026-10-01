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
Every AUTOMATED Application root syncs is read: `applications/*.yaml`,
`projects/*.yaml` (root itself) and the template of each ApplicationSet under
`applicationsets/`. For each: its backoff waits, plus its per-attempt allowance
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
bounded". A hook with a documented bound (a script timeout,
`activeDeadlineSeconds`) is allowed that bound, labelled "bounded".

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
ARGO_FILES = ("applications/*.yaml", "projects/*.yaml", "applicationsets/*.yaml")

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

# ─── yadgar: chart 0.3.13, which embeds `platform` 0.1.21 ─────────────────────
#
# Rendered with `applications/yadgar.yaml`'s `valuesObject`, every attempt runs
# four hook Jobs from the platform subchart, none with `activeDeadlineSeconds`.
# The bounds are the ones `yadgarhq/platform@v0.1.21 chart/values.yaml` documents:
#
#   preflight (PreSync, hook-weight -7). `timeoutSeconds: 120` (L857). "worst
#     case with every probe on is eight 120s loops plus the same 300s margin"
#     (L1035-1036); this cluster runs all four operators.
PREFLIGHT_SECONDS = 8 * 120 + 300
#   envoy-gateway-probe (PostSync, hook-weight -7). `timeoutSeconds: 300`
#     (L1043). "the script's worst case is three times this number" (L1026-1030),
#     and "the composed bound is 900s, and the documented `--timeout 25m` leaves
#     600s over it for scheduling, image pull and request time" (L1033-1035).
PROBE_SECONDS = 3 * 300 + 600
#   bootstrap-secrets and admin-bootstrap-token (PreSync, both hook-weight -5).
#     They run IN PARALLEL: same weight, and the live Jobs both started at
#     2026-10-01 08:09:39 and completed at 08:09:42 (3 s). Each is one curl with
#     no timeout and `backoffLimit: 4`. No deadline, so the bound is the Job's
#     own pod backoff (10 + 20 + 40 + 80 s) plus 5 attempts of the measured run
#     time, times MEASURED_MARGIN. The two are equal; the larger is taken.
BOOTSTRAP_JOB_SECONDS = (10 + 20 + 40 + 80) + 5 * 3 * MEASURED_MARGIN
YADGAR_ATTEMPT_SECONDS = PREFLIGHT_SECONDS + PROBE_SECONDS + max(BOOTSTRAP_JOB_SECONDS, BOOTSTRAP_JOB_SECONDS)

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
        "bounded: preflight 8x120+300, probe 3x300+600 (platform 0.1.21); "
        "measured+job-backoff, not bounded: the two parallel bootstrap Jobs",
    ),
    # The CA preflight Job's `activeDeadlineSeconds: 300` (`manifests/tls/ca-preflight.yaml`).
    "tls": (300, "bounded: preflight activeDeadlineSeconds"),
    "envoy-gateway": (38 * MEASURED_MARGIN, "measured, not bounded: certgen, sync 38 s"),
    "cert-manager": (44 * MEASURED_MARGIN, "measured, not bounded: startupapicheck, sync 44 s"),
    "keda": (2 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 2 s"),
    "mariadb-operator": (1 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 1 s"),
    "prometheus": (1 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 1 s"),
    # gha-runner-scale-set-controller 0.14.2 templates no Job.
    "arc": (2 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 2 s"),
    # `manifests/estate-front` is a Service and a NetworkPolicy.
    "estate-front": (11 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 11 s"),
    # gha-runner-scale-set 0.14.2 templates no Job.
    "estate-front-runner": (1 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 1 s"),
    # `verifier/manifests`: a Namespace, a ServiceAccount, a ClusterRole and a binding.
    "post-merge-verifier": (1 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 0 s"),
    # A directory of Application manifests; no hook.
    "root": (12 * MEASURED_MARGIN, "measured, not bounded: no hook, sync 12 s"),
    # Generates NO Application today: all 7 `yadgar-deployable` repositories are
    # in its NotIn list (2026-10-01), so nothing can be measured. The module
    # charts carry no hook (yadgar 0.3.13 render), so this takes the largest
    # no-hook sync measured here, estate-front's 11 s.
    "applicationset/yadgar-modules": (11 * MEASURED_MARGIN, "measured, not bounded: no instance; largest no-hook sync"),
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
    for directory in ("applications", "projects", "applicationsets"):
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
    assert (PREFLIGHT_SECONDS, PROBE_SECONDS, BOOTSTRAP_JOB_SECONDS) == (1260, 1500, 195)
    assert YADGAR_ATTEMPT_SECONDS == 2955


def test_the_floor_is_yadgars_chain() -> None:
    # 1050 s of backoff plus 7 attempts of 2955 s.
    assert sync_timeout_floor() == 1050 + 7 * 2955 == 21735


def test_every_automated_application_is_read() -> None:
    # The denominator: argocd is manual, so it is not here.
    assert sorted(automated_applications()) == sorted(
        [
            "applicationset/yadgar-modules",
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


def test_root_and_the_applicationset_use_the_implicit_retry() -> None:
    applications = automated_applications()
    assert applications["root"] is None
    assert applications["applicationset/yadgar-modules"] is None


def test_every_allowance_is_labelled() -> None:
    for name, (seconds, label) in ATTEMPT_ALLOWANCE.items():
        assert seconds > 0, name
        assert label.startswith(("bounded: ", "measured, not bounded: ")), (name, label)


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
    assert sync_timeout_floor(copy) == 21735


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
    # 310 s of implicit backoff plus 6 attempts of 2955 s.
    assert sync_timeout_floor(copy) == 310 + 6 * 2955


@pytest.mark.parametrize(
    ("value", "named"),
    [
        (None, "has no"),
        ("0", "disables"),
        ("900", "below"),
        ("4200", "below"),
        ("21734", "below"),
        ("2147483648", "MaxInt32"),
        ("3000000000", "MaxInt32"),
        (22200, "not a quoted"),
        ("6h", "not a quoted"),
    ],
    ids=["missing", "zero", "900", "old-4200", "one-under-floor", "maxint32-plus-1", "3e9", "unquoted", "go-duration"],
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
