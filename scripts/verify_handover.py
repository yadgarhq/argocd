#!/usr/bin/env python3
"""Read-only post-merge verifier for what `root` deploys.

It replaces the per-merge scratch scripts the operators handover (ADR-0824) ran
by hand: snapshot, merge, wait for root to sync the merge, diff. One generic
script instead of one per merge.

    verify_handover.py snapshot --context CTX --out FILE [options]
    verify_handover.py wait     --context CTX --app NAME (--since T | --revision SHA)
    verify_handover.py wait     --context CTX --settled --since T
    verify_handover.py diff     BEFORE AFTER

THE ORDER IS wait, then settle, then snapshot, then diff. A snapshot taken
before root has synced the merge captures the old state and the diff compares
nothing that changed.

READ-ONLY BY CONSTRUCTION. Every kubectl call goes through `kubectl()`. It
refuses to run without an explicit `--context` (there is no default, because the
default context on the operator's machine is production), refuses every verb but
`get`, accepts only the flags -n/--namespace, -A, one -o and --no-headers, requires
`-o json`, and allows exactly one Secret argv: name, uid and resourceVersion columns.

WHAT `diff` FAILS ON (exit 1):
  - a changed or vanished uid: Application, CRD, custom resource, tracked
    object, workload, pod, PVC, Secret;
  - a generation change on a Deployment or a CRD;
  - any deletionTimestamp;
  - a changed Application finalizer list (a finalizer makes a prune cascade);
  - an Application not Healthy, an automated one not Synced, a manual one that
    went from Synced to OutOfSync, or a last operation that Failed or Errored;
  - an Application root declares that does not exist;
  - a Secret resourceVersion change (only when both snapshots read Secrets);
  - an edge probe that is not the expected status over a verified TLS session.

A NON-DESTRUCTIVE FAIL ON AN OBJECT ANOTHER APPLICATION OWNS IS A WARN (see
`_classify`). A custom resource or workload that vanishes or gets a
deletionTimestamp fails whoever owns it. An object root owns in EITHER snapshot
is root's. Hook resources are never compared.

WHAT IT ONLY WARNS ON (exit 0): a syncPolicy change, a restart-count increase, a
generation change outside Deployments and CRDs, and root pruning something on a
new revision. Additions are INFO.

`diff` REFUSES (exit 2) two snapshots from different clusters (the kube-system
namespace uid differs), an unknown schema, and a snapshot with no Applications
or no CRDs. A comparison of nothing is not a pass.

Standard library only: the pre-commit hook that runs the tests installs pytest
and PyYAML and nothing else.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable
from urllib.parse import urlsplit

SCHEMA = 2

# Pods that come and go by design: hook and Job pods, and ARC runner pods (the
# verifier's own runner among them).
EPHEMERAL_POD_OWNERS = frozenset({"Job", "EphemeralRunner"})
FINISHED_POD_PHASES = frozenset({"Succeeded", "Failed"})
WORKLOAD_LIST = "deployments.apps,statefulsets.apps,daemonsets.apps,persistentvolumeclaims,pods"
SECRET_COLUMNS = "custom-columns=NAME:.metadata.name,UID:.metadata.uid,RV:.metadata.resourceVersion"
# CRDs whose instances `collect` never lists. cert-manager's issuance records are
# created and garbage-collected per renewal, as Job pods are per hook; and
# Applications are compared in their own section, where an operation's
# generation bump is not a finding.
TRACKING_ID = "argocd.argoproj.io/tracking-id"
UNLISTED_INSTANCE_CRDS = frozenset(
    {
        "certificaterequests.cert-manager.io",
        "orders.acme.cert-manager.io",
        "challenges.acme.cert-manager.io",
        "applications.argoproj.io",
        # ARC's per-job objects. Once the runner Application lands under root,
        # the controller creates and deletes these on every poll; their uids
        # always change. The AutoscalingRunnerSet itself stays compared.
        "ephemeralrunners.actions.github.com",
        "ephemeralrunnersets.actions.github.com",
        "autoscalinglisteners.actions.github.com",
    }
)


class UsageError(Exception):
    """A call this tool refuses to make."""


class KubectlError(Exception):
    """kubectl exited non-zero. Never read as an empty result."""


class SnapshotError(Exception):
    """A snapshot that cannot be taken or compared honestly."""


# --- the one kubectl function -------------------------------------------------


def _is_secret_resource(token: str) -> bool:
    for part in token.split(","):
        name = part.split("/")[0].lower()
        if name in {"secret", "secrets"} or name.startswith(("secret.", "secrets.")):
            return True
    return False


# THE ARGUMENT ALLOWLIST. Anything else starting with `-` is refused, so a
# kubectl flag that selects a target, a credential or a template cannot slip
# through a denylist. `--context` is added by this tool and is never accepted
# from the caller.
VALUE_FLAGS = frozenset({"-n", "--namespace", "-o", "--output"})
BOOL_FLAGS = frozenset({"-A", "--all-namespaces", "--no-headers"})


def _parse(rest: list[str]) -> tuple[list[str], list[str], list[str]]:
    """(positionals, namespaces, outputs) of the arguments after `get`. Refuses any flag outside the allowlist."""
    positionals: list[str] = []
    namespaces: list[str] = []
    outputs: list[str] = []
    index = 0
    while index < len(rest):
        arg = rest[index]
        if not arg.startswith("-"):
            positionals.append(arg)
        else:
            name, equals, value = arg.partition("=")
            if name in VALUE_FLAGS:
                if not equals:
                    index += 1
                    value = rest[index] if index < len(rest) else ""
                if not value or value.startswith("-"):
                    raise UsageError(f"{name} needs a value, not {value!r}")
                (outputs if name in {"-o", "--output"} else namespaces).append(value)
            elif arg not in BOOL_FLAGS:
                raise UsageError(
                    f"flag {arg!r} is not allowed: only -n/--namespace, -A/--all-namespaces, -o and --no-headers are."
                    " --context is set by this tool, and nothing else may select the target or the credential"
                )
        index += 1
    return positionals, namespaces, outputs


def _check_args(context: str | None, args: list[str]) -> None:
    if not isinstance(context, str) or not context.strip():
        raise UsageError("an explicit --context is required; this tool never uses kubectl's default context")
    if not args or args[0] != "get":
        raise UsageError(f"read-only: only `get` is allowed, not {args[:1]!r}")
    positionals, namespaces, outputs = _parse(args[1:])
    if len(outputs) > 1:
        raise UsageError(f"repeated output flag {outputs!r}: kubectl uses the last one, so only one is allowed")
    if any(_is_secret_resource(p) for p in positionals):
        # ONE Secret argv, exactly. A custom-columns path such as
        # `.metadata.annotations` returns `last-applied-configuration`, which
        # holds the whole Secret, data included (measured live).
        if not (len(namespaces) == 1 and args == ["get", "secrets", "-n", namespaces[0], "-o", SECRET_COLUMNS, "--no-headers"]):
            raise UsageError(
                f"a Secret may be read only as `get secrets -n NS -o {SECRET_COLUMNS} --no-headers`, never its data"
            )
        return
    if outputs != ["json"]:
        raise UsageError(f"output must be `-o json` outside the one Secret metadata read, not {outputs!r}")


def kubectl(context: str | None, args: list[str], *, runner: Callable = subprocess.run) -> str:
    """Run `kubectl --context CONTEXT <args>` and return stdout. Refuses before running anything.

    THE GATE CHECKS ARGV ONLY. The environment kubectl inherits (KUBECONFIG,
    PATH, and so which `kubectl` binary runs) is the caller's: in CI the
    workflow writes it, and on a workstation the operator owns it.
    """
    _check_args(context, list(args))
    cmd = ["kubectl", "--context", context, *args]
    proc = runner(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise KubectlError(f"`kubectl {' '.join(args)}` exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}")
    return proc.stdout


class Cluster:
    """A kubectl target. Holds the context and the runner; every call goes through `kubectl()`."""

    def __init__(self, context: str | None, *, runner: Callable = subprocess.run) -> None:
        if not isinstance(context, str) or not context.strip():
            raise UsageError("an explicit --context is required; this tool never uses kubectl's default context")
        self.context = context
        self.runner = runner

    def json(self, *args: str) -> dict:
        return json.loads(kubectl(self.context, [*args, "-o", "json"], runner=self.runner))

    def text(self, *args: str) -> str:
        return kubectl(self.context, list(args), runner=self.runner)


# --- snapshot -----------------------------------------------------------------


def _meta(obj: dict) -> dict:
    return obj.get("metadata") or {}


def _identity(obj: dict) -> dict:
    meta = _meta(obj)
    return {"uid": meta.get("uid"), "generation": meta.get("generation"), "deletionTimestamp": meta.get("deletionTimestamp")}


def _ownership(obj: dict, kind: str | None = None) -> dict:
    """Working fields `_classify` reads and then removes: who tracks the object, and who owns it."""
    meta = _meta(obj)
    tracking = str((meta.get("annotations") or {}).get(TRACKING_ID) or "").split(":", 1)[0] or None
    return {
        "_kind": kind or obj.get("kind"),
        "_ns": meta.get("namespace") or "",
        "_name": meta.get("name"),
        "_tracking": tracking,
        "_owners": [(o.get("kind"), o.get("name")) for o in meta.get("ownerReferences") or []],
    }


def _classify(sections: list[tuple[dict, str]], root_apps: set[str]) -> None:
    """Give every record a `scope`: `root`, or `foreign:<why>`. `diff` lowers a foreign FAIL to WARN.

    An Argo tracking-id names the Application that owns an object. With none,
    the object takes the scope of its owner, following ownerReferences
    (a ReplicaSet stands for the Deployment its name extends). With neither, it
    takes its section's default: `root` for workloads and pods in a root
    Application's namespace, `foreign:untracked` for custom resources listed
    cluster-wide. An owner that is not in the snapshot also falls to the
    default.
    """
    index = {(r["_kind"], r["_ns"], r["_name"]): r for records, _default in sections for r in records.values()}

    def scope(record: dict, default: str, depth: int = 0) -> str:
        if record.get("_tracking"):
            return "root" if record["_tracking"] in root_apps else f"foreign:{record['_tracking']}"
        for kind, name in record.get("_owners") or []:
            if kind == "ReplicaSet" and "-" in (name or ""):
                kind, name = "Deployment", name.rsplit("-", 1)[0]
            owner = index.get((kind, record["_ns"], name)) or index.get((kind, "", name))
            if owner is not None and owner is not record and depth < 8:
                return scope(owner, default, depth + 1)
        return default

    scopes = {id(r): scope(r, default) for records, default in sections for r in records.values()}
    for records, _default in sections:
        for record in records.values():
            for key in [k for k in record if k.startswith("_")]:
                del record[key]
            record["scope"] = scopes[id(record)]


def _operation(app: dict) -> dict | None:
    op = (app.get("status") or {}).get("operationState")
    if not op:
        return None
    return {
        "phase": op.get("phase"),
        "startedAt": op.get("startedAt"),
        "finishedAt": op.get("finishedAt"),
        "revision": (op.get("syncResult") or {}).get("revision"),
    }


def _resource_key(resource: dict) -> str:
    return "/".join([resource.get("group") or "", resource["kind"], resource.get("namespace") or "", resource["name"]])


def _application_record(app: dict) -> dict:
    spec, status = app.get("spec") or {}, app.get("status") or {}
    sync = status.get("sync") or {}
    sync_policy = spec.get("syncPolicy") or {}
    return {
        "name": _meta(app)["name"],
        "uid": _meta(app).get("uid"),
        "finalizers": sorted(_meta(app).get("finalizers") or []),
        "syncPolicy": sync_policy,
        "automated": bool(sync_policy.get("automated")),
        "sync": sync.get("status"),
        "revision": sync.get("revision") if sync.get("revision") is not None else sync.get("revisions"),
        "health": (status.get("health") or {}).get("status"),
        "operation": _operation(app),
        "reconciledAt": status.get("reconciledAt"),
        "destinationNamespace": (spec.get("destination") or {}).get("namespace"),
        # A hook is deleted and recreated on every sync (BeforeHookCreation), so
        # its uid always changes. It is neither listed nor compared.
        "resources": sorted(_resource_key(r) for r in status.get("resources") or [] if not r.get("hook")),
    }


def read_applications(cluster: Cluster, root: str = "root", namespace: str = "argocd") -> tuple[dict, list[str], dict]:
    """(records of every Application under root, root included; the declared child names; root's raw object).

    Under root means: an Application root itself tracks, or an Application
    owned by an ApplicationSet root tracks. Nothing else, so the deploy-era
    Applications (`infra` and what it owns) are out of scope.
    """
    items = cluster.json("get", "applications.argoproj.io", "-n", namespace).get("items") or []
    by_name = {_meta(a).get("name"): a for a in items}
    if root not in by_name:
        raise SnapshotError(f"root Application {root!r} not found in namespace {namespace!r}")
    root_app = by_name[root]
    tracked = (root_app.get("status") or {}).get("resources") or []
    children = {r["name"] for r in tracked if r.get("kind") == "Application" and r.get("group") == "argoproj.io"}
    appsets = {r["name"] for r in tracked if r.get("kind") == "ApplicationSet"}
    generated = {
        name
        for name, app in by_name.items()
        if any(o.get("kind") == "ApplicationSet" and o.get("name") in appsets for o in _meta(app).get("ownerReferences") or [])
    }
    declared = sorted((children | generated) - {root})
    if not declared:
        raise SnapshotError(f"root Application {root!r} tracks no Application; refusing a snapshot of nothing")
    records = {name: _application_record(by_name[name]) for name in [root, *declared] if name in by_name}
    return records, declared, root_app


def _root_operation(root_app: dict) -> dict:
    op = (root_app.get("status") or {}).get("operationState") or {}
    resources = (op.get("syncResult") or {}).get("resources") or []
    pruned = sorted(
        f"{r.get('group') or ''}/{r.get('kind')}/{r.get('name')}"
        for r in resources
        if r.get("status") in {"Pruned", "PruneSkipped"} or "prun" in (r.get("message") or "").lower()
    )
    return {**(_operation(root_app) or {}), "pruned": pruned}


def _tracked_objects(cluster: Cluster, records: dict) -> dict:
    wanted: dict[tuple[str, str], set[str]] = {}
    objects: dict[str, dict] = {}
    for record in records.values():
        for key in record["resources"]:
            group, kind, _namespace, _name = key.split("/", 3)
            if kind in {"Application", "CustomResourceDefinition"}:
                continue  # covered by the Application and CRD sections
            if kind == "Secret" and group == "":
                objects[key] = {"skipped": "Secret"}  # never read; see the module docstring
                continue
            wanted.setdefault((group, kind), set()).add(key)
    for (group, kind), keys in sorted(wanted.items()):
        resource = f"{kind}.{group}" if group else kind
        live = {}
        for item in cluster.json("get", resource, "-A").get("items") or []:
            meta = _meta(item)
            live[f"{group}/{kind}/{meta.get('namespace') or ''}/{meta.get('name')}"] = _identity(item)
        for key in sorted(keys):
            objects[key] = live.get(key, {"missing": True})
    return objects


def _custom_resources(cluster: Cluster, records: dict, crds: dict) -> dict:
    """uid, generation and deletionTimestamp of every instance of every CRD an Application under root tracks.

    This is what a CRD swap destroys when it goes wrong: deleting a CRD cascades
    to its instances, and they show a deletionTimestamp first. A tracked CRD
    absent from the cluster is not listed; the CRD section reports it.
    """
    names = sorted(
        {
            key.split("/", 3)[3]
            for record in records.values()
            for key in record["resources"]
            if key.split("/", 3)[1] == "CustomResourceDefinition"
        }
    )
    instances: dict[str, dict] = {}
    for name in names:
        if name in UNLISTED_INSTANCE_CRDS or name not in crds:
            continue
        for item in cluster.json("get", name, "-A").get("items") or []:
            meta = _meta(item)
            instances[f"{name}/{meta.get('namespace') or ''}/{meta.get('name')}"] = {**_identity(item), **_ownership(item)}
    return instances


def _workloads(cluster: Cluster, namespaces: Iterable[str]) -> tuple[dict, dict]:
    workloads: dict[str, dict] = {}
    pods: dict[str, dict] = {}
    for namespace in namespaces:
        for item in cluster.json("get", WORKLOAD_LIST, "-n", namespace).get("items") or []:
            kind, meta = item.get("kind"), _meta(item)
            name = f"{meta.get('namespace') or namespace}/{meta.get('name')}"
            if kind == "Pod":
                owners = {o.get("kind") for o in meta.get("ownerReferences") or []}
                phase = (item.get("status") or {}).get("phase")
                if owners & EPHEMERAL_POD_OWNERS or phase in FINISHED_POD_PHASES:
                    continue
                statuses = (item.get("status") or {}).get("containerStatuses") or []
                pods[name] = {
                    "uid": meta.get("uid"),
                    "restarts": sum(int(s.get("restartCount") or 0) for s in statuses),
                    "deletionTimestamp": meta.get("deletionTimestamp"),
                    **_ownership(item, "Pod"),
                }
            elif kind == "PersistentVolumeClaim":
                workloads[f"{kind}/{name}"] = {
                    "uid": meta.get("uid"),
                    "volumeName": (item.get("spec") or {}).get("volumeName"),
                    "phase": (item.get("status") or {}).get("phase"),
                    "deletionTimestamp": meta.get("deletionTimestamp"),
                    **_ownership(item),
                }
            else:
                workloads[f"{kind}/{name}"] = {**_identity(item), **_ownership(item)}
    return workloads, pods


def _secrets(cluster: Cluster, namespaces: Iterable[str]) -> dict:
    secrets: dict[str, dict] = {}
    for namespace in namespaces:
        text = cluster.text("get", "secrets", "-n", namespace, "-o", SECRET_COLUMNS, "--no-headers")
        for line in text.splitlines():
            if not line.strip():
                continue
            fields = line.split()
            if len(fields) != 3:
                raise SnapshotError(f"unexpected Secret metadata line in {namespace!r}: {len(fields)} field(s)")
            name, uid, resource_version = fields
            secrets[f"{namespace}/{name}"] = {"uid": uid, "resourceVersion": resource_version}
    return secrets


def collect(
    cluster: Cluster,
    *,
    root: str = "root",
    argocd_namespace: str = "argocd",
    secrets_namespaces: Iterable[str] = (),
    extra_namespaces: Iterable[str] = (),
    edge: Callable[[], dict] | None = None,
    edge_expect: int = 405,
) -> dict:
    """Snapshot every invariant the diff compares. Read-only."""
    cluster_id = _meta(cluster.json("get", "namespace", "kube-system")).get("uid")
    if not cluster_id:
        raise SnapshotError("kube-system has no uid; cannot identify the cluster")
    records, declared, root_app = read_applications(cluster, root, argocd_namespace)

    crds = {
        _meta(item)["name"]: _identity(item)
        for item in cluster.json("get", "customresourcedefinitions.apiextensions.k8s.io").get("items") or []
    }
    if not crds:
        raise SnapshotError("the cluster lists no CRDs; refusing a snapshot of nothing")
    crd_hash = hashlib.sha256("".join(f"{n}={crds[n]['uid']}\n" for n in sorted(crds)).encode()).hexdigest()[:16]

    namespaces = sorted({r["destinationNamespace"] for r in records.values() if r["destinationNamespace"]} | set(extra_namespaces))
    workloads, pods = _workloads(cluster, namespaces)
    custom_resources = _custom_resources(cluster, records, crds)
    _classify([(custom_resources, "foreign:untracked"), (workloads, "root"), (pods, "root")], set(records))
    secrets_namespaces = list(secrets_namespaces)

    return {
        "schema": SCHEMA,
        "taken_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "context": cluster.context,
        "cluster_id": cluster_id,
        "root": root,
        "declared": declared,
        "applications": records,
        "root_operation": _root_operation(root_app),
        "crds": crds,
        "crd_count": len(crds),
        "crd_hash": crd_hash,
        "objects": _tracked_objects(cluster, records),
        "custom_resources": custom_resources,
        "namespaces": namespaces,
        "workloads": workloads,
        "pods": pods,
        "secrets": _secrets(cluster, secrets_namespaces) if secrets_namespaces else None,
        "edge": {**edge(), "expect": edge_expect} if edge else {"skipped": "no --edge-url/--edge-ca given"},
    }


def tls_context(cafile: str | None) -> ssl.SSLContext:
    """Verifying client context: hostname checked, certificate required, TLS 1.2 or newer."""
    context = ssl.create_default_context(cafile=cafile)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def probe_edge(url: str, cafile: str, connect: str | None = None, timeout: float = 10.0) -> dict:
    """GET `url` over TLS verified against `cafile`, optionally dialling `connect` (HOST:PORT) instead.

    The SNI and Host header stay the URL's host, the same as `curl --resolve`.
    There is no insecure mode.
    """
    parts = urlsplit(url)
    host, port = parts.hostname or "", parts.port or 443
    address = (connect.rsplit(":", 1)[0], int(connect.rsplit(":", 1)[1])) if connect else (host, port)
    context = tls_context(cafile)
    try:
        raw = socket.create_connection(address, timeout=timeout)
        conn = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
        conn.sock = context.wrap_socket(raw, server_hostname=host)
        conn.request("GET", parts.path or "/")
        status = conn.getresponse().status
        conn.close()
        return {"url": url, "status": status, "verified": True, "error": None}
    except ssl.SSLError as error:
        return {"url": url, "status": None, "verified": False, "error": f"{type(error).__name__}: {error}"}
    except OSError as error:
        return {"url": url, "status": None, "verified": False, "error": f"{type(error).__name__}: {error}"}


# --- diff ---------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Finding:
    level: str  # FAIL, WARN or INFO
    key: str
    message: str


@dataclasses.dataclass
class Report:
    findings: list[Finding] = dataclasses.field(default_factory=list)
    notes: list[str] = dataclasses.field(default_factory=list)

    def add(self, level: str, key: str, message: str) -> None:
        self.findings.append(Finding(level, key, message))

    @property
    def exit_code(self) -> int:
        return 1 if any(f.level == "FAIL" for f in self.findings) else 0

    def render(self) -> str:
        lines = list(self.notes)
        order = {"FAIL": 0, "WARN": 1, "INFO": 2}
        lines += [f"{f.level} {f.key}: {f.message}" for f in sorted(self.findings, key=lambda f: (order[f.level], f.key))]
        counts = {level: sum(1 for f in self.findings if f.level == level) for level in order}
        verdict = "FAILED" if self.exit_code else "PASSED"
        lines.append(f"{verdict}: {counts['FAIL']} FAIL, {counts['WARN']} WARN, {counts['INFO']} INFO")
        return "\n".join(lines)


def _compare_identities(
    report: Report,
    area: str,
    before: dict,
    after: dict,
    generation_fails: Callable[[str], bool],
    destruction_fails: bool = False,
) -> None:
    """Compare two identity maps. `destruction_fails`: a vanished object or a deletionTimestamp is a
    FAIL whatever the object's scope; only non-destructive changes to a foreign object soften to WARN."""
    for key in sorted(set(before) | set(after)):
        b, a = before.get(key), after.get(key)
        label = f"{area}/{key}"
        if (a or {}).get("skipped") or (b or {}).get("skipped"):
            continue
        # Another Application's object, or one nobody tracks: a non-destructive
        # change is reported, not failed, since its owner's own sync makes it.
        # Root in EITHER snapshot counts as root, so an object root lost to
        # another Application still fails.
        scopes = [r.get("scope", "root") for r in (b, a) if r is not None]
        foreign = all(s.startswith("foreign:") for s in scopes)
        scope = " -> ".join(dict.fromkeys(scopes))
        fail = "WARN" if foreign else "FAIL"
        destroyed = "FAIL" if destruction_fails else fail
        suffix = f" [{scope}]" if foreign else ""
        if b is None:
            report.add("INFO", label, f"added (uid {a.get('uid')}){suffix}")
        elif a is None or a.get("missing"):
            if b.get("missing"):
                report.add("WARN", label, "tracked but missing in both snapshots")
            else:
                report.add(destroyed, label, f"gone (was uid {b.get('uid')}){suffix}")
            continue
        elif b.get("missing"):
            report.add("INFO", label, f"appeared (uid {a.get('uid')})")
        else:
            if a.get("uid") != b.get("uid"):
                report.add(fail, label, f"uid {b.get('uid')} -> {a.get('uid')}{suffix}")
            if a.get("generation") != b.get("generation") and "generation" in a and "generation" in b:
                level = fail if generation_fails(key) else "WARN"
                report.add(level, label, f"generation {b.get('generation')} -> {a.get('generation')}{suffix}")
        if a.get("deletionTimestamp"):
            report.add(destroyed, label, f"deletionTimestamp {a['deletionTimestamp']}{suffix}")


def _compare_applications(report: Report, before: dict, after: dict) -> None:
    b_apps, a_apps = before["applications"], after["applications"]
    for name in after.get("declared") or []:
        if name not in a_apps:
            report.add("FAIL", f"application/{name}", "declared by root but absent")
    for name in sorted(set(b_apps) | set(a_apps)):
        b, a = b_apps.get(name), a_apps.get(name)
        label = f"application/{name}"
        if a is None:
            report.add("FAIL", label, f"gone (was uid {b['uid']})")
            continue
        if b is None:
            report.add("INFO", label, f"added (uid {a['uid']})")
        else:
            if a["uid"] != b["uid"]:
                report.add("FAIL", label, f"uid {b['uid']} -> {a['uid']}")
            if a["finalizers"] != b["finalizers"]:
                report.add("FAIL", label, f"finalizers {b['finalizers']} -> {a['finalizers']}")
            if a["syncPolicy"] != b["syncPolicy"]:
                report.add("WARN", label, f"syncPolicy {json.dumps(b['syncPolicy'], sort_keys=True)} -> {json.dumps(a['syncPolicy'], sort_keys=True)}")
            if not a["automated"] and b["sync"] == "Synced" and a["sync"] != "Synced":
                report.add("FAIL", label, f"manual Application went Synced -> {a['sync']}")
        if a["health"] != "Healthy":
            report.add("FAIL", label, f"health {a['health']}")
        if a["automated"] and a["sync"] != "Synced":
            report.add("FAIL", label, f"automated Application is {a['sync']}")
        phase = (a.get("operation") or {}).get("phase")
        if phase in {"Failed", "Error"}:
            report.add("FAIL", label, f"last operation {phase}")


def _validate(snapshot: dict, which: str) -> None:
    if snapshot.get("schema") != SCHEMA:
        raise SnapshotError(f"{which} snapshot has schema {snapshot.get('schema')!r}, expected {SCHEMA}")
    if not snapshot.get("applications") or not snapshot.get("crds"):
        raise SnapshotError(f"{which} snapshot is empty (no Applications or no CRDs); refusing to compare nothing")


def diff(before: dict, after: dict) -> Report:
    _validate(before, "BEFORE")
    _validate(after, "AFTER")
    if before.get("cluster_id") != after.get("cluster_id"):
        raise SnapshotError(f"snapshots are from different clusters ({before.get('cluster_id')} vs {after.get('cluster_id')})")

    report = Report()
    report.notes.append(f"BEFORE {before.get('taken_at')} ({before.get('context')})  AFTER {after.get('taken_at')} ({after.get('context')})")
    report.notes.append(
        f"compared {len(after['applications'])} Application(s), {len(after['crds'])} CRD(s), "
        f"{len(after.get('objects') or {})} tracked object(s), {len(after.get('custom_resources') or {})} custom resource(s), "
        f"{len(after.get('workloads') or {})} workload(s), "
        f"{len(after.get('pods') or {})} pod(s); CRD hash {before.get('crd_hash')} -> {after.get('crd_hash')}"
    )

    _compare_applications(report, before, after)
    _compare_identities(report, "crd", before["crds"], after["crds"], lambda _key: True)
    _compare_identities(
        report, "object", before.get("objects") or {}, after.get("objects") or {}, lambda key: key.split("/")[1] == "Deployment"
    )
    # Workloads and custom resources: a deletion fails whoever owns it. A
    # root-managed operator upgrade that deletes yadgar's MariaDB, or the edge
    # proxy, is foreign by construction and is the damage this exists to catch.
    # Pods stay softened: a foreign roll replaces pods by design.
    _compare_identities(
        report,
        "workload",
        before.get("workloads") or {},
        after.get("workloads") or {},
        lambda key: key.startswith("Deployment/"),
        destruction_fails=True,
    )
    _compare_identities(
        report,
        "cr",
        before.get("custom_resources") or {},
        after.get("custom_resources") or {},
        lambda _key: False,
        destruction_fails=True,
    )
    _compare_identities(report, "pod", before.get("pods") or {}, after.get("pods") or {}, lambda _key: False)
    for key in sorted(set(before.get("pods") or {}) & set(after.get("pods") or {})):
        b, a = before["pods"][key], after["pods"][key]
        if a.get("restarts", 0) > b.get("restarts", 0):
            report.add("WARN", f"pod/{key}", f"restarts {b.get('restarts')} -> {a.get('restarts')}")

    b_op, a_op = before.get("root_operation") or {}, after.get("root_operation") or {}
    if a_op.get("revision") != b_op.get("revision") and a_op.get("pruned"):
        report.add("WARN", "root/pruned", f"root pruned on {a_op.get('revision')}: {', '.join(a_op['pruned'])}")

    if before.get("secrets") is None or after.get("secrets") is None:
        report.notes.append("Secrets not compared (no --secrets-namespace on both snapshots)")
    else:
        _compare_identities(report, "secret", before["secrets"], after["secrets"], lambda _key: False)
        for key in sorted(set(before["secrets"]) & set(after["secrets"])):
            b, a = before["secrets"][key], after["secrets"][key]
            if a["uid"] == b["uid"] and a["resourceVersion"] != b["resourceVersion"]:
                report.add("FAIL", f"secret/{key}", f"resourceVersion {b['resourceVersion']} -> {a['resourceVersion']}")

    edge = after.get("edge") or {}
    if "skipped" in edge:
        report.notes.append(f"edge probe SKIPPED: {edge['skipped']}")
    elif not edge.get("verified") or edge.get("status") != edge.get("expect"):
        report.add("FAIL", "edge", f"{edge.get('url')}: status {edge.get('status')}, expected {edge.get('expect')}, verified={edge.get('verified')}, error={edge.get('error')}")
    else:
        report.notes.append(f"edge probe {edge.get('url')}: {edge.get('status')} over verified TLS")
    return report


# --- wait ---------------------------------------------------------------------

TERMINAL_FAILURES = {"Failed", "Error"}


def _time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def revision_state(
    app: dict, sha: str, is_ancestor: Callable[[str, str], bool] | None = None
) -> tuple[str, str]:
    """`done` once the Application is Synced at `sha`, or at a commit descending from it, with no operation running.

    No operation on `sha` is required: a merge that changes nothing root renders
    (a README edit) moves the revision without starting an operation. A
    descendant counts because root resolves `main` once per poll: two merges
    inside one poll take root straight past the first.
    """
    status = app.get("status") or {}
    sync, op = status.get("sync") or {}, status.get("operationState") or {}
    op_revision = (op.get("syncResult") or {}).get("revision")

    def reached(revision: str | None) -> bool:
        return bool(revision) and (revision == sha or (is_ancestor is not None and is_ancestor(sha, revision)))

    if op.get("phase") in {"Running", "Terminating"}:
        return "pending", f"operation {op.get('phase')} on {op_revision}"
    if reached(op_revision) and op.get("phase") in TERMINAL_FAILURES:
        return "failed", f"operation on {op_revision} {op.get('phase')}: {op.get('message', '')}"
    revisions = [r for r in [sync.get("revision"), *(sync.get("revisions") or [])] if r]
    at = next((r for r in revisions if reached(r)), None)
    if at is None:
        return "pending", f"at {revisions}, waiting for {sha} or a descendant"
    if sync.get("status") != "Synced":
        return "pending", f"{sync.get('status')} at {at}"
    return "done", f"Synced at {at}" + ("" if at == sha else f", which descends from {sha}")


def _git(repo: Path, args: list[str], runner: Callable) -> int | None:
    """Exit code of `git -C repo <args>`, or None when there is no git binary."""
    # GIT_DIR and friends override `-C`; a pre-commit hook exports them.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        return runner(["git", "-C", str(repo), *args], capture_output=True, check=False, env=env).returncode
    except FileNotFoundError:
        return None


def ancestry_problem(repo: Path, *, runner: Callable = subprocess.run) -> str | None:
    """Why `repo` cannot answer ancestry questions, or None when it can."""
    code = _git(repo, ["rev-parse", "--git-dir"], runner)
    if code is None:
        return "no git binary on PATH"
    if code != 0:
        return f"{repo} is not a git checkout"
    return None


def git_is_ancestor(repo: Path, *, runner: Callable = subprocess.run) -> Callable[[str, str], bool]:
    """`git merge-base --is-ancestor` in `repo`, fetching `origin` once if a revision is unknown.

    Only full shas are compared; anything else (a chart version) is never an
    ancestor. No git, or a repository without the history, answers False,
    which leaves `revision_state` waiting for the exact sha. `--require-ancestry`
    turns that into a refusal up front instead of a timeout.
    """
    fetched = False

    def check(old: str, new: str) -> bool:
        nonlocal fetched
        if not all(len(r) == 40 and all(c in "0123456789abcdef" for c in r) for r in (old, new)):
            return False
        for _ in range(2):
            code = _git(repo, ["merge-base", "--is-ancestor", old, new], runner)
            if code in (0, 1):
                return code == 0
            if code is None or fetched:
                return False
            fetched = True
            _git(repo, ["fetch", "--quiet", "origin"], runner)
        return False

    return check


def operation_state(app: dict, since: str) -> tuple[str, str]:
    """`done` once an operation that started after `since` Succeeded and the Application is Synced/Healthy."""
    status = app.get("status") or {}
    op = status.get("operationState") or {}
    started = _time(op.get("startedAt"))
    if started is None or started <= _time(since):
        return "pending", f"no operation since {since} (last started {op.get('startedAt')})"
    phase = op.get("phase")
    if phase in TERMINAL_FAILURES:
        return "failed", f"operation {phase}: {op.get('message', '')}"
    sync, health = (status.get("sync") or {}).get("status"), (status.get("health") or {}).get("status")
    if phase != "Succeeded" or sync != "Synced" or health != "Healthy":
        return "pending", f"operation {phase}, {sync}/{health}"
    return "done", f"operation started {op.get('startedAt')} Succeeded, Synced/Healthy"


def settled_state(records: Iterable[dict], since: str) -> tuple[str, str]:
    """`done` once every Application was reconciled after `since`, is Healthy, runs no operation,
    and, when automated, is Synced. A manual Application may stay OutOfSync."""
    waiting = []
    for record in records:
        name = record["name"]
        reconciled = _time(record.get("reconciledAt"))
        if (record.get("operation") or {}).get("phase") in {"Running", "Terminating"}:
            waiting.append(f"{name}: operation running")
        elif reconciled is None or reconciled <= _time(since):
            waiting.append(f"{name}: not reconciled since {since}")
        elif record.get("health") != "Healthy":
            waiting.append(f"{name}: {record.get('health')}")
        elif record.get("automated") and record.get("sync") != "Synced":
            waiting.append(f"{name}: {record.get('sync')}")
    return ("pending", "; ".join(waiting)) if waiting else ("done", "every Application settled")


def poll(
    check: Callable[[], tuple[str, str]],
    *,
    timeout: float,
    interval: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[str, str]:
    start = clock()
    while True:
        state, message = check()
        if state in {"done", "failed"}:
            return state, message
        if clock() - start >= timeout:
            return "failed", f"timed out after {timeout:g}s: {message}"
        sleep(interval)


# --- CLI ----------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    def target(p: argparse.ArgumentParser) -> None:
        p.add_argument("--context", required=True, help="kubectl context; REQUIRED, there is no default")
        p.add_argument("--root", default="root")
        p.add_argument("--argocd-namespace", default="argocd")

    snapshot = sub.add_parser("snapshot", help="capture the invariants to a JSON file")
    target(snapshot)
    snapshot.add_argument("--out", required=True, type=Path)
    snapshot.add_argument("--extra-namespace", action="append", default=[], help="also snapshot this namespace's workloads")
    snapshot.add_argument(
        "--secrets-namespace",
        action="append",
        default=[],
        help="record Secret name/uid/resourceVersion here (metadata columns only; needs `list secrets`, which CI is not granted)",
    )
    snapshot.add_argument("--edge-url")
    snapshot.add_argument("--edge-ca", help="CA bundle the edge certificate must verify against; there is no insecure mode")
    snapshot.add_argument("--edge-connect", help="HOST:PORT to dial instead of the URL's host, like curl --resolve")
    snapshot.add_argument("--edge-expect", type=int, default=405)

    wait = sub.add_parser("wait", help="block until root, one Application, or all of them settle")
    target(wait)
    wait.add_argument("--app")
    wait.add_argument("--settled", action="store_true", help="every Application under root reconciled after --since")
    wait.add_argument("--since", help="RFC 3339 UTC time, e.g. 2026-10-01T12:00:00Z")
    wait.add_argument("--revision", help="git sha the Application must be Synced at")
    wait.add_argument(
        "--ancestry-repo", type=Path, default=Path("."), help="git checkout used to accept a descendant of --revision"
    )
    wait.add_argument(
        "--require-ancestry",
        action="store_true",
        help="refuse to start when --ancestry-repo cannot answer ancestry (no git, no checkout)",
    )
    wait.add_argument("--timeout", type=float, default=900)
    wait.add_argument("--interval", type=float, default=10)

    compare = sub.add_parser("diff", help="compare two snapshots; exit 1 on a failure, 2 on a refusal")
    compare.add_argument("before", type=Path)
    compare.add_argument("after", type=Path)
    return parser


def _wait(args: argparse.Namespace, parser: argparse.ArgumentParser) -> int:
    if args.settled == bool(args.app):
        parser.error("wait needs exactly one of --app NAME or --settled")
    if args.settled and not args.since:
        parser.error("wait --settled needs --since")
    if args.app and bool(args.since) == bool(args.revision):
        parser.error("wait --app needs exactly one of --since or --revision")
    if args.revision and args.require_ancestry:
        problem = ancestry_problem(args.ancestry_repo)
        if problem:
            raise UsageError(f"--require-ancestry: {problem}; a later commit on main could never be accepted")
    cluster = Cluster(args.context)
    is_ancestor = git_is_ancestor(args.ancestry_repo)

    def check() -> tuple[str, str]:
        if args.settled:
            records, _declared, _root = read_applications(cluster, args.root, args.argocd_namespace)
            return settled_state(records.values(), args.since)
        app = cluster.json("get", "applications.argoproj.io", args.app, "-n", args.argocd_namespace)
        return revision_state(app, args.revision, is_ancestor) if args.revision else operation_state(app, args.since)

    state, message = poll(check, timeout=args.timeout, interval=args.interval)
    print(f"{state}: {message}")
    return 0 if state == "done" else 1


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "diff":
            report = diff(json.loads(args.before.read_text()), json.loads(args.after.read_text()))
            print(report.render())
            return report.exit_code
        if args.command == "wait":
            return _wait(args, parser)
        if bool(args.edge_url) != bool(args.edge_ca):
            parser.error("--edge-url and --edge-ca go together; there is no unverified probe")
        edge = (lambda: probe_edge(args.edge_url, args.edge_ca, args.edge_connect)) if args.edge_url else None
        snapshot = collect(
            Cluster(args.context),
            root=args.root,
            argocd_namespace=args.argocd_namespace,
            secrets_namespaces=args.secrets_namespace,
            extra_namespaces=args.extra_namespace,
            edge=edge,
            edge_expect=args.edge_expect,
        )
        args.out.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n")
        print(
            f"snapshot {args.out}: {len(snapshot['applications'])} Application(s), {snapshot['crd_count']} CRD(s) "
            f"(hash {snapshot['crd_hash']}), {len(snapshot['objects'])} tracked object(s), "
            f"{len(snapshot['custom_resources'])} custom resource(s), "
            f"{len(snapshot['workloads'])} workload(s), {len(snapshot['pods'])} pod(s) in {len(snapshot['namespaces'])} namespace(s)"
        )
        return 0
    except (SnapshotError, KubectlError, UsageError) as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
