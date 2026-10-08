"""The operator Applications root adopted at E3 are the ones `deploy` released.

E3 OF THE OPERATORS HANDOVER (ADR-0824). Six operator Applications —
`cert-manager`, `keda`, `mariadb-operator`, `mariadb-operator-crds`,
`envoy-gateway` and `prometheus` — were declared in `yadgarhq/deploy`'s
`infra/`. E1 (`deploy#76`) put `argocd.argoproj.io/sync-options: Prune=false`
on each live object, and E2 deleted the six files, so the live objects run
unowned. This repository's `root` Application now declares them under
`applications/`. Root adopts each live object by its identity — group, kind,
namespace and name — so the uid does not change. D7.1 later deleted
`mariadb-operator-crds` (check 9), so five remain: `cert-manager`, `keda`,
`mariadb-operator`, `envoy-gateway` and `prometheus`. Since D7.3 all five
source `platform` (check 8).

WHAT IS ASSERTED, and each has a red case below:

  1. No `spec` is pinned to deploy's copy any more. E3 hashed each spec
     against `yadgarhq/deploy` at `fa7ccb5`, the E1 merge and the last commit
     that declared them. D4 moved four Applications to check 8, D7.1 deleted
     a fifth, and D7.3 moved the last, `mariadb-operator`, to check 8. So
     PINNED_SPECS is empty, and the test asserts that it is empty rather than
     iterating it: a gate over an empty set passes while checking nothing.
  2. Each file is `argoproj.io/Application`, named `<name>`, in namespace
     `argocd`. A different name or namespace is a new object, not an adoption.
  3. Each file is inside root's own source: `projects/root.yaml`'s `path` and
     `directory.include` glob are read, never copied here.
  4. No manifest under `applications/` carries `Prune=false`. Root's apply
     removes the one E1 left on the live object, which is what lets a later
     step prune the old operator Application. A census prints the count read.
  5. No file carries a finalizer, so a later prune of an Application never
     cascades into the operator it runs.
  6. No `automated` block carries a `prune` key at all. S0 deletes the key
     rather than writing `prune: false`, matching the chart's own example.
  7. Each `retry` block has a finite, positive `limit` and matches the
     chart's own example byte-for-byte once parsed.
  8. D4 OF THE OPERATORS HANDOVER (ADR-0824): `keda`, `cert-manager`,
     `envoy-gateway` and `prometheus` no longer pin to deploy's copy, and
     since D7.3 neither does `mariadb-operator`. Each sources `yadgarhq`'s
     `platform` chart at the version `scripts/chart_pin.json` commits (ledger
     1206), with its own `operators.<op>.create` true and every other
     operator, Argo CD included, explicitly false. Deploy's values, where it
     had any, are carried over under the operator's subchart key;
     `mariadb-operator` had none, so its values hold `operators` alone. Each
     name, destination, `syncPolicy` and release name is pinned unchanged, so
     the release instance label and every immutable selector stay as they
     are. PLATFORM_SOURCED holds one row per operator. Checks 2 to 7 cover
     all five.
  9. D7.1 OF THE OPERATORS HANDOVER (ADR-0824): `mariadb-operator-crds` is
     retired. No file under `applications/` is named for it, and no manifest
     there declares an Application with its name. `retired_present` below
     also still checks an `applicationsets/` directory by name, generic
     logic kept after ledger 1270b retired that directory itself. Its 12
     CRDs stayed in the cluster, untracked, until D7.3 swapped
     `mariadb-operator` to `platform`, which renders them. A revived
     Application would apply the same CRDs beside that one, as the same
     server-side apply manager. RETIRED holds the name.
  10. LEDGER 1212. Each Application's `metadata.annotations` — at minimum its
      `argocd.argoproj.io/sync-wave` — is pinned per row. Before this, nothing
      read the annotation a wave is declared with, so moving `mariadb-operator`
      from `-10` to `5` left every other gate here green: wrong wave order
      against the CRDs it now renders is a cold-install failure mode
      (ledger 1212), not a diff `platform_errors` would ever have reported.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import shutil
import sys
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
ROOT = REPOSITORY / "projects" / "root.yaml"
APPLICATIONS = REPOSITORY / "applications"

# E3 pinned each spec to the sha256 of its file's text from the `spec:` line
# to the end, taken from
# `git -C deploy show fa7ccb5:infra/<name>.yaml | sed -n '/^spec:/,$p' | sha256sum`,
# after undoing S0's two `syncPolicy` changes.
SOURCE = "yadgarhq/deploy@fa7ccb529fd12911a7ccca1dc53f10490063f446"
# `keda`, `cert-manager`, `envoy-gateway` and `prometheus` left this table at
# D4 (ADR-0824): each now sources `platform`, which PLATFORM_SOURCED below
# pins. Their deploy-era hashes were
# keda 19e6856ba5ebc99ba0f24702cc5840b1d94f19a5f0e0d38115b0d280f666f6b6,
# cert-manager f402c7c04defb9ba09118f243357d16dc2df91eff35ea47f5e52dd0b19d579cb,
# envoy-gateway 1d8b30ab563c10c623bd0e0bebd502273c8a8fab833d9c58661b82d6aa0059b4 and
# prometheus c03d2900b2f5f23f76e20c4252f98243984aeb0dfd156aa43913949bb5204a79.
# `mariadb-operator-crds` left it at D7.1 (ADR-0824), when its file was deleted
# (RETIRED below). Its deploy-era hash was
# bd68b4915105319e1fdf3dcc10f7d6651955cf9b546db944ce4172be9d90536e.
# `mariadb-operator` left it at D7.3 (ADR-0824): it now sources `platform`.
# Its deploy-era hash was
# 485a08bcc620570e35ea6c216872e0d64c757d47ea43cc6479de9edf407c65be.
#
# E3'S PIN ROLE ENDED AT D7.3. The table is empty, and the test asserts that
# it is empty. The hashing code went with the last row: a hash gate over an
# empty table passes while checking nothing. A spec that ever needs pinning to
# deploy's copy again needs that gate restored, not a row added here.
PINNED_SPECS: dict[str, str] = {}

# D7.1 OF THE OPERATORS HANDOVER (ADR-0824). Applications this repository
# deleted and must not declare again. `mariadb-operator-crds` managed the 12
# `k8s.mariadb.com` CRDs. Since D7.3 `mariadb-operator` renders them from
# `platform`, so a revived copy would apply the same CRDs from a second
# Application.
RETIRED: tuple[str, ...] = ("mariadb-operator-crds",)

SYNC_OPTIONS = "argocd.argoproj.io/sync-options"
PRUNE_FALSE = "Prune=false"

# S0 OF THE OPERATORS HANDOVER (ADR-0824). `retry` below is copied verbatim from
# `yadgarhq/chart`'s own `example/operators-application.yaml` at its latest
# release tag, read with `gh-personal api repos/yadgarhq/chart/contents/...`.
# That example also omits `automated.prune`, which is the other half of S0.
#
# PINNED SEPARATELY FROM `CHART_TAG` (`v0.3.13`, ledger 1206), AND DELIBERATELY
# NOT RE-DERIVED FROM IT. This constant names "the latest tag read for S0's
# example", `CHART_TAG` names "the tag this organisation runs" (ledger 1206),
# and the two have no reason to move together. MEASURED 2026-10-01:
# `example/operators-application.yaml` is byte-identical at `v0.3.13`,
# `v0.3.14` and `v0.3.15` (`diff` on all three pulled copies, exit 0) — the
# `retry` block below is the same at `CHART_TAG` too, so there is nothing for
# this difference to hide today.
CHART_EXAMPLE_SOURCE = "yadgarhq/chart@v0.3.15:example/operators-application.yaml"
EXPECTED_RETRY: dict = {
    "limit": 6,
    "backoff": {
        "duration": "15s",
        "factor": 2,
        "maxDuration": "5m",
    },
}

# D4 OF THE OPERATORS HANDOVER (ADR-0824). Each Application here sources the
# `platform` chart with exactly one operator on. The version is the `platform`
# dependency embedded at `yadgarhq/chart`'s `chart/Chart.yaml`, at the chart tag
# this organisation runs.
#
# LEDGER 1206. This used to be a hardcoded literal with no test holding it equal
# to the parent chart's embedded version. It now comes from `chart_pin.json`,
# committed beside this file, which is the single source both halves read:
#
#   `chart_tag`         the `yadgarhq/chart` git tag this organisation runs,
#                        read from kind-yadgar's `yadgar` Application
#                        (`spec.source.targetRevision`, as the published OCI
#                        chart version — the same number with a `v` in front is
#                        the git tag `ref=` takes).
#   `platform_version`  `platform`'s pinned `version` among that tag's
#                        `chart/Chart.yaml` `dependencies`.
#
# This test module reads the file and, with it, needs no network: every
# platform-sourced Application's `targetRevision` is asserted equal to
# `PLATFORM_VERSION` below (the `target-revision` clause of
# `platform_clause_errors`), which is exactly "the platform pin in every
# platform-sourced Application equals the committed `platform_version`".
# `test_a_mismatched_chart_pin_reddens` is this gate's red case.
#
# THE REVERSE CHECKS — that the committed file still describes `yadgarhq/chart`'s
# actual state, AND that `chart_tag` itself is still the tag this organisation
# runs rather than one that merely matched once — each need a real request, so
# both run as their own CI job (`chart-pin` in `.github/workflows/ci.yaml`,
# `scripts/check_chart_pin.py`), not here and not as a pre-commit hook.
CHART_PIN_PATH = REPOSITORY / "scripts" / "chart_pin.json"
CHART_PIN: dict = json.loads(CHART_PIN_PATH.read_text())
CHART_TAG = CHART_PIN["chart_tag"]
PLATFORM_SOURCE = f"yadgarhq/chart@{CHART_TAG}:chart/Chart.yaml"
PLATFORM_VERSION = CHART_PIN["platform_version"]

# `chart_tag` is a git tag (`v` prefix); `platform_version` is the OCI chart
# version `yadgarhq/chart`'s `Chart.yaml` and `applications/yadgar.yaml`'s
# `targetRevision` both write it as (no `v`). `chart_pin_errors` below is the
# one clause that reads these.
CHART_TAG_PATTERN = re.compile(r"v\d+\.\d+\.\d+")
PLATFORM_VERSION_PATTERN = re.compile(r"\d+\.\d+\.\d+")

OPERATOR_KEYS = ("argoCd", "certManager", "envoyGateway", "keda", "mariadbOperator", "prometheus")

# Every field of a platform-sourced Application's `spec.syncPolicy`, as deploy's
# copy declared it with S0's two changes. The same for every operator.
PLATFORM_SYNC_POLICY: dict = {
    "automated": {"selfHeal": True},
    "syncOptions": ["CreateNamespace=true", "ServerSideApply=true"],
    "retry": EXPECTED_RETRY,
}

# One row per Application that sources `platform` (D4, ADR-0824). The next swap
# adds a row. `platform_clause_errors` reads these rows, OPERATOR_KEYS and
# PLATFORM_VERSION, and nothing else.
#
#   toggle     the `operators.<toggle>.create` key that is true. Every other
#              key in OPERATOR_KEYS is written false.
#   values     optional. The `platform` subchart key that carries deploy's
#              values over. A row without it has no deploy values to carry:
#              its values hold `operators` alone, and `carried` and
#              `carried-digest` are not evaluated for it, rather than passing
#              on an empty set. Only `mariadb-operator` has none.
#   form       optional. The `helm` key that holds the values: `valuesObject`
#              when absent, or `values`, the YAML string, which is parsed.
#              Only `prometheus` uses `values`, because its `null` must reach
#              helm and a `null` inside `valuesObject` can be dropped on apply
#              (reasoned from client-side apply's merge patch, not measured).
#   digests    optional. Keys under `values` pinned by the sha256 of their
#              canonical JSON (`json.dumps(..., sort_keys=True)`) rather than
#              written out here. `carried` holds every other key.
#   carried    required when `values` is set. Exactly what sits under
#              `values`, less `digests`: deploy's
#              copy, moved under that key, plus any value set since D4 (the
#              row's comment names it: prometheus's ledger-1210 sizing). Measured 2026-10-01: without a row's sizing blocks
#              the render's pod templates change and the Deployments roll.
#              cert-manager's `crds` block changes nothing in today's render,
#              because `platform` and cert-manager v1.21.1 already default to
#              `enabled: true` and `keep: true`. It is pinned as a defence
#              against a later change to either default.
#   unchanged  every field of `spec` other than `source`, as deploy's copy
#              declared it with S0's two changes. A change here is a change to
#              the release.
#   annotations  required (ledger 1212). Exactly `metadata.annotations`, sync-
#              wave included. Nothing else gated this: changing a wave alone
#              touches no `spec` field `unchanged` reads, and a wrong wave
#              order against the CRDs an operator now renders is a cold-install
#              failure mode, not a diff.
PLATFORM_SOURCED: dict[str, dict] = {
    "keda": {
        "toggle": "keda",
        "values": "keda",
        # Development sizing (D55), deploy's copy.
        "carried": {
            "resources": {
                "operator": {"requests": {"cpu": "50m", "memory": "128Mi"}},
                "metricServer": {"requests": {"cpu": "50m", "memory": "128Mi"}},
            },
        },
        "unchanged": {
            "project": "default",
            "destination": {"server": "https://kubernetes.default.svc", "namespace": "keda"},
            "syncPolicy": PLATFORM_SYNC_POLICY,
        },
        "annotations": {"argocd.argoproj.io/sync-wave": "-10"},
    },
    "cert-manager": {
        "toggle": "certManager",
        "values": "cert-manager",
        # `crds.enabled` and `crds.keep`, and the development sizing (D55),
        # deploy's copy. `keep` renders `helm.sh/resource-policy: keep` on the
        # six CRDs, which stops a helm uninstall deleting them and every
        # Certificate with them. Both `crds` keys equal today's defaults.
        "carried": {
            "crds": {"enabled": True, "keep": True},
            "resources": {"requests": {"cpu": "10m", "memory": "64Mi"}},
            "webhook": {"resources": {"requests": {"cpu": "10m", "memory": "32Mi"}}},
            "cainjector": {"resources": {"requests": {"cpu": "10m", "memory": "64Mi"}}},
        },
        "unchanged": {
            "project": "default",
            "destination": {"server": "https://kubernetes.default.svc", "namespace": "cert-manager"},
            "syncPolicy": PLATFORM_SYNC_POLICY,
        },
        "annotations": {"argocd.argoproj.io/sync-wave": "-10"},
    },
    "envoy-gateway": {
        "toggle": "envoyGateway",
        "values": "gateway-helm",
        # Development sizing (D55), deploy's copy. Without it the envoy-gateway
        # Deployment's pod template changes and it rolls.
        "carried": {
            "deployment": {"envoyGateway": {"resources": {"requests": {"cpu": "50m", "memory": "128Mi"}}}},
        },
        "unchanged": {
            "project": "default",
            "destination": {"server": "https://kubernetes.default.svc", "namespace": "envoy-gateway-system"},
            "syncPolicy": PLATFORM_SYNC_POLICY,
        },
        "annotations": {"argocd.argoproj.io/sync-wave": "-10"},
    },
    "prometheus": {
        "toggle": "prometheus",
        "values": "prometheus",
        "form": "values",
        # deploy's four alerting rules, verbatim (D4 measured `serverFiles`
        # parsed from deploy's copy at SOURCE equal to this file's, digest
        # fc7255aec26edb02827487ecb30f4780343d67504390ba5c3f55fc7948b35435),
        # plus ledger 1210's fifth, `PrometheusSizeRetentionDeletedBlocks`.
        # `platform` carries no rules.
        "digests": {"serverFiles": "a448774a4fb225a2f4d3a0e75cc5c35d8496f896701334f7e28bb876d56b8a0a"},
        # deploy's PVC (`platform` defaults `enabled` to false, which renders an
        # emptyDir), and `null` on the reload sidecar's resources, which deletes
        # `platform`'s 10m/32Mi requests. Measured 2026-10-01: without either the
        # pod template changes and the pod rolls; without the PVC the TSDB is
        # empty. An empty map `{}` does not delete the requests.
        #
        # `server.resources` and `server.retentionSize` are NOT deploy's: ledger
        # 1210 set them after D4, and the pod rolls once for them. Measured
        # 2026-10-01 at the 15s scrape interval: RSS peak >=790 MB and still
        # rising, so the request covers it and the limit leaves room for WAL
        # replay. The TSDB's 24h upper bound is PROJECTED at 1.5 to 1.6 GB, so
        # 4GB (4 GiB: Prometheus counts in powers of 2) keeps `retention: 24h`
        # the bound that binds, with room for about 2.5 times the series.
        # local-path does not enforce the nominal 2Gi. A drop or a change of
        # any of them reddens `carried`.
        "carried": {
            "server": {
                "persistentVolume": {"enabled": True, "size": "2Gi"},
                "resources": {"requests": {"cpu": "100m", "memory": "1Gi"}, "limits": {"memory": "2Gi"}},
                "retentionSize": "4GB",
            },
            "configmapReload": {"prometheus": {"resources": None}},
        },
        "unchanged": {
            "project": "default",
            "destination": {"server": "https://kubernetes.default.svc", "namespace": "observability"},
            # deploy declared no `ServerSideApply=true` for prometheus.
            "syncPolicy": {**PLATFORM_SYNC_POLICY, "syncOptions": ["CreateNamespace=true"]},
        },
        # Before the modules, which is what it scrapes — but after the
        # operators, so it is not competing for the first wave.
        "annotations": {"argocd.argoproj.io/sync-wave": "-8"},
    },
    # D7.3 (ADR-0824). deploy's copy set no values, so there is nothing to
    # carry and no `mariadb-operator:` subchart key. `platform` vendors the 12
    # `k8s.mariadb.com` CRDs as its own templates behind
    # `operators.mariadbOperator.create`, and sets the subchart's own
    # `crds.enabled` false. Measured 2026-10-01: with `operators` alone the
    # render is deploy's 20 objects plus the 12 CRDs, which differ from the
    # deleted `mariadb-operator-crds` Application's copies only by
    # `helm.sh/resource-policy: keep` and `argocd.argoproj.io/sync-options:
    # Prune=false`.
    "mariadb-operator": {
        "toggle": "mariadbOperator",
        "unchanged": {
            "project": "default",
            "destination": {"server": "https://kubernetes.default.svc", "namespace": "mariadb-system"},
            "syncPolicy": PLATFORM_SYNC_POLICY,
        },
        # ONE WAVE, NOT TWO (ledger 1212): the CRDs used to sync at -12 from
        # their own Application; within -10, Argo applies CRDs before the RBAC
        # and workload kinds this chart renders.
        "annotations": {"argocd.argoproj.io/sync-wave": "-10"},
    },
}

ADOPTED: tuple[str, ...] = (*PINNED_SPECS, *PLATFORM_SOURCED)


def automated_blocks(tree: Path) -> dict[str, dict]:
    """Each pinned name's parsed `spec.syncPolicy.automated` (empty dict if absent)."""
    blocks = {}
    for name in ADOPTED:
        path = tree / "applications" / f"{name}.yaml"
        document = yaml.safe_load(path.read_text()) if path.is_file() else {}
        blocks[name] = ((document or {}).get("spec") or {}).get("syncPolicy", {}).get("automated") or {}
    return blocks


def automated_prune_present(tree: Path) -> list[str]:
    """Every pinned name whose `automated` block still carries a `prune` key."""
    return sorted(name for name, automated in automated_blocks(tree).items() if "prune" in automated)


def retry_blocks(tree: Path) -> dict[str, object]:
    """Each pinned name's parsed `spec.syncPolicy.retry` (None if absent)."""
    blocks = {}
    for name in ADOPTED:
        path = tree / "applications" / f"{name}.yaml"
        document = yaml.safe_load(path.read_text()) if path.is_file() else {}
        blocks[name] = ((document or {}).get("spec") or {}).get("syncPolicy", {}).get("retry")
    return blocks


def retry_errors(tree: Path) -> list[str]:
    """Every pinned name whose `retry` is absent, non-finite/non-positive, or not the chart's block.

    `-1` is Argo CD's own sentinel for an unlimited retry budget — the chart's
    example calls that out explicitly ("Never -1"), so a finite positive int is
    required rather than merely "is an int".
    """
    errors = []
    for name, retry in retry_blocks(tree).items():
        limit = retry.get("limit") if isinstance(retry, dict) else None
        valid_limit = isinstance(limit, int) and not isinstance(limit, bool) and limit > 0
        if not valid_limit or retry != EXPECTED_RETRY:
            errors.append(name)
    return sorted(errors)


def identity_errors(tree: Path) -> list[str]:
    """Every pinned name whose file is not `argoproj.io/Application` `argocd/<name>`."""
    errors = []
    for name in ADOPTED:
        path = tree / "applications" / f"{name}.yaml"
        documents = [d for d in yaml.safe_load_all(path.read_text()) if d] if path.is_file() else []
        identity = [
            (
                d.get("apiVersion", "").split("/")[0],
                d.get("kind"),
                (d.get("metadata") or {}).get("namespace"),
                (d.get("metadata") or {}).get("name"),
            )
            for d in documents
        ]
        if identity != [("argoproj.io", "Application", "argocd", name)]:
            errors.append(name)
    return errors


def expand_braces(pattern: str) -> list[str]:
    """`{a,b}/*.yaml` → `a/*.yaml`, `b/*.yaml`. Argo's include glob syntax, one level."""
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    head, tail = pattern[: match.start()], pattern[match.end() :]
    return [p for option in match.group(1).split(",") for p in expand_braces(head + option + tail)]


def outside_root(tree: Path) -> list[str]:
    """Every pinned name whose file root's `path` + `directory.include` does not select."""
    root = yaml.safe_load((tree / "projects" / "root.yaml").read_text())
    source = root["spec"]["source"]
    base = (tree / source.get("path", ".")).resolve()
    include = (source.get("directory") or {}).get("include", "*")
    patterns = expand_braces(include)
    missing = []
    for name in ADOPTED:
        path = (tree / "applications" / f"{name}.yaml").resolve()
        relative = path.relative_to(base).as_posix() if path.is_relative_to(base) else None
        if relative is None or not path.is_file() or not any(fnmatch.fnmatchcase(relative, p) for p in patterns):
            missing.append(name)
    return missing


def manifests_with_prune_false(tree: Path) -> tuple[list[str], int]:
    """Every `applications/**/*.yaml` carrying `Prune=false`, and how many files were read.

    Recursive, because root sets `directory.recurse` and Argo's include glob
    lets `*` cross `/`: root applies a nested file too. Each hit is named by its
    path relative to `applications/`.
    """
    found, read = [], 0
    applications = tree / "applications"
    for path in sorted(applications.rglob("*.yaml")):
        read += 1
        for document in yaml.safe_load_all(path.read_text()):
            annotations = ((document or {}).get("metadata") or {}).get("annotations") or {}
            options = [o.strip() for o in str(annotations.get(SYNC_OPTIONS, "")).split(",")]
            if PRUNE_FALSE in options:
                found.append(path.relative_to(applications).as_posix())
    return found, read


def finalized(tree: Path) -> list[str]:
    """Every pinned name whose Application declares a finalizer."""
    names = []
    for name in ADOPTED:
        path = tree / "applications" / f"{name}.yaml"
        for document in yaml.safe_load_all(path.read_text()):
            if ((document or {}).get("metadata") or {}).get("finalizers"):
                names.append(name)
    return names


def parsed_values(text: object) -> object:
    """A `helm.values` string parsed as YAML; None when it is not a string or does not parse."""
    if not isinstance(text, str):
        return None
    try:
        return yaml.safe_load(text)
    except yaml.YAMLError:
        return None


def canonical_digest(value: object) -> str:
    """sha256 of `value` as canonical JSON, so formatting and comments do not count."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def platform_clause_errors(tree: Path) -> list[tuple[str, str]]:
    """Every (name, clause) where a platform-sourced Application is not the D4 shape (ADR-0824).

    One clause per requirement, so a red case can name the clause it breaks:

      repo-url         `source.repoURL` is `ghcr.io/yadgarhq/charts`
      chart            `source.chart` is `platform`
      target-revision  `source.targetRevision` is PLATFORM_VERSION
      source-keys      `source` holds those three and `helm`, nothing else
      helm             `helm` holds the row's `form` only (`valuesObject` by
                       default): no `releaseName`, so the release keeps the
                       Application's name
      toggle-on        `operators.<toggle>.create` is true
      others-off       every other key in OPERATOR_KEYS is written, as false
      operator-keys    `operators` holds OPERATOR_KEYS and nothing else, so no
                       `operators.create`
      value-keys       the values hold `operators` and the row's subchart key
                       only; `operators` alone for a row without `values`
      carried          the row's subchart key, less `digests`, holds exactly
                       `carried`. Only for a row with `values`.
      carried-digest   each key in the row's `digests` hashes to its value.
                       Only for a row with `values`.
      unchanged        every field of `spec` outside `source` equals `unchanged`
      annotations      `metadata.annotations` equals the row's `annotations`
                       (ledger 1212), sync-wave included
    """
    errors = []
    for name, row in PLATFORM_SOURCED.items():
        path = tree / "applications" / f"{name}.yaml"
        document = (yaml.safe_load(path.read_text()) if path.is_file() else None) or {}
        metadata = document.get("metadata") or {}
        spec = document.get("spec") or {}
        source = spec.get("source") or {}
        helm = source.get("helm") or {}
        form = row.get("form", "valuesObject")
        values = parsed_values(helm.get(form)) if form == "values" else helm.get(form)
        values = values if isinstance(values, dict) else {}
        subchart_key = row.get("values")
        subchart = values.get(subchart_key) if subchart_key else None
        subchart = subchart if isinstance(subchart, dict) else {}
        digests = row.get("digests", {})
        operators = values.get("operators") or {}
        rest = {key: value for key, value in spec.items() if key != "source"}
        checks = {
            "repo-url": source.get("repoURL") == "ghcr.io/yadgarhq/charts",
            "chart": source.get("chart") == "platform",
            "target-revision": source.get("targetRevision") == PLATFORM_VERSION,
            "source-keys": set(source) == {"repoURL", "chart", "targetRevision", "helm"},
            "helm": set(helm) == {form},
            "toggle-on": operators.get(row["toggle"]) == {"create": True},
            "others-off": all(operators.get(key) == {"create": False} for key in OPERATOR_KEYS if key != row["toggle"]),
            "operator-keys": set(operators) == set(OPERATOR_KEYS),
            "value-keys": set(values) == ({"operators", subchart_key} if subchart_key else {"operators"}),
        }
        if subchart_key:
            checks["carried"] = subchart_key in values and {
                key: value for key, value in subchart.items() if key not in digests
            } == row["carried"]
            checks["carried-digest"] = all(
                key in subchart and canonical_digest(subchart[key]) == digest for key, digest in digests.items()
            )
        checks["unchanged"] = rest == row["unchanged"]
        checks["annotations"] = (metadata.get("annotations") or {}) == row["annotations"]
        errors.extend((name, clause) for clause, passed in checks.items() if not passed)
    return errors


def platform_errors(tree: Path) -> list[str]:
    """Every platform-sourced name with at least one clause of `platform_clause_errors` failing."""
    return list(dict.fromkeys(name for name, _ in platform_clause_errors(tree)))


def chart_pin_errors(pin: dict) -> list[str]:
    """Every clause of `chart_pin.json`'s own shape that `pin` fails (ledger 1206).

    `keys` short-circuits the other two: a missing key leaves nothing for
    either pattern to check, and `pin["chart_tag"]` would raise rather than
    fail a clause.
    """
    if set(pin) != {"chart_tag", "platform_version"}:
        return ["keys"]
    errors = []
    if not CHART_TAG_PATTERN.fullmatch(pin["chart_tag"]):
        errors.append("chart_tag")
    if not PLATFORM_VERSION_PATTERN.fullmatch(pin["platform_version"]):
        errors.append("platform_version")
    return errors


def retired_present(tree: Path) -> list[str]:
    """Every RETIRED name that root's directories still carry, as a file name or as an Application name.

    Both, because a revival need not keep the old file name: an Application
    `mariadb-operator-crds` declared in `applications/mariadb-crds.yaml` is the
    same live object. Recursive, because root sets `directory.recurse` and
    Argo's include glob lets `*` cross `/`, so root selects a nested file too.
    """
    found = set()
    for directory in ("applications", "applicationsets"):
        for path in sorted((tree / directory).rglob("*.yaml")):
            if path.stem in RETIRED:
                found.add(path.stem)
            for document in yaml.safe_load_all(path.read_text()):
                document = document or {}
                name = (document.get("metadata") or {}).get("name")
                if document.get("kind") == "Application" and name in RETIRED:
                    found.add(name)
    return sorted(found)


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    """A writable copy of the two directories these gates read."""
    tree = tmp_path / "tree"
    shutil.copytree(APPLICATIONS, tree / "applications")
    shutil.copytree(REPOSITORY / "projects", tree / "projects")
    return tree


def test_no_spec_is_pinned_to_deploy_any_more() -> None:
    """E3's pin role ended at D7.3: every adopted Application is platform-sourced.

    Asserted directly, because a gate iterating an empty table passes while
    checking nothing.
    """
    print(f"[E3] {len(PINNED_SPECS)} spec(s) pinned to {SOURCE}; {len(PLATFORM_SOURCED)} platform-sourced")
    assert PINNED_SPECS == {}
    assert set(ADOPTED) == {"cert-manager", "keda", "mariadb-operator", "envoy-gateway", "prometheus"}


def test_every_platform_sourced_application_is_the_d4_shape() -> None:
    print(
        f"[D4] {len(PLATFORM_SOURCED)} Application(s) ({', '.join(PLATFORM_SOURCED)}) pinned to platform"
        f" {PLATFORM_VERSION} from {PLATFORM_SOURCE}"
    )
    assert platform_errors(REPOSITORY) == []


def test_chart_pin_file_is_well_formed() -> None:
    """LEDGER 1206. `chart_pin.json` carries exactly the two keys this module reads,
    each in the shape `scripts/check_chart_pin.py`'s requests expect.

    Whether its `platform_version` and `chart_tag` are still true of
    `yadgarhq/chart` and the running `yadgar` Application is that script's
    question, in CI, not this one's — this is the one clause for the file's
    own shape.
    """
    assert chart_pin_errors(CHART_PIN) == []


def test_a_chart_tag_missing_the_v_prefix_reddens() -> None:
    """The contents API's `ref=` query string takes a git tag, which this
    organisation always writes with a leading `v` (`git tag` convention); a
    bare semver string is not one.
    """
    assert chart_pin_errors({**CHART_PIN, "chart_tag": "0.3.13"}) == ["chart_tag"]


def test_a_platform_version_carrying_a_v_prefix_reddens() -> None:
    """`platform_version` is the OCI chart version as `Chart.yaml` and
    `applications/yadgar.yaml`'s `targetRevision` both write it: no leading `v`.
    """
    assert chart_pin_errors({**CHART_PIN, "platform_version": "v0.1.21"}) == ["platform_version"]


def test_an_extra_chart_pin_key_reddens() -> None:
    assert chart_pin_errors({**CHART_PIN, "extra": "x"}) == ["keys"]


def test_a_mismatched_chart_pin_reddens(monkeypatch: pytest.MonkeyPatch) -> None:
    """LEDGER 1206's gate: every platform-sourced Application's pin equals `PLATFORM_VERSION`.

    `scripts/chart_pin.json` is read once at import, so a drift in the committed
    file cannot be reproduced by editing it here without reloading the module.
    What CAN be shown without that is the gate itself: with `PLATFORM_VERSION`
    wrong, every row's `target-revision` clause fails, because `applications/`
    still carries the real (correct) pin. The reverse direction — a correct
    `PLATFORM_VERSION` against a wrong `targetRevision` in one file — is already
    covered by `test_a_moved_platform_pin_reddens`.
    """
    monkeypatch.setattr(sys.modules[__name__], "PLATFORM_VERSION", "9.9.9")
    assert sorted(platform_errors(REPOSITORY)) == sorted(PLATFORM_SOURCED)


def test_no_retired_application_is_declared() -> None:
    print(f"[D7.1] {len(RETIRED)} retired Application(s) ({', '.join(RETIRED)}) must be absent")
    assert retired_present(REPOSITORY) == []


def test_every_file_is_the_application_root_adopts_by_name() -> None:
    assert identity_errors(REPOSITORY) == []


def test_root_selects_every_file() -> None:
    assert outside_root(REPOSITORY) == []


def test_no_application_carries_prune_false() -> None:
    found, read = manifests_with_prune_false(REPOSITORY)
    print(f"[E3] {read} manifest(s) under applications/ read, {len(found)} carry {PRUNE_FALSE}")
    assert read >= len(ADOPTED)
    assert found == []


def test_no_application_carries_a_finalizer() -> None:
    assert finalized(REPOSITORY) == []


def test_no_application_automated_block_carries_prune() -> None:
    assert automated_prune_present(REPOSITORY) == []


def test_every_application_retry_matches_the_chart_example() -> None:
    print(f"[S0] {len(ADOPTED)} retry block(s) compared against {CHART_EXAMPLE_SOURCE}")
    assert retry_errors(REPOSITORY) == []


def test_a_changed_spec_reddens(copy: Path) -> None:
    path = copy / "applications" / "mariadb-operator.yaml"
    text = path.read_text()
    assert "selfHeal: true" in text
    path.write_text(text.replace("selfHeal: true", "selfHeal: false", 1))
    assert platform_clause_errors(copy) == [("mariadb-operator", "unchanged")]


def test_a_missing_file_reddens(copy: Path) -> None:
    (copy / "applications" / "mariadb-operator.yaml").unlink()
    assert platform_errors(copy) == ["mariadb-operator"]


RETIRED_APPLICATION = """apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: mariadb-operator-crds
  namespace: argocd
spec:
  project: default
  source:
    repoURL: https://helm.mariadb.com/mariadb-operator
    chart: mariadb-operator-crds
    targetRevision: 26.6.0
  destination:
    server: https://kubernetes.default.svc
    namespace: mariadb-system
"""


def test_a_retired_application_in_a_subdirectory_reddens(copy: Path) -> None:
    """Root recurses, and Argo's include glob lets `*` cross `/`, so root selects a nested file."""
    (copy / "applications" / "sub").mkdir()
    (copy / "applications" / "sub" / "x.yaml").write_text(RETIRED_APPLICATION)
    assert retired_present(copy) == ["mariadb-operator-crds"]


def test_a_restored_retired_file_reddens(copy: Path) -> None:
    (copy / "applications" / "mariadb-operator-crds.yaml").write_text(RETIRED_APPLICATION)
    assert retired_present(copy) == ["mariadb-operator-crds"]


def test_a_retired_application_under_another_file_name_reddens(copy: Path) -> None:
    (copy / "applications" / "mariadb-crds.yaml").write_text(RETIRED_APPLICATION)
    assert retired_present(copy) == ["mariadb-operator-crds"]


def test_a_renamed_application_reddens(copy: Path) -> None:
    path = copy / "applications" / "envoy-gateway.yaml"
    text = path.read_text()
    assert "  name: envoy-gateway\n" in text
    path.write_text(text.replace("  name: envoy-gateway\n", "  name: envoy-gateway-new\n", 1))
    assert identity_errors(copy) == ["envoy-gateway"]


def test_a_narrowed_root_include_reddens(copy: Path) -> None:
    path = copy / "projects" / "root.yaml"
    text = path.read_text()
    assert "applications/*.yaml" in text
    path.write_text(text.replace("applications/*.yaml", "nowhere/*.yaml"))
    assert outside_root(copy) == list(ADOPTED)


def test_prune_false_on_one_copy_reddens(copy: Path) -> None:
    path = copy / "applications" / "cert-manager.yaml"
    text = path.read_text()
    anchor = '    argocd.argoproj.io/sync-wave: "-10"\n'
    assert anchor in text
    path.write_text(text.replace(anchor, anchor + "    argocd.argoproj.io/sync-options: Prune=false\n", 1))
    found, _ = manifests_with_prune_false(copy)
    assert found == ["cert-manager.yaml"]


def test_prune_false_in_a_subdirectory_reddens(copy: Path) -> None:
    """Root recurses, and Argo's include glob lets `*` cross `/`, so root applies a nested file."""
    text = (copy / "applications" / "cert-manager.yaml").read_text()
    anchor = '    argocd.argoproj.io/sync-wave: "-10"\n'
    assert anchor in text
    (copy / "applications" / "sub").mkdir()
    (copy / "applications" / "sub" / "x.yaml").write_text(
        text.replace(anchor, anchor + "    argocd.argoproj.io/sync-options: Prune=false\n", 1)
    )
    found, _ = manifests_with_prune_false(copy)
    assert found == ["sub/x.yaml"]


def test_a_finalizer_reddens(copy: Path) -> None:
    path = copy / "applications" / "mariadb-operator.yaml"
    text = path.read_text()
    anchor = "  namespace: argocd\n"
    assert anchor in text
    path.write_text(
        text.replace(anchor, anchor + "  finalizers:\n    - resources-finalizer.argocd.argoproj.io\n", 1)
    )
    assert finalized(copy) == ["mariadb-operator"]


def test_a_reintroduced_prune_reddens(copy: Path) -> None:
    path = copy / "applications" / "keda.yaml"
    text = path.read_text()
    anchor = "    automated:\n      selfHeal: true\n"
    assert anchor in text
    path.write_text(text.replace(anchor, "    automated:\n      prune: true\n      selfHeal: true\n", 1))
    assert automated_prune_present(copy) == ["keda"]


def test_a_negative_retry_limit_reddens(copy: Path) -> None:
    path = copy / "applications" / "cert-manager.yaml"
    text = path.read_text()
    anchor = "      limit: 6\n"
    assert anchor in text
    path.write_text(text.replace(anchor, "      limit: -1\n", 1))
    assert retry_errors(copy) == ["cert-manager"]


def test_content_appended_after_retry_reddens(copy: Path) -> None:
    """A `spec` key appended after the retry block is a field outside `source`, and `unchanged` names it."""
    path = copy / "applications" / "mariadb-operator.yaml"
    text = path.read_text()
    assert text.endswith("        maxDuration: 5m\n")
    path.write_text(text + "  ignoreDifferences: []\n")
    assert platform_clause_errors(copy) == [("mariadb-operator", "unchanged")]


def test_keda_turned_off_reddens(copy: Path) -> None:
    path = copy / "applications" / "keda.yaml"
    document = yaml.safe_load(path.read_text())
    document["spec"]["source"]["helm"]["valuesObject"]["operators"]["keda"]["create"] = False
    path.write_text(yaml.safe_dump(document))
    assert platform_errors(copy) == ["keda"]


def test_argo_cd_left_unset_reddens(copy: Path) -> None:
    """An unset key falls back to `operators.create`; each one must be written false."""
    path = copy / "applications" / "keda.yaml"
    document = yaml.safe_load(path.read_text())
    del document["spec"]["source"]["helm"]["valuesObject"]["operators"]["argoCd"]
    path.write_text(yaml.safe_dump(document))
    assert platform_errors(copy) == ["keda"]


def test_a_release_name_reddens(copy: Path) -> None:
    path = copy / "applications" / "keda.yaml"
    document = yaml.safe_load(path.read_text())
    document["spec"]["source"]["helm"]["releaseName"] = "operators"
    path.write_text(yaml.safe_dump(document))
    assert platform_errors(copy) == ["keda"]


def test_a_moved_platform_pin_reddens(copy: Path) -> None:
    path = copy / "applications" / "keda.yaml"
    document = yaml.safe_load(path.read_text())
    document["spec"]["source"]["targetRevision"] = "0.1.20"
    path.write_text(yaml.safe_dump(document))
    assert platform_errors(copy) == ["keda"]


def test_a_moved_destination_reddens(copy: Path) -> None:
    path = copy / "applications" / "keda.yaml"
    document = yaml.safe_load(path.read_text())
    document["spec"]["destination"]["namespace"] = "yadgar-operators"
    path.write_text(yaml.safe_dump(document))
    assert platform_errors(copy) == ["keda"]


def test_a_changed_sync_wave_reddens(copy: Path) -> None:
    """LEDGER 1212. Before `annotations`, moving a wave changed no `spec` field

    `unchanged` reads, so it stayed green: a wrong wave order against the CRDs
    an operator now renders is a cold-install failure mode, not a diff any
    other clause here would have reported.
    """
    path = copy / "applications" / "mariadb-operator.yaml"
    document = yaml.safe_load(path.read_text())
    document["metadata"]["annotations"]["argocd.argoproj.io/sync-wave"] = "5"
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("mariadb-operator", "annotations")]


def test_a_dropped_annotation_reddens(copy: Path) -> None:
    path = copy / "applications" / "mariadb-operator.yaml"
    document = yaml.safe_load(path.read_text())
    del document["metadata"]["annotations"]
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("mariadb-operator", "annotations")]


def test_an_added_annotation_reddens(copy: Path) -> None:
    """A clause that compared only `sync-wave` would stay green here.

    Both other `annotations` red cases change or remove `sync-wave` itself, so
    a clause reading only that one key still catches them — a mutant that
    narrows the comparison to `sync-wave` survives both. An annotation added
    BESIDE an unchanged `sync-wave` is the case only a full `metadata.annotations`
    comparison catches.
    """
    path = copy / "applications" / "mariadb-operator.yaml"
    document = yaml.safe_load(path.read_text())
    document["metadata"]["annotations"]["argocd.argoproj.io/example"] = "x"
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("mariadb-operator", "annotations")]


def test_dropped_keda_resources_reddens(copy: Path) -> None:
    """Without the sizing, both KEDA Deployments' pod templates change and they roll.

    The `keda:` key stays, so only the resources clause can catch this.
    """
    path = copy / "applications" / "keda.yaml"
    document = yaml.safe_load(path.read_text())
    del document["spec"]["source"]["helm"]["valuesObject"]["keda"]["resources"]
    path.write_text(yaml.safe_dump(document))
    assert platform_errors(copy) == ["keda"]


def test_extra_platform_value_reddens(copy: Path) -> None:
    path = copy / "applications" / "keda.yaml"
    document = yaml.safe_load(path.read_text())
    document["spec"]["source"]["helm"]["valuesObject"]["nats"] = {"create": True}
    path.write_text(yaml.safe_dump(document))
    assert platform_errors(copy) == ["keda"]


def test_a_changed_repo_url_reddens(copy: Path) -> None:
    path = copy / "applications" / "keda.yaml"
    document = yaml.safe_load(path.read_text())
    document["spec"]["source"]["repoURL"] = "https://kedacore.github.io/charts"
    path.write_text(yaml.safe_dump(document))
    assert platform_errors(copy) == ["keda"]


def test_a_changed_chart_reddens(copy: Path) -> None:
    path = copy / "applications" / "keda.yaml"
    document = yaml.safe_load(path.read_text())
    document["spec"]["source"]["chart"] = "keda"
    path.write_text(yaml.safe_dump(document))
    assert platform_errors(copy) == ["keda"]


def test_an_extra_source_key_reddens(copy: Path) -> None:
    path = copy / "applications" / "keda.yaml"
    document = yaml.safe_load(path.read_text())
    document["spec"]["source"]["path"] = "chart"
    path.write_text(yaml.safe_dump(document))
    assert platform_errors(copy) == ["keda"]


def cert_manager_values(tree: Path) -> tuple[Path, dict]:
    path = tree / "applications" / "cert-manager.yaml"
    document = yaml.safe_load(path.read_text())
    return path, document


def test_cert_manager_crds_keep_false_reddens(copy: Path) -> None:
    """`crds.keep: false` takes `helm.sh/resource-policy: keep` off all six CRDs.

    Measured 2026-10-01: that is the only change in the render. Without the
    annotation a helm uninstall deletes the CRDs and every Certificate with
    them. Dropping the key changes nothing today, because cert-manager v1.21.1
    defaults it to true, so the mutation writes false.
    """
    path, document = cert_manager_values(copy)
    document["spec"]["source"]["helm"]["valuesObject"]["cert-manager"]["crds"]["keep"] = False
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("cert-manager", "carried")]


def test_dropped_cert_manager_webhook_resources_reddens(copy: Path) -> None:
    """Without the webhook's sizing, its pod template changes and the Deployment rolls."""
    path, document = cert_manager_values(copy)
    del document["spec"]["source"]["helm"]["valuesObject"]["cert-manager"]["webhook"]["resources"]
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("cert-manager", "carried")]


def test_cert_manager_turned_off_reddens(copy: Path) -> None:
    path, document = cert_manager_values(copy)
    document["spec"]["source"]["helm"]["valuesObject"]["operators"]["certManager"]["create"] = False
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("cert-manager", "toggle-on")]


def test_a_second_operator_on_cert_manager_reddens(copy: Path) -> None:
    """KEDA on beside cert-manager installs a second KEDA into `cert-manager`."""
    path, document = cert_manager_values(copy)
    document["spec"]["source"]["helm"]["valuesObject"]["operators"]["keda"]["create"] = True
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("cert-manager", "others-off")]


def test_operators_create_on_cert_manager_reddens(copy: Path) -> None:
    """`operators.create` is the fallback for an unset key; with every key written it must be absent."""
    path, document = cert_manager_values(copy)
    document["spec"]["source"]["helm"]["valuesObject"]["operators"]["create"] = False
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("cert-manager", "operator-keys")]


def envoy_gateway_values(tree: Path) -> tuple[Path, dict]:
    path = tree / "applications" / "envoy-gateway.yaml"
    document = yaml.safe_load(path.read_text())
    return path, document


def test_dropped_envoy_gateway_resources_reddens(copy: Path) -> None:
    """Without the sizing, the envoy-gateway Deployment's pod template changes and it rolls.

    The `gateway-helm:` key stays, so only the carried clause can catch this.
    """
    path, document = envoy_gateway_values(copy)
    del document["spec"]["source"]["helm"]["valuesObject"]["gateway-helm"]["deployment"]["envoyGateway"]["resources"]
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("envoy-gateway", "carried")]


def test_envoy_gateway_turned_off_reddens(copy: Path) -> None:
    path, document = envoy_gateway_values(copy)
    document["spec"]["source"]["helm"]["valuesObject"]["operators"]["envoyGateway"]["create"] = False
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("envoy-gateway", "toggle-on")]


def test_a_second_operator_on_envoy_gateway_reddens(copy: Path) -> None:
    """cert-manager on beside envoy-gateway installs a second cert-manager into `envoy-gateway-system`."""
    path, document = envoy_gateway_values(copy)
    document["spec"]["source"]["helm"]["valuesObject"]["operators"]["certManager"]["create"] = True
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("envoy-gateway", "others-off")]


def mutate_prometheus_values(tree: Path, change) -> None:
    """Parse prometheus's `helm.values` string, apply `change` to it, and write it back as a string."""
    path = tree / "applications" / "prometheus.yaml"
    document = yaml.safe_load(path.read_text())
    helm = document["spec"]["source"]["helm"]
    values = yaml.safe_load(helm["values"])
    change(values)
    helm["values"] = yaml.safe_dump(values)
    path.write_text(yaml.safe_dump(document))


def test_prometheus_values_round_trip_is_green(copy: Path) -> None:
    """The mutation helper itself changes nothing a clause reads, so each red case below is its own change."""
    mutate_prometheus_values(copy, lambda values: None)
    assert platform_clause_errors(copy) == []


def test_prometheus_pvc_disabled_reddens(copy: Path) -> None:
    """`enabled: false` renders an emptyDir: the pod rolls onto an empty TSDB, and the PVC is left extraneous."""
    mutate_prometheus_values(
        copy, lambda values: values["prometheus"]["server"]["persistentVolume"].update(enabled=False)
    )
    assert platform_clause_errors(copy) == [("prometheus", "carried")]


def test_prometheus_rules_dropped_reddens(copy: Path) -> None:
    """Without `serverFiles`, `platform` renders no alerting rules at all."""
    mutate_prometheus_values(copy, lambda values: values["prometheus"].pop("serverFiles"))
    assert platform_clause_errors(copy) == [("prometheus", "carried-digest")]


def test_prometheus_one_rule_dropped_reddens(copy: Path) -> None:
    def drop_first_rule(values: dict) -> None:
        values["prometheus"]["serverFiles"]["alerting_rules.yml"]["groups"][0]["rules"].pop(0)

    mutate_prometheus_values(copy, drop_first_rule)
    assert platform_clause_errors(copy) == [("prometheus", "carried-digest")]


def test_prometheus_reloader_resources_empty_map_reddens(copy: Path) -> None:
    """`{}` merges with `platform`'s 10m/32Mi instead of deleting them, so the pod rolls."""
    mutate_prometheus_values(
        copy, lambda values: values["prometheus"]["configmapReload"]["prometheus"].update(resources={})
    )
    assert platform_clause_errors(copy) == [("prometheus", "carried")]


def test_prometheus_reloader_resources_dropped_reddens(copy: Path) -> None:
    mutate_prometheus_values(copy, lambda values: values["prometheus"].pop("configmapReload"))
    assert platform_clause_errors(copy) == [("prometheus", "carried")]


def test_prometheus_memory_limit_dropped_reddens(copy: Path) -> None:
    """Ledger 1210: with no limit, the server's growth has no bound but the node's memory."""
    mutate_prometheus_values(copy, lambda values: values["prometheus"]["server"]["resources"].pop("limits"))
    assert platform_clause_errors(copy) == [("prometheus", "carried")]


def test_prometheus_memory_request_lowered_reddens(copy: Path) -> None:
    """`platform`'s 256Mi is below the measured >=790 MB peak, which is what ledger 1210 corrected."""
    mutate_prometheus_values(
        copy, lambda values: values["prometheus"]["server"]["resources"]["requests"].update(memory="256Mi")
    )
    assert platform_clause_errors(copy) == [("prometheus", "carried")]


def test_prometheus_server_resources_dropped_reddens(copy: Path) -> None:
    """Without the block, `platform`'s 100m/256Mi with no limit comes back, and the pod rolls."""
    mutate_prometheus_values(copy, lambda values: values["prometheus"]["server"].pop("resources"))
    assert platform_clause_errors(copy) == [("prometheus", "carried")]


def test_prometheus_retention_size_dropped_reddens(copy: Path) -> None:
    """local-path does not enforce the PVC's 2Gi, so without it nothing bounds the TSDB's size."""
    mutate_prometheus_values(copy, lambda values: values["prometheus"]["server"].pop("retentionSize"))
    assert platform_clause_errors(copy) == [("prometheus", "carried")]


def test_prometheus_retention_size_changed_reddens(copy: Path) -> None:
    """Any other value reddens; this one is the first revision's 1700MB.

    The clause is an equality, not a threshold: it cannot tell a safe value
    from one that cuts the 24h window. 1700 MiB is about 11% above the
    PROJECTED 1.5 to 1.6 GB 24h bound, which a review judged too thin.
    """
    mutate_prometheus_values(copy, lambda values: values["prometheus"]["server"].update(retentionSize="1700MB"))
    assert platform_clause_errors(copy) == [("prometheus", "carried")]


def test_prometheus_size_retention_rule_dropped_reddens(copy: Path) -> None:
    """Without it, size retention binding would shorten every `[24h]` range with no sign."""

    def drop_size_rule(values: dict) -> None:
        rules = values["prometheus"]["serverFiles"]["alerting_rules.yml"]["groups"][0]["rules"]
        rules[:] = [rule for rule in rules if rule["alert"] != "PrometheusSizeRetentionDeletedBlocks"]

    mutate_prometheus_values(copy, drop_size_rule)
    assert platform_clause_errors(copy) == [("prometheus", "carried-digest")]


def test_prometheus_turned_off_reddens(copy: Path) -> None:
    mutate_prometheus_values(copy, lambda values: values["operators"]["prometheus"].update(create=False))
    assert platform_clause_errors(copy) == [("prometheus", "toggle-on")]


def test_a_second_operator_on_prometheus_reddens(copy: Path) -> None:
    """KEDA on beside prometheus installs a second KEDA into `observability`."""
    mutate_prometheus_values(copy, lambda values: values["operators"]["keda"].update(create=True))
    assert platform_clause_errors(copy) == [("prometheus", "others-off")]


def test_prometheus_values_as_value_object_reddens(copy: Path) -> None:
    """`valuesObject` in place of the `values` string: the `null` it carries can be dropped on apply.

    That drop is reasoned from client-side apply's merge patch, not measured.

    Membership, not the exact list, on purpose: the row reads `values`, so with
    it gone the parsed values are empty and every clause that reads them fails
    too. `helm` is the clause that names this change.
    """
    path = copy / "applications" / "prometheus.yaml"
    document = yaml.safe_load(path.read_text())
    helm = document["spec"]["source"]["helm"]
    helm["valuesObject"] = yaml.safe_load(helm.pop("values"))
    path.write_text(yaml.safe_dump(document))
    assert ("prometheus", "helm") in platform_clause_errors(copy)


def mariadb_operator_values(tree: Path) -> tuple[Path, dict]:
    path = tree / "applications" / "mariadb-operator.yaml"
    document = yaml.safe_load(path.read_text())
    return path, document


def test_mariadb_operator_turned_off_reddens(copy: Path) -> None:
    path, document = mariadb_operator_values(copy)
    document["spec"]["source"]["helm"]["valuesObject"]["operators"]["mariadbOperator"]["create"] = False
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("mariadb-operator", "toggle-on")]


def test_a_second_operator_on_mariadb_operator_reddens(copy: Path) -> None:
    """KEDA on beside mariadb-operator installs a second KEDA into `mariadb-system`."""
    path, document = mariadb_operator_values(copy)
    document["spec"]["source"]["helm"]["valuesObject"]["operators"]["keda"]["create"] = True
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("mariadb-operator", "others-off")]


def test_operators_create_on_mariadb_operator_reddens(copy: Path) -> None:
    """`operators.create` is the fallback for an unset key; with every key written it must be absent."""
    path, document = mariadb_operator_values(copy)
    document["spec"]["source"]["helm"]["valuesObject"]["operators"]["create"] = False
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("mariadb-operator", "operator-keys")]


def test_a_mariadb_operator_subchart_key_reddens(copy: Path) -> None:
    """deploy's copy set no values, so the values hold `operators` and nothing else.

    A `mariadb-operator:` block reaches the subchart's own values, where
    `crds.enabled: true` would render the 12 CRDs a second time without
    `keep`. The row has no `values`, so `value-keys` is the clause that names it.
    """
    path, document = mariadb_operator_values(copy)
    document["spec"]["source"]["helm"]["valuesObject"]["mariadb-operator"] = {"crds": {"enabled": True}}
    path.write_text(yaml.safe_dump(document))
    assert platform_clause_errors(copy) == [("mariadb-operator", "value-keys")]
