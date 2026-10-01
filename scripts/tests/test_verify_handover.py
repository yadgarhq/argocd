"""`scripts/verify_handover.py`: the read-only post-merge verifier.

No live cluster. Every kubectl call is answered from
`fixtures/verify_handover/cluster.json` by a fake runner, keyed by the argument
list after `--context`. An argument list the fixture does not hold answers as a
kubectl failure, so a call the code makes but the fixture never planned for is
a red test, never a silent empty list.

WHAT IS ASSERTED, and each has a red case below:

  1. The one kubectl function refuses a missing or blank `--context`, any verb
     other than `get`, and a Secret read that is not custom-columns metadata.
     It never reaches the runner when it refuses. A non-zero kubectl exit,
     Forbidden included, raises rather than reading as empty.
  2. `collect` finds every Application under root (root's own resources, plus
     the Applications owned by an ApplicationSet root tracks), and nothing
     else. It records uids, finalizers and syncPolicy, the CRD map and its
     hash, the objects those Applications track (Secrets skipped by kind,
     never read), and the workloads and pods in their destination
     namespaces (Job and runner pods, and finished pods, left out).
  3. `collect` refuses a snapshot with no CRDs, no root, or a root that
     tracks no Application.
  4. `diff` refuses two snapshots from different clusters, and empty ones.
  5. `diff` fails on: a changed or vanished uid (Application, CRD, tracked
     object, workload, pod, PVC, Secret); a generation change on a
     Deployment or a CRD; any deletionTimestamp; a changed finalizer list; an
     automated Application that is not Synced; any Application not Healthy;
     a manual Application that went from Synced to OutOfSync; a Secret
     resourceVersion change; an edge probe that is not the expected status
     over a verified TLS session.
  6. `diff` passes, with a warning, on a syncPolicy change, a restart-count
     increase, and a generation change outside Deployments and CRDs. A
     skipped edge probe prints SKIPPED and is never counted as a pass.
  7. The wait states: a doc-only merge (no operation) counts as done once
     root's revision is the sha; a Running operation is pending; a Failed one
     fails; `--since` needs an operation that started after T; `--settled`
     needs every Application reconciled after T.
"""

from __future__ import annotations

import copy as copy_module
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "verify_handover" / "cluster.json"
CONTEXT = "kind-test"

# `scripts/` is not a package, so the module is loaded by path.
_spec = importlib.util.spec_from_file_location("verify_handover", REPOSITORY / "scripts" / "verify_handover.py")
vh = importlib.util.module_from_spec(_spec)
sys.modules["verify_handover"] = vh
_spec.loader.exec_module(vh)


class FakeRunner:
    """Answers kubectl from the fixture; records every command it is handed."""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **_kwargs):
        self.calls.append(list(cmd))
        assert cmd[:3] == ["kubectl", "--context", CONTEXT], cmd
        key = " ".join(cmd[3:])
        if key not in self.responses:
            return subprocess.CompletedProcess(cmd, 1, "", f'Error from server (Forbidden): no fixture for "{key}"')
        value = self.responses[key]
        stdout = value if isinstance(value, str) else json.dumps(value)
        return subprocess.CompletedProcess(cmd, 0, stdout, "")


@pytest.fixture
def responses() -> dict:
    data = json.loads(FIXTURE.read_text())
    data.pop("_comment")
    return data


def cluster(responses: dict) -> "vh.Cluster":
    return vh.Cluster(CONTEXT, runner=FakeRunner(responses))


@pytest.fixture
def snap(responses: dict) -> dict:
    return vh.collect(cluster(responses), secrets_namespaces=["yadgar"])


def levels(report) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"FAIL": [], "WARN": [], "INFO": []}
    for finding in report.findings:
        out[finding.level].append(finding.key)
    return out


# --- 1. the kubectl function -------------------------------------------------


@pytest.mark.parametrize("context", [None, "", "   "])
def test_kubectl_refuses_without_a_context(context) -> None:
    runner = FakeRunner({})
    with pytest.raises(vh.UsageError, match="--context"):
        vh.kubectl(context, ["get", "pods"], runner=runner)
    assert runner.calls == []


@pytest.mark.parametrize("verb", ["apply", "delete", "patch", "exec", "create", "edit", "annotate"])
def test_kubectl_refuses_any_verb_but_get(verb) -> None:
    runner = FakeRunner({})
    with pytest.raises(vh.UsageError, match="read-only"):
        vh.kubectl(CONTEXT, [verb, "pods"], runner=runner)
    assert runner.calls == []


@pytest.mark.parametrize("kind", ["secrets", "secret", "Secret", "deployments,secrets", "secret/foo"])
def test_kubectl_refuses_a_secret_read_that_is_not_metadata_columns(kind) -> None:
    runner = FakeRunner({})
    with pytest.raises(vh.UsageError, match="Secret"):
        vh.kubectl(CONTEXT, ["get", kind, "-n", "yadgar", "-o", "json"], runner=runner)
    assert runner.calls == []


@pytest.mark.parametrize(
    "args",
    [
        ["get", "-n", "yadgar", "secrets", "-o", "json"],
        ["get", "--namespace=yadgar", "secret", "x", "-o", "yaml"],
        ["get", "-A", "deployments,secrets", "-o", "json"],
    ],
)
def test_kubectl_refuses_a_secret_in_any_position(args) -> None:
    runner = FakeRunner({})
    with pytest.raises(vh.UsageError, match="Secret"):
        vh.kubectl(CONTEXT, args, runner=runner)
    assert runner.calls == []


SECRET_ARGV = ["get", "secrets", "-n", "yadgar", "-o", vh.SECRET_COLUMNS, "--no-headers"]


def test_kubectl_runs_the_one_secret_argv_it_allows() -> None:
    runner = FakeRunner({" ".join(SECRET_ARGV): "a b c\n"})
    assert vh.kubectl(CONTEXT, SECRET_ARGV, runner=runner) == "a b c\n"
    assert len(runner.calls) == 1


SECRET_BYPASSES = {
    # `last-applied-configuration` holds the whole Secret, data included (measured live).
    "annotations-column": ["get", "secrets", "-n", "yadgar", "-o", "custom-columns=A:.metadata.annotations", "--no-headers"],
    "managed-fields-column": ["get", "secrets", "-n", "yadgar", "-o", "custom-columns=M:.metadata.managedFields", "--no-headers"],
    "data-column": ["get", "secrets", "-n", "yadgar", "-o", "custom-columns=NAME:.metadata.name,D:.data", "--no-headers"],
    # kubectl honours the LAST -o.
    "repeated-output": ["get", "secrets", "-n", "yadgar", "-o", vh.SECRET_COLUMNS, "--no-headers", "-o", "json"],
    "repeated-output-eq": ["get", "secrets", "-n", "yadgar", "-o", vh.SECRET_COLUMNS, "--no-headers", "--output=json"],
    "filename": ["get", "-f", "secret.yaml", "-o", vh.SECRET_COLUMNS, "--no-headers"],
    "filename-long": ["get", "--filename=secret.yaml", "-o", "json"],
    "kustomize": ["get", "-k", "overlay", "-o", "json"],
    "template": ["get", "secrets", "-n", "yadgar", "--template={{.data}}"],
    "show-managed-fields": ["get", "secrets", "-n", "yadgar", "-o", vh.SECRET_COLUMNS, "--no-headers", "--show-managed-fields"],
    "named-secret": ["get", "secrets", "iam-keys", "-n", "yadgar", "-o", vh.SECRET_COLUMNS, "--no-headers"],
    "go-template": ["get", "secrets", "-n", "yadgar", "-o=go-template={{.data}}"],
}


@pytest.mark.parametrize("case", sorted(SECRET_BYPASSES))
def test_every_secret_bypass_is_refused(case) -> None:
    runner = FakeRunner({})
    with pytest.raises(vh.UsageError):
        vh.kubectl(CONTEXT, SECRET_BYPASSES[case], runner=runner)
    assert runner.calls == []


@pytest.mark.parametrize(
    "flag",
    [
        "-shttps://x",
        "-s",
        "--server=https://x",
        "--as-group=system:masters",
        "--as-uid=0",
        "--as=admin",
        "--username=admin",
        "--password=x",
        "--client-key=k.pem",
        "--client-certificate=c.pem",
        "--certificate-authority=ca.pem",
        "--insecure-skip-tls-verify",
        "--insecure-skip-tls-verify=true",
        "--token=x",
        "--kubeconfig=/tmp/k",
        "--cluster=prod",
        "--user=admin",
        "--raw=/api",
        "-l",
        "--selector=a=b",
        "-w",
    ],
)
def test_kubectl_refuses_every_flag_outside_the_allowlist(flag) -> None:
    runner = FakeRunner({})
    with pytest.raises(vh.UsageError, match="not allowed"):
        vh.kubectl(CONTEXT, ["get", "pods", "-n", "keda", flag, "-o", "json"], runner=runner)
    assert runner.calls == []


@pytest.mark.parametrize("output", ["yaml", "wide", "name", "custom-columns=N:.metadata.name", "jsonpath={.items}"])
def test_kubectl_refuses_any_output_but_json_outside_the_secret_argv(output) -> None:
    with pytest.raises(vh.UsageError, match="output"):
        vh.kubectl(CONTEXT, ["get", "pods", "-n", "keda", "-o", output], runner=FakeRunner({}))


def test_kubectl_refuses_a_flag_as_the_namespace_value() -> None:
    with pytest.raises(vh.UsageError):
        vh.kubectl(CONTEXT, ["get", "pods", "-n", "--kubeconfig=/tmp/k", "-o", "json"], runner=FakeRunner({}))


def test_kubectl_refuses_an_override_of_the_context_in_args() -> None:
    runner = FakeRunner({})
    with pytest.raises(vh.UsageError, match="--context"):
        vh.kubectl(CONTEXT, ["get", "pods", "--context", "production"], runner=runner)
    assert runner.calls == []


def test_kubectl_non_zero_exit_raises(responses) -> None:
    with pytest.raises(vh.KubectlError, match="Forbidden"):
        vh.kubectl(CONTEXT, ["get", "nodes", "-o", "json"], runner=FakeRunner(responses))


def test_a_forbidden_call_inside_collect_is_fatal(responses) -> None:
    del responses["get ConfigMap -A -o json"]
    with pytest.raises(vh.KubectlError, match="Forbidden"):
        vh.collect(cluster(responses))


def test_cluster_refuses_without_a_context() -> None:
    with pytest.raises(vh.UsageError, match="--context"):
        vh.Cluster("", runner=FakeRunner({}))


# --- 2. collect ---------------------------------------------------------------


def test_collect_finds_every_application_under_root_and_nothing_else(snap) -> None:
    assert sorted(snap["applications"]) == ["argocd", "iam", "keda", "root"]
    assert snap["declared"] == ["argocd", "iam", "keda"]
    assert snap["cluster_id"] == "cluster-0000"
    assert snap["context"] == CONTEXT


def test_collect_records_application_identity(snap) -> None:
    keda = snap["applications"]["keda"]
    assert keda["uid"] == "app-keda"
    assert keda["finalizers"] == []
    assert keda["automated"] is True
    assert keda["syncPolicy"] == {"automated": {"selfHeal": True}, "retry": {"limit": 6}}
    assert keda["sync"] == "Synced" and keda["health"] == "Healthy"
    assert "generation" not in keda
    assert snap["applications"]["argocd"]["automated"] is False


def test_collect_records_root_operation_and_prune_result(snap) -> None:
    op = snap["root_operation"]
    assert op["revision"] == "1111111111111111111111111111111111111111"
    assert op["phase"] == "Succeeded"
    assert op["pruned"] == ["argoproj.io/Application/old-operator"]


def test_collect_records_crds_and_their_hash(snap) -> None:
    assert snap["crds"] == {
        "applications.argoproj.io": {"uid": "crd-app", "generation": 2, "deletionTimestamp": None},
        "certificaterequests.cert-manager.io": {"uid": "crd-cr", "generation": 1, "deletionTimestamp": None},
        "gatewayclasses.gateway.networking.k8s.io": {"uid": "crd-gc", "generation": 1, "deletionTimestamp": None},
        "scaledobjects.keda.sh": {"uid": "crd-so", "generation": 1, "deletionTimestamp": None},
    }
    assert snap["crd_count"] == 4
    assert len(snap["crd_hash"]) == 16


def test_collect_records_tracked_objects_and_never_reads_a_secret(snap, responses) -> None:
    runner = FakeRunner(responses)
    vh.collect(vh.Cluster(CONTEXT, runner=runner))
    assert not any("secret" in " ".join(c).lower() for c in runner.calls)
    objects = snap["objects"]
    assert objects["apps/Deployment/keda/keda-operator"] == {"uid": "dep-keda", "generation": 5, "deletionTimestamp": None}
    assert objects["/ConfigMap/keda/keda-cfg"]["uid"] == "cm-keda"
    assert objects["/Secret/keda/keda-certs"] == {"skipped": "Secret"}
    assert objects["argoproj.io/ApplicationSet/argocd/yadgar-modules"]["uid"] == "appset-modules"
    assert "/ConfigMap/keda/unrelated" not in objects
    # CRDs live in the CRD map, not here.
    assert not any("CustomResourceDefinition" in key for key in objects)


def test_collect_records_namespace_workloads_and_filters_pods(snap) -> None:
    w = snap["workloads"]
    assert w["Deployment/keda/keda-operator"] == {"uid": "dep-keda", "generation": 5, "deletionTimestamp": None, "scope": "root"}
    assert w["StatefulSet/argocd/argocd-application-controller"]["uid"] == "sts-argocd"
    assert w["PersistentVolumeClaim/keda/keda-data"] == {
        "uid": "pvc-keda",
        "volumeName": "pv-1",
        "phase": "Bound",
        "deletionTimestamp": None,
        "scope": "root",
    }
    pods = snap["pods"]
    assert sorted(pods) == [
        "argocd/argocd-application-controller-0",
        "keda/envoy-proxy-6689769b8b-abcde",
        "keda/keda-operator-5569f5fbcc-fn6ll",
    ]
    assert pods["keda/keda-operator-5569f5fbcc-fn6ll"]["restarts"] == 2
    assert snap["namespaces"] == ["argocd", "iam-ns", "keda"]


def test_collect_records_instances_of_every_tracked_crd(snap) -> None:
    crs = snap["custom_resources"]
    assert crs["scaledobjects.keda.sh/yadgar/gateway"] == {
        "uid": "so-gateway",
        "generation": 3,
        "deletionTimestamp": None,
        "scope": "foreign:yadgar",
    }
    assert crs["scaledobjects.keda.sh/keda/keda-own"]["scope"] == "root"
    assert crs["scaledobjects.keda.sh/keda/loose"]["scope"] == "foreign:untracked"
    assert crs["gatewayclasses.gateway.networking.k8s.io//eg"]["scope"] == "foreign:yadgar"
    assert not any(k.startswith("_") for record in crs.values() for k in record)


def test_collect_never_lists_issuance_records_or_absent_crds(responses) -> None:
    runner = FakeRunner(responses)
    vh.collect(vh.Cluster(CONTEXT, runner=runner))
    resources = [c[4] for c in runner.calls]
    assert "scaledobjects.keda.sh" in resources
    assert "certificaterequests.cert-manager.io" not in resources
    assert "absent.example.com" not in resources


def test_the_edge_probe_refuses_tls_below_1_2(monkeypatch) -> None:
    """Set explicitly, not inherited: whatever floor the platform default carries, the probe's is 1.2."""
    import ssl
    import types

    handed = types.SimpleNamespace(minimum_version="platform default")
    monkeypatch.setattr(vh.ssl, "create_default_context", lambda cafile=None: handed)
    assert vh.tls_context(None) is handed
    assert handed.minimum_version == ssl.TLSVersion.TLSv1_2


def test_the_edge_probe_context_verifies() -> None:
    import ssl

    context = vh.tls_context(None)
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True


def test_an_object_owned_through_another_apps_object_is_foreign(snap) -> None:
    """envoy-proxy is owned by GatewayClass eg, which yadgar tracks; its pod inherits that through the ReplicaSet."""
    assert snap["workloads"]["Deployment/keda/envoy-proxy"]["scope"] == "foreign:yadgar"
    assert snap["pods"]["keda/envoy-proxy-6689769b8b-abcde"]["scope"] == "foreign:yadgar"
    assert snap["pods"]["keda/keda-operator-5569f5fbcc-fn6ll"]["scope"] == "root"
    assert snap["pods"]["argocd/argocd-application-controller-0"]["scope"] == "root"


def test_hook_resources_are_never_listed_or_compared(snap) -> None:
    """A hook is recreated on every sync (BeforeHookCreation), so its uid always changes."""
    assert not any("keda-certgen" in key for key in snap["objects"])
    assert not any("keda-certgen" in key for key in snap["applications"]["keda"]["resources"])


def test_collect_reads_secret_metadata_only_when_asked(snap) -> None:
    assert snap["secrets"] == {
        "yadgar/iam-keys": {"uid": "sec-iam", "resourceVersion": "100"},
        "yadgar/yadgar-dev-ca": {"uid": "sec-ca", "resourceVersion": "200"},
    }


def test_collect_records_the_edge_probe_it_is_handed(responses) -> None:
    probe = lambda: {"url": "https://gw/", "status": 405, "verified": True, "error": None}  # noqa: E731
    result = vh.collect(cluster(responses), edge=probe, edge_expect=405)
    assert result["edge"] == {"url": "https://gw/", "status": 405, "verified": True, "error": None, "expect": 405}


def test_collect_without_a_probe_records_it_skipped(snap) -> None:
    assert snap["edge"] == {"skipped": "no --edge-url/--edge-ca given"}


# --- 3. collect refusals ------------------------------------------------------


def test_collect_refuses_zero_crds(responses) -> None:
    responses["get customresourcedefinitions.apiextensions.k8s.io -o json"]["items"] = []
    with pytest.raises(vh.SnapshotError, match="CRD"):
        vh.collect(cluster(responses))


def test_collect_refuses_a_missing_root(responses) -> None:
    items = responses["get applications.argoproj.io -n argocd -o json"]["items"]
    responses["get applications.argoproj.io -n argocd -o json"]["items"] = [a for a in items if a["metadata"]["name"] != "root"]
    with pytest.raises(vh.SnapshotError, match="root"):
        vh.collect(cluster(responses))


def test_collect_refuses_a_root_that_tracks_no_application(responses) -> None:
    root = responses["get applications.argoproj.io -n argocd -o json"]["items"][0]
    root["status"]["resources"] = []
    with pytest.raises(vh.SnapshotError, match="no Application"):
        vh.collect(cluster(responses))


# --- 4. diff refusals ---------------------------------------------------------


def test_identical_snapshots_pass_and_report_what_was_compared(snap) -> None:
    report = vh.diff(snap, copy_module.deepcopy(snap))
    assert report.exit_code == 0, report.render()
    text = report.render()
    assert "compared 4 Application(s), 4 CRD(s)" in text
    assert levels(report)["FAIL"] == []


def test_diff_refuses_two_clusters(snap) -> None:
    after = copy_module.deepcopy(snap)
    after["cluster_id"] = "cluster-9999"
    with pytest.raises(vh.SnapshotError, match="different clusters"):
        vh.diff(snap, after)


@pytest.mark.parametrize("field", ["applications", "crds"])
def test_diff_refuses_an_empty_snapshot(snap, field) -> None:
    after = copy_module.deepcopy(snap)
    after[field] = {}
    with pytest.raises(vh.SnapshotError, match="empty"):
        vh.diff(snap, after)


def test_diff_refuses_an_unknown_schema(snap) -> None:
    after = copy_module.deepcopy(snap)
    after["schema"] = 999
    with pytest.raises(vh.SnapshotError, match="schema"):
        vh.diff(snap, after)


# --- 5. diff failures ---------------------------------------------------------


def mutated(snap: dict, mutate) -> "vh.Report":
    after = copy_module.deepcopy(snap)
    mutate(after)
    return vh.diff(snap, after)


FAILURES = {
    "app-uid": (lambda s: s["applications"]["keda"].update(uid="new"), "application/keda"),
    "app-gone": (lambda s: s["applications"].pop("keda"), "application/keda"),
    "app-declared-missing": (lambda s: s["declared"].append("ghost"), "application/ghost"),
    "app-finalizer": (
        lambda s: s["applications"]["keda"].update(finalizers=["resources-finalizer.argocd.argoproj.io"]),
        "application/keda",
    ),
    "automated-outofsync": (lambda s: s["applications"]["keda"].update(sync="OutOfSync"), "application/keda"),
    "degraded": (lambda s: s["applications"]["argocd"].update(health="Degraded"), "application/argocd"),
    "manual-synced-to-outofsync": (None, "application/argocd"),
    "op-failed": (
        lambda s: s["applications"]["keda"].update(operation={"phase": "Failed", "startedAt": "x", "finishedAt": "y", "revision": "z"}),
        "application/keda",
    ),
    "crd-uid": (lambda s: s["crds"]["scaledobjects.keda.sh"].update(uid="new"), "crd/scaledobjects.keda.sh"),
    "crd-gone": (lambda s: s["crds"].pop("scaledobjects.keda.sh"), "crd/scaledobjects.keda.sh"),
    "crd-generation": (lambda s: s["crds"]["scaledobjects.keda.sh"].update(generation=2), "crd/scaledobjects.keda.sh"),
    "crd-terminating": (
        lambda s: s["crds"]["scaledobjects.keda.sh"].update(deletionTimestamp="2026-10-01T12:00:00Z"),
        "crd/scaledobjects.keda.sh",
    ),
    "object-uid": (lambda s: s["objects"]["/ConfigMap/keda/keda-cfg"].update(uid="new"), "object//ConfigMap/keda/keda-cfg"),
    "object-gone": (lambda s: s["objects"].pop("/ConfigMap/keda/keda-cfg"), "object//ConfigMap/keda/keda-cfg"),
    "object-terminating": (
        lambda s: s["objects"]["/ConfigMap/keda/keda-cfg"].update(deletionTimestamp="t"),
        "object//ConfigMap/keda/keda-cfg",
    ),
    "object-deployment-generation": (
        lambda s: s["objects"]["apps/Deployment/keda/keda-operator"].update(generation=6),
        "object/apps/Deployment/keda/keda-operator",
    ),
    "deployment-generation": (
        lambda s: s["workloads"]["Deployment/keda/keda-operator"].update(generation=6),
        "workload/Deployment/keda/keda-operator",
    ),
    "deployment-uid": (
        lambda s: s["workloads"]["Deployment/keda/keda-operator"].update(uid="new"),
        "workload/Deployment/keda/keda-operator",
    ),
    "statefulset-uid": (
        lambda s: s["workloads"]["StatefulSet/argocd/argocd-application-controller"].update(uid="new"),
        "workload/StatefulSet/argocd/argocd-application-controller",
    ),
    "pvc-uid": (
        lambda s: s["workloads"]["PersistentVolumeClaim/keda/keda-data"].update(uid="new"),
        "workload/PersistentVolumeClaim/keda/keda-data",
    ),
    "pvc-terminating": (
        lambda s: s["workloads"]["PersistentVolumeClaim/keda/keda-data"].update(deletionTimestamp="t"),
        "workload/PersistentVolumeClaim/keda/keda-data",
    ),
    "pod-gone": (lambda s: s["pods"].pop("keda/keda-operator-5569f5fbcc-fn6ll"), "pod/keda/keda-operator-5569f5fbcc-fn6ll"),
    "pod-uid": (
        lambda s: s["pods"]["keda/keda-operator-5569f5fbcc-fn6ll"].update(uid="new"),
        "pod/keda/keda-operator-5569f5fbcc-fn6ll",
    ),
    "pod-terminating": (
        lambda s: s["pods"]["keda/keda-operator-5569f5fbcc-fn6ll"].update(deletionTimestamp="t"),
        "pod/keda/keda-operator-5569f5fbcc-fn6ll",
    ),
    "cr-uid": (
        lambda s: s["custom_resources"]["scaledobjects.keda.sh/keda/keda-own"].update(uid="new"),
        "cr/scaledobjects.keda.sh/keda/keda-own",
    ),
    "cr-gone": (
        lambda s: s["custom_resources"].pop("scaledobjects.keda.sh/keda/keda-own"),
        "cr/scaledobjects.keda.sh/keda/keda-own",
    ),
    "cr-terminating": (
        lambda s: s["custom_resources"]["scaledobjects.keda.sh/keda/keda-own"].update(deletionTimestamp="t"),
        "cr/scaledobjects.keda.sh/keda/keda-own",
    ),
    "secret-uid": (lambda s: s["secrets"]["yadgar/iam-keys"].update(uid="new"), "secret/yadgar/iam-keys"),
    "secret-rv": (lambda s: s["secrets"]["yadgar/iam-keys"].update(resourceVersion="101"), "secret/yadgar/iam-keys"),
    "secret-gone": (lambda s: s["secrets"].pop("yadgar/iam-keys"), "secret/yadgar/iam-keys"),
    "edge-status": (
        lambda s: s.update(edge={"url": "u", "status": 503, "verified": True, "error": None, "expect": 405}),
        "edge",
    ),
    "edge-unverified": (
        lambda s: s.update(edge={"url": "u", "status": None, "verified": False, "error": "CERTIFICATE_VERIFY_FAILED", "expect": 405}),
        "edge",
    ),
}


@pytest.mark.parametrize("case", sorted(FAILURES))
def test_each_failure_rule_reddens(snap, case) -> None:
    mutate, key = FAILURES[case]
    if case == "manual-synced-to-outofsync":
        before = copy_module.deepcopy(snap)
        before["applications"]["argocd"]["sync"] = "Synced"
        report = vh.diff(before, snap)
    else:
        report = mutated(snap, mutate)
    assert report.exit_code == 1, report.render()
    assert key in levels(report)["FAIL"], report.render()


def test_a_manual_application_left_outofsync_is_not_a_failure(snap) -> None:
    assert snap["applications"]["argocd"]["sync"] == "OutOfSync"
    report = vh.diff(snap, copy_module.deepcopy(snap))
    assert report.exit_code == 0, report.render()


# --- 6. diff warnings ---------------------------------------------------------


WARNINGS = {
    "sync-policy": (
        lambda s: s["applications"]["keda"]["syncPolicy"].update(retry={"limit": 7}),
        "application/keda",
    ),
    "restarts": (
        lambda s: s["pods"]["keda/keda-operator-5569f5fbcc-fn6ll"].update(restarts=3),
        "pod/keda/keda-operator-5569f5fbcc-fn6ll",
    ),
    "statefulset-generation": (
        lambda s: s["workloads"]["StatefulSet/argocd/argocd-application-controller"].update(generation=3),
        "workload/StatefulSet/argocd/argocd-application-controller",
    ),
    "cr-generation": (
        lambda s: s["custom_resources"]["scaledobjects.keda.sh/keda/keda-own"].update(generation=4),
        "cr/scaledobjects.keda.sh/keda/keda-own",
    ),
    "root-pruned": (
        lambda s: s["root_operation"].update(revision="2" * 40),
        "root/pruned",
    ),
}


WARNINGS.update(
    {
        "foreign-cr-uid": (
            lambda s: s["custom_resources"]["scaledobjects.keda.sh/yadgar/gateway"].update(uid="new"),
            "cr/scaledobjects.keda.sh/yadgar/gateway",
        ),
        "foreign-cr-terminating": (
            lambda s: s["custom_resources"]["scaledobjects.keda.sh/yadgar/gateway"].update(deletionTimestamp="t"),
            "cr/scaledobjects.keda.sh/yadgar/gateway",
        ),
        "untracked-cr-gone": (
            lambda s: s["custom_resources"].pop("scaledobjects.keda.sh/keda/loose"),
            "cr/scaledobjects.keda.sh/keda/loose",
        ),
        "foreign-deployment-generation": (
            lambda s: s["workloads"]["Deployment/keda/envoy-proxy"].update(generation=3),
            "workload/Deployment/keda/envoy-proxy",
        ),
        "foreign-pod-gone": (
            lambda s: s["pods"].pop("keda/envoy-proxy-6689769b8b-abcde"),
            "pod/keda/envoy-proxy-6689769b8b-abcde",
        ),
        "missing-in-both": (None, "object//ConfigMap/keda/keda-cfg"),
    }
)


@pytest.mark.parametrize("case", sorted(WARNINGS))
def test_each_warning_rule_warns_and_passes(snap, case) -> None:
    mutate, key = WARNINGS[case]
    if case == "missing-in-both":
        before = copy_module.deepcopy(snap)
        before["objects"]["/ConfigMap/keda/keda-cfg"] = {"missing": True}
        report = mutated(before, lambda _s: None)
    else:
        report = mutated(snap, mutate)
    assert report.exit_code == 0, report.render()
    assert key in levels(report)["WARN"], report.render()


def test_a_secret_skipped_before_and_absent_after_is_not_compared(snap) -> None:
    report = mutated(snap, lambda s: s["objects"].pop("/Secret/keda/keda-certs"))
    assert report.exit_code == 0, report.render()
    assert not any("keda-certs" in f.key for f in report.findings)


def test_a_foreign_failure_names_its_owner(snap) -> None:
    report = mutated(snap, lambda s: s["custom_resources"]["scaledobjects.keda.sh/yadgar/gateway"].update(uid="new"))
    assert "foreign:yadgar" in report.render()


def test_an_added_object_is_info(snap) -> None:
    report = mutated(snap, lambda s: s["pods"].update({"keda/new-pod": {"uid": "p", "restarts": 0, "deletionTimestamp": None}}))
    assert report.exit_code == 0
    assert "pod/keda/new-pod" in levels(report)["INFO"]


def test_a_skipped_edge_probe_says_skipped(snap) -> None:
    report = vh.diff(snap, copy_module.deepcopy(snap))
    assert "edge probe SKIPPED" in report.render()
    assert "edge" not in levels(report)["FAIL"]


def test_secrets_absent_from_both_is_not_compared(snap) -> None:
    before, after = copy_module.deepcopy(snap), copy_module.deepcopy(snap)
    before["secrets"] = after["secrets"] = None
    report = vh.diff(before, after)
    assert report.exit_code == 0
    assert "Secrets not compared" in report.render()


# --- 7. wait states -----------------------------------------------------------


def app(**status) -> dict:
    return {"metadata": {"name": "root"}, "status": status}


SHA = "a" * 40


def test_revision_reached_with_no_operation_is_done() -> None:
    state, _ = vh.revision_state(app(sync={"status": "Synced", "revision": SHA}), SHA)
    assert state == "done"


def test_revision_not_yet_reached_is_pending() -> None:
    state, _ = vh.revision_state(app(sync={"status": "Synced", "revision": "b" * 40}), SHA)
    assert state == "pending"


def test_revision_with_running_operation_is_pending() -> None:
    a = app(sync={"status": "OutOfSync", "revision": SHA}, operationState={"phase": "Running", "syncResult": {"revision": SHA}})
    assert vh.revision_state(a, SHA)[0] == "pending"


def test_revision_running_operation_on_an_older_sha_is_pending() -> None:
    a = app(sync={"status": "Synced", "revision": SHA}, operationState={"phase": "Running", "syncResult": {"revision": "b" * 40}})
    assert vh.revision_state(a, SHA)[0] == "pending"


def test_revision_failed_operation_fails() -> None:
    a = app(sync={"status": "OutOfSync", "revision": SHA}, operationState={"phase": "Failed", "syncResult": {"revision": SHA}})
    assert vh.revision_state(a, SHA)[0] == "failed"


def test_revision_matches_a_multi_source_revision() -> None:
    a = app(sync={"status": "Synced", "revisions": ["8.6.1", SHA]})
    assert vh.revision_state(a, SHA)[0] == "done"


def test_revision_descending_from_the_sha_is_done() -> None:
    """Two merges inside one poll: root jumps past the earlier sha. Its descendant counts."""
    later = "c" * 40
    a = app(sync={"status": "Synced", "revision": later})
    assert vh.revision_state(a, SHA, is_ancestor=lambda old, new: (old, new) == (SHA, later))[0] == "done"


def test_an_unrelated_revision_stays_pending() -> None:
    a = app(sync={"status": "Synced", "revision": "d" * 40})
    assert vh.revision_state(a, SHA, is_ancestor=lambda _old, _new: False)[0] == "pending"


def test_a_failed_operation_on_a_descendant_fails() -> None:
    later = "c" * 40
    a = app(sync={"status": "OutOfSync", "revision": later}, operationState={"phase": "Failed", "syncResult": {"revision": later}})
    assert vh.revision_state(a, SHA, is_ancestor=lambda old, new: (old, new) == (SHA, later))[0] == "failed"


def test_new_operation_since_t() -> None:
    since = "2026-10-01T12:00:00Z"
    old = app(sync={"status": "Synced"}, health={"status": "Healthy"}, operationState={"phase": "Succeeded", "startedAt": "2026-10-01T11:59:59Z"})
    new = app(sync={"status": "Synced"}, health={"status": "Healthy"}, operationState={"phase": "Succeeded", "startedAt": "2026-10-01T12:00:01Z"})
    bad = app(sync={"status": "OutOfSync"}, health={"status": "Healthy"}, operationState={"phase": "Error", "startedAt": "2026-10-01T12:00:01Z"})
    assert vh.operation_state(old, since)[0] == "pending"
    assert vh.operation_state(new, since)[0] == "done"
    assert vh.operation_state(bad, since)[0] == "failed"


def test_settled_needs_every_application_reconciled_after_t(snap) -> None:
    apps = [
        {"name": "keda", "automated": True, "sync": "Synced", "health": "Healthy", "operation": {"phase": "Succeeded"}, "reconciledAt": "2026-10-01T12:05:00Z"},
        {"name": "argocd", "automated": False, "sync": "OutOfSync", "health": "Healthy", "operation": None, "reconciledAt": "2026-10-01T12:05:00Z"},
    ]
    assert vh.settled_state(apps, "2026-10-01T12:00:00Z")[0] == "done"
    assert vh.settled_state(apps, "2026-10-01T12:06:00Z")[0] == "pending"
    running = copy_module.deepcopy(apps)
    running[0]["operation"] = {"phase": "Running"}
    assert vh.settled_state(running, "2026-10-01T12:00:00Z")[0] == "pending"
    progressing = copy_module.deepcopy(apps)
    progressing[0]["health"] = "Progressing"
    assert vh.settled_state(progressing, "2026-10-01T12:00:00Z")[0] == "pending"


def test_wait_times_out_as_a_failure() -> None:
    clock = iter(range(0, 10_000, 10))
    state, message = vh.poll(lambda: ("pending", "not yet"), timeout=30, interval=10, clock=lambda: next(clock), sleep=lambda _s: None)
    assert state == "failed"
    assert "timed out" in message


def test_wait_returns_on_done() -> None:
    answers = iter([("pending", "a"), ("done", "b")])
    clock = iter(range(0, 10_000, 10))
    assert vh.poll(lambda: next(answers), timeout=300, interval=10, clock=lambda: next(clock), sleep=lambda _s: None) == ("done", "b")


# --- CLI ----------------------------------------------------------------------


@pytest.mark.parametrize("argv", [["snapshot", "--out", "x.json"], ["wait", "--app", "root", "--revision", SHA]])
def test_cli_refuses_to_run_without_a_context(argv, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        vh.main(argv)
    assert exit_info.value.code == 2
    assert "--context" in capsys.readouterr().err


def test_cli_diff_exit_codes(snap, tmp_path) -> None:
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps(snap))
    after.write_text(json.dumps(snap))
    assert vh.main(["diff", str(before), str(after)]) == 0
    changed = copy_module.deepcopy(snap)
    changed["crds"]["scaledobjects.keda.sh"]["uid"] = "new"
    after.write_text(json.dumps(changed))
    assert vh.main(["diff", str(before), str(after)]) == 1
    changed["cluster_id"] = "other"
    after.write_text(json.dumps(changed))
    assert vh.main(["diff", str(before), str(after)]) == 2


# --- the CI ServiceAccount's ClusterRole --------------------------------------
#
# `verifier/manifests/` is what `applications/post-merge-verifier.yaml` syncs. The
# ClusterRole is read-only, never names Secrets, never wildcards a group or a
# resource (a wildcard on the core group grants Secrets), and covers every type
# `collect` reads unconditionally. A type an Application starts tracking later
# is not listed here; `collect` then fails loudly as Forbidden, which is the
# intended signal to add it.

import yaml  # noqa: E402  (PyYAML: the hook installs it)

RBAC = REPOSITORY / "verifier" / "manifests"
READ_VERBS = {"get", "list", "watch"}
# (apiGroup, resource) that `collect` reads on every run, whatever root tracks,
# plus the types root's Applications tracked on kind-yadgar on 2026-10-01.
REQUIRED = {
    ("", "namespaces"),
    ("", "pods"),
    ("", "persistentvolumeclaims"),
    ("", "configmaps"),
    ("", "services"),
    ("", "serviceaccounts"),
    ("apps", "deployments"),
    ("apps", "statefulsets"),
    ("apps", "daemonsets"),
    ("apiextensions.k8s.io", "customresourcedefinitions"),
    ("argoproj.io", "applications"),
    ("argoproj.io", "applicationsets"),
    ("admissionregistration.k8s.io", "mutatingwebhookconfigurations"),
    ("admissionregistration.k8s.io", "validatingwebhookconfigurations"),
    ("admissionregistration.k8s.io", "validatingadmissionpolicies"),
    ("admissionregistration.k8s.io", "validatingadmissionpolicybindings"),
    ("apiregistration.k8s.io", "apiservices"),
    ("rbac.authorization.k8s.io", "clusterroles"),
    ("rbac.authorization.k8s.io", "clusterrolebindings"),
    ("rbac.authorization.k8s.io", "roles"),
    ("rbac.authorization.k8s.io", "rolebindings"),
    ("argoproj.io", "appprojects"),
}
# Every CRD root's Applications tracked on kind-yadgar on 2026-10-01, less the
# issuance records and Applications `collect` never lists. `collect` lists each
# one's instances, so the role must grant it.
TRACKED_CUSTOM_RESOURCES = {
    "cert-manager.io": {"certificates", "clusterissuers", "issuers"},
    "eventing.keda.sh": {"cloudeventsources", "clustercloudeventsources"},
    "gateway.envoyproxy.io": {
        "backends", "backendtrafficpolicies", "clienttrafficpolicies", "envoyextensionpolicies",
        "envoypatchpolicies", "envoyproxies", "httproutefilters", "securitypolicies",
    },
    "gateway.networking.k8s.io": {
        "backendtlspolicies", "gatewayclasses", "gateways", "grpcroutes", "httproutes",
        "listenersets", "referencegrants", "tcproutes", "tlsroutes", "udproutes",
    },
    "gateway.networking.x-k8s.io": {"xbackends", "xbackendtrafficpolicies", "xmeshes"},
    "k8s.mariadb.com": {
        "backups", "connections", "databases", "externalmariadbs", "grants", "mariadbs", "maxscales",
        "physicalbackups", "pointintimerecoveries", "restores", "sqljobs", "users",
    },
    "keda.sh": {"clustertriggerauthentications", "scaledjobs", "scaledobjects", "triggerauthentications"},
}
REQUIRED |= {(group, plural) for group, plurals in TRACKED_CUSTOM_RESOURCES.items() for plural in plurals}


def rbac_documents(tree: Path) -> list[dict]:
    return [d for path in sorted(tree.glob("*.yaml")) for d in yaml.safe_load_all(path.read_text()) if d]


def clusterrole_errors(tree: Path) -> list[str]:
    roles = [d for d in rbac_documents(tree) if d.get("kind") == "ClusterRole"]
    if len(roles) != 1:
        return [f"expected one ClusterRole, found {len(roles)}"]
    errors, granted = [], set()
    for rule in roles[0].get("rules") or []:
        groups, resources, verbs = rule.get("apiGroups") or [], rule.get("resources") or [], rule.get("verbs") or []
        if "*" in groups or "*" in resources or "*" in verbs:
            errors.append(f"wildcard in {rule}")
        if any(r.split("/")[0] == "secrets" for r in resources):
            errors.append("grants secrets")
        if set(verbs) - READ_VERBS:
            errors.append(f"non-read verbs {sorted(set(verbs) - READ_VERBS)}")
        if rule.get("nonResourceURLs") or rule.get("resourceNames"):
            errors.append(f"unexpected rule shape {rule}")
        granted |= {(g, r) for g in groups for r in resources if {"get", "list"} <= set(verbs)}
    errors += [f"missing get+list on {g or 'core'}/{r}" for g, r in sorted(REQUIRED - granted)]
    return errors


def binding_errors(tree: Path) -> list[str]:
    docs = rbac_documents(tree)
    role = next((d for d in docs if d.get("kind") == "ClusterRole"), {})
    sa = next((d for d in docs if d.get("kind") == "ServiceAccount"), {})
    bindings = [d for d in docs if d.get("kind") == "ClusterRoleBinding"]
    if len(bindings) != 1:
        return [f"expected one ClusterRoleBinding, found {len(bindings)}"]
    binding = bindings[0]
    errors = []
    if binding.get("roleRef") != {"apiGroup": "rbac.authorization.k8s.io", "kind": "ClusterRole", "name": role.get("metadata", {}).get("name")}:
        errors.append("roleRef is not the verifier ClusterRole")
    subject = {"kind": "ServiceAccount", "name": sa.get("metadata", {}).get("name"), "namespace": sa.get("metadata", {}).get("namespace")}
    if binding.get("subjects") != [subject]:
        errors.append("subjects are not exactly the verifier ServiceAccount")
    if any(d.get("kind") in {"Role", "RoleBinding"} for d in docs):
        errors.append("a namespaced Role or RoleBinding widens the grant")
    return errors


@pytest.fixture
def rbac_copy(tmp_path: Path) -> Path:
    tree = tmp_path / "rbac"
    tree.mkdir()
    for path in RBAC.glob("*.yaml"):
        (tree / path.name).write_text(path.read_text())
    return tree


def test_the_clusterrole_is_read_only_and_complete() -> None:
    assert clusterrole_errors(RBAC) == []


def test_the_binding_names_only_the_verifier() -> None:
    assert binding_errors(RBAC) == []


def _edit_role(tree: Path, edit) -> None:
    path = tree / "clusterrole.yaml"
    doc = yaml.safe_load(path.read_text())
    edit(doc)
    path.write_text(yaml.safe_dump(doc))


@pytest.mark.parametrize(
    "edit, expected",
    [
        (lambda d: d["rules"][0]["resources"].append("secrets"), "grants secrets"),
        (lambda d: d["rules"].append({"apiGroups": [""], "resources": ["*"], "verbs": ["get"]}), "wildcard"),
        (lambda d: d["rules"].append({"apiGroups": ["*"], "resources": ["pods"], "verbs": ["get"]}), "wildcard"),
        (lambda d: d["rules"][0]["verbs"].append("patch"), "non-read verbs"),
        (lambda d: d["rules"].pop(0), "missing get+list"),
    ],
)
def test_a_widened_or_narrowed_clusterrole_reddens(rbac_copy, edit, expected) -> None:
    _edit_role(rbac_copy, edit)
    assert any(expected in e for e in clusterrole_errors(rbac_copy))


def test_a_second_subject_reddens(rbac_copy) -> None:
    path = rbac_copy / "clusterrolebinding.yaml"
    doc = yaml.safe_load(path.read_text())
    doc["subjects"].append({"kind": "ServiceAccount", "name": "default", "namespace": "default"})
    path.write_text(yaml.safe_dump(doc))
    assert "subjects are not exactly the verifier ServiceAccount" in binding_errors(rbac_copy)


# --- who may run on the verifier's runner -------------------------------------
#
# The `argocd-verify` runner's pod holds a cluster-read token. A workflow that
# runs branch code (`pull_request`, `workflow_dispatch` from a branch, ...) and
# names that label would hand the token to unreviewed code. actionlint's label
# list cannot say which workflow may use a label, so this does.

WORKFLOWS = REPOSITORY / ".github" / "workflows"
VERIFIER_LABEL = "argocd-verify"
VERIFIER_WORKFLOW = "post-merge-verify.yaml"


def _runs_on(job: dict) -> list[str]:
    value = job.get("runs-on")
    if isinstance(value, dict):
        value = value.get("labels")
    return [value] if isinstance(value, str) else list(value or [])


def label_errors(workflows: Path) -> list[str]:
    errors = []
    for path in sorted(workflows.glob("*.y*ml")):
        doc = yaml.safe_load(path.read_text()) or {}
        triggers = doc.get("on", doc.get(True))  # PyYAML reads a bare `on` key as True
        for name, job in (doc.get("jobs") or {}).items():
            if VERIFIER_LABEL not in _runs_on(job) and VERIFIER_LABEL not in json.dumps(job.get("runs-on")):
                continue
            if path.name != VERIFIER_WORKFLOW:
                errors.append(f"{path.name}:{name} names {VERIFIER_LABEL}")
            elif triggers != {"push": {"branches": ["main"]}}:
                errors.append(f"{path.name} triggers on {triggers!r}, not only push to main")
    return errors


@pytest.fixture
def workflows_copy(tmp_path: Path) -> Path:
    tree = tmp_path / "workflows"
    tree.mkdir()
    for path in WORKFLOWS.glob("*.y*ml"):
        (tree / path.name).write_text(path.read_text())
    return tree


def test_only_the_post_merge_workflow_names_the_verifier_label() -> None:
    text = (WORKFLOWS / VERIFIER_WORKFLOW).read_text()
    assert f"runs-on: {VERIFIER_LABEL}" in text
    assert label_errors(WORKFLOWS) == []


def test_another_workflow_naming_the_label_reddens(workflows_copy) -> None:
    (workflows_copy / "pr.yaml").write_text(
        "on: pull_request\njobs:\n  x:\n    runs-on: [self-hosted, argocd-verify]\n    steps: [{run: 'true'}]\n"
    )
    assert label_errors(workflows_copy) == ["pr.yaml:x names argocd-verify"]


def test_a_pull_request_trigger_on_the_verifier_workflow_reddens(workflows_copy) -> None:
    path = workflows_copy / VERIFIER_WORKFLOW
    path.write_text(path.read_text().replace("on:\n  push:\n    branches: [main]\n", "on:\n  push:\n    branches: [main]\n  pull_request:\n"))
    assert len(label_errors(workflows_copy)) == 1


def test_a_repeated_output_flag_is_refused_by_name() -> None:
    """kubectl honours the LAST -o, so even two identical ones are refused, before any other output rule."""
    with pytest.raises(vh.UsageError, match="repeated"):
        vh.kubectl(CONTEXT, ["get", "pods", "-n", "keda", "-o", "json", "--output=json"], runner=FakeRunner({}))
