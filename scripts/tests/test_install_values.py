"""`install/values.yaml` sets Argo CD's sync timeout (ledger 1208).

THE DEFECT: Argo CD v3.1.8 ships `controller.sync.timeout.seconds: "0"`, which
means no timeout. A PreSync hook Job with no `activeDeadlineSeconds` (the
envoy-gateway certgen hook) whose image cannot be pulled holds its operation
Running for ever. `retry` never fires, because retry runs only after an attempt
FAILS.

WHAT THE TIMEOUT DOES, read in argo-cd v3.1.8 `controller/appcontroller.go`:
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
For each Application under `applications/`: its own backoff waits, plus its
per-attempt allowance (ATTEMPT_ALLOWANCE below) for each of its `limit + 1`
attempts. The floor is the largest of those. Retry blocks are read from the
files, so a retry block that grows raises the floor.

AN UNBOUNDED HOOK MUST NOT RAISE THE FLOOR. The timeout exists to END an
unbounded hang, so a hook with no bound is allowed only what it was measured to
take, times a stated margin, and is labelled "measured, not bounded". A hook
with a real bound (`activeDeadlineSeconds`, or a script timeout) is allowed
that bound.

`Application/tls` MUST BE AMONG THEM. Its `retry` used to be `limit: 60`, about
ten hours of chain, in `yadgarhq/deploy`, where this test could not read it.
M3 moves it here with the operator shape (limit 6, 15s x2, max 5m). Until M3
lands, `test_tls_is_read_from_this_repository` is red on purpose: a floor that
silently left tls out would pass while cutting off its retry.

THE RETRY ARITHMETIC is Argo's `RetryStrategy.NextRetryAt` (v3.1.8
`pkg/apis/application/v1alpha1/types.go:1483`): retry k waits
`min(maxDuration, duration * factor^k)`, k = 1..limit. The same arithmetic is
`retry_window` in `yadgarhq/chart`'s `scripts/tests/test_parent_chart.py`.

Run: python3 -m pytest scripts/tests/test_install_values.py -q
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
VALUES = REPOSITORY / "install" / "values.yaml"
APPLICATIONS = REPOSITORY / "applications"

SYNC_TIMEOUT_KEY = "controller.sync.timeout.seconds"

# THE VALUE MUST FIT A GO INT32. The controller reads the env var with
# `env.ParseNumFromEnv(..., 0, 0, math.MaxInt32)` (v3.1.8
# `cmd/argocd-application-controller/commands/argocd_application_controller.go:278`),
# and a value above the maximum logs a warning and falls back to the default, 0
# (`util/env/env.go:36-39`): no timeout at all, which is ledger 1208 again.
MAX_INT32 = 2_147_483_647

# The Application whose retry must be read from here (M3).
TLS_FILE = "tls.yaml"

# THE MARGIN ON A MEASURED, UNBOUNDED DURATION.
MEASURED_MARGIN = 3

# PER-ATTEMPT ALLOWANCE, one row per Application file: (seconds, source).
#
# MEASURED on kind-yadgar 2026-10-01, read-only. The hook Jobs of envoy-gateway
# and cert-manager are deleted after they succeed, so no Job start/completion is
# left to read. Each operation's `status.history` deployStartedAt -> deployedAt
# holds the hook, with the rest of the sync, so it is an UPPER bound on the hook.
# The largest of each Application's entries; the 2026-09-05 rows are the first
# install, with cold image pulls:
#   envoy-gateway     38 s (09-05), 10 s (10-01). PreSync Job gateway-helm-certgen.
#   cert-manager      44 s (09-05). PostSync Job cert-manager-startupapicheck.
#   keda               2 s. No hook.
#   mariadb-operator   1 s. No hook.
#   prometheus         1 s. No hook.
#   arc                2 s. No hook: gha-runner-scale-set-controller 0.14.2
#                      templates no Job.
#   estate-front      11 s. No hook: `manifests/estate-front` is a Service and a
#                      NetworkPolicy.
#   estate-front-runner 1 s. No hook: gha-runner-scale-set 0.14.2 templates no Job.
#   post-merge-verifier 0 s (history has second resolution, so counted as 1 s).
#                      No hook: `verifier/manifests` is a Namespace, a
#                      ServiceAccount, a ClusterRole and a ClusterRoleBinding.
#
# `yadgar` (chart 0.3.13, rendered with `applications/yadgar.yaml`'s
# `valuesObject`) runs four hook Jobs in sequence on every attempt, none with
# `activeDeadlineSeconds`:
#   preflight              PreSync   script TIMEOUT_SECONDS=120: bounded by script
#   bootstrap-secrets      PreSync   one curl, no timeout: measured 3 s
#   admin-bootstrap-token  PreSync   one curl, no timeout: measured 3 s
#   envoy-gateway-probe    PostSync  script TIMEOUT_SECONDS=300: bounded by script
# The two 3 s figures are the live Jobs' startTime -> completionTime
# (2026-10-01 08:09:39 -> 08:09:42). Script bounds, not pod bounds: a pod that
# never starts is bounded only by this timeout.
YADGAR_ATTEMPT_SECONDS = 120 + 300 + 3 * MEASURED_MARGIN + 3 * MEASURED_MARGIN

ATTEMPT_ALLOWANCE = {
    # BOUNDED: the CA preflight Job's `activeDeadlineSeconds: 300`
    # (`infra/tls/ca-preflight.yaml`, moved here at M3).
    TLS_FILE: (300, "bounded: preflight activeDeadlineSeconds"),
    "envoy-gateway.yaml": (38 * MEASURED_MARGIN, "measured, not bounded: certgen"),
    "cert-manager.yaml": (44 * MEASURED_MARGIN, "measured, not bounded: startupapicheck"),
    "keda.yaml": (2 * MEASURED_MARGIN, "measured, not bounded: no hook"),
    "mariadb-operator.yaml": (1 * MEASURED_MARGIN, "measured, not bounded: no hook"),
    "prometheus.yaml": (1 * MEASURED_MARGIN, "measured, not bounded: no hook"),
    "arc.yaml": (2 * MEASURED_MARGIN, "measured, not bounded: no hook"),
    "estate-front.yaml": (11 * MEASURED_MARGIN, "measured, not bounded: no hook"),
    "estate-front-runner.yaml": (1 * MEASURED_MARGIN, "measured, not bounded: no hook"),
    "post-merge-verifier.yaml": (1 * MEASURED_MARGIN, "measured, not bounded: no hook"),
    "yadgar.yaml": (
        YADGAR_ATTEMPT_SECONDS,
        "bounded: preflight 120 s + probe 300 s scripts; measured, not bounded: two 3 s curl Jobs x3",
    ),
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


def retry_window(retry: dict) -> float:
    """Total seconds Argo v3.1.8 waits across every retry. PURE."""
    backoff = retry["backoff"]
    duration, factor = go_seconds(backoff["duration"]), int(backoff["factor"])
    ceiling = go_seconds(backoff["maxDuration"])
    total, wait = 0.0, duration
    for _ in range(int(retry["limit"])):
        wait = min(ceiling, wait * factor) if ceiling > 0 else wait * factor
        total += wait
    return total


def chain_seconds(retry: dict, allowance: float) -> float:
    """The longest legitimate operation under one retry block. PURE."""
    return retry_window(retry) + (int(retry["limit"]) + 1) * allowance


def retry_blocks() -> dict[str, dict]:
    """Every retry block under `applications/`, by file name."""
    blocks: dict[str, dict] = {}
    for path in sorted(APPLICATIONS.glob("*.yaml")):
        document = yaml.safe_load(path.read_text()) or {}
        retry = ((document.get("spec") or {}).get("syncPolicy") or {}).get("retry")
        if isinstance(retry, dict):
            blocks[path.name] = retry
    return blocks


def sync_timeout_floor() -> float:
    blocks = retry_blocks()
    missing = sorted(set(blocks) - set(ATTEMPT_ALLOWANCE))
    assert not missing, f"no ATTEMPT_ALLOWANCE row for {missing}: measure or bound it first"
    return max(chain_seconds(retry, ATTEMPT_ALLOWANCE[name][0]) for name, retry in blocks.items())


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


def test_the_sync_timeout_outlasts_every_retry_chain_on_the_cluster() -> None:
    assert sync_timeout_failures(values()) == []


def test_the_retry_arithmetic_is_argos() -> None:
    # Hand-computed against NextRetryAt: 30, 60, 120, 240, 300, 300.
    operator = {"limit": 6, "backoff": {"duration": "15s", "factor": 2, "maxDuration": "5m"}}
    assert retry_window(operator) == 30 + 60 + 120 + 240 + 300 + 300
    # 1050 s of backoff plus 7 attempts of 300 s.
    assert chain_seconds(operator, 300) == 1050 + 7 * 300


def test_every_allowance_is_labelled_bounded_or_measured() -> None:
    for name, (seconds, source) in ATTEMPT_ALLOWANCE.items():
        assert seconds > 0, name
        assert source.startswith(("bounded: ", "measured, not bounded: ")), (name, source)


def test_the_floor_is_yadgars_chain() -> None:
    # With every retry at limit 6 (M3), yadgar's 438 s per attempt is the largest
    # allowance: 1050 s of backoff plus 7 x (120 + 300 + 9 + 9) = 4116 s.
    # Red until M3.
    assert YADGAR_ATTEMPT_SECONDS == 438
    assert sync_timeout_floor() == 1050 + 7 * 438


def test_an_application_without_an_allowance_reddens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(ATTEMPT_ALLOWANCE, "keda.yaml")
    with pytest.raises(AssertionError, match="no ATTEMPT_ALLOWANCE row"):
        sync_timeout_floor()


def test_tls_is_read_from_this_repository() -> None:
    # RED UNTIL M3 MOVES `Application/tls` HERE. Merge this after M3.
    assert TLS_FILE in retry_blocks(), (
        f"applications/{TLS_FILE} has no retry block here yet, so the floor "
        "cannot see tls's chain; merge after M3"
    )


def test_every_application_retry_is_read() -> None:
    # The denominator: the five operator Applications, M3's five and #52's verifier.
    assert sorted(retry_blocks()) == sorted(
        [
            "arc.yaml",
            "estate-front.yaml",
            "estate-front-runner.yaml",
            "post-merge-verifier.yaml",
            "yadgar.yaml",
            "cert-manager.yaml",
            "envoy-gateway.yaml",
            "keda.yaml",
            "mariadb-operator.yaml",
            "prometheus.yaml",
            TLS_FILE,
        ]
    )


@pytest.mark.parametrize(
    ("value", "named"),
    [
        (None, "has no"),
        ("0", "disables"),
        ("900", "below"),
        ("3600", "below"),
        ("4115", "below"),
        ("2147483648", "MaxInt32"),
        ("3000000000", "MaxInt32"),
        (4200, "not a quoted"),
        ("1h", "not a quoted"),
    ],
    ids=["missing", "zero", "900", "old-3600", "one-under-floor", "maxint32-plus-1", "3e9", "unquoted", "go-duration"],
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
