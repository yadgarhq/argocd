"""`applications/yadgar.yaml` is the chart's own example, and renders what ran (E3, K3).

RETIRING `infra`, OPTION A (ADR-0828). The `yadgar` Application moved here from
`yadgarhq/deploy` as the parent chart's `example/application.yaml` at the
pinned tag, with this organisation's values inlined as `valuesObject`. Two
properties hold that shape, and both need the network, so this file sits in
`scripts/gates/` beside `test_no_two_owners.py` and runs in the `two-owners`
CI job, never in the offline pre-commit hook.

  E3  The file equals `yadgarhq/chart@v<targetRevision>:example/application.yaml`
      everywhere EXCEPT `spec.syncPolicy` and `spec.source.helm.valuesObject`.
      `syncPolicy` is excepted because S0 (ADR-0828) removes the example's
      `automated.prune`; `scripts/tests/test_infra_children.py` pins it exactly,
      offline. So a `syncPolicy`-only change stays green HERE by design, and
      that is asserted below rather than left to be discovered. The example is
      read at the tag the file pins, so a pin bump compares against that
      release's example and the file moves with it.

  K3  The render of the pinned parent at `valuesObject` equals, object for
      object, the committed per-object digests in `yadgar_render.sha256`
      beside this file: 88 objects, measured 2026-10-01 at 0.3.13. That table was
      written from this exact render AND proved equal to the render of deploy's
      `infra/yadgar/values.yaml` at 05b160b with the same flags — "this
      organisation's render, before and after the move of its values:
      byte-identical, 0 differing objects". A difference is named by object.
      Re-measured 2026-10-03 at 0.3.38 (ledger 1266): still 88 objects, none
      added or removed, 10 changed (six module Deployments and four platform
      hook Jobs). Re-measured 2026-10-08 at 0.13.13: 90 objects, 2 added
      (`Certificate/nats-tls` and `Certificate/valkey-tls`, platform 0.1.36's
      serving leaves), none removed, 13 changed (seven module Deployments,
      the `preflight` and `envoy-gateway-probe` hook Jobs, and `Prune=false`
      on the four edge objects). Re-measured 2026-10-08 with the four client
      leaves (ledger 770): 90 objects, 4 changed (the `gateway`, `iam`, `task`
      and `project` Deployments, each gaining its client-leaf volume, mount
      and `*_TLS_CLIENT_{CERT,KEY}_FILE` env). Re-measured 2026-10-09 at
      0.19.1 with each server's `clientAuth: "off"` and client CA (PB-2,
      ADR-0883): 90 objects, none added or removed, 6 changed (the six
      server Deployments: image, `LISTEN_TLS_CLIENT_AUTH`,
      `LISTEN_TLS_CLIENT_CA_FILE`, and a new `client-ca` volume and mount).
      Re-measured 2026-10-09 with task-db's `clientAuth: "optional"` (B-U8a,
      ledger 925): 90 objects, none added or removed, 1 changed (the
      `task-db` Deployment's `LISTEN_TLS_CLIENT_AUTH`, `off` to `optional`).
      Re-measured 2026-10-09 with iam-db's and project-db's `clientAuth:
      "optional"` (B-U8b, ledger 925): 90 objects, none added or removed, 2
      changed (the `iam-db` and `project-db` Deployments'
      `LISTEN_TLS_CLIENT_AUTH`, `off` to `optional`).
      Re-measured 2026-10-09 with project's `clientAuth: "optional"` (B-U8c,
      ledger 925): 90 objects, none added or removed, 1 changed (the
      `project` Deployment's `LISTEN_TLS_CLIENT_AUTH`, `off` to `optional`).
      Re-measured 2026-10-09 with task's `clientAuth: "optional"` (B-U8d,
      ledger 925): 90 objects, none added or removed, 1 changed (the `task`
      Deployment's `LISTEN_TLS_CLIENT_AUTH`, `off` to `optional`).
      Re-measured 2026-10-09 with iam's `clientAuth: "optional"` (B-U8e,
      ledger 925): 90 objects, none added or removed, 1 changed (the `iam`
      Deployment's `LISTEN_TLS_CLIENT_AUTH`, `off` to `optional`).
      Re-measured 2026-10-09 with iam-db's and project-db's `clientAuth:
      "required"` (B-U9, ledger 925): 90 objects, none added or removed, 2
      changed (the `iam-db` and `project-db` Deployments'
      `LISTEN_TLS_CLIENT_AUTH`, `optional` to `required`).
      Re-measured 2026-10-09 with project's `clientAuth: "required"` (B-U9,
      ledger 925): 90 objects, none added or removed, 1 changed (the
      `project` Deployment's `LISTEN_TLS_CLIENT_AUTH`, `optional` to
      `required`).
      Re-measured 2026-10-09 at 0.23.1 with the NATS and valkey TLS keys
      stated off (PB-3, ledger 925): 90 objects, none added or removed, 6
      changed (the `gateway`, `iam`, `task` and `project` images; gateway's
      `NATS_TLS_ENABLED` and `VALKEY_TLS_ENABLED` "0" and iam's
      `NATS_TLS_ENABLED` "0"; valkey's unix-socket args, probes and
      `/run/valkey` `emptyDir`; the nats pod's `checksum/config` annotation).
      Re-measured 2026-10-10 at 0.23.1 with task-db's `clientAuth:
      "required"` (B-U9, ledger 925): 90 objects, none added or removed, 1
      changed (the `task-db` Deployment's `LISTEN_TLS_CLIENT_AUTH`,
      `optional` to `required`).
      Re-measured 2026-10-09 at 0.23.1 with task's `clientAuth:
      "required"` (B-U9.4, ledger 925): 90 objects, none added or removed, 1
      changed (the `task` Deployment's `LISTEN_TLS_CLIENT_AUTH`, `optional`
      to `required`).
      Re-measured 2026-10-09 at 0.23.1 with iam's `clientAuth:
      "required"` (B-U9.5, ledger 925): 90 objects, none added or removed, 1
      changed (the `iam` Deployment's `LISTEN_TLS_CLIENT_AUTH`, `optional`
      to `required`).
      Re-measured 2026-10-10 at 0.23.2 with the broker's TLS listener on and
      `allow_non_tls` (B-N4.1, ledger 925): 90 objects, none added or
      removed, 4 changed (`ConfigMap/nats-config` gains `allow_non_tls` and
      the `tls` cert/key block; `StatefulSet/nats` gains the `nats-tls`
      volume, its mount in both containers, the reloader's two `-config`
      args and a new `checksum/config`; `Service/nats` and
      `Service/nats-headless` move `appProtocol` from `tcp` to `tls`).
      Re-measured 2026-10-10 at 0.23.2 with gateway's and iam's
      `nats.tls.enabled: true` (B-N4.2, ledger 925): 90 objects, none added
      or removed, 2 changed (the `gateway` and `iam` Deployments:
      `NATS_TLS_ENABLED` "1", `NATS_TLS_CA_FILE` and the client cert/key
      env, a `nats-ca` volume and mount from `nats-tls`, and iam's own
      `nats-client-cert` volume and mount from `iam-client-tls`).
      Re-measured 2026-10-10 at 0.23.2 with `allow_non_tls` dropped
      (B-N4.3, ledger 925): 90 objects, none added or removed, 2 changed
      (`ConfigMap/nats-config` loses `allow_non_tls`; `StatefulSet/nats`
      gets a new `checksum/config`).
      Re-measured 2026-10-10 at 0.23.2 with `clientAuth: "required"`
      (B-N5, ledger 925): 90 objects, none added or removed, 2 changed
      (`ConfigMap/nats-config` gains `tls.verify` true and `tls.ca_file`;
      `StatefulSet/nats` gets a new `checksum/config` and its reloader
      watches `ca.crt` too).
      Re-measured 2026-10-10 at 0.23.2 with `platform.valkey.tls.enabled:
      true` (B-V4.1, ledger 925): 90 objects, none added or removed, 3
      changed (`Deployment/valkey` gains the TLS args, the boot hash, the
      liveness hash check, `VALKEY_TLS_AUTH_CLIENTS` "no", port `valkey-tls`
      6380 and the `valkey-tls` volume and mount; `Service/valkey` gains port
      `valkey-tls`; `NetworkPolicy/valkey-ingress` admits 6380 beside 6379).
      Re-measured 2026-10-10 at 0.23.2 with gateway's `valkey.tls.enabled:
      true` (B-V4.2, ledger 925): 90 objects, none added or removed, 1
      changed (`Deployment/gateway`: `VALKEY_TLS_ENABLED` "1",
      `VALKEY_TLS_CA_FILE`, `VALKEY_TLS_CLIENT_CERT_FILE` and
      `VALKEY_TLS_CLIENT_KEY_FILE`, and a `valkey-ca` volume and mount from
      `valkey-tls`).
      Re-measured 2026-10-10 at 0.23.2 with `platform.valkey.tls.plaintext:
      false` (B-V4.3, ledger 925): 90 objects, none added or removed, 3
      changed (`Deployment/valkey`: `--port 6379` becomes `--port 0` and the
      containerPort `valkey` 6379 goes; `Service/valkey` drops port
      `valkey`; `NetworkPolicy/valkey-ingress` admits 6380 only).
      Re-measured 2026-10-10 at 0.23.2 with `platform.valkey.tls.clientAuth:
      "optional"` (B-V5, ledger 925): 90 objects, none added or removed, 1
      changed (`Deployment/valkey`: `VALKEY_TLS_AUTH_CLIENTS` "no" becomes
      "optional", nothing else).
      Re-measured 2026-10-10 at 0.23.2 with `platform.valkey.tls.clientAuth:
      "required"` (B-V6, ledger 925): 90 objects, none added or removed, 1
      changed (`Deployment/valkey`: `VALKEY_TLS_AUTH_CLIENTS` "optional"
      becomes "yes", nothing else).
      Re-measured 2026-10-11 at 0.23.8, carrying gateway 0.12.2 (ledger
      1430), iam 0.11.3 (ledgers 1422, 1424) and task 0.7.2 (ledger 1422):
      90 objects, none added or removed, 3 changed (the `gateway`, `iam`
      and `task` Deployments, `image` only — each new digest equals its
      module's own GHCR tag digest; no new env, arg or probe).
      Re-measured 2026-10-11 at 0.23.10, carrying platform 0.2.3 (ledger
      1437): 90 objects, none added or removed, 1 changed
      (`Deployment/valkey`: the boot script opens with `set -eu`, hashes
      `/etc/valkey/auth/password` beside the TLS files into
      `/run/valkey/watched.sha256` and reads the password from that file;
      the liveness probe checks the new hash path; the readiness probe reads
      the file into `VALKEYCLI_AUTH`; `VALKEY_PASSWORD` and `VALKEYCLI_AUTH`
      leave `env`; `valkey-password` is mounted as a directory at
      `/etc/valkey/auth`).
      Re-measured 2026-10-16 at 0.23.15, carrying gateway 0.12.3 (ledger
      1438), iam 0.11.4 (ledger 1439), and iam-db 0.10.1, project-db 0.6.1
      and task-db 0.9.1 (each a `Cargo.lock`-only dependency bump): 90
      objects, none added or removed, 5 changed (the `gateway`, `iam`,
      `iam-db`, `project-db` and `task-db` Deployments, `image` only — each
      new digest equals its module's own GHCR tag digest; no new env, arg,
      probe, volume or mount).
      Re-measured 2026-10-16 at 0.24.9, carrying gateway 0.12.5, iam 0.11.6,
      iam-db 0.10.3, project 0.3.3, project-db 0.6.3, task 0.7.4 and task-db
      0.9.3 (config 0.2.1 and platform 0.2.4 render nothing new): 90 objects,
      none added or removed, 7 changed (the seven module Deployments, `image`
      only — each new digest equals its module's own GHCR tag digest; no new
      env, arg, probe, volume or mount).
      Re-measured 2026-10-17 at 0.24.17, carrying gateway 0.12.6, iam 0.11.7,
      iam-db 0.10.4, project 0.3.4, project-db 0.6.4, task 0.7.5, task-db
      0.9.4 and platform 0.2.5 (config 0.2.1 unchanged): 90 objects, none
      added or removed, 8 changed. The seven module Deployments change
      `image` (each new digest equals its module's own GHCR tag digest), and
      four of them gain request-timeout env from the chart defaults (ADR-0925
      Q-C1): gateway `TASK_REQUEST_TIMEOUT_SECONDS` "16",
      `IAM_REQUEST_TIMEOUT_SECONDS` "30", `PROJECT_REQUEST_TIMEOUT_SECONDS`
      "30"; iam `IAM_DB_REQUEST_TIMEOUT_SECONDS` "30"; project
      `PROJECT_DB_REQUEST_TIMEOUT_SECONDS` "30"; task
      `TASK_DB_REQUEST_TIMEOUT_SECONDS` "30". `StatefulSet/nats` (ADR-0924):
      a `/bin/sh -c` wrapper hashes the two passwords mounted from
      `nats-auth` and `nats-auth-gateway` into `/var/run/nats/auth.sha256`
      and execs nats-server; `NATS_PASSWORD` and `NATS_PASSWORD_GATEWAY`
      leave `env` as `secretKeyRef`s; the liveness probe is an `exec` that
      runs `sha256sum -c` before `/healthz`; two secret volumes and their
      read-only mounts are added.
      Re-measured 2026-10-18 at 0.25.8, carrying gateway 0.12.9, iam
      0.11.8, iam-db 0.10.6, project 0.3.5, project-db 0.7.0, platform 0.2.6
      and the new seeder 0.1.0 (config 0.2.1, task 0.7.5 and task-db 0.9.4
      unchanged), with this organisation's `seeder.registry` declared and
      `seeder` added to `project.networkPolicy.clients` (plans/the-d35-
      seeder.md stage 7): 94 objects, 4 added, none removed, 6 changed. Added:
      `Certificate/seeder-client-tls` (platform 0.2.6, common name
      `seeder-caller`), and the seeder's `Job/seeder`, `ConfigMap/seeder-
      registry` and `ServiceAccount/seeder`. Changed: the `gateway`, `iam`,
      `iam-db`, `project` and `project-db` Deployments, `image` only (each
      new digest equals its module's own GHCR tag digest), and
      `NetworkPolicy/project-ingress`, which admits `app: seeder` beside
      `app: gateway` on 50052.
      Re-measured 2026-10-18 at 0.25.9, carrying project-db 0.7.1 (every
      other module and platform 0.2.6 unchanged): 94 objects, none added or
      removed, 1 changed (the `project-db` Deployment, `image` only; the new
      digest equals `project-db:0.7.1`'s GHCR tag digest). 0.7.1 adds
      append-only migration 6, `project_class_columns`, which a store
      migrated by v0.1.x never received (the 2026-10-18 seeder failure,
      `1054 Unknown column 'source_repo'`).
      Re-measured 2026-10-18 at 0.25.12, carrying gateway 0.12.10, project
      0.3.6 and project-db 0.7.2 (every other module and platform 0.2.6
      unchanged): 94 objects, none added or removed, 3 changed (the
      `gateway`, `project` and `project-db` Deployments, `image` only; each
      new digest equals its module's own GHCR tag digest; no new env, arg,
      probe, volume or mount, and no migration).

      A PIN BUMP OR A VALUES CHANGE IS A RENDER CHANGE, AND THIS REDDENS ON IT
      ON PURPOSE. Re-measure in the same pull request, read the named objects,
      and rewrite the table:

          python3 scripts/gates/test_yadgar_application.py --write

      (helm 3.18.4 on PATH, as in CI). The diff of `yadgar_render.sha256` is
      then the review: every changed line is an object the sync would change.

      `sha256sum`'S LAYOUT, NOT JSON, AND THE REASON IS gitleaks. A JSON map of
      object name to digest reads to gitleaks' `generic-api-key` rule as
      `"<name containing secret|key>": "<high-entropy string>"`, and it refused
      the first draft on `bootstrap-secrets` and `valkey`. A digest written
      BEFORE its name carries no assignment for the rule to match, so the
      file stays scannable rather than allow-listed.

WHAT IS ONLY MEASURED, NOT GATED: that this render equals what Argo CD renders
live. Argo passes the cluster's own `--api-versions` and `--kube-version`; this
gate passes the six `test_no_two_owners.py` uses. The pull request that
adopted the file measured both, and both were 0-diff.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import urllib.request
from pathlib import Path

import pytest
import yaml
from test_no_two_owners import REPOSITORY, parent_render, tuples_of  # noqa: F401  (same flags as P)

APPLICATION = REPOSITORY / "applications" / "yadgar.yaml"
TABLE = Path(__file__).resolve().parent / "yadgar_render.sha256"
EXAMPLE_URL = "https://raw.githubusercontent.com/yadgarhq/chart/v{revision}/example/application.yaml"
EXPECTED_OBJECTS = 94

# The two paths E3 excepts, and nothing else.
EXCEPTED = (("spec", "syncPolicy"), ("spec", "source", "helm", "valuesObject"))


# ── E3 ───────────────────────────────────────────────────────────────────────


def fetch_example(revision: str) -> dict:
    with urllib.request.urlopen(EXAMPLE_URL.format(revision=revision), timeout=30) as response:  # noqa: S310
        return yaml.safe_load(response.read())


def without_excepted(document: dict) -> dict:
    trimmed = json.loads(json.dumps(document))
    for path in EXCEPTED:
        node = trimmed
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, dict):
            node.pop(path[-1], None)
    return trimmed


def field_differences(old, new, path: str = "") -> list[str]:
    """Every dotted path where `old` and `new` differ."""
    if isinstance(old, dict) and isinstance(new, dict):
        return [
            difference
            for key in sorted(set(old) | set(new))
            for difference in field_differences(old.get(key), new.get(key), f"{path}.{key}".lstrip("."))
        ]
    return [] if old == new else [path or "<root>"]


def example_differences(document: dict, example: dict) -> list[str]:
    return field_differences(without_excepted(example), without_excepted(document))


def committed() -> dict:
    return yaml.safe_load(APPLICATION.read_text())


def revision_of(document: dict) -> str:
    return document["spec"]["source"]["targetRevision"]


@pytest.fixture(scope="module")
def example() -> dict:
    return fetch_example(revision_of(committed()))


def test_the_application_is_the_example_outside_sync_policy_and_values(example: dict) -> None:
    document = committed()
    print(f"[E3] applications/yadgar.yaml vs yadgarhq/chart@v{revision_of(document)}:example/application.yaml")
    assert example_differences(document, example) == []


def test_an_added_ignore_differences_reddens(example: dict) -> None:
    """chart#27 dropped the MariaDB entry from the example (ledger 1266); a copy that keeps one is not the example."""
    document = committed()
    document["spec"]["ignoreDifferences"] = [
        {
            "group": "k8s.mariadb.com",
            "kind": "MariaDB",
            "jsonPointers": ["/spec/rootPasswordSecretKeyRef/generate", "/spec/passwordSecretKeyRef/generate"],
        }
    ]
    assert example_differences(document, example) == ["spec.ignoreDifferences"]


def test_an_added_annotation_reddens(example: dict) -> None:
    document = committed()
    document["metadata"]["annotations"] = {"argocd.argoproj.io/sync-wave": "10"}
    assert example_differences(document, example) == ["metadata.annotations"]


def test_a_second_source_reddens(example: dict) -> None:
    document = committed()
    document["spec"]["sources"] = [document["spec"].pop("source")]
    assert example_differences(document, example) == ["spec.source", "spec.sources"]


def test_a_value_file_beside_the_object_reddens(example: dict) -> None:
    document = committed()
    document["spec"]["source"]["helm"]["valueFiles"] = ["values.yaml"]
    assert example_differences(document, example) == ["spec.source.helm.valueFiles"]


def test_a_sync_policy_change_is_excepted_here(example: dict) -> None:
    """By design (Max, 2026-10-01): E3 is "the example except `syncPolicy`".

    `test_infra_children.py` is what reddens on this; see its
    `test_a_reintroduced_prune_reddens` and siblings.
    """
    document = committed()
    document["spec"]["syncPolicy"]["syncOptions"].append("ServerSideApply=true")
    assert example_differences(document, example) == []


def test_the_example_with_prune_differs_only_there(example: dict) -> None:
    """The one place S0 departs from the example, read off the example itself."""
    assert example["spec"]["syncPolicy"]["automated"] == {"prune": True, "selfHeal": True}
    assert committed()["spec"]["syncPolicy"]["automated"] == {"selfHeal": True}
    rest = {k: v for k, v in example["spec"]["syncPolicy"].items() if k != "automated"}
    assert rest == {k: v for k, v in committed()["spec"]["syncPolicy"].items() if k != "automated"}


# ── K3 ───────────────────────────────────────────────────────────────────────


def object_key(document: dict) -> str:
    api_version = str(document.get("apiVersion", ""))
    group = api_version.split("/")[0] if "/" in api_version else ""
    metadata = document.get("metadata") or {}
    return f"{group}/{document['kind']}/{metadata.get('namespace', '')}/{metadata['name']}"


def digests(documents: list) -> dict[str, str]:
    """Per object, sha256 of its canonical JSON. A key rendered twice is a failure, not an overwrite."""
    table: dict[str, str] = {}
    for document in documents:
        if not isinstance(document, dict) or not document.get("kind"):
            continue
        key = object_key(document)
        assert key not in table, f"{key} rendered twice"
        table[key] = hashlib.sha256(json.dumps(document, sort_keys=True).encode()).hexdigest()
    return table


def render_differences(table: dict[str, str], rendered: dict[str, str]) -> dict[str, list[str]]:
    return {
        "removed": sorted(table.keys() - rendered.keys()),
        "added": sorted(rendered.keys() - table.keys()),
        "changed": sorted(k for k in table.keys() & rendered.keys() if table[k] != rendered[k]),
    }


NO_DIFFERENCE = {"removed": [], "added": [], "changed": []}


TABLE_HEADER = "# K3: sha256 of each object `applications/yadgar.yaml` renders, as canonical JSON."
TABLE_REGENERATE = "# Regenerate: python3 scripts/gates/test_yadgar_application.py --write"


def read_table() -> tuple[str, dict[str, str]]:
    """`(targetRevision, {object: digest})` from the committed table."""
    revision, objects = "", {}
    for line in TABLE.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        first, _, rest = line.partition("  ")
        if first == "targetRevision":
            revision = rest
        else:
            assert rest not in objects, f"{rest} listed twice in {TABLE.name}"
            objects[rest] = first
    return revision, objects


@pytest.fixture(scope="module")
def table() -> dict[str, str]:
    return read_table()[1]


def test_the_render_is_the_committed_table(table: dict[str, str]) -> None:
    rendered = digests(parent_render(REPOSITORY))
    print(f"[K3] {len(rendered)} object(s) rendered, {len(table)} in {TABLE.name}")
    assert len(table) == EXPECTED_OBJECTS
    assert render_differences(table, rendered) == NO_DIFFERENCE


def test_the_table_names_its_pin(table: dict[str, str]) -> None:
    assert read_table()[0] == revision_of(committed())


def test_a_moved_node_port_names_the_edge_only(table: dict[str, str]) -> None:
    """Red case: the edge's pinned HTTPS nodePort moved. Only `EnvoyProxy/edge` changes."""
    rendered = digests(parent_render(REPOSITORY, ("--set", "platform.gatewayListener.envoyProxy.httpsNodePort=30444")))
    assert render_differences(table, rendered) == {
        "removed": [],
        "added": [],
        "changed": ["gateway.envoyproxy.io/EnvoyProxy//edge"],
    }


def test_autoscaling_off_names_the_scaled_object_and_the_deployment(table: dict[str, str]) -> None:
    """Red case: gateway's autoscaling off. Its ScaledObject goes, and its Deployment changes and rolls."""
    rendered = digests(parent_render(REPOSITORY, ("--set", "gateway.autoscaling.enabled=false")))
    assert render_differences(table, rendered) == {
        "removed": ["keda.sh/ScaledObject//gateway"],
        "added": [],
        "changed": ["apps/Deployment//gateway"],
    }


# ── THE FOUR CLIENT LEAVES (ledger 770, B-U1) ────────────────────────────────

# Each caller's Deployment and the client leaf `platform` issues for it.
# K3 alone cannot hold this: `--write` blesses whatever renders, so a misspelt
# Secret name would pass K3 while mounting nothing that exists.
CLIENT_LEAVES = {
    "gateway": "gateway-client-tls",
    "iam": "iam-client-tls",
    "task": "task-client-tls",
    "project": "project-client-tls",
}


def test_every_issued_client_leaf_is_mounted_by_its_caller() -> None:
    documents = [d for d in parent_render(REPOSITORY) if isinstance(d, dict) and d.get("kind")]
    issued = {
        d["spec"]["secretName"]
        for d in documents
        if d["kind"] == "Certificate" and d["spec"]["secretName"].endswith("-client-tls")
    }
    mounted = {
        (d["kind"], d["metadata"]["name"]): {
            v["secret"]["secretName"] for v in d["spec"]["template"]["spec"].get("volumes", []) if "secret" in v
        }
        for d in documents
        if d["kind"] in ("Deployment", "Job")
    }
    # The seeder's leaf is mounted by a Job, not a Deployment (JOB_CLIENT_LEAVES).
    expected = {("Deployment", name): leaf for name, leaf in CLIENT_LEAVES.items()} | {
        ("Job", name): leaf for name, leaf in JOB_CLIENT_LEAVES.items()
    }
    assert issued == set(expected.values())
    assert {caller: leaf for caller, leaf in expected.items() if leaf not in mounted.get(caller, set())} == {}


# ── THE SIX SERVERS' CLIENT AUTH MODE AND CLIENT CA (PB-2, B-U8, B-U9) ─────

# Each gRPC server and the cert-manager Secret it verifies callers against:
# its OWN serving leaf, `<server>-tls`, key `ca.crt` (ADR-0883). cert-manager
# writes the issuing CA into every leaf it issues from one Issuer, so
# `<server>-tls`'s `ca.crt` is the CA that issued the `*-client-tls` leaves
# above. The CA root's own Secret holds the CA private key and is never named.
SERVERS = ("iam", "iam-db", "task", "task-db", "project", "project-db")
CLIENT_CA_ENV = "LISTEN_TLS_CLIENT_CA_FILE"
CLIENT_CA_ITEMS = [{"key": "ca.crt", "path": "ca.crt"}]

# The mode each server states, one hop at a time (ADR-0852): off, then
# optional (B-U8a..e), then required (B-U9.1..5). PB-2 staged all six at
# `off`. B-U8a moves task-db, whose only caller is task. B-U8b moves iam-db
# and project-db, whose only callers are iam and project. B-U8c moves
# project, whose only caller is gateway. B-U8d moves task, whose only caller
# is gateway. B-U8e moves iam, whose only caller is gateway, so all six are
# `optional`. B-U9 moves iam-db and project-db to `required` first: each has
# one caller and steady passive traffic. task-db, the plan's first, waits: its
# hop carries no request without an operator call. B-U9 (project) moves
# project, whose only caller is gateway: the gateway's registry poll is its
# passive signal. B-U9 (task-db) moves task-db once operator find_tasks calls
# crossed its hop OK under optional (ADR-0884). B-U9.4 moves task, whose
# only caller is gateway, once operator find_tasks calls crossed gateway →
# task OK under optional (ADR-0884). B-U9.5 moves iam, the last, whose only
# caller is gateway, once operator logins and the credential resolves behind
# them crossed gateway → iam OK under optional (ADR-0884). No server is left
# at optional; a revert edits this one line.
EXPECTED_CLIENT_AUTH = {server: "off" for server in SERVERS} | {
    "task-db": "required",
    "iam-db": "required",
    "project-db": "required",
    "project": "required",
    "task": "required",
    "iam": "required",
}


def test_every_server_states_its_client_auth_and_stages_its_own_ca() -> None:
    """ADR-0854: the mode is stated, never absent. ADR-0883: the CA is staged.

    K3 alone cannot hold this: `--write` blesses whatever renders, so a
    misspelt Secret, a CA staged at the root, a wrong `ca.crt` item, or a
    dropped or unplanned `clientAuth` would pass K3. Read both the values and
    the render.
    """
    application = yaml.safe_load(APPLICATION.read_text())
    values = application["spec"]["source"]["helm"]["valuesObject"]
    stated = {server: (values.get(server) or {}).get("tls") or {} for server in SERVERS}
    assert {
        server: (tls.get("clientAuth"), tls.get("clientCaSecret"), tls.get("clientCaSecretKey"))
        for server, tls in stated.items()
        if (tls.get("clientAuth"), tls.get("clientCaSecret"), tls.get("clientCaSecretKey"))
        != (EXPECTED_CLIENT_AUTH[server], f"{server}-tls", "ca.crt")
    } == {}

    documents = [d for d in parent_render(REPOSITORY) if isinstance(d, dict) and d.get("kind")]
    issued = {d["spec"]["secretName"]: d["spec"]["issuerRef"] for d in documents if d["kind"] == "Certificate"}
    deployments = {d["metadata"]["name"]: d for d in documents if d["kind"] == "Deployment"}
    # The issuer check below is only as good as its reference. Without these
    # two lines, a client leaf and a server Certificate that both stopped
    # rendering compare None == None and pass (ledger 1392).
    leaf_issuers = {leaf: issued.get(leaf) for leaf in CLIENT_LEAVES.values()}
    assert {leaf: issuer for leaf, issuer in leaf_issuers.items() if not issuer} == {}, "a client leaf did not render"
    reference = leaf_issuers[CLIENT_LEAVES["gateway"]]
    assert {leaf: issuer for leaf, issuer in leaf_issuers.items() if issuer != reference} == {}
    wrong: dict[str, object] = {}
    for server in SERVERS:
        secret = f"{server}-tls"
        # Issued by the same Issuer as every client leaf, so its ca.crt verifies them.
        if issued.get(secret) != reference:
            wrong[f"{server}: issuer"] = issued.get(secret)
        pod = deployments[server]["spec"]["template"]["spec"]
        env = {e["name"]: e.get("value") for e in pod["containers"][0].get("env", [])}
        if env.get("LISTEN_TLS_CLIENT_AUTH") != EXPECTED_CLIENT_AUTH[server]:
            wrong[f"{server}: LISTEN_TLS_CLIENT_AUTH"] = env.get("LISTEN_TLS_CLIENT_AUTH")
        volume = next((v for v in pod.get("volumes", []) if v["name"] == "client-ca"), None)
        if (
            volume is None
            or volume["secret"].get("secretName") != secret
            or volume["secret"].get("optional", False)
            # Exactly the CA, at the path the env names, and never tls.key (ledger 1392).
            or volume["secret"].get("items") != CLIENT_CA_ITEMS
        ):
            wrong[f"{server}: client-ca volume"] = volume
        mount = {m["name"]: m for m in pod["containers"][0].get("volumeMounts", [])}.get("client-ca") or {}
        if not mount.get("readOnly"):
            wrong[f"{server}: client-ca mount"] = mount
        # The path is each chart's own (iam-db mounts under /var/run/secrets);
        # what matters is that the env names the file this volume projects.
        if env.get(CLIENT_CA_ENV) != f"{mount.get('mountPath')}/ca.crt":
            wrong[f"{server}: {CLIENT_CA_ENV}"] = env.get(CLIENT_CA_ENV)
    assert wrong == {}



# ── EACH CALLER'S UPSTREAM CLIENT IDENTITY (ledger 1396, before B-U9) ───────

# Every hop a caller dials, by the env prefix its binary reads. The gateway
# dials three servers with ONE leaf; each backend caller dials its `-db`.
# B-U9 makes a server `required`; from then on a caller that stops presenting
# its leaf on a hop is an outage. K3 cannot hold this: `--write` blesses
# whatever renders, so a dropped `*_TLS_CLIENT_*` env, an env pointing at a
# file no volume projects, `*_TLS_ENABLED` flipped, or the volume pointed at
# another Secret would pass K3. `test_every_issued_client_leaf_is_mounted_by_
# its_caller` above holds only that the Secret is mounted SOMEWHERE in the pod.
# The parent chart's own B-U6 refusal (0.19.1 `_validate.tpl`) covers a caller
# that presents NOTHING to an `optional`/`required` server; it does not check
# WHICH leaf (`gateway.clientCertificate.secret=iam-client-tls` renders), nor
# that each env names a file the volume projects, nor a hop whose server is
# `off`.
#
# WHY `optional: true` ON THE LEAF VOLUME IS NOT REFUSED HERE. The gateway
# chart (0.10.2) hard-codes it. It is not a silent path for the gateway at
# v0.10.2, read off the source:
#   - `src/upstream/tls.rs`: with `<PREFIX>_TLS_ENABLED` "1", both
#     `_TLS_CLIENT_*_FILE` set become the dial's identity; one without the
#     other refuses the boot.
#   - `src/boot/wiring.rs` (`connect_task`, `connect_iam`, `connect_project`):
#     each `.map_err(refusal)?`, and `yadgar_dial` v0.2.14 `TlsOptions::prepare`
#     reads the certificate and the key BEFORE the lazy dial, so a missing
#     Secret (an empty optional mount) refuses the boot naming the path.
#   - `src/rotate.rs`: both files are in the rotation watch set, and a change
#     ends the process, so a Secret deleted later restarts into that refusal.
# The three backend callers read the same way (B-U9, re-read at the 0.19.1
# parent's pins: iam v0.10.0, task v0.7.0, project v0.3.0, all on yadgar-dial
# v0.2.14 and yadgar-lifecycle v0.2.20). Each dials its `-db` through
# `upstream::connect` (iam `src/boot.rs:283`, task `src/main.rs:245`, project
# `src/main.rs:175`) with `.map_err(|e| refusal(&e))?`, and that call is
# `yadgar_dial::connect_tls` with the identity from `UpstreamTls::options()`
# (`src/upstream.rs:303-333` in each). `connect_tls` (`src/connect.rs:226`)
# runs `TlsOptions::prepare` (`src/tls.rs:115`), which reads the certificate
# (`:187`) and the key (`:193`) before the lazy dial, so a missing file
# refuses the boot naming the path. Each `src/rotate.rs` puts the identity in
# the watch set as `Presented::Client` (iam `:146-148`, task `:88-90`,
# project `:90-92`), and a change ends the process (each `src/rotate.rs:15`,
# "The ruling: exit on change").
# So for all four callers the one silent path is a dropped or misdirected
# env, and that is what this pins.
#
# THE MAP IS COMPLETE, AND THE RENDER SAYS SO. `rendered_client_hops` reads
# every non-`LISTEN_` `*_TLS_ENABLED` and `*_TLS_CLIENT_CERT_FILE` env NAME in
# every container, init container and ephemeral container of every object in
# the render, hooks included. That set must equal this map plus
# `JOB_CLIENT_HOPS`, so a caller or hop added by a chart bump reddens here
# instead of going unpinned. No container in the render uses `envFrom`; one
# that did could carry a hop this scan cannot see.
CLIENT_HOPS = {
    "gateway": ("TASK", "IAM", "PROJECT", "NATS", "VALKEY"),
    "iam": ("IAM_DB", "NATS"),
    "task": ("TASK_DB",),
    "project": ("PROJECT_DB",),
}
# THE ONE CALLER THAT IS A JOB, NOT A DEPLOYMENT (plans/the-d35-seeder.md stage
# 7). The seeder dials `project` once per Argo sync and presents
# `seeder-client-tls`, common name `seeder-caller`: the one leaf `project`
# admits to `SyncProjects` (ADR-0936). Its chart has NO `PROJECT_TLS_ENABLED`
# switch, because TLS is not optional for it, so a scan of `_TLS_ENABLED` names
# alone never saw this hop. That is why the scan also reads
# `_TLS_CLIENT_CERT_FILE` names, and why `client_identity_failures` checks the
# leaf's common name for this caller: `project`'s authorisation reads it.
JOB_CLIENT_HOPS = {
    "seeder": ("PROJECT",),
}
JOB_CLIENT_LEAVES = {
    "seeder": "seeder-client-tls",
}
# The env-name suffixes that mark a client hop, by NAME whatever the value.
HOP_ENV_SUFFIXES = ("_TLS_ENABLED", "_TLS_CLIENT_CERT_FILE")
# A CALLER DIALED EVERY HOP ABOVE IN CLEARTEXT UNTIL ITS HOP STEP (PB-3, ledger
# 925): `CLEARTEXT_HOPS` and its check (`cleartext_failures`) held that interim
# posture, and `GATEWAY_VALKEY_CLEARTEXT` was its last entry (gateway/VALKEY,
# moved to `CLIENT_HOPS` by B-V4.2). ADR-0890 retired both once the ledger 925
# train closed (B-V4.3, B-N4.3): a caller in this map cannot return without
# first reverting the hop step that moved it out, so the interim state the map
# existed to hold is unreachable again. `pinned_hops` below now names every
# hop (`CLIENT_HOPS` alone), and `cache_failures`/`broker_failures` assert the
# final posture unconditionally — see ledger 925 post-close cleanup (ADR-0890).
# The cert-manager Issuer (not its Secret) every client leaf is issued from.
LEAF_ISSUER = {"group": "cert-manager.io", "kind": "Issuer", "name": "yadgar-internal-ca"}
# Each env half and the leaf key the volume must project to that file.
LEAF_KEYS = {"CERT": "tls.crt", "KEY": "tls.key"}
# The server leaf Secret whose `ca.crt` each hop's `<PREFIX>_TLS_CA_FILE` must
# trust (ledger 1428). `iam`/`task`/`project` and their `-db`s are the
# `<server>-tls` Secret ADR-0883 stages as each server's own `clientCaSecret`;
# `nats` and `valkey` are the chart's own `tls.caSecret` defaults, `nats-tls`
# and `valkey-tls`. Before this map existed, `client_identity_failures` read
# only `LEAF_KEYS` off a hop's env, so a `*_TLS_CA_FILE` pointed at ANY Secret
# keyed `ca.crt` passed — a caller trusting the wrong server's CA was invisible.
HOP_CA_SECRET = {
    "IAM": "iam-tls",
    "TASK": "task-tls",
    "PROJECT": "project-tls",
    "IAM_DB": "iam-db-tls",
    "TASK_DB": "task-db-tls",
    "PROJECT_DB": "project-db-tls",
    "NATS": "nats-tls",
    "VALKEY": "valkey-tls",
}


CONTAINER_LISTS = ("containers", "initContainers", "ephemeralContainers")


def rendered_client_hops(documents: list) -> set[tuple[str, str, str]]:
    """Every `(kind, name, PREFIX)` whose render carries a non-`LISTEN_` hop env (`HOP_ENV_SUFFIXES`).

    By NAME, whatever its value: a hop rendered `"0"` is still a hop. A hop
    with no `_TLS_ENABLED` switch (the seeder's) is seen by its client
    certificate env instead.
    """
    hops: set[tuple[str, str, str]] = set()

    def walk(node: object, kind: str, name: str) -> None:
        if isinstance(node, dict):
            for key in CONTAINER_LISTS:
                for container in node.get(key) or []:
                    for env in container.get("env") or []:
                        variable = env.get("name", "")
                        if variable.startswith("LISTEN_"):
                            continue
                        for suffix in HOP_ENV_SUFFIXES:
                            if variable.endswith(suffix):
                                hops.add((kind, name, variable.removesuffix(suffix)))
            for value in node.values():
                walk(value, kind, name)
        elif isinstance(node, list):
            for value in node:
                walk(value, kind, name)

    for document in documents:
        walk(document, document["kind"], document["metadata"]["name"])
    return hops


def env_value(documents: list, kind: str, name: str, variable: str) -> object:
    """The value of `variable` in any container of one object, for a failure message."""
    document = next(d for d in documents if d["kind"] == kind and d["metadata"]["name"] == name)
    found: list = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key in CONTAINER_LISTS:
                for container in node.get(key) or []:
                    found.extend(e.get("value") for e in container.get("env") or [] if e.get("name") == variable)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(document)
    return found[0] if found else None


def pinned_hops() -> set[tuple[str, str, str]]:
    """Every hop this file names (`CLIENT_HOPS` on Deployments, `JOB_CLIENT_HOPS` on Jobs)."""
    return {
        (kind, caller, prefix)
        for kind, hops in (("Deployment", CLIENT_HOPS), ("Job", JOB_CLIENT_HOPS))
        for caller, prefixes in hops.items()
        for prefix in prefixes
    }


def client_identity_failures(documents: list) -> dict[str, object]:
    """Every hop whose caller does not present its own issued leaf, keyed `<caller>/<PREFIX>: <what>`.

    THE PATHS ARE READ OFF THE RENDER, never written here: each env names a
    file, the mount at that file's directory names a volume, and that volume
    must project the caller's leaf with `tls.crt`/`tls.key` at the file's name.
    A rendered hop that neither `CLIENT_HOPS` nor `JOB_CLIENT_HOPS` names is
    keyed `<kind>/<name>/<PREFIX>: not in CLIENT_HOPS`.

    A Deployment caller's hop must also render `<PREFIX>_TLS_ENABLED` "1". A
    Job caller (the seeder) has no such switch, so instead its leaf's common
    name must be `<caller>-caller`: the identity `project` admits.
    """
    certificates = {d["spec"]["secretName"]: d["spec"] for d in documents if d["kind"] == "Certificate"}
    workloads = {(d["kind"], d["metadata"]["name"]): d for d in documents if d["kind"] in ("Deployment", "Job")}
    failures: dict[str, object] = {}
    for kind, name, prefix in sorted(rendered_client_hops(documents) - pinned_hops()):
        failures[f"{kind}/{name}/{prefix}: not in CLIENT_HOPS"] = env_value(
            documents, kind, name, f"{prefix}_TLS_ENABLED"
        )
    callers = [("Deployment", caller, prefixes, CLIENT_LEAVES[caller]) for caller, prefixes in CLIENT_HOPS.items()]
    callers += [("Job", caller, prefixes, JOB_CLIENT_LEAVES[caller]) for caller, prefixes in JOB_CLIENT_HOPS.items()]
    for kind, caller, prefixes, leaf in callers:
        certificate = certificates.get(leaf) or {}
        if certificate.get("issuerRef") != LEAF_ISSUER:
            failures[f"{caller}: {leaf} issuerRef"] = certificate.get("issuerRef")
        if "client auth" not in (certificate.get("usages") or []):
            failures[f"{caller}: {leaf} usages"] = certificate.get("usages")
        if kind == "Job" and certificate.get("commonName") != f"{caller}-caller":
            failures[f"{caller}: {leaf} commonName"] = certificate.get("commonName")
        workload = workloads.get((kind, caller))
        if workload is None:
            failures[f"{caller}: {kind}"] = None
            continue
        pod = workload["spec"]["template"]["spec"]
        container = pod["containers"][0]
        env = {e["name"]: e.get("value") for e in container.get("env", [])}
        mounts = {m["mountPath"]: m for m in container.get("volumeMounts", [])}
        volumes = {v["name"]: v for v in pod.get("volumes", [])}
        for prefix in prefixes:
            hop = f"{caller}/{prefix}"
            if kind == "Deployment" and env.get(f"{prefix}_TLS_ENABLED") != "1":
                failures[f"{hop}: {prefix}_TLS_ENABLED"] = env.get(f"{prefix}_TLS_ENABLED")
            for half, key in LEAF_KEYS.items():
                name = f"{prefix}_TLS_CLIENT_{half}_FILE"
                directory, _, file = (env.get(name) or "").rpartition("/")
                mount = mounts.get(directory) or {}
                secret = (volumes.get(mount.get("name")) or {}).get("secret") or {}
                if not (
                    mount.get("readOnly")
                    and secret.get("secretName") == leaf
                    and {"key": key, "path": file} in (secret.get("items") or [])
                ):
                    failures[f"{hop}: {name}"] = env.get(name)
            # ledger 1428: the CA this hop trusts, never checked before. A
            # `*_TLS_CA_FILE` that mounts `ca.crt` from ANY Secret passed; this
            # pins it to the server-leaf Secret `HOP_CA_SECRET` names.
            ca_name = f"{prefix}_TLS_CA_FILE"
            directory, _, file = (env.get(ca_name) or "").rpartition("/")
            mount = mounts.get(directory) or {}
            secret = (volumes.get(mount.get("name")) or {}).get("secret") or {}
            if not (
                mount.get("readOnly")
                and secret.get("secretName") == HOP_CA_SECRET[prefix]
                and {"key": "ca.crt", "path": file} in (secret.get("items") or [])
            ):
                failures[f"{hop}: {ca_name}"] = env.get(ca_name)
    return failures


@pytest.fixture(scope="module")
def rendered() -> list:
    return [d for d in parent_render(REPOSITORY) if isinstance(d, dict) and d.get("kind")]


def test_every_caller_presents_its_own_leaf_on_every_hop(rendered: list) -> None:
    assert client_identity_failures(rendered) == {}


def mutated(documents: list, kind: str, name: str, change) -> list:
    """A deep copy of `documents` with `change` applied to one object."""
    copy = json.loads(json.dumps(documents))
    change(next(d for d in copy if d["kind"] == kind and d["metadata"]["name"] == name))
    return copy


def env_of(deployment: dict) -> list:
    return deployment["spec"]["template"]["spec"]["containers"][0]["env"]


def set_env(name: str, value: str):
    def change(deployment: dict) -> None:
        next(e for e in env_of(deployment) if e["name"] == name)["value"] = value

    return change


def test_a_dropped_client_env_names_its_hop(rendered: list) -> None:
    def drop(deployment: dict) -> None:
        env_of(deployment)[:] = [e for e in env_of(deployment) if e["name"] != "TASK_TLS_CLIENT_KEY_FILE"]

    assert client_identity_failures(mutated(rendered, "Deployment", "gateway", drop)) == {
        "gateway/TASK: TASK_TLS_CLIENT_KEY_FILE": None
    }


def test_a_client_env_at_a_file_the_leaf_does_not_project_names_its_hop(rendered: list) -> None:
    """Not reachable by a values override (the chart writes these paths), so the render is edited."""
    wrong = "/var/run/secrets/client-cert/tls.crt"
    change = set_env("PROJECT_TLS_CLIENT_CERT_FILE", wrong)
    assert client_identity_failures(mutated(rendered, "Deployment", "gateway", change)) == {
        "gateway/PROJECT: PROJECT_TLS_CLIENT_CERT_FILE": wrong
    }


def test_a_hop_flipped_to_cleartext_names_its_hop(rendered: list) -> None:
    change = set_env("IAM_TLS_ENABLED", "0")
    assert client_identity_failures(mutated(rendered, "Deployment", "gateway", change)) == {
        "gateway/IAM: IAM_TLS_ENABLED": "0"
    }


def test_a_backend_callers_env_at_a_wrong_directory_names_its_hop(rendered: list) -> None:
    wrong = "/var/run/secrets/client-cert/client.pem"
    change = set_env("IAM_DB_TLS_CLIENT_CERT_FILE", wrong)
    assert client_identity_failures(mutated(rendered, "Deployment", "iam", change)) == {
        "iam/IAM_DB: IAM_DB_TLS_CLIENT_CERT_FILE": wrong
    }


def test_a_writable_leaf_mount_names_its_hop(rendered: list) -> None:
    def writable(deployment: dict) -> None:
        mounts = deployment["spec"]["template"]["spec"]["containers"][0]["volumeMounts"]
        next(m for m in mounts if m["name"] == "client-cert")["readOnly"] = False

    assert client_identity_failures(mutated(rendered, "Deployment", "task", writable)) == {
        "task/TASK_DB: TASK_DB_TLS_CLIENT_CERT_FILE": "/var/run/secrets/client-cert/client.crt",
        "task/TASK_DB: TASK_DB_TLS_CLIENT_KEY_FILE": "/var/run/secrets/client-cert/client.key",
    }


def test_a_leaf_without_client_auth_names_its_caller(rendered: list) -> None:
    def server_only(certificate: dict) -> None:
        certificate["spec"]["usages"] = ["server auth", "digital signature"]

    assert client_identity_failures(mutated(rendered, "Certificate", "gateway-client-tls", server_only)) == {
        "gateway: gateway-client-tls usages": ["server auth", "digital signature"]
    }


def test_a_leaf_from_another_issuer_names_its_caller(rendered: list) -> None:
    def other_issuer(certificate: dict) -> None:
        certificate["spec"]["issuerRef"]["name"] = "another-ca"

    assert client_identity_failures(mutated(rendered, "Certificate", "task-client-tls", other_issuer)) == {
        "task: task-client-tls issuerRef": {"group": "cert-manager.io", "kind": "Issuer", "name": "another-ca"}
    }


def test_another_secret_on_the_gateway_names_every_hop() -> None:
    """End to end through the chart: the gateway's one volume carries iam's leaf instead of its own."""
    documents = [
        d
        for d in parent_render(REPOSITORY, ("--set", "gateway.clientCertificate.secret=iam-client-tls"))
        if isinstance(d, dict) and d.get("kind")
    ]
    path = "/var/run/secrets/client-cert/client"
    assert client_identity_failures(documents) == {
        f"gateway/{prefix}: {prefix}_TLS_CLIENT_{half}_FILE": f"{path}.{suffix}"
        for prefix in CLIENT_HOPS["gateway"]
        for half, suffix in (("CERT", "crt"), ("KEY", "key"))
    }


def test_another_secret_on_iams_broker_hop_names_that_hop_only() -> None:
    """End to end through the chart: iam's NATS leaf is its own key, independent of `iamDb.tls` (ADR-0885)."""
    documents = [
        d
        for d in parent_render(REPOSITORY, ("--set", "iam.nats.tls.clientCertSecret=gateway-client-tls"))
        if isinstance(d, dict) and d.get("kind")
    ]
    path = "/var/run/secrets/nats-client-tls/client"
    assert client_identity_failures(documents) == {
        "iam/NATS: NATS_TLS_CLIENT_CERT_FILE": f"{path}.pem",
        "iam/NATS: NATS_TLS_CLIENT_KEY_FILE": f"{path}-key.pem",
    }


def test_a_broker_hop_flipped_to_cleartext_names_its_hop(rendered: list) -> None:
    change = set_env("NATS_TLS_ENABLED", "0")
    assert client_identity_failures(mutated(rendered, "Deployment", "iam", change)) == {
        "iam/NATS: NATS_TLS_ENABLED": "0"
    }



def test_a_missing_caller_deployment_names_its_caller(rendered: list) -> None:
    documents = [d for d in rendered if not (d["kind"] == "Deployment" and d["metadata"]["name"] == "iam")]
    assert client_identity_failures(documents) == {"iam: Deployment": None}


def test_the_rendered_client_hops_are_exactly_the_pinned_hops(rendered: list) -> None:
    assert rendered_client_hops(rendered) == pinned_hops()


def test_a_cache_hop_flipped_to_cleartext_names_its_hop(rendered: list) -> None:
    change = set_env("VALKEY_TLS_ENABLED", "0")
    assert client_identity_failures(mutated(rendered, "Deployment", "gateway", change)) == {
        "gateway/VALKEY: VALKEY_TLS_ENABLED": "0"
    }


def test_a_cache_hop_without_its_client_key_names_its_hop(rendered: list) -> None:
    """gateway 0.12.0 refuses the boot on one half without the other; the gate names it first."""

    def drop(deployment: dict) -> None:
        env_of(deployment)[:] = [e for e in env_of(deployment) if e["name"] != "VALKEY_TLS_CLIENT_KEY_FILE"]

    assert client_identity_failures(mutated(rendered, "Deployment", "gateway", drop)) == {
        "gateway/VALKEY: VALKEY_TLS_CLIENT_KEY_FILE": None
    }


def add_env(container_list: str, name: str, value: str):
    """Add one env to the first container of `container_list`, creating an init container if there is none."""

    def change(workload: dict) -> None:
        pod = workload["spec"]["template"]["spec"]
        containers = pod.setdefault(container_list, [])
        if not containers:
            containers.append({"name": "probe", "image": "probe"})
        containers[0].setdefault("env", []).append({"name": name, "value": value})

    return change


def test_an_unpinned_hop_in_a_hook_jobs_init_container_names_it(rendered: list) -> None:
    """By the env NAME: a hop written `"0"` is still a hop somebody may later turn on."""
    change = add_env("initContainers", "NATS_TLS_ENABLED", "0")
    assert client_identity_failures(mutated(rendered, "Job", "preflight", change)) == {
        "Job/preflight/NATS: not in CLIENT_HOPS": "0"
    }


def test_an_unpinned_hop_on_a_server_names_it(rendered: list) -> None:
    change = add_env("containers", "NATS_TLS_ENABLED", "1")
    assert client_identity_failures(mutated(rendered, "Deployment", "iam-db", change)) == {
        "Deployment/iam-db/NATS: not in CLIENT_HOPS": "1"
    }


def test_every_client_hop_prefix_has_a_pinned_ca_secret() -> None:
    """`client_identity_failures` indexes `HOP_CA_SECRET[prefix]` unconditionally for every `CLIENT_HOPS` prefix.

    A prefix added to `CLIENT_HOPS` with no matching `HOP_CA_SECRET` entry
    would raise `KeyError` inside the gate rather than failing with a named
    key; this holds the two maps to the same prefix set so that shows up
    here, by name, instead.
    """
    assert set(HOP_CA_SECRET) == {
        prefix for hops in (CLIENT_HOPS, JOB_CLIENT_HOPS) for prefixes in hops.values() for prefix in prefixes
    }


def test_a_dropped_ca_env_names_its_hop(rendered: list) -> None:
    def drop(deployment: dict) -> None:
        env_of(deployment)[:] = [e for e in env_of(deployment) if e["name"] != "NATS_TLS_CA_FILE"]

    assert client_identity_failures(mutated(rendered, "Deployment", "iam", drop)) == {
        "iam/NATS: NATS_TLS_CA_FILE": None
    }


def test_a_ca_pointed_at_another_servers_secret_names_its_hop(rendered: list) -> None:
    """ledger 1428: the regression this closes — before it, any Secret keyed `ca.crt` passed."""

    def change(deployment: dict) -> None:
        volume = next(v for v in deployment["spec"]["template"]["spec"]["volumes"] if v["name"] == "task-ca")
        volume["secret"]["secretName"] = "valkey-tls"

    assert client_identity_failures(mutated(rendered, "Deployment", "gateway", change)) == {
        "gateway/TASK: TASK_TLS_CA_FILE": "/var/run/config/task-ca/ca.pem"
    }


# ── THE SEEDER'S HOP TO project (plans/the-d35-seeder.md stage 7) ──────────


def test_the_seeder_job_is_a_pinned_hop(rendered: list) -> None:
    """The seeder Job renders, and its hop is seen by its client-certificate env, not by a switch."""
    assert ("Job", "seeder", "PROJECT") in rendered_client_hops(rendered)


def test_a_seeder_leaf_with_another_common_name_names_it(rendered: list) -> None:
    """`project` admits `SyncProjects` from `seeder-caller` only (ADR-0936); another name is refused live."""

    def rename(certificate: dict) -> None:
        certificate["spec"]["commonName"] = "project-caller"

    assert client_identity_failures(mutated(rendered, "Certificate", "seeder-client-tls", rename)) == {
        "seeder: seeder-client-tls commonName": "project-caller"
    }


def test_the_seeder_mounting_another_leaf_names_both_halves(rendered: list) -> None:
    def other_leaf(job: dict) -> None:
        volume = next(v for v in job["spec"]["template"]["spec"]["volumes"] if v["name"] == "client-cert")
        volume["secret"]["secretName"] = "project-client-tls"

    assert client_identity_failures(mutated(rendered, "Job", "seeder", other_leaf)) == {
        "seeder/PROJECT: PROJECT_TLS_CLIENT_CERT_FILE": "/var/run/secrets/client-cert/client.crt",
        "seeder/PROJECT: PROJECT_TLS_CLIENT_KEY_FILE": "/var/run/secrets/client-cert/client.key",
    }


def test_the_seeder_trusting_another_servers_ca_names_its_hop(rendered: list) -> None:
    def other_ca(job: dict) -> None:
        volume = next(v for v in job["spec"]["template"]["spec"]["volumes"] if v["name"] == "project-ca")
        volume["secret"]["secretName"] = "project-db-tls"

    assert client_identity_failures(mutated(rendered, "Job", "seeder", other_ca)) == {
        "seeder/PROJECT: PROJECT_TLS_CA_FILE": "/var/run/config/project-ca/ca.pem"
    }


def test_a_missing_seeder_job_names_it(rendered: list) -> None:
    documents = [d for d in rendered if not (d["kind"] == "Job" and d["metadata"]["name"] == "seeder")]
    assert client_identity_failures(documents) == {"seeder: Job": None}


def test_an_unpinned_hop_with_no_switch_names_it(rendered: list) -> None:
    """A hop carried by a client-certificate env alone is still a hop (the seeder's shape)."""
    change = add_env("containers", "TASK_TLS_CLIENT_CERT_FILE", "/x/client.crt")
    assert client_identity_failures(mutated(rendered, "Job", "preflight", change)) == {
        "Job/preflight/TASK: not in CLIENT_HOPS": None
    }


# Each gRPC server's ingress allow-list on its gRPC port, by the `app` label of
# the pods it admits. `project` admits the gateway and the seeder; `project-db`
# admits `project` alone, because no module but its twin reaches a `-db`
# (ADR-0936, ADR-0938). K3 alone cannot hold this: `--write` blesses whatever
# renders, so a seeder added to `project-db`'s list would pass K3.
INGRESS_PEERS = {
    "project-ingress": (50052, {"gateway", "seeder"}),
    "project-db-ingress": (50051, {"project"}),
}


def ingress_peer_failures(documents: list) -> dict[str, object]:
    """Every policy in `INGRESS_PEERS` whose gRPC-port peers differ, and every admitted label no pod carries."""
    policies = {d["metadata"]["name"]: d for d in documents if d["kind"] == "NetworkPolicy"}
    pod_apps = {
        ((d["spec"]["template"].get("metadata") or {}).get("labels") or {}).get("app")
        for d in documents
        if d["kind"] in ("Deployment", "Job", "StatefulSet")
    }
    failures: dict[str, object] = {}
    for name, (port, expected) in INGRESS_PEERS.items():
        policy = policies.get(name)
        if policy is None:
            failures[f"{name}: NetworkPolicy"] = None
            continue
        peers = {
            peer["podSelector"]["matchLabels"].get("app")
            for rule in policy["spec"].get("ingress") or []
            if any(p.get("port") == port for p in rule.get("ports") or [])
            for peer in rule.get("from") or []
            if "podSelector" in peer
        }
        if peers != expected:
            failures[f"{name}: peers on {port}"] = sorted(peers)
        if peers - pod_apps:
            failures[f"{name}: peers no pod carries"] = sorted(peers - pod_apps)
    return failures


def test_project_admits_the_gateway_and_the_seeder_and_project_db_admits_project_only(rendered: list) -> None:
    assert ingress_peer_failures(rendered) == {}


def test_the_seeder_admitted_to_project_db_names_it() -> None:
    """End to end through the chart: the twin-only rule (ADR-0938) broken in values."""
    documents = [
        d
        for d in parent_render(REPOSITORY, ("--set", "project-db.networkPolicy.clients={project,seeder}"))
        if isinstance(d, dict) and d.get("kind")
    ]
    assert ingress_peer_failures(documents) == {"project-db-ingress: peers on 50051": ["project", "seeder"]}


def test_a_seeder_pod_without_its_label_names_the_peer(rendered: list) -> None:
    def relabel(job: dict) -> None:
        job["spec"]["template"]["metadata"]["labels"]["app"] = "seeder-job"

    assert ingress_peer_failures(mutated(rendered, "Job", "seeder", relabel)) == {
        "project-ingress: peers no pod carries": ["seeder"]
    }


# ── THE ESTATE'S REGISTRY DECLARATION (plans/the-d35-seeder.md stage 7) ──────

# Max's ruling (2026-10-18): "Declare every yadgarhq repo that holds tasks, plus
# the estate roots." Every non-archived repository of the yadgarhq organisation,
# read with `gh repo list yadgarhq --json name,isArchived` on 2026-10-18; the
# one archived repository, `deploy`, is left out. A repository created or
# archived later is a values change, and this list moves with it.
DECLARED_REPOSITORIES = (
    "actions",
    "argocd",
    "argocd-verify",
    "chart",
    "config",
    "dial",
    "docs",
    "estate",
    "gateway",
    "iam",
    "iam-db",
    "lifecycle",
    "platform",
    "project",
    "project-db",
    "proto",
    "seeder",
    "store",
    "task",
    "task-db",
    "telemetry",
    "yadgar",
)
# The namespace anchors (ADR-0673) and the estate's one landing path, ledger
# 1244 option B: smoke runs claim `estate/smoke`, a project under the root
# `estate`, because the store refuses a root that contains `/`.
DECLARED_ROOTS = {"yadgarhq": "yadgarhq/argocd", "estate": "yadgarhq/estate"}
DECLARED_EXTRA_PROJECTS = {"estate/smoke": "yadgarhq/estate"}


def registry_failures(registry: object) -> dict[str, object]:
    """Every way `seeder.registry` differs from the ruling above."""
    if not isinstance(registry, dict):
        return {"seeder.registry": registry}
    failures: dict[str, object] = {}
    if registry.get("allowMassArchive") is not False:
        failures["allowMassArchive"] = registry.get("allowMassArchive")
    roots = {r.get("path"): r.get("sourceRepo") for r in registry.get("roots") or []}
    if roots != DECLARED_ROOTS:
        failures["roots"] = roots
    projects = {p.get("path"): p.get("sourceRepo") for p in registry.get("projects") or []}
    expected = {f"yadgarhq/{name}": f"yadgarhq/{name}" for name in DECLARED_REPOSITORIES} | DECLARED_EXTRA_PROJECTS
    if projects != expected:
        failures["projects"] = {
            "missing": sorted(expected.keys() - projects.keys()),
            "extra": sorted(projects.keys() - expected.keys()),
            "sourceRepo": sorted(k for k in expected.keys() & projects.keys() if expected[k] != projects[k]),
        }
    return failures


def declared_registry() -> object:
    values = committed()["spec"]["source"]["helm"]["valuesObject"]
    return (values.get("seeder") or {}).get("registry")


def test_the_registry_declares_every_repository_and_the_estate_roots() -> None:
    assert registry_failures(declared_registry()) == {}


def test_the_rendered_registry_is_the_declaration(rendered: list) -> None:
    """The ConfigMap the Job reads carries exactly the declared rows and the guard."""
    configmap = next(d for d in rendered if d["kind"] == "ConfigMap" and d["metadata"]["name"] == "seeder-registry")
    assert registry_failures(json.loads(configmap["data"]["registry.json"])) == {}


def test_a_mass_archive_flag_names_it() -> None:
    registry = json.loads(json.dumps(declared_registry()))
    registry["allowMassArchive"] = True
    assert registry_failures(registry) == {"allowMassArchive": True}


def test_a_dropped_repository_names_it() -> None:
    registry = json.loads(json.dumps(declared_registry()))
    registry["projects"] = [p for p in registry["projects"] if p["path"] != "yadgarhq/docs"]
    assert registry_failures(registry) == {"projects": {"missing": ["yadgarhq/docs"], "extra": [], "sourceRepo": []}}


# ── THE BROKER'S TLS LISTENER (B-N4.1, B-N5, ledger 925, ADR-0852) ───────────

# The broker's side of the NATS hop, stated beside the two clients' side above.
# K3 cannot hold this: `--write` blesses whatever renders, so a values change
# that dropped `allow_non_tls`, pointed the listener at another Secret, or
# dropped `verify` would pass K3. platform 0.2.1's own render check holds that
# its two sources agree; it cannot know this estate's step.
#
# THE STEP IS B-N5: the listener speaks TLS with the `nats-tls` serving leaf
# (B-N4.1), both clients dial it with TLS and present their leaves
# (`CLIENT_HOPS` above, B-N4.2), it refuses plaintext (`allow_non_tls`
# dropped, B-N4.3), and it verifies every client's leaf (B-N5): `verify` a
# bool true and `ca_file` the `ca.crt` beside the serving leaf. nats-server has
# no optional client-certificate mode (ADR-0854). Both were pinned to the
# hops rather than a literal while any NATS hop could sit in `CLEARTEXT_HOPS`;
# ADR-0890 retired that map at ledger 925's close (B-N4.3), so both are
# required unconditionally now, and `allow_non_tls` is unconditionally absent
# or false.
NATS_STATED = {"enabled": True, "clientAuth": "required"}
NATS_CERT_DIR = "/etc/nats-certs/nats"
NATS_SECRET = "nats-tls"
# ledger 1427: the reloader watches all three files the broker loaded at
# boot (B-N4.1's two, `tls.crt`/`tls.key`; B-N5's `ca.crt`), each its own
# `-config` argument, from the same `nats-tls` mount the server reads.
RELOADER_CONFIG_FILES = ("tls.crt", "tls.key", "ca.crt")


def nats_conf(document: dict) -> dict:
    """The rendered `nats.conf`: JSON, except `$VAR` references the server expands, which are quoted here."""
    return json.loads(re.sub(r":\s*(\$[A-Z_]+)", r': "\1"', document["data"]["nats.conf"]))


def broker_failures(documents: list) -> dict[str, object]:
    """Every way the broker's rendered TLS posture is not this step's, keyed `nats: <what>`."""
    by_name = {(d["kind"], d["metadata"]["name"]): d for d in documents}
    config_map, statefulset = by_name.get(("ConfigMap", "nats-config")), by_name.get(("StatefulSet", "nats"))
    if config_map is None or statefulset is None:
        return {"nats: ConfigMap/nats-config and StatefulSet/nats": None}
    conf = nats_conf(config_map)
    tls = conf.get("tls") or {}
    failures: dict[str, object] = {}
    for key, file in (("cert_file", "tls.crt"), ("key_file", "tls.key")):
        if tls.get(key) != f"{NATS_CERT_DIR}/{file}":
            failures[f"nats: tls.{key}"] = tls.get(key)
    if tls.get("verify") is not True:
        failures["nats: tls.verify with no NATS hop in cleartext"] = tls.get("verify")
    if tls.get("ca_file") != f"{NATS_CERT_DIR}/ca.crt":
        failures["nats: tls.ca_file with no NATS hop in cleartext"] = tls.get("ca_file")
    if conf.get("allow_non_tls", False) is not False:
        failures["nats: allow_non_tls with no NATS hop in cleartext"] = conf["allow_non_tls"]
    pod = statefulset["spec"]["template"]["spec"]
    volume = next((v for v in pod.get("volumes") or [] if v["name"] == NATS_SECRET), None)
    if (volume or {}).get("secret", {}).get("secretName") != NATS_SECRET:
        failures["nats: volume nats-tls"] = volume
    server = next((c for c in pod["containers"] if c["name"] == "nats"), {})
    mount = {m["name"]: m["mountPath"] for m in server.get("volumeMounts") or []}.get(NATS_SECRET)
    if mount != NATS_CERT_DIR:
        failures["nats: mount nats-tls"] = mount
    reloader = next((c for c in pod["containers"] if c["name"] == "reloader"), None)
    if reloader is None:
        failures["nats: reloader"] = None
    else:
        args = reloader.get("args") or []
        for file in RELOADER_CONFIG_FILES:
            pair = ["-config", f"{NATS_CERT_DIR}/{file}"]
            if not any(args[index : index + 2] == pair for index in range(len(args) - 1)):
                failures[f"nats: reloader -config {file}"] = args
        reloader_mount = next((m for m in reloader.get("volumeMounts") or [] if m["name"] == NATS_SECRET), None)
        if (
            reloader_mount is None
            or reloader_mount.get("mountPath") != NATS_CERT_DIR
            or reloader_mount.get("subPath")
        ):
            failures["nats: reloader mount nats-tls"] = reloader_mount
    return failures


def test_the_broker_states_its_tls_posture() -> None:
    values = committed()["spec"]["source"]["helm"]["valuesObject"]
    assert values["platform"]["nats"]["tls"] == NATS_STATED


def test_the_broker_listens_with_its_leaf_and_refuses_cleartext(rendered: list) -> None:
    assert broker_failures(rendered) == {}


def with_nats_conf(change):
    def apply(config_map: dict) -> None:
        conf = nats_conf(config_map)
        change(conf)
        config_map["data"]["nats.conf"] = json.dumps(conf)

    return apply


def test_with_no_nats_hop_in_cleartext_a_kept_allow_non_tls_names_it(rendered: list) -> None:
    """B-N4.3: with every NATS hop in `CLIENT_HOPS`, the broker must refuse cleartext, so `allow_non_tls` is gone."""
    change = with_nats_conf(lambda conf: conf.update(allow_non_tls=True))
    assert broker_failures(mutated(rendered, "ConfigMap", "nats-config", change)) == {
        "nats: allow_non_tls with no NATS hop in cleartext": True
    }


def test_a_non_boolean_allow_non_tls_names_it(rendered: list) -> None:
    """Only absent or `false` is the refusing posture; a string the server may read as true is not."""
    change = with_nats_conf(lambda conf: conf.update(allow_non_tls="true"))
    assert broker_failures(mutated(rendered, "ConfigMap", "nats-config", change)) == {
        "nats: allow_non_tls with no NATS hop in cleartext": "true"
    }


def test_an_explicit_false_allow_non_tls_is_the_refusing_posture(rendered: list) -> None:
    """`false` is nats-server's default, so it states the same refusal as the absent key."""
    change = with_nats_conf(lambda conf: conf.update(allow_non_tls=False))
    assert broker_failures(mutated(rendered, "ConfigMap", "nats-config", change)) == {}


def test_a_dropped_tls_block_names_both_files_and_the_verification(rendered: list) -> None:
    change = with_nats_conf(lambda conf: conf.pop("tls", None))
    assert broker_failures(mutated(rendered, "ConfigMap", "nats-config", change)) == {
        "nats: tls.cert_file": None,
        "nats: tls.key_file": None,
        "nats: tls.verify with no NATS hop in cleartext": None,
        "nats: tls.ca_file with no NATS hop in cleartext": None,
    }


def test_a_dropped_verify_names_it(rendered: list) -> None:
    """B-N5: with every NATS hop in `CLIENT_HOPS`, the broker verifies every client's leaf."""
    change = with_nats_conf(lambda conf: conf["tls"].pop("verify", None))
    assert broker_failures(mutated(rendered, "ConfigMap", "nats-config", change)) == {
        "nats: tls.verify with no NATS hop in cleartext": None
    }


def test_a_non_boolean_verify_names_it(rendered: list) -> None:
    """Only a bool `true` is the verifying posture; platform 0.2.1 refuses a quoted one too."""
    change = with_nats_conf(lambda conf: conf["tls"].update(verify="true"))
    assert broker_failures(mutated(rendered, "ConfigMap", "nats-config", change)) == {
        "nats: tls.verify with no NATS hop in cleartext": "true"
    }


def test_a_ca_file_other_than_the_leafs_ca_names_it(rendered: list) -> None:
    """The broker verifies against the internal CA cert-manager writes beside its leaf, never a public bundle."""
    bundle = "/etc/ssl/certs/ca-certificates.crt"
    change = with_nats_conf(lambda conf: conf["tls"].update(ca_file=bundle))
    assert broker_failures(mutated(rendered, "ConfigMap", "nats-config", change)) == {
        "nats: tls.ca_file with no NATS hop in cleartext": bundle
    }


def test_the_leaf_volume_at_another_secret_names_it(rendered: list) -> None:
    def change(statefulset: dict) -> None:
        volume = next(v for v in statefulset["spec"]["template"]["spec"]["volumes"] if v["name"] == NATS_SECRET)
        volume["secret"]["secretName"] = "valkey-tls"

    failures = broker_failures(mutated(rendered, "StatefulSet", "nats", change))
    assert list(failures) == ["nats: volume nats-tls"]


def test_a_dropped_reloader_config_arg_names_it(rendered: list) -> None:
    """ledger 1427: the reloader must watch `ca.crt` too, not only the two B-N4.1 files."""

    def drop_ca_arg(statefulset: dict) -> None:
        pod = statefulset["spec"]["template"]["spec"]
        reloader = next(c for c in pod["containers"] if c["name"] == "reloader")
        args = reloader["args"]
        index = args.index(f"{NATS_CERT_DIR}/ca.crt")
        del args[index - 1 : index + 1]  # the "-config" / path pair

    failures = broker_failures(mutated(rendered, "StatefulSet", "nats", drop_ca_arg))
    assert list(failures) == ["nats: reloader -config ca.crt"]


def test_a_reloader_mount_with_a_subpath_names_it(rendered: list) -> None:
    def add_subpath(statefulset: dict) -> None:
        pod = statefulset["spec"]["template"]["spec"]
        reloader = next(c for c in pod["containers"] if c["name"] == "reloader")
        mount = next(m for m in reloader["volumeMounts"] if m["name"] == NATS_SECRET)
        mount["subPath"] = "tls.crt"

    failures = broker_failures(mutated(rendered, "StatefulSet", "nats", add_subpath))
    assert list(failures) == ["nats: reloader mount nats-tls"]


# ── THE CACHE'S TLS LISTENER (B-V4.1, ledger 925, ADR-0852) ──────────────────

# The server's side of the valkey hop, stated beside the gateway's side above.
# K3 cannot hold this: `--write` blesses whatever renders,
# so a values change that turned the TLS listener off, pointed it at another
# Secret, asked for client leaves early or closed 6379 while the gateway still
# dials it would pass K3. platform 0.2.1's own render checks hold that its keys
# agree; they cannot know this estate's step.
#
# THE STEP IS B-V6: valkey listens only with TLS, on 6380 with the
# `valkey-tls` serving leaf (B-V4.1); the gateway dials 6380 with TLS and
# presents its `gateway-client-tls` leaf (gateway/VALKEY in `CLIENT_HOPS`
# above, B-V4.2); 6379 is closed (B-V4.3); valkey asked for a client leaf
# and verified one that was presented (B-V5, `--tls-auth-clients optional`);
# and valkey now requires one (B-V6, `--tls-auth-clients yes`, verified against
# `ca.crt` beside the serving leaf): a client with no leaf is refused.
# Plaintext was pinned to the hops rather than a literal while any VALKEY hop
# could sit in `CLEARTEXT_HOPS`; ADR-0890 retired that map at ledger 925's
# close (B-V4.3), so 6379 must now be ABSENT unconditionally from the
# Deployment, the Service and `valkey-ingress`, and the boot script must say
# `--port 0` (valkey-server listens on 6379 when `--port` is absent).
# `VALKEY_TLS_AUTH_CLIENTS` is required unconditionally too, now that every
# VALKEY hop presents a leaf: this step's value, `VALKEY_AUTH_CLIENTS`. valkey
# reads its leaf only at boot, so the boot script hashes
# the files it loaded and the liveness probe re-checks that hash: a renewed
# leaf restarts the container (one more `Recreate`-sized cache gap).
# platform 0.2.3 (ledger 1437) puts the password in the same hash: valkey reads
# it from `valkey-password` mounted as a directory at `VALKEY_AUTH_DIR`, never
# from `env`, so a rotated password restarts valkey as it restarts `gateway`.
VALKEY_STATED = {"enabled": True, "clientAuth": "required", "plaintext": False}
VALKEY_AUTH_CLIENTS = "yes"
VALKEY_TLS_DIR = "/etc/valkey/tls"
VALKEY_SECRET = "valkey-tls"
VALKEY_HASH = "/run/valkey/watched.sha256"
VALKEY_AUTH_DIR = "/etc/valkey/auth"
VALKEY_PASSWORD_SECRET = "valkey-password"
# ledger 1447: platform#42 projects the Secret's one key `password` to a file `password`, in
# valkey's pod (read at VALKEY_AUTH_DIR) and in gateway's (read at YADGAR_VALKEY_PASSWORD_FILE).
VALKEY_PASSWORD_ITEMS = [{"key": "password", "path": "password"}]
# ledger 1437: an env copy of the password is fixed at process start and outlives a rotation.
VALKEY_PASSWORD_ENV = ("VALKEY_PASSWORD", "VALKEYCLI_AUTH")
VALKEY_PORTS = {"valkey": 6379, "valkey-tls": 6380}
VALKEY_TLS_ARGS = (
    "--tls-port 6380",
    f"--tls-cert-file {VALKEY_TLS_DIR}/tls.crt",
    f"--tls-key-file {VALKEY_TLS_DIR}/tls.key",
    f"--tls-ca-cert-file {VALKEY_TLS_DIR}/ca.crt",
    '--tls-auth-clients "$VALKEY_TLS_AUTH_CLIENTS"',
)
# ledger 1429: the files the boot script hashes, in the order it hashes them;
# ledger 1437: the password file first.
VALKEY_HASHED_FILES = (f"{VALKEY_AUTH_DIR}/password",) + tuple(
    f"{VALKEY_TLS_DIR}/{file}" for file in ("tls.crt", "tls.key", "ca.crt")
)
# `valkey-ingress`'s one rule names gateway as its peer (`app: gateway`); the
# 6380 check must hold for THAT rule, never for "6380 open from some other
# peer while gateway's own rule stays closed" (ledger 1429).
VALKEY_INGRESS_PEER = {"podSelector": {"matchLabels": {"app": "gateway"}}}



def plaintext_ports(script: str) -> list[str]:
    """Every `--port N` in valkey's boot script; `--tls-port` is not one."""
    return re.findall(r"(?<![\w-])--port (\d+)", script)


def cache_failures(documents: list) -> dict[str, object]:
    """Every way the cache's rendered TLS posture is not this step's, keyed `valkey: <what>`."""
    by_name = {(d["kind"], d["metadata"]["name"]): d for d in documents}
    deployment = by_name.get(("Deployment", "valkey"))
    service = by_name.get(("Service", "valkey"))
    policy = by_name.get(("NetworkPolicy", "valkey-ingress"))
    if deployment is None or service is None or policy is None:
        return {"valkey: Deployment/valkey, Service/valkey and NetworkPolicy/valkey-ingress": None}
    failures: dict[str, object] = {}
    pod = deployment["spec"]["template"]["spec"]
    server = next((c for c in pod["containers"] if c["name"] == "valkey"), {})
    script = " ".join(server.get("args") or [])
    for arg in VALKEY_TLS_ARGS:
        if arg not in script:
            failures[f"valkey: arg {arg.split()[0]}"] = None
    hash_line = next((line for line in script.splitlines() if f"> {VALKEY_HASH}" in line), None)
    if hash_line is None:
        failures["valkey: boot hash"] = None
    else:
        # ledger 1429: not just THAT it hashes, but that it hashes all three files.
        hashed = hash_line.split(">", 1)[0].split()[1:]
        if hashed != list(VALKEY_HASHED_FILES):
            failures["valkey: boot hash files"] = hashed
    liveness = " ".join(((server.get("livenessProbe") or {}).get("exec") or {}).get("command") or [])
    if f"sha256sum -c --status {VALKEY_HASH}" not in liveness:
        failures["valkey: liveness hash check"] = liveness
    env = {e["name"]: e.get("value") for e in server.get("env") or []}
    if env.get("VALKEY_TLS_AUTH_CLIENTS") != VALKEY_AUTH_CLIENTS:
        failures["valkey: VALKEY_TLS_AUTH_CLIENTS with no VALKEY hop in cleartext"] = env.get("VALKEY_TLS_AUTH_CLIENTS")
    volume = next((v for v in pod.get("volumes") or [] if v["name"] == VALKEY_SECRET), None)
    if (volume or {}).get("secret", {}).get("secretName") != VALKEY_SECRET:
        failures["valkey: volume valkey-tls"] = volume
    mount = next((m for m in server.get("volumeMounts") or [] if m["name"] == VALKEY_SECRET), None)
    # ledger 1429: the mountPath AND no subPath — a subPath would serve one
    # file at that whole path, not the directory valkey's args read from.
    if mount is None or mount.get("mountPath") != VALKEY_TLS_DIR or mount.get("subPath"):
        failures["valkey: mount valkey-tls"] = mount
    # ledger 1437: the password as a mounted directory (kubelet never refreshes
    # a subPath), and no env copy of it.
    password_mount = next((m for m in server.get("volumeMounts") or [] if m["name"] == VALKEY_PASSWORD_SECRET), None)
    if password_mount is None or password_mount.get("mountPath") != VALKEY_AUTH_DIR or password_mount.get("subPath"):
        failures["valkey: mount valkey-password"] = password_mount
    for name in VALKEY_PASSWORD_ENV:
        if name in env:
            failures[f"valkey: env {name}"] = None
    # ledger 1447: the mount passes while the volume projects another Secret or key, so check
    # the volume on both sides of the password: valkey (reads it) and gateway (presents it).
    gateway = by_name.get(("Deployment", "gateway"))
    for owner, deploy in (("valkey", deployment), ("gateway", gateway)):
        volumes = ((deploy or {}).get("spec", {}).get("template", {}).get("spec", {})).get("volumes") or []
        volume = next((v for v in volumes if v["name"] == VALKEY_PASSWORD_SECRET), None)
        secret = (volume or {}).get("secret") or {}
        if secret.get("secretName") != VALKEY_PASSWORD_SECRET or secret.get("items") != VALKEY_PASSWORD_ITEMS:
            failures[f"{owner}: volume valkey-password"] = volume
    # ledger 1447/1437, gateway half: the mount has no subPath (kubelet never refreshes one) and the
    # file env names THE mount's `<mountPath>/password`.
    if gateway is not None:
        container = next((c for c in gateway["spec"]["template"]["spec"]["containers"] if c["name"] == "gateway"), {})
        gateway_mount = next((m for m in container.get("volumeMounts") or [] if m["name"] == VALKEY_PASSWORD_SECRET), None)
        if gateway_mount is None or gateway_mount.get("subPath"):
            failures["gateway: mount valkey-password"] = gateway_mount
        gateway_env = {e["name"]: e.get("value") for e in container.get("env") or []}
        wanted = f"{gateway_mount['mountPath']}/password" if gateway_mount else None
        if wanted is None or gateway_env.get("YADGAR_VALKEY_PASSWORD_FILE") != wanted:
            failures["gateway: env YADGAR_VALKEY_PASSWORD_FILE"] = gateway_env.get("YADGAR_VALKEY_PASSWORD_FILE")
    wanted = {"valkey-tls": VALKEY_PORTS["valkey-tls"]}
    container_ports = {p.get("name"): p.get("containerPort") for p in server.get("ports") or []}
    service_ports = {p.get("name"): (p.get("port"), p.get("targetPort")) for p in service["spec"].get("ports") or []}
    allowed = {p.get("port") for rule in policy["spec"].get("ingress") or [] for p in rule.get("ports") or []}
    # ledger 1429: the rule naming gateway as its peer, not any rule at all —
    # 6380 open from another peer while gateway's own rule lacks it must fail.
    gateway_ports = {
        p.get("port")
        for rule in policy["spec"].get("ingress") or []
        if VALKEY_INGRESS_PEER in (rule.get("from") or [])
        for p in rule.get("ports") or []
    }
    for name, port in wanted.items():
        if container_ports.get(name) != port:
            failures[f"valkey: containerPort {name}"] = container_ports.get(name)
        if service_ports.get(name) != (port, name):
            failures[f"valkey: Service port {name}"] = service_ports.get(name)
        if port not in gateway_ports:
            failures[f"valkey: valkey-ingress port {port} from app: gateway"] = sorted(gateway_ports)
    plaintext, closed = VALKEY_PORTS["valkey"], " with no VALKEY hop in cleartext"
    if "valkey" in container_ports or plaintext in container_ports.values():
        failures[f"valkey: containerPort 6379{closed}"] = None
    if "valkey" in service_ports or any(port == plaintext for port, _ in service_ports.values()):
        failures[f"valkey: Service port 6379{closed}"] = None
    if plaintext in allowed:
        failures[f"valkey: valkey-ingress port 6379{closed}"] = None
    if plaintext_ports(script) != ["0"]:
        failures[f"valkey: --port{closed}"] = plaintext_ports(script)
    return failures


def test_the_cache_states_its_tls_posture() -> None:
    values = committed()["spec"]["source"]["helm"]["valuesObject"]
    assert values["platform"]["valkey"]["tls"] == VALKEY_STATED


def test_the_cache_listens_with_its_leaf_and_only_with_tls(rendered: list) -> None:
    assert cache_failures(rendered) == {}


def test_the_rendered_cache_has_no_plaintext_port(rendered: list) -> None:
    """B-V4.3: `--port 0`, and 6379 on none of the Deployment, the Service and `valkey-ingress`."""
    deployment, service, policy = valkey_objects(rendered)
    container = valkey_container(deployment)
    assert plaintext_ports(" ".join(container["args"])) == ["0"]
    assert [p["containerPort"] for p in container["ports"]] == [6380]
    assert [p["port"] for p in service["spec"]["ports"]] == [6380]
    assert {p["port"] for rule in policy["spec"]["ingress"] for p in rule["ports"]} == {6380}


def valkey_container(deployment: dict) -> dict:
    return next(c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "valkey")


def with_valkey_script(change):
    def apply(deployment: dict) -> None:
        container = valkey_container(deployment)
        container["args"] = [change(arg) for arg in container["args"]]

    return apply


def test_a_dropped_tls_port_names_it(rendered: list) -> None:
    change = with_valkey_script(lambda script: script.replace("--tls-port 6380", "--tls-port 0"))
    assert cache_failures(mutated(rendered, "Deployment", "valkey", change)) == {"valkey: arg --tls-port": None}


def test_a_dropped_boot_hash_names_it(rendered: list) -> None:
    change = with_valkey_script(lambda script: script.replace(f"> {VALKEY_HASH}", "> /dev/null"))
    assert cache_failures(mutated(rendered, "Deployment", "valkey", change)) == {"valkey: boot hash": None}


def test_a_boot_hash_missing_a_file_names_it(rendered: list) -> None:
    """ledger 1429: the hash must cover `ca.crt` too, not only `tls.crt`/`tls.key`.

    ledger 1437: the script now opens with `set -eu`, so the edit targets the
    hash line itself rather than the script's first line.
    """

    def drop_ca_from_hash(script: str) -> str:
        return "\n".join(
            line.replace(f" {VALKEY_TLS_DIR}/ca.crt", "") if f"> {VALKEY_HASH}" in line else line
            for line in script.split("\n")
        )

    change = with_valkey_script(drop_ca_from_hash)
    assert cache_failures(mutated(rendered, "Deployment", "valkey", change)) == {
        "valkey: boot hash files": [f"{VALKEY_AUTH_DIR}/password", f"{VALKEY_TLS_DIR}/tls.crt", f"{VALKEY_TLS_DIR}/tls.key"]
    }


def test_a_boot_hash_without_the_password_names_it(rendered: list) -> None:
    """ledger 1437: a rotated password must restart valkey, so the hash must cover it."""

    def drop_password_from_hash(script: str) -> str:
        return "\n".join(
            line.replace(f" {VALKEY_AUTH_DIR}/password", "") if f"> {VALKEY_HASH}" in line else line
            for line in script.split("\n")
        )

    change = with_valkey_script(drop_password_from_hash)
    assert cache_failures(mutated(rendered, "Deployment", "valkey", change)) == {
        "valkey: boot hash files": list(VALKEY_HASHED_FILES[1:])
    }


def test_a_valkey_password_mount_with_a_subpath_names_it(rendered: list) -> None:
    """ledger 1437: kubelet never refreshes a subPath, so a rotation would never reach the file."""

    def change(deployment: dict) -> None:
        mount = next(m for m in valkey_container(deployment)["volumeMounts"] if m["name"] == VALKEY_PASSWORD_SECRET)
        mount["subPath"] = "password"

    assert list(cache_failures(mutated(rendered, "Deployment", "valkey", change))) == ["valkey: mount valkey-password"]


def password_volume(deployment: dict) -> dict:
    return next(v for v in deployment["spec"]["template"]["spec"]["volumes"] if v["name"] == VALKEY_PASSWORD_SECRET)


@pytest.mark.parametrize(
    ("kind_name", "failure"),
    [("valkey", "valkey: volume valkey-password"), ("gateway", "gateway: volume valkey-password")],
)
@pytest.mark.parametrize("field", ["secretName", "key", "path"])
def test_a_wrong_valkey_password_volume_names_it(rendered: list, kind_name: str, failure: str, field: str) -> None:
    """ledger 1447: the mount alone passes while the volume projects another Secret or key.

    Constructed mutations of the volume platform#42 renders: another
    `secretName`, another `items[].key`, another `items[].path` (the file
    `YADGAR_VALKEY_PASSWORD_FILE` and `VALKEY_AUTH_DIR/password` name).
    """

    def change(deployment: dict) -> None:
        secret = password_volume(deployment)["secret"]
        if field == "secretName":
            secret["secretName"] = "valkey-tls"
        else:
            secret["items"][0][field] = "wrong"

    assert list(cache_failures(mutated(rendered, "Deployment", kind_name, change))) == [failure]


def gateway_container(deployment: dict) -> dict:
    return next(c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "gateway")


GATEWAY_PASSWORD_MOUNT_FAILURE = "gateway: mount valkey-password"
GATEWAY_PASSWORD_ENV_FAILURE = "gateway: env YADGAR_VALKEY_PASSWORD_FILE"


def test_the_gateway_reads_the_valkey_password_from_its_mount(rendered: list) -> None:
    deployment = next(d for d in rendered if d["kind"] == "Deployment" and d["metadata"]["name"] == "gateway")
    env = {e["name"]: e.get("value") for e in gateway_container(deployment)["env"]}
    mount = next(m for m in gateway_container(deployment)["volumeMounts"] if m["name"] == VALKEY_PASSWORD_SECRET)
    assert env["YADGAR_VALKEY_PASSWORD_FILE"] == f"{mount['mountPath']}/password"


@pytest.mark.parametrize(
    ("mutation", "failures"),
    [
        ("env removed", [GATEWAY_PASSWORD_ENV_FAILURE]),
        ("env moved", [GATEWAY_PASSWORD_ENV_FAILURE]),
        ("mount removed", [GATEWAY_PASSWORD_MOUNT_FAILURE, GATEWAY_PASSWORD_ENV_FAILURE]),
        ("mountPath moved", [GATEWAY_PASSWORD_ENV_FAILURE]),
        ("subPath added", [GATEWAY_PASSWORD_MOUNT_FAILURE]),
    ],
)
def test_a_gateway_password_mount_or_env_gone_wrong_names_it(rendered: list, mutation: str, failures: list) -> None:
    """ledger 1447/1437: the volume can be right while the mount or the file env is not.

    A `subPath` is never refreshed by kubelet, so a rotation would never reach
    gateway. The env must name `<mountPath>/password` of THE mount, so moving
    only the mountPath, or only the env, is a failure.
    """

    def change(deployment: dict) -> None:
        container = gateway_container(deployment)
        mount = next(m for m in container["volumeMounts"] if m["name"] == VALKEY_PASSWORD_SECRET)
        env = container["env"]
        if mutation == "env removed":
            env[:] = [e for e in env if e["name"] != "YADGAR_VALKEY_PASSWORD_FILE"]
        elif mutation == "env moved":
            next(e for e in env if e["name"] == "YADGAR_VALKEY_PASSWORD_FILE")["value"] = "/elsewhere/password"
        elif mutation == "mount removed":
            container["volumeMounts"].remove(mount)
        elif mutation == "mountPath moved":
            mount["mountPath"] = "/var/run/secrets/moved"
        else:
            mount["subPath"] = "password"

    assert list(cache_failures(mutated(rendered, "Deployment", "gateway", change))) == failures


def test_a_valkey_password_volume_with_no_items_names_it(rendered: list) -> None:
    """Without `items` the Secret mounts every key it holds, not only `password`."""

    def change(deployment: dict) -> None:
        password_volume(deployment)["secret"].pop("items")

    assert list(cache_failures(mutated(rendered, "Deployment", "valkey", change))) == ["valkey: volume valkey-password"]


@pytest.mark.parametrize("name", VALKEY_PASSWORD_ENV)
def test_a_password_in_the_valkey_env_names_it(rendered: list, name: str) -> None:
    """ledger 1437: an env copy is fixed at process start and outlives a rotation."""

    def change(deployment: dict) -> None:
        valkey_container(deployment)["env"].append(
            {"name": name, "valueFrom": {"secretKeyRef": {"name": VALKEY_PASSWORD_SECRET, "key": "password"}}}
        )

    assert cache_failures(mutated(rendered, "Deployment", "valkey", change)) == {f"valkey: env {name}": None}


def test_a_liveness_without_the_hash_check_names_it(rendered: list) -> None:
    probe = ["sh", "-c", "valkey-cli -s /run/valkey/valkey.sock ping >/dev/null"]

    def change(deployment: dict) -> None:
        valkey_container(deployment)["livenessProbe"]["exec"]["command"] = probe

    assert cache_failures(mutated(rendered, "Deployment", "valkey", change)) == {
        "valkey: liveness hash check": " ".join(probe)
    }


def with_auth_clients(value: str):
    def change(deployment: dict) -> None:
        env = valkey_container(deployment)["env"]
        next(e for e in env if e["name"] == "VALKEY_TLS_AUTH_CLIENTS")["value"] = value

    return change


def test_the_rendered_cache_requires_a_leaf_and_verifies_it_against_its_ca(rendered: list) -> None:
    """B-V6: `--tls-auth-clients yes`, read from the env var, verified against `ca.crt` from `valkey-tls`."""
    deployment, _, _ = valkey_objects(rendered)
    container = valkey_container(deployment)
    env = {e["name"]: e.get("value") for e in container["env"]}
    assert env["VALKEY_TLS_AUTH_CLIENTS"] == "yes"
    script = " ".join(container["args"])
    assert '--tls-auth-clients "$VALKEY_TLS_AUTH_CLIENTS"' in script
    assert f"--tls-ca-cert-file {VALKEY_TLS_DIR}/ca.crt" in script


@pytest.mark.parametrize("value", ["no", "optional", "", "false"])
def test_another_client_auth_names_it(rendered: list, value: str) -> None:
    """Off ("no", or the "false" an unquoted `no` coerces to) and "optional" (B-V5) are reverts: neither is this step."""
    change = with_auth_clients(value)
    assert cache_failures(mutated(rendered, "Deployment", "valkey", change)) == {
        "valkey: VALKEY_TLS_AUTH_CLIENTS with no VALKEY hop in cleartext": value
    }


def test_the_leaf_volume_at_another_secret_names_it_for_the_cache(rendered: list) -> None:
    def change(deployment: dict) -> None:
        volume = next(v for v in deployment["spec"]["template"]["spec"]["volumes"] if v["name"] == VALKEY_SECRET)
        volume["secret"]["secretName"] = "nats-tls"

    assert list(cache_failures(mutated(rendered, "Deployment", "valkey", change))) == ["valkey: volume valkey-tls"]


def test_a_valkey_tls_mount_with_a_subpath_names_it(rendered: list) -> None:
    """ledger 1429: a subPath would serve one file, not the directory the boot script reads from."""

    def change(deployment: dict) -> None:
        mount = next(m for m in valkey_container(deployment)["volumeMounts"] if m["name"] == VALKEY_SECRET)
        mount["subPath"] = "tls.crt"

    assert list(cache_failures(mutated(rendered, "Deployment", "valkey", change))) == ["valkey: mount valkey-tls"]


def valkey_objects(documents: list) -> tuple[dict, dict, dict]:
    by_name = {(d["kind"], d["metadata"]["name"]): d for d in documents}
    return by_name[("Deployment", "valkey")], by_name[("Service", "valkey")], by_name[("NetworkPolicy", "valkey-ingress")]


def reopen_container_port(deployment: dict) -> None:
    valkey_container(deployment)["ports"].insert(0, {"name": "valkey", "containerPort": 6379})


def reopen_service_port(service: dict) -> None:
    service["spec"]["ports"].insert(0, {"name": "valkey", "port": 6379, "targetPort": "valkey"})


def reopen_policy_port(policy: dict) -> None:
    for rule in policy["spec"]["ingress"]:
        rule["ports"].insert(0, {"port": 6379, "protocol": "TCP"})


def reopen_listener(deployment: dict) -> None:
    container = valkey_container(deployment)
    container["args"] = [a.replace("--port 0", "--port 6379") for a in container["args"]]


REOPENED = {
    "valkey: containerPort 6379 with no VALKEY hop in cleartext": ("Deployment", "valkey", reopen_container_port),
    "valkey: Service port 6379 with no VALKEY hop in cleartext": ("Service", "valkey", reopen_service_port),
    "valkey: valkey-ingress port 6379 with no VALKEY hop in cleartext": (
        "NetworkPolicy",
        "valkey-ingress",
        reopen_policy_port,
    ),
}


@pytest.mark.parametrize("failure", sorted(REOPENED))
def test_a_reopened_plaintext_port_names_it(rendered: list, failure: str) -> None:
    """6379 must be ABSENT everywhere (B-V4.3)."""
    kind, name, change = REOPENED[failure]
    assert list(cache_failures(mutated(rendered, kind, name, change))) == [failure]


def test_a_reopened_plaintext_listener_names_it(rendered: list) -> None:
    assert cache_failures(mutated(rendered, "Deployment", "valkey", reopen_listener)) == {
        "valkey: --port with no VALKEY hop in cleartext": ["6379"]
    }


def test_a_listener_with_no_port_argument_names_it(rendered: list) -> None:
    """valkey-server listens on 6379 when `--port` is absent, so only an explicit `--port 0` closes it."""
    change = with_valkey_script(lambda script: script.replace("  --port 0 \\\n", ""))
    assert cache_failures(mutated(rendered, "Deployment", "valkey", change)) == {
        "valkey: --port with no VALKEY hop in cleartext": []
    }


def test_6379_reopened_everywhere_names_every_place(rendered: list) -> None:
    documents = rendered
    for kind, name, change in REOPENED.values():
        documents = mutated(documents, kind, name, change)
    documents = mutated(documents, "Deployment", "valkey", reopen_listener)
    assert sorted(cache_failures(documents)) == sorted([*REOPENED, "valkey: --port with no VALKEY hop in cleartext"])


def test_a_closed_tls_port_on_the_policy_names_it(rendered: list) -> None:
    def change(policy: dict) -> None:
        for rule in policy["spec"]["ingress"]:
            rule["ports"] = [p for p in rule["ports"] if p["port"] != 6380]

    assert cache_failures(mutated(rendered, "NetworkPolicy", "valkey-ingress", change)) == {
        "valkey: valkey-ingress port 6380 from app: gateway": []
    }


def test_a_valkey_ingress_port_from_another_peer_names_it(rendered: list) -> None:
    """ledger 1429: 6380 open from a DIFFERENT peer must still fail — the check is gateway's rule, not any rule."""

    def change(policy: dict) -> None:
        policy["spec"]["ingress"] = [
            {
                "from": [{"podSelector": {"matchLabels": {"app": "gateway"}}}],
                "ports": [{"protocol": "TCP", "port": 9999}],
            },
            {
                "from": [{"podSelector": {"matchLabels": {"app": "not-gateway"}}}],
                "ports": [{"protocol": "TCP", "port": 6380}],
            },
        ]

    assert cache_failures(mutated(rendered, "NetworkPolicy", "valkey-ingress", change)) == {
        "valkey: valkey-ingress port 6380 from app: gateway": [9999]
    }


def write_table() -> None:
    document = committed()
    rendered = digests(parent_render(REPOSITORY))
    lines = [TABLE_HEADER, TABLE_REGENERATE, f"targetRevision  {revision_of(document)}"]
    lines += [f"{digest}  {key}" for key, digest in sorted(rendered.items())]
    TABLE.write_text("\n".join(lines) + "\n")
    print(f"wrote {len(rendered)} object digest(s) at {revision_of(document)} to {TABLE}")


if __name__ == "__main__":
    if sys.argv[1:] != ["--write"]:
        sys.exit("usage: python3 scripts/gates/test_yadgar_application.py --write")
    write_table()
