#!/usr/bin/env python3
"""E2-interim of the automatic pin: judge a pin sha against kind-yadgar, read-only (ledger 1632 C4, ADR-0946, ADR-0948 R2).

    pin_dryrun.py run  --repo DIR --main-sha SHA --sha SHA --context CTX --out FILE
    pin_dryrun.py find --sha SHA --main-sha SHA --pin X.Y.Z --out FILE [--repository R] [--workflow-path P]

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

  0. A self-check, not a control: every file in CHECKER_FILES that this
     process runs is compared with the same file at `--main-sha`, and a
     mismatch is an infrastructure failure. That catches an honest mix-up (a
     checkout at the wrong sha). It cannot stop a modified checker, which can
     skip the check and write main's hashes. The result's `checker` block
     (`main_sha`, a sha256 per file) is therefore a RECORD only. The guard
     against a stale or weaker checker is `find`'s run binding: the run must
     be main's own workflow file at the default branch's current head, and
     the result must name argocd main's current sha.

  1. Refuse unless main and the sha have exactly one merge base (a
     criss-cross history makes `main...sha` ambiguous), and `main...sha`
     (`--no-renames`, so a rename shows its old path) changes only the three
     pin files: `applications/yadgar.yaml`, `scripts/chart_pin.json`,
     `scripts/gates/yadgar_render.sha256`, plus, on a platform move, the five
     operator Applications (`OPERATOR_FILES`, car C10), plus `scripts/pin_needs_human_ack.json`
     only as an append (`ack_refusals`, ADR-1094; a re-ack of a target already on main
     rewrites an entry, so it leaves E2 refused: run the hand dry run). Any other path is
     refused, `scripts/gates/migration_seed_allowlist.json` among them (ADR-1099). A platform move is
     refused unless each operator file is main's with only its one
     `targetRevision` line moved to the sha's `platform_version`, main's files
     all pinned main's `platform_version`, and the new one is the target
     chart's `platform`; an operator file changed with no platform move is
     refused, and so is a pin that keeps `platform_version` while the target
     chart's `platform` differs from it (the operators would stay behind). So
     every run reads the target chart's `Chart.yaml`; an unreadable one is a
     refusal. A pure platform move then goes on to step 9 as well.
  2. Read ONE value from the sha's data: `spec.source.targetRevision`, a bare
     release `X.Y.Z` not lower than main's pin (a downgrade is a human revert).
     Refuse unless the sha's `yadgar.yaml` is main's with that
     one line rewritten (`pin_bump.rewrite_target_revision`), byte for byte,
     and its `chart_pin.json` is main's with only `chart_tag` and
     `chart_digest` moved, plus `platform_version` on a platform move, all in
     one comparison (the digest read from the sha and checked for shape
     only; CI's `chart-pin` job resolves the tag and compares it, yaadgaar
     250). So no PR
     value reaches the credential: the head render is main's values at the
     target version. A values change beside the pin is refused, not ignored.
     ONE PARSE ONLY. PyYAML, Go's YAML (Argo CD, Helm, kubectl) and a JSON
     reader can disagree on the same bytes, so `yadgar.yaml` at main and at the
     sha is refused when it holds any construct two readers may read apart: a
     duplicate key (PyYAML keeps the last, a first-key reader the first), an
     anchor, alias or merge key, an explicit tag, more than one document, a
     byte order mark, a tab, or a plain scalar YAML 1.1 and 1.2 read
     differently (`on`, `no`, `y`, `0755`, `1:30`). `chart_pin.json` is refused
     on a duplicate key. With those gone, the line-anchored `targetRevision`
     edit and the parsed `spec.source.targetRevision` name the same value.
  3. Render main's `yadgar.yaml` (the base) and the rewritten one (the head)
     through the gates' own `parent_render` (helm v3.18.4 in the workflow).
     Refuse when the sha's K3 table disagrees with the head render, when a
     render holds a Secret (never read), or a kind the read table below does
     not name. `helm.sh/hook` objects are not read from the cluster, as in the
     hand method of argocd#103 to #112 (Argo runs hooks; they are not
     steady-state objects), but they ARE judged in step 7's render diff.
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
  7. The render diff: the base render against the head render, EVERY object,
     hooks included, in both directions (a field removed at head counts). Green
     only when the object set is the same at both pins, every object keeps its
     hook status (`helm.sh/hook` neither added nor removed), and every changed
     path is exactly `spec.template.spec.containers[N].image` (anchored) of a
     Deployment, StatefulSet or Job whose image repository does not move. An
     initContainer image is red, as in `pin_bump.py`'s classifier.
  8. The live head diff: the fields where the head render differs from live,
     steady objects only, held to the same image rule.
  9. On a platform move only (car C10 part 2), the same over the five operator
     Applications, ALL OF THEM ONE JUDGMENT. Each is rendered from MAIN's file
     (the sha's differs only in `targetRevision`, step 1) at main's and at the
     sha's `platform_version`, on the workflow's one helm, CRDs included
     (`pin_bump.render_operator`). Before any cluster read: refused when a
     render fails, holds an object twice, holds a Secret, or holds a steady
     kind outside `OPERATOR_READS`. Then, per operator Application: settled
     as in step 5, at main's `platform_version`; the baseline as in step 6,
     each object read in its rendered namespace or else the Application's
     destination, owned by a tracking-id naming the Application (a
     cluster-scoped object's names the destination namespace, as Argo writes
     it), or, for a CRD, which carries no tracking-id on kind-yadgar
     (measured 2026-10-31), a row in the Application's `status.resources`;
     and every render-diff path (hooks included) and every live head-diff path
     in the PLATFORM-MOVE SHAPE: a container image of a workload whose
     repository does not move; a derived label (`helm.sh/chart`,
     `app.kubernetes.io/version`) on the object's own or its pod template's
     labels whose key no selector in any Application of the estate reads
     (ADR-1095: the five operator renders at both pins, the `yadgar` render,
     and every live object of `SELECTOR_READS` in every namespace, tracked by
     any Application or by none; a CRD-backed kind of `SELECTOR_CRD_READS` is
     read when its CRD is served); or a CRD's `spec.versions`. A selector kind
     the verifier cannot list refuses the run, naming the grant it needs. The object set and every hook status stay the same.
     THE SHAPE IS NOT E1. The values (a tag at its subchart's `appVersion`, a
     digest at the registry's, a CRD version change inside the allowlist) are
     judged by E1, `pin_bump.classify_platform`, on two helms, in the required
     `pin-evidence` job; this workflow has one helm. An automatic head needs
     both E1 accept and this green.

THE COMPARATOR. Rendered fields only, so `managedFields`, `resourceVersion`,
`generation`, `status`, uids, server defaults and extra live annotations never
count. A rendered `null`, `{}` or `[]` matches an absent live field (the API
server drops them), and so does a rendered `value: ""` of a container env entry
(EnvVar.value is `omitempty`); no other zero value is matched to an absent
field. Lists compare by index and must have the same length. A
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
would add a write-shaped verb and validate nothing (reasoned from kubectl's
validator code, NOT measured on kind-yadgar). That omission stands only
together with step 7: the head render may differ from the base render, which
live already admitted, in an image string of the same repository alone, and
an image string needs no schema check.

`Job/seeder` carries `Replace=true,Force=true`; its pod template is immutable,
so even E2-full would judge it by the render alone. Here it is compared like
any other object.

THE RESULT holds object keys and field paths, never a value: `sha`,
`main_sha`, `pin`, `target`, `result` (green, red or refused), `interim`,
`not_run`, `problems`, `baseline`, `objects` (the head diff), `hooks_left_out`,
`compared`, `admission`. On a platform move, `objects`, `baseline`,
`hooks_left_out` and `compared` carry the operator Applications' too, each
operator head diff entry naming its `application`, and `selectors` holds the
live selector keys per tracking Application (`untracked` for none) and the
CRD-backed selector kinds not served (keys only, never a value). `pin-dryrun.yaml` uploads it
as artifact `pin-dryrun` (`result.json`).

`find` is the reader, and the evidence is only as good as its filters.

  TRUST. It considers a `pin-dryrun` artifact only when its run is a
  `workflow_dispatch` of exactly the workflow's path, with this repository as
  repository and head, on the default branch, AND with a `head_sha` reachable
  from the default branch's head COMMIT: `compare/{branch head sha}...{sha}`.
  Never the bare branch name, because git resolves `refs/tags/main` before
  the branch; and `head_branch` alone is not enough either, because a run on a
  tag named `main` reports `head_branch=main` (argocd#111 S1). The artifact's
  own `sha` must be the one asked for. Never `commits/{sha}`: GitHub serves
  the whole fork network there.

  BINDING. The NEWEST trusted artifact for the sha decides, and nothing older
  is consulted behind it. Its verdict is:
    pending  its run has not completed (an artifact is uploaded before the
             job's last step, so a red run is visible before it ends);
    red      its result is red or refused, or its run concluded anything but
             `success`;
    stale    its result names another main sha or another pin than the ones
             the caller passes (`--main-sha`, `--pin`: argocd main now), or
             its `read_at` is missing, older than FRESH_SECONDS, or in the
             future past FUTURE_SKEW_SECONDS;
    stale    also: its run's head_sha is an older default-branch commit
             (an older `pin-dryrun.yaml` ran: the trust is main's current
             workflow file, never the run's own version). THIS run binding is
             the guard against a stale checker: that workflow checks out
             argocd main at a full sha and runs its code. A result whose
             `checker.main_sha` is absent or differs is stale too, but that
             field is self-attested, a consistency record, not a control;
             `find` never reads `checker.files`;
    green    otherwise. Only green exits 0. A `result.json` with a duplicate
             key is an infrastructure failure (exit 3), never a verdict.

  WHY THE BINDING. The token `auto-pin.yaml` reads with
  (`yadgarhq-settled-dispatch`, `actions: write` on argocd-verify) can also
  DELETE artifacts and CANCEL runs. Without the binding, whoever holds it
  could cancel or delete a newer red and let an older green, judged against an
  older main, authorise a merge. Bound to argocd main's current sha and pin
  and to a fresh `read_at`, an older green is stale, and a cancelled run is
  red.

STANDARD LIBRARY AT IMPORT. `find` runs in a job that may install nothing, so
PyYAML, `settled_gate` (which imports PyYAML at module level), `pin_bump` and
the gate modules load only inside `run`, which `pin-dryrun.yaml` runs after
installing its hash-pinned requirements.

Exit codes: 0 green (for `find`: a green verdict); 1 red, refused, or for
`find` pending, stale or nothing found; 2 usage; 3 infrastructure (nothing
written for `run`).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
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
# A human pin PR may also append its NEEDS-HUMAN acknowledgement here (ADR-1080, ADR-1094); see `ack_refusals`.
ACK_FILE = "scripts/pin_needs_human_ack.json"
# A platform move also changes these five, each by its one `targetRevision` line (car C10), and no more.
OPERATOR_FILES = tuple(
    f"applications/{name}.yaml" for name in ("cert-manager", "envoy-gateway", "keda", "mariadb-operator", "prometheus")
)

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
# THE FRESHNESS WINDOW of a result `find` may call green: `auto-pin.yaml`
# dispatches, waits behind the shared `maxRunners: 1` runner, then reads. An
# hour bounds that wait; an older result was judged against a cluster that may
# have moved since.
FRESH_SECONDS = 3600
# A `read_at` this far in the future is a clock skew; any further is refused.
FUTURE_SKEW_SECONDS = 300
# THE CHECKER: every file `run` executes, relative to the argocd checkout.
CHECKER_FILES = (
    "scripts/pin_dryrun.py",
    "scripts/pin_bump.py",
    "scripts/pin_evidence.py",
    "scripts/auto_pin.py",
    "scripts/settled_gate.py",
    "scripts/verify_handover.py",
    "scripts/gates/test_no_two_owners.py",
    "scripts/gates/test_yadgar_application.py",
)
# Plain scalars YAML 1.1 reads as a bool, an octal or a sexagesimal number and
# YAML 1.2 reads otherwise (or the reverse).
AMBIGUOUS_SCALAR = re.compile(
    r"(?:y|Y|yes|Yes|YES|n|N|no|No|NO|on|On|ON|off|Off|OFF)"
    r"|[-+]?0[0-7]+|[-+]?0o[0-7]+|[-+]?0b[01_]+|[-+]?\d[\d_]*(?::[0-5]?\d)+(?:\.\d*)?"
)

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
    ("gateway.envoyproxy.io", "BackendTrafficPolicy"): ("backendtrafficpolicies.gateway.envoyproxy.io", True),
    ("gateway.envoyproxy.io", "ClientTrafficPolicy"): ("clienttrafficpolicies.gateway.envoyproxy.io", True),
    ("gateway.envoyproxy.io", "EnvoyProxy"): ("envoyproxies.gateway.envoyproxy.io", True),
    ("gateway.networking.k8s.io", "Gateway"): ("gateways.gateway.networking.k8s.io", True),
    ("gateway.networking.k8s.io", "GatewayClass"): ("gatewayclasses.gateway.networking.k8s.io", False),
    ("gateway.networking.k8s.io", "HTTPRoute"): ("httproutes.gateway.networking.k8s.io", True),
    ("k8s.mariadb.com", "MariaDB"): ("mariadbs.k8s.mariadb.com", True),
    ("keda.sh", "ScaledObject"): ("scaledobjects.keda.sh", True),
    ("networking.k8s.io", "NetworkPolicy"): ("networkpolicies.networking.k8s.io", True),
    ("policy", "PodDisruptionBudget"): ("poddisruptionbudgets.policy", True),
}
# THE OPERATOR READ TABLE (car C10 part 2): the steady kinds of the five
# operator Applications at platform 0.2.17 (158 objects with CRDs, 147 steady),
# each read by the verifier's ClusterRole (`verifier/manifests/clusterrole.yaml`,
# held by `scripts/tests/test_pin_dryrun_operators.py`). Kept exact by
# `scripts/gates/test_pin_dryrun_kinds.py`. No Secret, ever.
OPERATOR_READS: dict[tuple[str, str], tuple[str, bool]] = {
    ("", "ConfigMap"): ("configmaps", True),
    ("", "PersistentVolumeClaim"): ("persistentvolumeclaims", True),
    ("", "Service"): ("services", True),
    ("", "ServiceAccount"): ("serviceaccounts", True),
    ("admissionregistration.k8s.io", "MutatingWebhookConfiguration"): ("mutatingwebhookconfigurations.admissionregistration.k8s.io", False),
    ("admissionregistration.k8s.io", "ValidatingAdmissionPolicy"): ("validatingadmissionpolicies.admissionregistration.k8s.io", False),
    ("admissionregistration.k8s.io", "ValidatingAdmissionPolicyBinding"): (
        "validatingadmissionpolicybindings.admissionregistration.k8s.io", False,
    ),
    ("admissionregistration.k8s.io", "ValidatingWebhookConfiguration"): (
        "validatingwebhookconfigurations.admissionregistration.k8s.io", False,
    ),
    ("apiextensions.k8s.io", "CustomResourceDefinition"): ("customresourcedefinitions.apiextensions.k8s.io", False),
    ("apiregistration.k8s.io", "APIService"): ("apiservices.apiregistration.k8s.io", False),
    ("apps", "Deployment"): ("deployments.apps", True),
    ("rbac.authorization.k8s.io", "ClusterRole"): ("clusterroles.rbac.authorization.k8s.io", False),
    ("rbac.authorization.k8s.io", "ClusterRoleBinding"): ("clusterrolebindings.rbac.authorization.k8s.io", False),
    ("rbac.authorization.k8s.io", "Role"): ("roles.rbac.authorization.k8s.io", True),
    ("rbac.authorization.k8s.io", "RoleBinding"): ("rolebindings.rbac.authorization.k8s.io", True),
}
OPERATOR_WORKLOAD_KINDS = frozenset({"Deployment", "StatefulSet", "DaemonSet", "Job"})
# THE SELECTOR READS (ADR-1095): on a platform move, every object of these kinds
# in every namespace, read-only, `get <resource> -A`, so a selector in ANY
# Application (or none: kube-system's count too) is in the set. The kinds that
# carry a label selector or a pod template (topologySpread, affinity), and the
# admission objects with an `objectSelector`. Each is granted to the verifier's
# ClusterRole (`verifier/manifests/clusterrole.yaml`, held by
# `scripts/tests/test_pin_dryrun_operators.py`); a Forbidden read refuses the
# run, never passes it. No Secret, ever.
SELECTOR_READS: dict[tuple[str, str], str] = {
    ("", "Pod"): "pods",
    ("", "Service"): "services",
    ("admissionregistration.k8s.io", "MutatingWebhookConfiguration"): "mutatingwebhookconfigurations.admissionregistration.k8s.io",
    ("admissionregistration.k8s.io", "ValidatingAdmissionPolicy"): "validatingadmissionpolicies.admissionregistration.k8s.io",
    ("admissionregistration.k8s.io", "ValidatingAdmissionPolicyBinding"): "validatingadmissionpolicybindings.admissionregistration.k8s.io",
    ("admissionregistration.k8s.io", "ValidatingWebhookConfiguration"): "validatingwebhookconfigurations.admissionregistration.k8s.io",
    ("apps", "DaemonSet"): "daemonsets.apps",
    ("apps", "Deployment"): "deployments.apps",
    ("apps", "ReplicaSet"): "replicasets.apps",
    ("apps", "StatefulSet"): "statefulsets.apps",
    ("batch", "CronJob"): "cronjobs.batch",
    ("batch", "Job"): "jobs.batch",
    ("networking.k8s.io", "NetworkPolicy"): "networkpolicies.networking.k8s.io",
    ("policy", "PodDisruptionBudget"): "poddisruptionbudgets.policy",
}
# CRD-backed selector kinds: read when their CRD is served, else no instance can
# exist. kind-yadgar serves neither on 2026-10-31 (no prometheus-operator CRDs).
SELECTOR_CRD_READS: dict[tuple[str, str], str] = {
    ("monitoring.coreos.com", "PodMonitor"): "podmonitors.monitoring.coreos.com",
    ("monitoring.coreos.com", "ServiceMonitor"): "servicemonitors.monitoring.coreos.com",
}
CRD_KIND = "CustomResourceDefinition"
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


def merge_bases(repo: Path, base: str, sha: str, runner: Callable) -> list[str]:
    out = _git(repo, ["merge-base", "--all", base, sha], runner)
    return [line for line in out.decode().split() if line]


def read_at(repo: Path, sha: str, path: str, runner: Callable) -> bytes | None:
    settled_gate = _load("settled_gate")
    try:
        return settled_gate.read_at(repo, sha, path, runner)
    except settled_gate.InfrastructureError as error:
        raise InfrastructureError(str(error)) from error


# ── one parse only ──────────────────────────────────────────────────────────


class DuplicateKey(ValueError):
    pass


def _strict_pairs(pairs: list) -> dict:
    seen: dict = {}
    for key, value in pairs:
        if key in seen:
            raise DuplicateKey(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def strict_json(text: str | bytes):
    """`json.loads` that refuses a duplicate key instead of keeping the last."""
    return json.loads(text, object_pairs_hook=_strict_pairs)


def yaml_ambiguities(text: str) -> list[str]:
    """Every construct in `text` two YAML readers may read apart, or [] when it parses one way only."""
    yaml = _load("yaml")
    problems: list[str] = []
    if text.startswith("\ufeff"):
        problems.append("a byte order mark")
    if "\t" in text:
        problems.append("a tab")
    try:
        tokens = list(yaml.scan(text))
        documents = list(yaml.compose_all(text))
    except yaml.YAMLError as error:
        return [*problems, f"does not parse: {type(error).__name__}"]
    names = {
        yaml.AnchorToken: "an anchor", yaml.AliasToken: "an alias", yaml.TagToken: "an explicit tag",
        yaml.DirectiveToken: "a directive", yaml.DocumentStartToken: "an explicit document marker",
        yaml.DocumentEndToken: "an explicit document marker",
    }  # fmt: skip
    for token in tokens:
        for kind, name in names.items():
            if isinstance(token, kind) and name not in problems:
                problems.append(name)
    if len(documents) != 1:
        problems.append(f"{len(documents)} documents, not 1")

    def walk(node, where: str) -> None:
        if isinstance(node, yaml.MappingNode):
            keys: set = set()
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode):
                    if key.value == "<<" and key.style is None:
                        problems.append(f"a merge key at {where or '<root>'}")
                    if key.value in keys:
                        problems.append(f"duplicate key {key.value!r} at {where or '<root>'}")
                    keys.add(key.value)
                walk(key, where)
                walk(value, f"{where}.{getattr(key, 'value', '?')}".lstrip("."))
        elif isinstance(node, yaml.SequenceNode):
            for i, item in enumerate(node.value):
                walk(item, f"{where}[{i}]")
        elif isinstance(node, yaml.ScalarNode) and node.style is None and AMBIGUOUS_SCALAR.fullmatch(node.value):
            problems.append(f"plain scalar {node.value!r} at {where or '<root>'} reads differently in YAML 1.1 and 1.2")

    for document in documents:
        walk(document, "")
    return problems


def checker_identity(repo: Path, base: str, runner: Callable) -> dict:
    """`{main_sha, files: {path: sha256}}` of the code running, refused unless it is main's byte for byte.

    A SELF-CHECK AND A RECORD, NOT A CONTROL. It catches an honest mix-up (a
    checkout at the wrong sha). A modified checker can skip it and write
    main's hashes. The guard is `find`'s run binding: main's workflow file at
    the default branch's head, which checks out argocd main and runs its code.
    """
    files: dict[str, str] = {}
    for relative in CHECKER_FILES:
        running = (HERE.parent / relative).read_bytes()
        at_main = read_at(repo, base, relative, runner)
        if at_main != running:
            raise InfrastructureError(f"the checker running is not main's: {relative} differs from {base[:12]}")
        files[relative] = hashlib.sha256(running).hexdigest()
    return {"main_sha": base, "files": files}


# ── step 1-3: refusals, before any cluster read ─────────────────────────────


def pin_refusals(
    repo: Path, base: str, sha: str, runner: Callable, platform_of: Callable[[str], str] | None = None
) -> tuple[str | None, str | None, bytes | None, list[str]]:
    """`(pin, target, head application, problems)`. Git and text only, plus the target chart's `platform` version
    (`platform_of`) on a platform move; no render, no cluster."""
    pin_bump = _load("pin_bump")
    changed = changed_paths(repo, base, sha, runner)
    problems = [
        f"{path}: changed by main...{sha[:12]}, outside the three pin files and the five operator Applications "
        f"({', '.join(sorted(PIN_FILES | set(OPERATOR_FILES)))}) and the append-only {ACK_FILE}"
        for path in changed
        if path not in PIN_FILES and path not in OPERATOR_FILES and path != ACK_FILE
    ]
    bases = merge_bases(repo, base, sha, runner)
    if len(bases) != 1:
        problems.append(f"main and the sha have {len(bases)} merge bases, not 1: main...sha is ambiguous")
    main_application = read_at(repo, base, APPLICATION, runner)
    head_application = read_at(repo, sha, APPLICATION, runner)
    if main_application is None or head_application is None:
        raise InfrastructureError(f"{APPLICATION} is absent at main or at the sha")
    for side, text in (("main", main_application), ("the sha", head_application)):
        problems += [f"{APPLICATION} at {side}: {p}; refused, two readers may read it apart" for p in yaml_ambiguities(text.decode())]
    for side, commit in (("main", base), ("the sha", sha)):
        raw = read_at(repo, commit, CHART_PIN, runner)
        try:
            if raw is not None:
                strict_json(raw)
        except DuplicateKey as error:
            problems.append(f"{CHART_PIN} at {side}: {error}; refused, two readers may read it apart")
        except ValueError:
            problems.append(f"{CHART_PIN} at {side}: not JSON")
    if problems and any("two readers" in p for p in problems):
        return None, None, None, problems
    try:
        pin = pin_bump.current_pin(main_application.decode())
        target = pin_bump.current_pin(head_application.decode())
    except pin_bump.Refused as refused:
        return None, None, None, [*problems, f"{APPLICATION}: {refused}"]
    if not RELEASE.fullmatch(target):
        problems.append(f"{APPLICATION}: targetRevision {target!r} at the sha is not a release X.Y.Z")
        return pin, target, None, problems
    if RELEASE.fullmatch(pin) and _version(target) < _version(pin):
        problems.append(f"{APPLICATION}: targetRevision {target} is lower than main's pin {pin}; a downgrade is a human revert")
    if ACK_FILE in changed:
        problems += ack_refusals(repo, base, sha, runner, target)
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
        return pin, target, expected, problems
    try:
        main_parsed, head_parsed = strict_json(main_pin), strict_json(head_pin)
        platform, moved_to = main_parsed.get("platform_version"), head_parsed.get("platform_version")
        # The head's own digest: its shape is checked by `rewrite_chart_pin`, its value by CI's `chart-pin` job.
        digest = head_parsed.get("chart_digest")
    except (ValueError, AttributeError):  # already named above as not JSON; nothing more can be read from it
        return pin, target, expected, problems or [f"{CHART_PIN}: not a JSON object"]
    moved = moved_to != platform
    try:
        rewritten = pin_bump.rewrite_chart_pin(
            main_pin.decode(), target, digest=digest if isinstance(digest, str) else "", platform=moved_to if moved else None
        )
        if head_pin.decode() != rewritten:
            what = f"chart_tag v{target}, chart_digest" + (f" and platform_version {moved_to}" if moved else "")
            problems.append(f"{CHART_PIN}: differs from main's in more than {what}")
    except pin_bump.Refused as refused:
        problems.append(f"{CHART_PIN}: {refused}")
    problems += operator_refusals(repo, base, sha, runner, changed, platform, moved_to if moved else None, target, platform_of)
    return pin, target, expected, problems


def ack_refusals(repo: Path, base: str, sha: str, runner: Callable, target: str) -> list[str]:
    """ADR-1094: a changed `ACK_FILE` only appends. Main's entries are an unchanged prefix of the sha's list (parsed
    JSON), and every new entry is well-formed (`pin_evidence`'s own validator) and names `target`."""
    evidence = _load("pin_evidence")
    texts = [read_at(repo, commit, ACK_FILE, runner) for commit in (base, sha)]
    if texts[1] is None:
        return [f"{ACK_FILE}: deleted by the sha; the file is append-only"]
    for side, text in (("main", texts[0]), ("the sha", texts[1])):
        try:  # one parse only: a duplicate key is refused, as in CHART_PIN
            if text is not None:
                strict_json(text)
        except DuplicateKey as error:
            return [f"{ACK_FILE} at {side}: {error}; refused, two readers may read it apart"]
        except ValueError:
            return [f"{ACK_FILE} at {side}: not JSON; refused"]
    try:
        # The validator refuses a non-list, a malformed or placeholder entry and a repeated target.
        evidence.parse_acknowledgements(texts[1].decode())
        before = [] if texts[0] is None else json.loads(texts[0])
        after = json.loads(texts[1])
        if texts[0] is not None:
            evidence.parse_acknowledgements(texts[0].decode())
    except (evidence.Refused, ValueError) as error:
        return [f"{ACK_FILE}: {error}; refused"]
    if after[: len(before)] != before:
        return [f"{ACK_FILE}: main's entries are not an unchanged prefix of the sha's; the file is append-only"]
    added = after[len(before) :]
    if not added:
        return [f"{ACK_FILE}: changed with no entry appended; the file is append-only"]
    return [
        f"{ACK_FILE}: appended entry targets {entry['target']}, not this pin's {target}"
        for entry in added
        if entry["target"] != target
    ]


def operator_refusals(
    repo: Path, base: str, sha: str, runner: Callable, changed: list[str], platform: str, moved_to: str | None,
    target: str, platform_of: Callable[[str], str] | None,
) -> list[str]:
    """Car C10: the five operator files are untouched, or, on a platform move, a pure version move to `moved_to`.

    For each file the head is main's with only `spec.source.targetRevision` rewritten, byte for byte; main's pinned
    `platform_version` and the head pins `moved_to`, which must be the target chart's `platform`. A values change,
    files that disagree with each other or with `platform_version`, and a file left behind are all refused.
    """
    pin_bump = _load("pin_bump")
    if moved_to is None:
        problems = [f"{path}: changed by main...{sha[:12]} while platform_version stays {platform}" for path in OPERATOR_FILES if path in changed]
        # A target whose chart moved `platform` is a platform move: three files would leave the operators behind.
        try:
            wanted = (platform_of or pin_bump.target_platform)(target)
        except Exception as error:  # noqa: BLE001  (an unreadable chart is a refusal, never a pass)
            return [*problems, f"the target chart {target}'s platform version could not be read: {error}"]
        if wanted != platform:
            problems.append(
                f"{CHART_PIN}: platform_version stays {platform}, but the target chart {target}'s platform is {wanted}; "
                "a platform move moves platform_version and all five operator Applications"
            )
        return problems
    problems = []
    for path in OPERATOR_FILES:
        main_text, head_text = read_at(repo, base, path, runner), read_at(repo, sha, path, runner)
        if main_text is None or head_text is None:
            problems.append(f"{path}: absent at main or at the sha; a platform move moves all five operator Applications")
            continue
        for side, text in (("main", main_text), ("the sha", head_text)):
            problems += [f"{path} at {side}: {p}; refused, two readers may read it apart" for p in yaml_ambiguities(text.decode())]
        try:
            expected, old = pin_bump.rewrite_target_revision(main_text.decode(), moved_to, path)
        except pin_bump.Refused as refused:
            problems.append(f"{path}: {refused}")
            continue
        if old != platform:
            problems.append(f"{path}: main pins platform {old}, not main's platform_version {platform}")
        if head_text.decode() != expected:
            problems.append(f"{path}: differs from main's in more than the one targetRevision line moved to platform_version {moved_to}")
    try:
        wanted = (platform_of or pin_bump.target_platform)(target)
    except Exception as error:  # noqa: BLE001  (an unreadable chart is a refusal, never a pass)
        problems.append(f"the target chart {target}'s platform version could not be read: {error}")
    else:
        if wanted != moved_to:
            problems.append(f"{CHART_PIN}: platform_version {moved_to} is not the target chart {target}'s platform {wanted}")
    return problems


def objects_of(documents: list) -> dict[str, dict]:
    return {object_key(d): d for d in documents if isinstance(d, dict) and d.get("kind")}


def rendered_twice(documents: list) -> list[str]:
    """Every object key a render holds more than once. A dict of objects would silently keep one."""
    seen: set[str] = set()
    twice: set[str] = set()
    for document in documents:
        if isinstance(document, dict) and document.get("kind"):
            key = object_key(document)
            (twice if key in seen else seen).add(key)
    return sorted(twice)


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
# EnvVar.value is `omitempty`: the API server drops a rendered `value: ""`
# (measured in the 0.25.27 render: gateway env[43] and env[44]). Only this
# path. A rendered `false` or `0` is NOT matched to an absent field: on a
# pointer field (`automountServiceAccountToken`) absent means the default.
ENV_VALUE = re.compile(r"(?:^|\.)(?:containers|initContainers)\[\d+\]\.env\[\d+\]\.value$")
# Two more omitempty zero values, measured in the operator renders at platform
# 0.2.17 against kind-yadgar on 2026-10-31: keda's and mariadb-operator's
# `hostNetwork: false` (PodSpec.HostNetwork, a plain bool), and prometheus's
# `subPath: ""` (VolumeMount.SubPath, a plain string). Only these paths.
HOST_NETWORK = re.compile(r"spec\.template\.spec\.hostNetwork")
SUB_PATH = re.compile(r"(?:^|\.)(?:containers|initContainers)\[\d+\]\.volumeMounts\[\d+\]\.subPath$")


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
    if rendered == "" and live is None and (ENV_VALUE.search(path) or SUB_PATH.search(path)):
        return []
    if rendered is False and live is None and HOST_NETWORK.fullmatch(path):
        return []
    if isinstance(rendered, bool) != isinstance(live, bool):
        return [path]
    if rendered == live:
        return []
    if is_quantity_path(path):
        left, right = quantity(rendered), quantity(live)
        if left is not None and left == right:
            return []
    return [path]


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def render_paths(old, new, path: str = "") -> list[str]:
    """Every path where two renders differ, both directions: a field on one side only counts."""
    if isinstance(old, dict) and isinstance(new, dict):
        return [found for key in sorted(set(old) | set(new), key=str) for found in render_paths(old.get(key), new.get(key), _join(path, str(key)))]
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        return [found for i, (a, b) in enumerate(zip(old, new, strict=True)) for found in render_paths(a, b, f"{path}[{i}]")]
    if isinstance(old, bool) != isinstance(new, bool) or old != new:
        return [path or "<root>"]
    return []


def _at(document, path: str):
    node = document
    for part in SEGMENT.findall(path):
        node = node[int(part[1:-1])] if part.startswith("[") else node[part]
    return node


def image_repository(reference: str) -> str:
    """The repository of `repo[:tag][@digest]`."""
    named = reference.partition("@")[0]
    last = named.rsplit("/", 1)[-1]
    return named.rsplit(":", 1)[0] if ":" in last else named


def render_problems(base: dict[str, dict], head: dict[str, dict]) -> list[str]:
    """Step 7: base render against head render, every object, hooks included."""
    problems: list[str] = []
    for key in sorted(base.keys() & head.keys()):
        old, new = base[key], head[key]
        if is_hook(old) != is_hook(new):
            problems.append(f"render diff: {key} hook status changed ({HOOK} {'added' if is_hook(new) else 'removed'} at head)")
        for path in render_paths(old, new):
            # The key carries the kind, so `old` and `new` share it. This clause is
            # the only guard on a hook of a non-workload kind (hooks skip READS).
            if new.get("kind") not in WORKLOAD_KINDS or not IMAGE_PATH.fullmatch(path):
                problems.append(f"render diff outside the image rule: {key} {path}")
                continue
            before, after = _at(old, path), _at(new, path)
            if not isinstance(before, str) or not isinstance(after, str) or image_repository(before) != image_repository(after):
                problems.append(f"render diff: {key} {path} image repository moved")
    return problems


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


def settled_problems(application: dict, pin: str, name: str = APPLICATION_NAME) -> list[str]:
    status = application.get("status") or {}
    problems = []
    sync = (status.get("sync") or {}).get("status")
    health = (status.get("health") or {}).get("status")
    phase = (status.get("operationState") or {}).get("phase")
    if sync != "Synced":
        problems.append(f"not settled: {name} sync status {sync}")
    if health != "Healthy":
        problems.append(f"not settled: {name} health {health}")
    if phase in {"Running", "Terminating"}:
        problems.append(f"not settled: {name} operation {phase}")
    if application.get("operation"):
        problems.append(f"not settled: {name} has an operation pending")
    spec_revision = ((application.get("spec") or {}).get("source") or {}).get("targetRevision")
    if str(spec_revision) != pin:
        problems.append(f"not settled: {name} spec.source.targetRevision {spec_revision}, main pins {pin}")
    synced_revision = (status.get("sync") or {}).get("revision")
    if str(synced_revision) != pin:
        problems.append(f"not settled: {name} status.sync.revision {synced_revision}, main pins {pin}")
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


# ── step 9: the operator Applications on a platform move (car C10 part 2) ──


def default_operator_render(text: str, version: str) -> list:
    """One operator Application at platform `version` on the `helm` on PATH (the workflow's v3.18.4)."""
    helm = shutil.which("helm")
    if helm is None:
        raise InfrastructureError("no helm on PATH to render the operator Applications")
    return _load("pin_bump").render_operator(text, version, helm)


def platform_versions(repo: Path, base: str, sha: str, runner: Callable) -> tuple[str, str]:
    """`(main's platform_version, the sha's)`, both already checked by step 1."""
    main_pin, head_pin = (strict_json(read_at(repo, commit, CHART_PIN, runner) or b"{}") for commit in (base, sha))
    return str(main_pin.get("platform_version")), str(head_pin.get("platform_version"))


def operator_renders(
    repo: Path, base: str, runner: Callable, old: str, new: str, render_operator: Callable[[str, str], list]
) -> tuple[dict[str, dict], list[str]]:
    """`({Application: {namespace, base, head}}, refusals)` from MAIN's five operator files; no cluster read."""
    pin_bump = _load("pin_bump")
    operators: dict[str, dict] = {}
    problems: list[str] = []
    for path in OPERATOR_FILES:
        text = (read_at(repo, base, path, runner) or b"").decode()
        try:
            name, namespace, _ = pin_bump.operator_inputs(text)
            sides = {side: render_operator(text, version) for side, version in (("base", old), ("head", new))}
        except pin_bump.HelmInfrastructure as error:
            # A registry or network failure judges nothing: no result (exit 3), so auto-pin dispatches again.
            raise InfrastructureError(f"{path}: the operator render hit an infrastructure failure: {error}") from error
        except pin_bump.Refused as refused:
            problems.append(f"{path}: the operator render failed: {refused}")
            continue
        for side, documents in sides.items():
            problems += [f"{name}: {key} rendered twice at the {side} platform; refused" for key in rendered_twice(documents)]
        operators[name] = {"namespace": namespace, **{side: objects_of(documents) for side, documents in sides.items()}}
        for key, document in sorted({**operators[name]["base"], **operators[name]["head"]}.items()):
            if is_hook(document):
                continue
            if document.get("kind") == "Secret":
                problems.append(f"{name}: {key}: the render holds a Secret, which this check never reads")
            elif group_kind(document) not in OPERATOR_READS:
                problems.append(f"{name}: {key}: kind not in pin_dryrun.OPERATOR_READS; extend the table (and the runner's ClusterRole) first")
    return operators, problems


def live_namespace(document: dict, destination: str) -> str:
    """Where Argo applies a namespaced object: its rendered namespace, else the Application's destination."""
    return str((document.get("metadata") or {}).get("namespace") or destination)


def operator_live(cluster, operators: dict[str, dict]) -> tuple[dict[str, dict], dict[tuple[str, str], dict]]:
    """`({Application: its live Application}, {(Application, key): live object})`, one list per kind and namespace."""
    applications = {name: cluster.json("get", "applications.argoproj.io", name, "-n", ARGOCD_NAMESPACE) for name in sorted(operators)}
    wanted: dict[tuple[str, str], dict[str, tuple[str, str]]] = {}
    for name, operator in sorted(operators.items()):
        for key, document in {**operator["base"], **operator["head"]}.items():
            if is_hook(document):
                continue
            resource, namespaced = OPERATOR_READS[group_kind(document)]
            namespace = live_namespace(document, operator["namespace"]) if namespaced else ""
            wanted.setdefault((resource, namespace), {})[str((document.get("metadata") or {}).get("name"))] = (name, key)
    found: dict[tuple[str, str], dict] = {}
    for (resource, namespace), names in sorted(wanted.items()):
        args = ["get", resource, "-n", namespace] if namespace else ["get", resource]
        for item in cluster.json(*args).get("items") or []:
            name = (item.get("metadata") or {}).get("name")
            if name in names:
                found[names[name]] = item
    return applications, found


def operator_owner_problem(name: str, operator: dict, application: dict, key: str, document: dict, live: dict) -> str | None:
    """The live object belongs to Application `name`: its tracking-id, or, for a CRD, `status.resources`."""
    group, kind = group_kind(document)
    object_name = (document.get("metadata") or {}).get("name")
    if kind == CRD_KIND:
        listed = {
            (r.get("group"), r.get("kind"), r.get("name"))
            for r in (application.get("status") or {}).get("resources") or []
            if isinstance(r, dict)
        }
        if (group, kind, object_name) not in listed:
            return f"baseline not empty: {name}: {key} is not in {name}'s status.resources (a CRD carries no tracking-id)"
        return None
    namespaced = OPERATOR_READS[(group, kind)][1]
    destination = operator["namespace"]
    namespace = live_namespace(document, destination) if namespaced else destination
    wanted = {f"{name}:{group}/{kind}:{namespace}/{object_name}"}
    if not namespaced:
        wanted.add(f"{name}:{group}/{kind}:/{object_name}")
    found = ((live.get("metadata") or {}).get("annotations") or {}).get(TRACKING_ID)
    if found not in wanted:
        return f"baseline not empty: {name}: {key} tracking-id does not name {name}'s {group}/{kind} {object_name}"
    return None


def live_selectors(cluster) -> tuple[dict[str, set[str]], list[str], list[str]]:
    """`({Application or "untracked": selector keys}, refusals, absent CRD-backed kinds)`, every namespace (ADR-1095).

    An object is keyed by the Application its tracking-id names. Its `status` and `metadata.managedFields` are no
    selector and are not read. A kind the verifier cannot list is a refusal naming the grant it needs.
    """
    verify_handover = _load("verify_handover")
    render_selector_keys = _load("pin_bump").render_selector_keys
    served = {(i.get("metadata") or {}).get("name") for i in cluster.json("get", OPERATOR_READS[("apiextensions.k8s.io", CRD_KIND)][0]).get("items") or []}
    keys: dict[str, set[str]] = {}
    refusals: list[str] = []
    absent = sorted(resource for resource in SELECTOR_CRD_READS.values() if resource not in served)
    for resource in sorted({*SELECTOR_READS.values(), *SELECTOR_CRD_READS.values()} - set(absent)):
        try:
            items = cluster.json("get", resource, "-A").get("items") or []
        except verify_handover.KubectlError as error:
            refusals.append(
                f"selectors: cannot list {resource} across all namespaces ({error}); grant get and list on it to the "
                f"verifier's ClusterRole, ADR-1095 leaves no selector out"
            )
            continue
        for item in items:
            metadata = {k: v for k, v in (item.get("metadata") or {}).items() if k != "managedFields"}
            declared = {**{k: v for k, v in item.items() if k != "status"}, "metadata": metadata}
            owner = str((metadata.get("annotations") or {}).get(TRACKING_ID) or "").split(":", 1)[0] or "untracked"
            keys.setdefault(owner, set()).update(render_selector_keys([declared]))
    return keys, refusals, absent


def shape_problem(name: str, key: str, document: dict, path: str, before, after, selected: set[str]) -> str | None:
    """One changed path of an operator object against the platform-move shape (step 9). None when inside it."""
    pin_bump = _load("pin_bump")
    kind = document.get("kind")
    if kind in OPERATOR_WORKLOAD_KINDS and IMAGE_PATH.fullmatch(path):
        if not isinstance(before, str) or not isinstance(after, str) or image_repository(before) != image_repository(after):
            return f"{name}: {key} {path} image repository moved"
        return None
    for label in sorted(pin_bump.DERIVED_LABELS):
        if path in (f"metadata.labels.{label}", f"spec.template.metadata.labels.{label}"):
            return f"{name}: {key} {path} changed inside a selector" if label in selected else None
    if kind == CRD_KIND and (path == "spec.versions" or path.startswith(("spec.versions.", "spec.versions["))):
        return None
    return f"{name}: {key} {path} changed; outside the platform-move shape"


def _value(document, path: str):
    try:
        return _at(document, path)
    except (KeyError, IndexError, TypeError):
        return None


def operator_judgment(
    operators: dict[str, dict], applications: dict[str, dict], live: dict[tuple[str, str], dict], platform: str, selected: set[str]
) -> tuple[list[str], list[str], list[dict], int, list[str]]:
    """`(problems, baseline, head diff objects, compared, hooks left out)` over every operator Application."""
    problems: list[str] = []
    baseline: list[str] = []
    objects: list[dict] = []
    hooks: list[str] = []
    compared = 0
    for name, operator in sorted(operators.items()):
        base, head = operator["base"], operator["head"]
        problems += settled_problems(applications[name], platform, name)
        problems += [f"{name}: {key}: added at head (the object set moved)" for key in sorted(head.keys() - base.keys())]
        problems += [f"{name}: {key}: removed at head (the object set moved)" for key in sorted(base.keys() - head.keys())]
        for key in sorted(base.keys() & head.keys()):
            old, new = base[key], head[key]
            if is_hook(old) != is_hook(new):
                problems.append(f"{name}: render diff: {key} hook status changed ({HOOK} {'added' if is_hook(new) else 'removed'} at head)")
            for path in render_paths(old, new):
                if (problem := shape_problem(name, key, new, path, _value(old, path), _value(new, path), selected)) is not None:
                    problems.append(f"render diff: {problem}")
        hooks += sorted(k for k, d in {**base, **head}.items() if is_hook(d))
        for key, document in sorted(base.items()):
            if is_hook(document):
                continue
            compared += 1
            found = live.get((name, key))
            if found is None:
                baseline.append(f"baseline not empty: {name}: {key} not live")
                continue
            if (problem := operator_owner_problem(name, operator, applications[name], key, document, found)) is not None:
                baseline.append(problem)
            baseline += [f"baseline not empty: {name}: {key} {path}" for path in differences(document, found)]
        for key, document in sorted(head.items()):
            found = live.get((name, key))
            if is_hook(document) or found is None:
                continue
            paths = differences(document, found)
            if not paths:
                continue
            objects.append({"application": name, "key": key, "paths": paths})
            for path in paths:
                if (problem := shape_problem(name, key, document, path, _value(found, path), _value(document, path), selected)) is not None:
                    problems.append(f"head diff: {problem}")
    return problems, baseline, objects, compared, hooks


# ── run ─────────────────────────────────────────────────────────────────────


def dry_run(
    repo: Path, base: str, sha: str, cluster, *, render: Callable[[bytes], list] | None = None,
    runner: Callable = subprocess.run, platform_of: Callable[[str], str] | None = None,
    render_operator: Callable[[str, str], list] | None = None,
) -> dict:
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
        "checker": checker_identity(repo, base, runner),
    }
    pin, target, head_application, problems = pin_refusals(repo, base, sha, runner, platform_of)
    result.update(pin=pin, target=target)
    if problems or head_application is None:
        return {**result, "problems": problems}
    # Step 1 has proved the operator files untouched, or a pure version move of all five: then step 9 runs too.
    platform_moved = any(read_at(repo, base, path, runner) != read_at(repo, sha, path, runner) for path in OPERATOR_FILES)

    settled_gate = _load("settled_gate")
    render = render or settled_gate.default_render
    main_application = read_at(repo, base, APPLICATION, runner)
    try:
        base_documents = render(main_application)
        head_documents = render(head_application)
    except settled_gate.InfrastructureError as error:
        raise InfrastructureError(str(error)) from error
    problems = [f"{key}: rendered twice at {side}; refused" for side, docs in (("main", base_documents), ("the head", head_documents)) for key in rendered_twice(docs)]
    if problems:
        return {**result, "problems": problems}
    base_objects, head_objects = objects_of(base_documents), objects_of(head_documents)
    try:
        problems = render_refusals(base_objects, head_objects, head_documents, read_at(repo, sha, TABLE, runner), target)
    except AssertionError as error:  # a gate helper's assert; never an exit-1 traceback
        raise InfrastructureError(f"a gate helper failed: {error}") from error
    if problems:
        return {**result, "problems": problems}

    problems = [f"{key}: added at head (the object set moved)" for key in sorted(head_objects.keys() - base_objects.keys())]
    problems += [f"{key}: removed at head (the object set moved)" for key in sorted(base_objects.keys() - head_objects.keys())]
    problems += render_problems(base_objects, head_objects)
    hooks = sorted(k for k, d in {**base_objects, **head_objects}.items() if is_hook(d))
    base_steady = {k: d for k, d in base_objects.items() if not is_hook(d)}
    head_steady = {k: d for k, d in head_objects.items() if not is_hook(d)}

    operators: dict[str, dict] = {}
    if platform_moved:
        platform, moved_to = platform_versions(repo, base, sha, runner)
        operators, refusals = operator_renders(repo, base, runner, platform, moved_to, render_operator or default_operator_render)
        if refusals:
            return {**result, "problems": refusals}

    verify_handover = _load("verify_handover")
    try:
        application = cluster.json("get", "applications.argoproj.io", APPLICATION_NAME, "-n", ARGOCD_NAMESPACE)
        live = live_objects(cluster, {**base_steady, **head_steady})
        operator_applications, operators_live = operator_live(cluster, operators) if operators else ({}, {})
        seen_admission = admission(cluster)
        estate, refusals, absent = live_selectors(cluster) if operators else ({}, [], [])
    except (verify_handover.KubectlError, verify_handover.UsageError, ValueError) as error:
        raise InfrastructureError(f"a cluster read failed: {error}") from error
    if refusals:
        return {**result, "problems": refusals}

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
    compared = len(base_steady)
    selectors = {}
    if operators:
        # ADR-1095: one selector set, the renders at both pins and every live object of every Application.
        selected = _load("pin_bump").render_selector_keys(
            [*base_documents, *head_documents, *(d for o in operators.values() for side in ("base", "head") for d in o[side].values())]
        )
        selected |= {key for found in estate.values() for key in found}
        selectors = {"selectors": {"live": {name: sorted(found) for name, found in sorted(estate.items())}, "absent": absent}}
        found, found_baseline, found_objects, found_compared, found_hooks = operator_judgment(
            operators, operator_applications, operators_live, platform, selected
        )
        problems += found
        baseline += found_baseline
        objects += found_objects
        compared += found_compared
        hooks = sorted({*hooks, *found_hooks})
    problems += baseline
    return {
        **result,
        "result": "red" if problems else "green",
        "problems": problems,
        "baseline": baseline,
        "objects": objects,
        "hooks_left_out": hooks,
        "compared": compared,
        "admission": seen_admission,
        **selectors,
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


def branch_head(github, repository: str, branch: str) -> str:
    """The commit sha a branch points at, read from the branches API (never resolved by name)."""
    head = ((github.get(f"/repos/{repository}/branches/{urllib.parse.quote(branch, safe='')}") or {}).get("commit") or {}).get("sha")
    if not isinstance(head, str) or not FULL_SHA.fullmatch(head):
        raise InfrastructureError(f"/repos/{repository}/branches/{branch} carries no commit sha")
    return head


def on_default_branch(github, repository: str, sha: object, branch_sha: str) -> bool:
    """`sha` is the default branch's head commit or an ancestor of it.

    Compared against the head COMMIT, never the branch name: `compare/main...X`
    resolves `refs/tags/main` first when a tag of that name exists. Never
    `commits/{sha}`.
    """
    if not isinstance(sha, str) or not FULL_SHA.fullmatch(sha):
        return False
    try:
        status = github.get(f"/repos/{repository}/compare/{branch_sha}...{sha}").get("status")
    except NotFound:
        return False
    return status in {"behind", "identical"}


def _result_of(archive: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as opened:
            return strict_json(opened.read(RESULT_FILE))
    except (zipfile.BadZipFile, KeyError, ValueError) as error:
        raise InfrastructureError(f"a {ARTIFACT} artifact holds no readable {RESULT_FILE}: {error}") from error


def _parse_time(value) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def verdict_of(run: dict, result: dict, *, main_sha: str, pin: str, now: dt.datetime, workflow_head: str | None = None) -> tuple[str, str]:
    """`(pending | red | stale | green, reason)` of the newest trusted result. Values here are shas, pins and times only."""
    if run.get("status") != "completed":
        return "pending", f"run {run.get('id')} is {run.get('status')}"
    outcome = result.get("result")
    if outcome != "green":
        return "red", f"result {outcome}"
    if run.get("conclusion") != "success":
        return "red", f"run {run.get('id')} conclusion {run.get('conclusion')}, not success"
    if result.get("main_sha") != main_sha:
        return "stale", f"judged against main {result.get('main_sha')}, main is {main_sha}"
    if result.get("pin") != pin:
        return "stale", f"judged against pin {result.get('pin')}, main pins {pin}"
    if not isinstance(result.get("checker"), dict) or result["checker"].get("main_sha") != main_sha:
        return "stale", f"checker {(result.get('checker') or {}).get('main_sha') if isinstance(result.get('checker'), dict) else None}, not main's {main_sha}"
    if workflow_head is not None and run.get("head_sha") != workflow_head:
        return "stale", f"run {run.get('id')} ran the workflow at {run.get('head_sha')}, the default branch is now at {workflow_head}"
    read = _parse_time(result.get("read_at"))
    if read is None:
        return "stale", "no read_at"
    age = (now - read).total_seconds()
    if age > FRESH_SECONDS:
        return "stale", f"read {int(age)} s ago, past FRESH_SECONDS {FRESH_SECONDS}"
    if age < -FUTURE_SKEW_SECONDS:
        return "stale", f"read_at {int(-age)} s in the future"
    return "green", f"run {run.get('id')} green, read {int(age)} s ago"


def find(github, repository: str, workflow_path: str, sha: str, *, main_sha: str, pin: str, now: dt.datetime | None = None) -> dict | None:
    """The newest trusted `pin-dryrun` result for `sha` with its verdict, or None."""
    now = now or dt.datetime.now(dt.timezone.utc)
    own = github.get(f"/repos/{repository}")
    if "id" not in own or not own.get("default_branch"):
        raise InfrastructureError(f"/repos/{repository} carries no id or default_branch")
    default_head = branch_head(github, repository, own["default_branch"])
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
            run.get("event") != "workflow_dispatch"
            or run.get("path") != workflow_path
            or run.get("head_branch") != own["default_branch"]
            or (run.get("repository") or {}).get("id") != own["id"]
            or (run.get("head_repository") or {}).get("id") != own["id"]
            or not on_default_branch(github, repository, run.get("head_sha"), default_head)
        ):
            continue
        result = _result_of(github.download(candidate["archive_download_url"]))
        if result.get("sha") != sha:
            continue
        verdict, reason = verdict_of(run, result, main_sha=main_sha, pin=pin, now=now, workflow_head=default_head)
        return {
            "found": True,
            "verdict": verdict,
            "reason": reason,
            "run_id": run["id"],
            "artifact_id": candidate["id"],
            "created_at": candidate.get("created_at"),
            "result": result,
        }
    return None


# ── CLI ─────────────────────────────────────────────────────────────────────


def _full_sha(value: str) -> str:
    if not FULL_SHA.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a full 40-hex commit sha")
    return value


def _release(value: str) -> str:
    if not RELEASE.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a release X.Y.Z")
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
    found.add_argument("--main-sha", type=_full_sha, required=True, help="argocd main's sha now; a result judged against another is stale")
    found.add_argument("--pin", type=_release, required=True, help="argocd main's pin now; a result judged against another is stale")
    found.add_argument("--out", type=Path, required=True)
    return parser


def _summary(result: dict) -> None:
    print(f"pin-dryrun {result['result']} (interim): sha {result['sha']} pin {result['pin']} -> {result['target']}, {result['compared']} objects compared")
    # repr(): a key, path or name holding a newline must not forge a line of the step summary.
    for item in result["objects"]:
        print(f"head diff: {item['key']!r} {', '.join(repr(p) for p in item['paths'])}")
    for problem in result["problems"]:
        print(f"{result['result'].upper()}: {problem!r}")
    for which, names in result["admission"].items():
        print(f"not run, {which}: {', '.join(repr(n) for n in names) or 'none'}")


def main(
    argv: list[str] | None = None,
    *,
    render: Callable[[bytes], list] | None = None,
    runner: Callable = subprocess.run,
    kube_runner: Callable = subprocess.run,
    github=None,
    now: dt.datetime | None = None,
) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exit_:
        return 2 if exit_.code else 0
    if args.command == "find":
        try:
            found = find(github or GitHub(), args.repository, args.workflow_path, args.sha, main_sha=args.main_sha, pin=args.pin, now=now)
        except InfrastructureError as error:
            print(f"INFRASTRUCTURE FAILURE: {error}", file=sys.stderr)
            return 3
        args.out.write_text(json.dumps(found or {"found": False}, indent=2, sort_keys=True) + "\n")
        verdict = (found or {}).get("verdict")
        print(f"pin-dryrun for {args.sha}: " + (f"{verdict} ({found['reason']}), run {found['run_id']}" if found else "none"))
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
