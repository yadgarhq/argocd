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
import sys
import urllib.request
from pathlib import Path

import pytest
import yaml
from test_no_two_owners import REPOSITORY, parent_render, tuples_of  # noqa: F401  (same flags as P)

APPLICATION = REPOSITORY / "applications" / "yadgar.yaml"
TABLE = Path(__file__).resolve().parent / "yadgar_render.sha256"
EXAMPLE_URL = "https://raw.githubusercontent.com/yadgarhq/chart/v{revision}/example/application.yaml"
EXPECTED_OBJECTS = 90

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
        d["metadata"]["name"]: {
            v["secret"]["secretName"] for v in d["spec"]["template"]["spec"].get("volumes", []) if "secret" in v
        }
        for d in documents
        if d["kind"] == "Deployment"
    }
    assert issued == set(CLIENT_LEAVES.values())
    assert {name: leaf for name, leaf in CLIENT_LEAVES.items() if leaf not in mounted.get(name, set())} == {}


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
# crossed its hop OK under optional (ADR-0884). Each later step edits this
# one line.
EXPECTED_CLIENT_AUTH = {server: "off" for server in SERVERS} | {
    "task-db": "required",
    "iam-db": "required",
    "project-db": "required",
    "project": "required",
    "task": "optional",
    "iam": "optional",
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
# every non-`LISTEN_` `*_TLS_ENABLED` env NAME in every container, init
# container and ephemeral container of every object in the render, hooks
# included. That set must equal this map, so a caller or hop added by a chart
# bump reddens here instead of going unpinned. No container in the render
# uses `envFrom`; one that did could carry a hop this scan cannot see.
CLIENT_HOPS = {
    "gateway": ("TASK", "IAM", "PROJECT"),
    "iam": ("IAM_DB",),
    "task": ("TASK_DB",),
    "project": ("PROJECT_DB",),
}
# THE HOPS A CALLER DIALS IN CLEARTEXT, BY DESIGN, UNTIL THEIR HOP STEP (PB-3).
# gateway 0.12.0 and iam 0.11.0 (parent 0.23.1) render `NATS_TLS_ENABLED` and
# gateway `VALKEY_TLS_ENABLED`, both "0", from the explicit `tls.enabled:
# false` this file states. They are pinned here, not in `CLIENT_HOPS`, because
# `CLIENT_HOPS` asserts "1" and a presented leaf, which a hop that is off does
# not have. ADR-0885's iam/NATS entry lands here first; each hop MOVES to
# `CLIENT_HOPS` in the values PR that turns it on (B-N4.2 for NATS, B-V4.2 for
# valkey). While a hop is off its chart renders the `_ENABLED` "0" line and
# nothing else: no other `<PREFIX>_TLS_*` env, and no volume projecting the
# server leaf whose `ca.crt` it would trust (the value named beside each).
CLEARTEXT_HOPS = {
    "gateway": {"NATS": "nats-tls", "VALKEY": "valkey-tls"},
    "iam": {"NATS": "nats-tls"},
}
# The cert-manager Issuer (not its Secret) every client leaf is issued from.
LEAF_ISSUER = {"group": "cert-manager.io", "kind": "Issuer", "name": "yadgar-internal-ca"}
# Each env half and the leaf key the volume must project to that file.
LEAF_KEYS = {"CERT": "tls.crt", "KEY": "tls.key"}


CONTAINER_LISTS = ("containers", "initContainers", "ephemeralContainers")


def rendered_client_hops(documents: list) -> set[tuple[str, str, str]]:
    """Every `(kind, name, PREFIX)` whose render carries a non-`LISTEN_` `<PREFIX>_TLS_ENABLED` env.

    By NAME, whatever its value: a hop rendered `"0"` is still a hop.
    """
    hops: set[tuple[str, str, str]] = set()

    def walk(node: object, kind: str, name: str) -> None:
        if isinstance(node, dict):
            for key in CONTAINER_LISTS:
                for container in node.get(key) or []:
                    for env in container.get("env") or []:
                        variable = env.get("name", "")
                        if variable.endswith("_TLS_ENABLED") and not variable.startswith("LISTEN_"):
                            hops.add((kind, name, variable.removesuffix("_TLS_ENABLED")))
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
    """Every hop this file names, presented (`CLIENT_HOPS`) or cleartext (`CLEARTEXT_HOPS`)."""
    return {
        ("Deployment", caller, prefix)
        for hops in (CLIENT_HOPS, CLEARTEXT_HOPS)
        for caller, prefixes in hops.items()
        for prefix in prefixes
    }


def cleartext_failures(documents: list) -> dict[str, object]:
    """Every `CLEARTEXT_HOPS` hop that renders more than its "0" switch, keyed `<caller>/<PREFIX>: <what>`."""
    deployments = {d["metadata"]["name"]: d for d in documents if d["kind"] == "Deployment"}
    failures: dict[str, object] = {}
    for caller, hops in CLEARTEXT_HOPS.items():
        deployment = deployments.get(caller)
        if deployment is None:
            failures[f"{caller}: Deployment"] = None
            continue
        pod = deployment["spec"]["template"]["spec"]
        env = {
            e["name"]: e.get("value")
            for key in CONTAINER_LISTS
            for container in pod.get(key) or []
            for e in container.get("env") or []
        }
        for prefix, server_leaf in hops.items():
            hop = f"{caller}/{prefix}"
            switch = f"{prefix}_TLS_ENABLED"
            if env.get(switch) != "0":
                failures[f"{hop}: {switch}"] = env.get(switch)
            for name in sorted(n for n in env if n.startswith(f"{prefix}_TLS_") and n != switch):
                failures[f"{hop}: {name}"] = env[name]
            for volume in pod.get("volumes") or []:
                if (volume.get("secret") or {}).get("secretName") == server_leaf:
                    failures[f"{hop}: volume {volume['name']}"] = server_leaf
    return failures


def client_identity_failures(documents: list) -> dict[str, object]:
    """Every hop whose caller does not present its own issued leaf, keyed `<caller>/<PREFIX>: <what>`.

    THE PATHS ARE READ OFF THE RENDER, never written here: each env names a
    file, the mount at that file's directory names a volume, and that volume
    must project the caller's leaf with `tls.crt`/`tls.key` at the file's name.
    A rendered hop that `CLIENT_HOPS` does not name is keyed
    `<kind>/<name>/<PREFIX>: not in CLIENT_HOPS`.
    """
    certificates = {d["spec"]["secretName"]: d["spec"] for d in documents if d["kind"] == "Certificate"}
    deployments = {d["metadata"]["name"]: d for d in documents if d["kind"] == "Deployment"}
    failures: dict[str, object] = {}
    for kind, name, prefix in sorted(rendered_client_hops(documents) - pinned_hops()):
        failures[f"{kind}/{name}/{prefix}: not in CLIENT_HOPS"] = env_value(
            documents, kind, name, f"{prefix}_TLS_ENABLED"
        )
    for caller, prefixes in CLIENT_HOPS.items():
        leaf = CLIENT_LEAVES[caller]
        certificate = certificates.get(leaf) or {}
        if certificate.get("issuerRef") != LEAF_ISSUER:
            failures[f"{caller}: {leaf} issuerRef"] = certificate.get("issuerRef")
        if "client auth" not in (certificate.get("usages") or []):
            failures[f"{caller}: {leaf} usages"] = certificate.get("usages")
        deployment = deployments.get(caller)
        if deployment is None:
            failures[f"{caller}: Deployment"] = None
            continue
        pod = deployment["spec"]["template"]["spec"]
        container = pod["containers"][0]
        env = {e["name"]: e.get("value") for e in container.get("env", [])}
        mounts = {m["mountPath"]: m for m in container.get("volumeMounts", [])}
        volumes = {v["name"]: v for v in pod.get("volumes", [])}
        for prefix in prefixes:
            hop = f"{caller}/{prefix}"
            if env.get(f"{prefix}_TLS_ENABLED") != "1":
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


def test_another_secret_on_the_gateway_names_all_three_hops() -> None:
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



def test_a_missing_caller_deployment_names_its_caller(rendered: list) -> None:
    documents = [d for d in rendered if not (d["kind"] == "Deployment" and d["metadata"]["name"] == "iam")]
    assert client_identity_failures(documents) == {"iam: Deployment": None}


def test_the_rendered_client_hops_are_exactly_the_pinned_hops(rendered: list) -> None:
    assert rendered_client_hops(rendered) == pinned_hops()


def test_every_cleartext_hop_renders_its_switch_off_and_nothing_else(rendered: list) -> None:
    assert cleartext_failures(rendered) == {}


def test_a_cleartext_hop_turned_on_names_its_hop(rendered: list) -> None:
    change = set_env("VALKEY_TLS_ENABLED", "1")
    assert cleartext_failures(mutated(rendered, "Deployment", "gateway", change)) == {
        "gateway/VALKEY: VALKEY_TLS_ENABLED": "1"
    }


def test_a_ca_env_on_a_cleartext_hop_names_its_hop(rendered: list) -> None:
    change = add_env("containers", "NATS_TLS_CA_FILE", "/var/run/secrets/nats-ca/ca.crt")
    assert cleartext_failures(mutated(rendered, "Deployment", "iam", change)) == {
        "iam/NATS: NATS_TLS_CA_FILE": "/var/run/secrets/nats-ca/ca.crt"
    }


def test_a_server_leaf_volume_on_a_cleartext_hop_names_its_hop(rendered: list) -> None:
    def mount_nats_ca(deployment: dict) -> None:
        deployment["spec"]["template"]["spec"]["volumes"].append(
            {"name": "nats-ca", "secret": {"secretName": "nats-tls"}}
        )

    assert cleartext_failures(mutated(rendered, "Deployment", "gateway", mount_nats_ca)) == {
        "gateway/NATS: volume nats-ca": "nats-tls"
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
