#!/usr/bin/env python3
"""E2-interim of the automatic pin: judge a pin sha against kind-yadgar, read-only (ledger 1632 C4, ADR-0946, ADR-0948 R2).

    pin_dryrun.py run  --repo DIR --main-sha SHA --sha SHA --context CTX --out FILE
    pin_dryrun.py find --sha SHA --out FILE [--repository R] [--workflow-path P]

THE PLAN IS `yadgarhq/docs` `plans/auto-deploy-parent-chart.md`, section 3,
"Check E2". `yadgarhq/argocd-verify`'s `pin-dryrun.yaml` runs `run` on the
private runner, at argocd `main`'s full sha (ADR-0844 (1)), against the DATA of
one pin sha. `auto-pin.yaml` (car C5) runs `find` to read the result back.

READ-ONLY, AND ONLY UNDER THE CURRENT ServiceAccount. The runner's
`post-merge-verifier` ServiceAccount holds get/list/watch and no Secrets
(ADR-0840). Every cluster read goes through `verify_handover.Cluster`, which
refuses any verb but `get`, any output but `-o json` and any Secret read, and
always carries an explicit `--context`. `kubectl diff` and a server dry run
are authorised as the real write verb, so they wait for E2-full (ADR-0948 R1,
car C8). This file never asks for them.

WHAT `run` DOES, in order. Every refusal comes before any cluster read.

  1. Refuse unless `main...sha` (`--no-renames`, so a rename shows its old
     path) changes only the three pin files: `applications/yadgar.yaml`,
     `scripts/chart_pin.json`, `scripts/gates/yadgar_render.sha256`.
  2. Read ONE value from the sha's data: `spec.source.targetRevision`, a bare
     release `X.Y.Z`. Refuse unless the sha's `yadgar.yaml` is main's with that
     one line rewritten (`pin_bump.rewrite_target_revision`), byte for byte,
     and its `chart_pin.json` is main's with only `chart_tag` moved. So no PR
     value reaches the credential: the head render is main's values at the
     target version. A values change beside the pin is refused, not ignored.
  3. Render main's `yadgar.yaml` (the base) and the rewritten one (the head)
     through the gates' own `parent_render` (helm v3.18.4 in the workflow).
     Refuse when the sha's K3 table disagrees with the head render, when a
     render holds a Secret (never read), or a kind the read table below does
     not name. `helm.sh/hook` objects are left out of every comparison, as in
     the hand method of argocd#103 to #112 (Argo runs hooks; they are not
     steady-state objects).
  4. Read, once: the `yadgar` Application, each rendered kind in namespace
     `yadgar` (cluster-scoped kinds without a namespace), and the mutating and
     validating webhook configurations and admission policies.
  5. Settled: `yadgar` Synced, Healthy, no operation running or pending, and
     at main's pin in `spec.source.targetRevision` and `status.sync.revision`.
     An Application not yet at main's pin is "not settled", not drift.
  6. The baseline: every base object is live, carries Argo's tracking-id
     naming `yadgar` and the object (`yadgar:<group>/<Kind>:yadgar/<name>`,
     the annotation the hand method added before `kubectl diff`), and its
     rendered fields are a subset of live. Any mismatch is drift: red,
     "baseline not empty".
  7. The head diff: the fields where the head render differs from live. Green
     only when every one is `spec.template.spec.containers[N].image` of a
     Deployment, StatefulSet or Job (an initContainer image is red, as in
     `pin_bump.py`'s classifier), and the object set is the same at both pins.

THE COMPARATOR. Rendered fields only, so `managedFields`, `resourceVersion`,
`generation`, `status`, uids, server defaults and extra live annotations never
count. A rendered `null`, `{}` or `[]` matches an absent live field (the API
server drops them). Lists compare by index and must have the same length. A
bool never equals a number. Quantities are normalised only where the path is a
resource quantity (a key under `limits` or `requests`, or `sizeLimit`): `1000m`
equals `1`, `1024Mi` equals `1Gi`. Elsewhere a quantity-looking string is a
string.

WHAT INTERIM LOSES, and the result says so: admission webhooks and server
defaulting never run. The result lists, by name, every webhook and admission
policy whose rules match `apps` or `batch` workloads, so a reviewer sees which
admission it did not run. ADR-0948 R2 lets a pin auto-merge on this evidence.
No client-side dry run either: it is not a `get`, and kubectl skips its own
schema check when the server supports field validation, so a client dry run
would add a write-shaped verb and validate nothing. A head diff limited to the
image of objects that already passed admission at the current pin needs no
schema check.

`Job/seeder` carries `Replace=true,Force=true`; its pod template is immutable,
so even E2-full would judge it by the render alone. Here it is compared like
any other object.

THE RESULT holds object keys and field paths, never a value: `sha`,
`main_sha`, `pin`, `target`, `result` (green, red or refused), `interim`,
`not_run`, `problems`, `baseline`, `objects` (the head diff), `hooks_left_out`,
`compared`, `admission`. `pin-dryrun.yaml` uploads it as artifact `pin-dryrun`
(`result.json`).

`find` is the reader, and the evidence is only as good as its filters. It takes
the newest `pin-dryrun` artifact whose run is a completed `workflow_dispatch`
of exactly the workflow's path, with this repository as repository and head,
on the default branch, AND with a `head_sha` reachable from the default branch
through `compare/{branch}...{sha}`: a tag named `main` reports
`head_branch=main` while running the tag's copy of the workflow (argocd#111
S1). The artifact's own `sha` must be the one asked for. A newer red beats an
older green. Never `commits/{sha}`: GitHub serves the whole fork network there.

STANDARD LIBRARY AT IMPORT. `find` runs in a job that may install nothing, so
PyYAML, `settled_gate` (which imports PyYAML at module level), `pin_bump` and
the gate modules load only inside `run`, which `pin-dryrun.yaml` runs after
installing its hash-pinned requirements.

Exit codes: 0 green (or found green); 1 red, refused, or not found green;
2 usage; 3 infrastructure (nothing written for `run`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path

HERE = Path(__file__).resolve().parent

APPLICATION = "applications/yadgar.yaml"
CHART_PIN = "scripts/chart_pin.json"
TABLE = "scripts/gates/yadgar_render.sha256"
PIN_FILES = frozenset({APPLICATION, CHART_PIN, TABLE})

APPLICATION_NAME = "yadgar"
ARGOCD_NAMESPACE = "argocd"
TARGET_NAMESPACE = "yadgar"
TRACKING_ID = "argocd.argoproj.io/tracking-id"
HOOK = "helm.sh/hook"

ARTIFACT = "pin-dryrun"
RESULT_FILE = "result.json"
REPOSITORY = "yadgarhq/argocd-verify"
WORKFLOW_PATH = ".github/workflows/pin-dryrun.yaml"
GITHUB_API = "https://api.github.com"
MAX_CANDIDATES = 50

FULL_SHA = re.compile(r"[0-9a-f]{40}")
RELEASE = re.compile(r"\d+\.\d+\.\d+")
IMAGE_PATH = re.compile(r"spec\.template\.spec\.containers\[\d+\]\.image")
WORKLOAD_KINDS = frozenset({"Deployment", "StatefulSet", "Job"})
WATCHED_GROUPS = frozenset({"apps", "batch", "*"})
WATCHED_RESOURCES = frozenset({"deployments", "statefulsets", "jobs", "*", "*/*"})

NOT_RUN = ("admission webhooks (interim: no server dry run)", "server defaulting")

# THE READ TABLE: (group, kind) -> (kubectl resource, namespaced). Exactly the
# kinds of the parent render's 81 steady objects at 0.25.27 (94 rendered, 13
# `helm.sh/hook`; the Roles and RoleBindings are hooks only). A kind not named here is
# refused before any read; `scripts/gates/test_pin_dryrun_kinds.py` renders
# main and fails CI when the render outgrows this table. No Secret, ever.
READS: dict[tuple[str, str], tuple[str, bool]] = {
    ("apps", "Deployment"): ("deployments.apps", True),
    ("apps", "StatefulSet"): ("statefulsets.apps", True),
    ("batch", "Job"): ("jobs.batch", True),
    ("", "ConfigMap"): ("configmaps", True),
    ("", "Service"): ("services", True),
    ("", "ServiceAccount"): ("serviceaccounts", True),
    ("cert-manager.io", "Certificate"): ("certificates.cert-manager.io", True),
    ("cert-manager.io", "Issuer"): ("issuers.cert-manager.io", True),
    ("gateway.envoyproxy.io", "EnvoyProxy"): ("envoyproxies.gateway.envoyproxy.io", True),
    ("gateway.networking.k8s.io", "Gateway"): ("gateways.gateway.networking.k8s.io", True),
    ("gateway.networking.k8s.io", "GatewayClass"): ("gatewayclasses.gateway.networking.k8s.io", False),
    ("gateway.networking.k8s.io", "HTTPRoute"): ("httproutes.gateway.networking.k8s.io", True),
    ("k8s.mariadb.com", "MariaDB"): ("mariadbs.k8s.mariadb.com", True),
    ("keda.sh", "ScaledObject"): ("scaledobjects.keda.sh", True),
    ("networking.k8s.io", "NetworkPolicy"): ("networkpolicies.networking.k8s.io", True),
    ("policy", "PodDisruptionBudget"): ("poddisruptionbudgets.policy", True),
}
ADMISSION = {
    "mutating": "mutatingwebhookconfigurations.admissionregistration.k8s.io",
    "validating": "validatingwebhookconfigurations.admissionregistration.k8s.io",
    "policies": "validatingadmissionpolicies.admissionregistration.k8s.io",
}


class InfrastructureError(Exception):
    """A failure of the run, not a finding: exit 3, nothing written."""


class NotFound(InfrastructureError):
    """A GitHub 404. Only the reachability check reads it as an answer."""


# ── the modules `run` needs, loaded late ────────────────────────────────────


def _load(name: str):
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    return __import__(name)


def gate_digests() -> Callable[[list], dict[str, str]]:
    return _load("settled_gate").gate_digests()


def object_key(document: dict) -> str:
    return _load("settled_gate")._gate_module("test_yadgar_application").object_key(document)


# ── git, read-only ──────────────────────────────────────────────────────────


def _git(repo: Path, args: list[str], runner: Callable) -> bytes:
    # GIT_DIR and friends override `-C`; a pre-commit hook exports them.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        finished = runner(["git", "-C", str(repo), *args], capture_output=True, check=False, env=env)
    except FileNotFoundError as error:
        raise InfrastructureError("no git binary on PATH") from error
    if finished.returncode != 0:
        raise InfrastructureError(f"`git {' '.join(args)}` exited {finished.returncode}")
    return finished.stdout if isinstance(finished.stdout, bytes) else finished.stdout.encode()


def changed_paths(repo: Path, base: str, sha: str, runner: Callable) -> list[str]:
    """Every path `base...sha` touches. `--no-renames`: a rename lists its old path too."""
    for commit in (base, sha):
        _git(repo, ["cat-file", "-e", f"{commit}^{{commit}}"], runner)
    out = _git(repo, ["diff", "--name-only", "--no-renames", "-z", f"{base}...{sha}"], runner)
    return sorted(path for path in out.decode().split("\0") if path)


def read_at(repo: Path, sha: str, path: str, runner: Callable) -> bytes | None:
    settled_gate = _load("settled_gate")
    try:
        return settled_gate.read_at(repo, sha, path, runner)
    except settled_gate.InfrastructureError as error:
        raise InfrastructureError(str(error)) from error


# ── step 1-3: refusals, before any cluster read ─────────────────────────────


def pin_refusals(repo: Path, base: str, sha: str, runner: Callable) -> tuple[str | None, str | None, bytes | None, list[str]]:
    """`(pin, target, head application, problems)`. Git and text only; no render, no cluster."""
    pin_bump = _load("pin_bump")
    problems = [
        f"{path}: changed by main...{sha[:12]}, outside the three pin files ({', '.join(sorted(PIN_FILES))})"
        for path in changed_paths(repo, base, sha, runner)
        if path not in PIN_FILES
    ]
    main_application = read_at(repo, base, APPLICATION, runner)
    head_application = read_at(repo, sha, APPLICATION, runner)
    if main_application is None or head_application is None:
        raise InfrastructureError(f"{APPLICATION} is absent at main or at the sha")
    try:
        pin = pin_bump.current_pin(main_application.decode())
        target = pin_bump.current_pin(head_application.decode())
    except pin_bump.Refused as refused:
        return None, None, None, [*problems, f"{APPLICATION}: {refused}"]
    if not RELEASE.fullmatch(target):
        problems.append(f"{APPLICATION}: targetRevision {target!r} at the sha is not a release X.Y.Z")
        return pin, target, None, problems
    try:
        expected = pin_bump.rewrite_target_revision(main_application.decode(), target)[0].encode()
    except pin_bump.Refused as refused:
        return pin, target, None, [*problems, f"{APPLICATION}: {refused}"]
    if head_application != expected:
        problems.append(f"{APPLICATION}: differs from main's in more than the one targetRevision line")
    main_pin = read_at(repo, base, CHART_PIN, runner)
    head_pin = read_at(repo, sha, CHART_PIN, runner)
    if main_pin is None or head_pin is None:
        problems.append(f"{CHART_PIN}: absent at main or at the sha")
    else:
        try:
            if head_pin.decode() != pin_bump.rewrite_chart_pin(main_pin.decode(), target):
                problems.append(f"{CHART_PIN}: differs from main's in more than chart_tag v{target}")
        except pin_bump.Refused as refused:
            problems.append(f"{CHART_PIN}: {refused}")
    return pin, target, expected, problems


def objects_of(documents: list) -> dict[str, dict]:
    return {object_key(d): d for d in documents if isinstance(d, dict) and d.get("kind")}


def is_hook(document: dict) -> bool:
    return HOOK in ((document.get("metadata") or {}).get("annotations") or {})


def group_kind(document: dict) -> tuple[str, str]:
    api_version = str(document.get("apiVersion", ""))
    return (api_version.split("/")[0] if "/" in api_version else "", str(document.get("kind")))


def render_refusals(base: dict[str, dict], head: dict[str, dict], head_documents: list, table: bytes | None, target: str) -> list[str]:
    settled_gate = _load("settled_gate")
    problems: list[str] = []
    if table is None:
        problems.append(f"{TABLE}: absent at the sha")
    else:
        revision, objects, shape = settled_gate.parse_table(table)
        problems += [f"{TABLE}: {p}" for p in shape]
        if revision != target:
            problems.append(f"{TABLE}: header names targetRevision {revision!r}, the sha pins {target}")
        problems += [f"{TABLE}: {p}" for p in settled_gate.table_problems(objects, gate_digests()(head_documents))]
    # Hooks are never read, so only the steady objects must be readable.
    for key, document in sorted((k, d) for k, d in {**base, **head}.items() if not is_hook(d)):
        if document.get("kind") == "Secret":
            problems.append(f"{key}: the render holds a Secret, which this check never reads")
        elif group_kind(document) not in READS:
            problems.append(f"{key}: kind not in pin_dryrun.READS; extend the table (and the runner's ClusterRole) first")
        elif (document.get("metadata") or {}).get("namespace", TARGET_NAMESPACE) != TARGET_NAMESPACE:
            problems.append(f"{key}: rendered into a namespace other than {TARGET_NAMESPACE}")
    return problems


# ── the comparator ──────────────────────────────────────────────────────────

QUANTITY = re.compile(r"([+-]?(?:\d+\.?\d*|\.\d+))(?:(Ki|Mi|Gi|Ti|Pi|Ei|n|u|m|k|M|G|T|P|E)|[eE]([+-]?\d+))?")
SUFFIX = {
    "n": Decimal("1e-9"), "u": Decimal("1e-6"), "m": Decimal("1e-3"), "": Decimal(1),
    "k": Decimal("1e3"), "M": Decimal("1e6"), "G": Decimal("1e9"), "T": Decimal("1e12"), "P": Decimal("1e15"), "E": Decimal("1e18"),
    "Ki": Decimal(2**10), "Mi": Decimal(2**20), "Gi": Decimal(2**30), "Ti": Decimal(2**40), "Pi": Decimal(2**50), "Ei": Decimal(2**60),
}  # fmt: skip
SEGMENT = re.compile(r"[^.\[\]]+|\[\d+\]")


def quantity(value) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    match = QUANTITY.fullmatch(str(value).strip())
    if not match:
        return None
    try:
        number = Decimal(match.group(1))
    except InvalidOperation:
        return None
    if match.group(3) is not None:
        return number * (Decimal(10) ** int(match.group(3)))
    return number * SUFFIX[match.group(2) or ""]


def is_quantity_path(path: str) -> bool:
    parts = [p for p in SEGMENT.findall(path) if not p.startswith("[")]
    return bool(parts) and (parts[-1] == "sizeLimit" or (len(parts) > 1 and parts[-2] in {"limits", "requests"}))


def _join(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def differences(rendered, live, path: str = "") -> list[str]:
    """Every path where a rendered field is not what live holds. Rendered fields only."""
    if rendered is None:
        return []
    if isinstance(rendered, dict):
        if live is None and not rendered:
            return []
        if not isinstance(live, dict):
            return [path or "<root>"]
        return [found for key in sorted(rendered) for found in differences(rendered[key], live.get(key), _join(path, str(key)))]
    if isinstance(rendered, list):
        if live is None and not rendered:
            return []
        if not isinstance(live, list) or len(live) != len(rendered):
            return [path or "<root>"]
        return [found for i, (a, b) in enumerate(zip(rendered, live, strict=True)) for found in differences(a, b, f"{path}[{i}]")]
    if isinstance(rendered, bool) != isinstance(live, bool):
        return [path]
    if rendered == live:
        return []
    if is_quantity_path(path):
        left, right = quantity(rendered), quantity(live)
        if left is not None and left == right:
            return []
    return [path]


def tracking_problem(key: str, document: dict, live: dict) -> str | None:
    group, kind = group_kind(document)
    name = (document.get("metadata") or {}).get("name")
    namespaced = READS[(group, kind)][1]
    wanted = {f"{APPLICATION_NAME}:{group}/{kind}:{TARGET_NAMESPACE}/{name}"}
    if not namespaced:
        wanted.add(f"{APPLICATION_NAME}:{group}/{kind}:/{name}")
    found = ((live.get("metadata") or {}).get("annotations") or {}).get(TRACKING_ID)
    if found not in wanted:
        return f"baseline not empty: {key} tracking-id does not name {APPLICATION_NAME}'s {group}/{kind} {name}"
    return None


# ── step 4-5: the reads and settledness ─────────────────────────────────────


def settled_problems(application: dict, pin: str) -> list[str]:
    status = application.get("status") or {}
    problems = []
    sync = (status.get("sync") or {}).get("status")
    health = (status.get("health") or {}).get("status")
    phase = (status.get("operationState") or {}).get("phase")
    if sync != "Synced":
        problems.append(f"not settled: {APPLICATION_NAME} sync status {sync}")
    if health != "Healthy":
        problems.append(f"not settled: {APPLICATION_NAME} health {health}")
    if phase in {"Running", "Terminating"}:
        problems.append(f"not settled: {APPLICATION_NAME} operation {phase}")
    if application.get("operation"):
        problems.append(f"not settled: {APPLICATION_NAME} has an operation pending")
    spec_revision = ((application.get("spec") or {}).get("source") or {}).get("targetRevision")
    if str(spec_revision) != pin:
        problems.append(f"not settled: {APPLICATION_NAME} spec.source.targetRevision {spec_revision}, main pins {pin}")
    synced_revision = (status.get("sync") or {}).get("revision")
    if str(synced_revision) != pin:
        problems.append(f"not settled: {APPLICATION_NAME} status.sync.revision {synced_revision}, main pins {pin}")
    return problems


def _matches(rule: dict) -> bool:
    groups = set(rule.get("apiGroups") or [])
    resources = {str(r).split("/")[0] if r != "*/*" else r for r in rule.get("resources") or []}
    return bool(groups & WATCHED_GROUPS) and bool(resources & WATCHED_RESOURCES)


def admission(cluster) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for which, resource in ADMISSION.items():
        names = []
        for item in cluster.json("get", resource).get("items") or []:
            name = (item.get("metadata") or {}).get("name")
            if which == "policies":
                rules = ((item.get("spec") or {}).get("matchConstraints") or {}).get("resourceRules") or []
                if any(_matches(r) for r in rules):
                    names.append(str(name))
                continue
            for webhook in item.get("webhooks") or []:
                if any(_matches(r) for r in webhook.get("rules") or []):
                    names.append(f"{name}/{webhook.get('name')}")
        found[which] = sorted(names)
    return found


def live_objects(cluster, documents: dict[str, dict]) -> dict[str, dict]:
    """`{key: live object}` for the rendered objects that exist, one list per kind."""
    wanted: dict[tuple[str, bool], dict[str, str]] = {}
    for key, document in documents.items():
        resource, namespaced = READS[group_kind(document)]
        wanted.setdefault((resource, namespaced), {})[(document.get("metadata") or {}).get("name")] = key
    found: dict[str, dict] = {}
    for (resource, namespaced), names in sorted(wanted.items()):
        args = ["get", resource, "-n", TARGET_NAMESPACE] if namespaced else ["get", resource]
        for item in cluster.json(*args).get("items") or []:
            name = (item.get("metadata") or {}).get("name")
            if name in names:
                found[names[name]] = item
    return found


# ── run ─────────────────────────────────────────────────────────────────────


def dry_run(repo: Path, base: str, sha: str, cluster, *, render: Callable[[bytes], list] | None = None, runner: Callable = subprocess.run) -> dict:
    """The E2-interim result for `sha` judged by main at `base`. Raises InfrastructureError for a failure of the run."""
    result = {
        "sha": sha,
        "main_sha": base,
        "pin": None,
        "target": None,
        "result": "refused",
        "interim": True,
        "not_run": list(NOT_RUN),
        "problems": [],
        "baseline": [],
        "objects": [],
        "hooks_left_out": [],
        "compared": 0,
        "admission": {},
    }
    pin, target, head_application, problems = pin_refusals(repo, base, sha, runner)
    result.update(pin=pin, target=target)
    if problems or head_application is None:
        return {**result, "problems": problems}

    settled_gate = _load("settled_gate")
    render = render or settled_gate.default_render
    main_application = read_at(repo, base, APPLICATION, runner)
    try:
        base_documents = render(main_application)
        head_documents = render(head_application)
    except settled_gate.InfrastructureError as error:
        raise InfrastructureError(str(error)) from error
    base_objects, head_objects = objects_of(base_documents), objects_of(head_documents)
    problems = render_refusals(base_objects, head_objects, head_documents, read_at(repo, sha, TABLE, runner), target)
    if problems:
        return {**result, "problems": problems}

    problems = [f"{key}: added at head (the object set moved)" for key in sorted(head_objects.keys() - base_objects.keys())]
    problems += [f"{key}: removed at head (the object set moved)" for key in sorted(base_objects.keys() - head_objects.keys())]
    hooks = sorted(k for k, d in {**base_objects, **head_objects}.items() if is_hook(d))
    base_steady = {k: d for k, d in base_objects.items() if not is_hook(d)}
    head_steady = {k: d for k, d in head_objects.items() if not is_hook(d)}

    verify_handover = _load("verify_handover")
    try:
        application = cluster.json("get", "applications.argoproj.io", APPLICATION_NAME, "-n", ARGOCD_NAMESPACE)
        live = live_objects(cluster, {**base_steady, **head_steady})
        seen_admission = admission(cluster)
    except (verify_handover.KubectlError, verify_handover.UsageError, ValueError) as error:
        raise InfrastructureError(f"a cluster read failed: {error}") from error

    problems += settled_problems(application, pin)
    baseline: list[str] = []
    for key, document in sorted(base_steady.items()):
        if key not in live:
            baseline.append(f"baseline not empty: {key} not live")
            continue
        if (problem := tracking_problem(key, document, live[key])) is not None:
            baseline.append(problem)
        baseline += [f"baseline not empty: {key} {path}" for path in differences(document, live[key])]
    objects = []
    for key, document in sorted(head_steady.items()):
        if key not in live:
            continue
        paths = differences(document, live[key])
        if not paths:
            continue
        objects.append({"key": key, "paths": paths})
        kind = document.get("kind")
        problems += [
            f"head diff outside the image rule: {key} {path}"
            for path in paths
            if kind not in WORKLOAD_KINDS or not IMAGE_PATH.fullmatch(path)
        ]
    problems += baseline
    return {
        **result,
        "result": "red" if problems else "green",
        "problems": problems,
        "baseline": baseline,
        "objects": objects,
        "hooks_left_out": hooks,
        "compared": len(base_steady),
        "admission": seen_admission,
    }


# ── find: the reader, stdlib only ───────────────────────────────────────────


class GitHub:
    """GET-only GitHub REST client. The token (GITHUB_TOKEN) is never printed and never follows a redirect."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")

    def _read(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        if self.token:
            # Unredirected: an artifact download redirects to blob storage, which must not see the token.
            request.add_unredirected_header("Authorization", f"Bearer {self.token}")
        where = url.removeprefix(GITHUB_API).split("?")[0]
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # GITHUB_API and its artifact redirects only
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                raise NotFound(f"GET {where}: HTTP 404") from error
            raise InfrastructureError(f"GET {where}: HTTP {error.code}") from error
        except (urllib.error.URLError, OSError) as error:
            raise InfrastructureError(f"GET {where}: {error!r}") from error

    def get(self, path: str):
        return json.loads(self._read(GITHUB_API + path))

    def download(self, url: str) -> bytes:
        return self._read(url)


def on_default_branch(github, repository: str, sha: object, branch: str) -> bool:
    """`sha` is the default branch's head or an ancestor of it. Never `commits/{sha}`."""
    if not isinstance(sha, str) or not FULL_SHA.fullmatch(sha):
        return False
    try:
        status = github.get(f"/repos/{repository}/compare/{urllib.parse.quote(branch, safe='/')}...{sha}").get("status")
    except NotFound:
        return False
    return status in {"behind", "identical"}


def _result_of(archive: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as opened:
            return json.loads(opened.read(RESULT_FILE))
    except (zipfile.BadZipFile, KeyError, ValueError) as error:
        raise InfrastructureError(f"a {ARTIFACT} artifact holds no readable {RESULT_FILE}: {error}") from error


def find(github, repository: str, workflow_path: str, sha: str) -> dict | None:
    """The newest trusted `pin-dryrun` result for `sha`, or None."""
    own = github.get(f"/repos/{repository}")
    if "id" not in own or not own.get("default_branch"):
        raise InfrastructureError(f"/repos/{repository} carries no id or default_branch")
    artifacts: list[dict] = []
    for page in range(1, 101):
        batch = github.get(f"/repos/{repository}/actions/artifacts?name={ARTIFACT}&per_page=100&page={page}").get("artifacts")
        if batch is None:
            raise InfrastructureError(f"no artifacts in the /repos/{repository} listing")
        artifacts += batch
        if len(batch) < 100:
            break
    candidates = sorted(
        (
            a
            for a in artifacts
            if a.get("name") == ARTIFACT
            and not a.get("expired")
            and (run := a.get("workflow_run") or {})
            and run.get("repository_id") == own["id"]
            and run.get("head_repository_id") == own["id"]
            and run.get("head_branch") == own["default_branch"]
        ),
        key=lambda a: str(a.get("created_at")),
        reverse=True,
    )
    for candidate in candidates[:MAX_CANDIDATES]:
        run = github.get(f"/repos/{repository}/actions/runs/{candidate['workflow_run']['id']}")
        if (
            run.get("status") != "completed"
            or run.get("event") != "workflow_dispatch"
            or run.get("path") != workflow_path
            or run.get("head_branch") != own["default_branch"]
            or (run.get("repository") or {}).get("id") != own["id"]
            or (run.get("head_repository") or {}).get("id") != own["id"]
            or not on_default_branch(github, repository, run.get("head_sha"), own["default_branch"])
        ):
            continue
        result = _result_of(github.download(candidate["archive_download_url"]))
        if result.get("sha") != sha:
            continue
        return {"found": True, "run_id": run["id"], "artifact_id": candidate["id"], "created_at": candidate.get("created_at"), "result": result}
    return None


# ── CLI ─────────────────────────────────────────────────────────────────────


def _full_sha(value: str) -> str:
    if not FULL_SHA.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a full 40-hex commit sha")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    ran = sub.add_parser("run", help="judge one pin sha against kind-yadgar, read-only")
    ran.add_argument("--repo", type=Path, required=True, help="the argocd checkout holding both shas")
    ran.add_argument("--main-sha", type=_full_sha, required=True, help="argocd main's full sha (the code and the base)")
    ran.add_argument("--sha", type=_full_sha, required=True, help="the pin sha whose data is judged")
    ran.add_argument("--context", required=True, help="kubectl context; there is no default")
    ran.add_argument("--out", type=Path, required=True, help="where result.json is written")
    found = sub.add_parser("find", help="the newest trusted pin-dryrun result for a sha")
    found.add_argument("--repository", default=REPOSITORY)
    found.add_argument("--workflow-path", default=WORKFLOW_PATH)
    found.add_argument("--sha", type=_full_sha, required=True)
    found.add_argument("--out", type=Path, required=True)
    return parser


def _summary(result: dict) -> None:
    print(f"pin-dryrun {result['result']} (interim): sha {result['sha']} pin {result['pin']} -> {result['target']}, {result['compared']} objects compared")
    for item in result["objects"]:
        print(f"head diff: {item['key']} {', '.join(item['paths'])}")
    for problem in result["problems"]:
        print(f"{result['result'].upper()}: {problem}")
    for which, names in result["admission"].items():
        print(f"not run, {which}: {', '.join(names) or 'none'}")


def main(
    argv: list[str] | None = None,
    *,
    render: Callable[[bytes], list] | None = None,
    runner: Callable = subprocess.run,
    kube_runner: Callable = subprocess.run,
    github=None,
) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exit_:
        return 2 if exit_.code else 0
    if args.command == "find":
        try:
            found = find(github or GitHub(), args.repository, args.workflow_path, args.sha)
        except InfrastructureError as error:
            print(f"INFRASTRUCTURE FAILURE: {error}", file=sys.stderr)
            return 3
        args.out.write_text(json.dumps(found or {"found": False}, indent=2, sort_keys=True) + "\n")
        verdict = (found or {}).get("result", {}).get("result")
        print(f"pin-dryrun for {args.sha}: " + (f"{verdict}, run {found['run_id']}" if found else "none"))
        return 0 if verdict == "green" else 1
    try:
        cluster = _load("verify_handover").Cluster(args.context, runner=kube_runner)
        result = dry_run(args.repo, args.main_sha, args.sha, cluster, render=render, runner=runner)
    except InfrastructureError as error:
        print(f"INFRASTRUCTURE FAILURE, no result: {error}", file=sys.stderr)
        return 3
    result["read_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    _summary(result)
    return 0 if result["result"] == "green" else 1


if __name__ == "__main__":
    sys.exit(main())
