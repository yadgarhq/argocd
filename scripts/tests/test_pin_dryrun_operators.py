"""`scripts/pin_dryrun.py` on a platform move: E2-interim over the five operator Applications (car C10 part 2).

No cluster, no helm and no network, as `test_pin_dryrun.py`: the throwaway
repository, the yadgar render and the fake kubectl come from there. The
operator render is injected (`render_operator`), one small render per
Application at main's and at the sha's `platform_version`.

WHAT IS ASSERTED, each with a red case:

  1. A pure platform move is judged, not held: green when every operator
     Application is settled at main's platform, its base render is live
     (tracking-id naming the Application, or, for a CRD, which carries none on
     kind-yadgar, a row in the Application's `status.resources`), and every
     render-diff and live head-diff path is in the platform-move shape.
  2. Refusals before any cluster read: an operator kind outside
     `OPERATOR_READS`, a rendered Secret, an object rendered twice.
  3. The shape: a container image of the same repository, a derived label
     (`helm.sh/chart`, `app.kubernetes.io/version`) whose key NO selector in
     the six Applications reads (ADR-1093: own metadata counts, a webhook
     `objectSelector` in another Application counts, the `yadgar` render
     counts), or a CRD's `spec.versions` (E1 judges its schema allowlist).
  4. Settledness, the baseline and ownership, per operator Application.
  5. The two omitempty zero values measured on platform 0.2.17 match an
     absent live field, and nothing wider: `automountServiceAccountToken:
     false` still differs.
  6. The read table is read by the verifier's ClusterRole.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for path in (SCRIPTS, TESTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import pin_dryrun  # noqa: E402
import verify_handover  # noqa: E402
from test_pin_dryrun import (  # noqa: E402,F401  (`repo` and `platform_repo` are pytest fixtures)
    CONTEXT,
    NEW_PLATFORM,
    OPERATORS,
    PLATFORM,
    TARGET,
    Kubectl,
    NoCluster,
    Render,
    live_objects,
    main_sha,
    platform_branch,
    platform_repo,
    repo,
    table_for,
    write,
)

KEDA_OLD, KEDA_NEW = "2.20.1", "2.20.2"
CHART, VERSION = "helm.sh/chart", "app.kubernetes.io/version"


# ── the injected operator render ────────────────────────────────────────────


def labels(keda: str) -> dict:
    return {"app": "keda-operator", CHART: f"keda-{keda}", VERSION: keda}


def keda_render(keda: str) -> list:
    """keda's operator Deployment, its Service, a CRD and a ClusterRole, with the derived labels at `keda`."""
    return [
        {
            "apiVersion": "apps/v1", "kind": "Deployment",
            "metadata": {"name": "keda-operator", "namespace": "keda", "labels": labels(keda)},
            "spec": {
                "selector": {"matchLabels": {"app": "keda-operator"}},
                "template": {
                    "metadata": {"labels": labels(keda)},
                    "spec": {
                        "hostNetwork": False,
                        "containers": [{
                            "name": "keda-operator", "image": f"ghcr.io/kedacore/keda:{keda}", "args": ["--leader-elect"],
                            "volumeMounts": [{"name": "certs", "mountPath": "/certs", "subPath": ""}],
                        }],
                    },
                },
            },
        },
        {
            "apiVersion": "v1", "kind": "Service",
            "metadata": {"name": "keda-operator", "namespace": "keda", "labels": labels(keda)},
            "spec": {"selector": {"app": "keda-operator"}, "ports": [{"port": 443}]},
        },
        {
            "apiVersion": "apiextensions.k8s.io/v1", "kind": "CustomResourceDefinition",
            "metadata": {"name": "scaledobjects.keda.sh", "labels": {CHART: f"keda-{keda}", VERSION: keda}},
            "spec": {"group": "keda.sh", "versions": [{"name": "v1alpha1", "served": True, "storage": True}]},
        },
        {
            "apiVersion": "rbac.authorization.k8s.io/v1", "kind": "ClusterRole",
            "metadata": {"name": "keda-operator", "labels": {CHART: f"keda-{keda}"}},
            "rules": [{"apiGroups": ["keda.sh"], "resources": ["*"], "verbs": ["get"]}],
        },
    ]


def config(name: str) -> list:
    """Every other operator Application: one ConfigMap, the same at both pins; mariadb-operator's has no namespace."""
    metadata = {"name": f"{name}-config"} if name == "mariadb-operator" else {"name": f"{name}-config", "namespace": name}
    return [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata, "data": {"a": "1"}}]


class OperatorRender:
    """`render_operator(text, version)`: the Application named in `text`, at platform `version`."""

    def __init__(self, renders: dict | None = None) -> None:
        self.renders = renders or {
            name: {PLATFORM: keda_render(KEDA_OLD), NEW_PLATFORM: keda_render(KEDA_NEW)} if name == "keda"
            else {PLATFORM: config(name), NEW_PLATFORM: config(name)}
            for name in OPERATORS
        }
        self.calls: list[tuple[str, str]] = []

    def __call__(self, text: str, version: str) -> list:
        name = yaml.safe_load(text)["metadata"]["name"]
        self.calls.append((name, version))
        return copy.deepcopy(self.renders[name][version])

    def edit(self, name: str, version: str, change) -> OperatorRender:
        change(self.renders[name][version])
        return self


# ── the live operators ──────────────────────────────────────────────────────


def resource_of(document: dict) -> tuple[str, bool]:
    return pin_dryrun.OPERATOR_READS[pin_dryrun.group_kind(document)]


def tracked(document: dict, application: str, destination: str) -> dict:
    """`document` as Argo applied it: the tracking-id (none on a CRD), server fields, and no omitempty zero value."""
    live = copy.deepcopy(document)
    group, kind = pin_dryrun.group_kind(document)
    metadata = live["metadata"]
    namespaced = resource_of(document)[1]
    if namespaced:
        metadata.setdefault("namespace", destination)
    if kind != "CustomResourceDefinition":
        name = metadata["name"]
        metadata.setdefault("annotations", {})[pin_dryrun.TRACKING_ID] = (
            f"{application}:{group}/{kind}:{metadata.get('namespace', destination)}/{name}"
        )
    metadata.update(uid="u-" + metadata["name"], resourceVersion="1", managedFields=[{"manager": "argocd"}])
    pod = (live.get("spec") or {}).get("template", {}).get("spec")
    if pod is not None:
        pod.pop("hostNetwork", None)
        for container in pod.get("containers") or []:
            for mount in container.get("volumeMounts") or []:
                if mount.get("subPath") == "":
                    del mount["subPath"]
    return live


def application(name: str, version: str = PLATFORM, *, sync: str = "Synced", crds: list | None = None) -> dict:
    return {
        "metadata": {"name": name, "namespace": "argocd"},
        "spec": {"source": {"targetRevision": version}},
        "status": {
            "sync": {"status": sync, "revision": version},
            "health": {"status": "Healthy"},
            "resources": [
                {"group": "apiextensions.k8s.io", "kind": "CustomResourceDefinition", "name": crd, "version": "v1"}
                for crd in (crds or [])
            ],
        },
    }


def operator_live(render: OperatorRender | None = None, *, version: str = PLATFORM, edit=None) -> dict:
    """The yadgar fixture's live objects plus every operator object at `version`, as `kubectl get ... -o json`."""
    live = live_objects()
    render = render or OperatorRender()
    for name in OPERATORS:
        crds = []
        for document in render.renders[name][version]:
            if pin_dryrun.is_hook(document):
                continue
            resource, namespaced = resource_of(document)
            if document["kind"] == "CustomResourceDefinition":
                crds.append(document["metadata"]["name"])
            namespace = document["metadata"].get("namespace", name)
            key = f"{resource} -n {namespace}" if namespaced else resource
            live.setdefault(key, {"items": []})["items"].append(tracked(document, name, name))
        live[f"applications.argoproj.io {name} -n argocd"] = application(name, crds=crds)
    if edit:
        edit(live)
    return live


def find_live(live: dict, key: str, name: str) -> dict:
    return next(i for i in live[key]["items"] if i["metadata"]["name"] == name)


def run(repo: Path, sha: str, *, render: OperatorRender | None = None, kubectl=None) -> dict:
    render = render or OperatorRender()
    cluster = verify_handover.Cluster(CONTEXT, runner=kubectl if kubectl is not None else Kubectl(operator_live(render)))
    return pin_dryrun.dry_run(
        repo, main_sha(repo), sha, cluster, render=Render(), platform_of=lambda target: NEW_PLATFORM, render_operator=render
    )


# ── 1. a pure platform move is judged, green ────────────────────────────────


def test_a_pure_platform_move_is_green_on_the_live_operators(platform_repo: Path) -> None:
    render = OperatorRender()
    result = run(platform_repo, platform_branch(platform_repo), render=render)
    assert result["result"] == "green", result["problems"]
    assert sorted(render.calls) == sorted((name, v) for name in OPERATORS for v in (PLATFORM, NEW_PLATFORM))
    keys = {o["key"]: o["paths"] for o in result["objects"]}
    assert keys["apps/Deployment/keda/keda-operator"] == [
        f"metadata.labels.{VERSION}", f"metadata.labels.{CHART}",
        f"spec.template.metadata.labels.{VERSION}", f"spec.template.metadata.labels.{CHART}",
        "spec.template.spec.containers[0].image",
    ]
    # the yadgar Application's 7 steady objects plus 4 keda objects and the 4 other ConfigMaps
    assert result["compared"] == 7 + 4 + 4
    assert not any("operator comparator" in p for p in result["problems"])


def test_the_operator_render_uses_mains_files(platform_repo: Path) -> None:
    """Main's operator text at both platform versions: the sha's file differs only in targetRevision (step 1)."""
    seen = []

    class Recording(OperatorRender):
        def __call__(self, text: str, version: str) -> list:
            seen.append(yaml.safe_load(text)["spec"]["source"]["targetRevision"])
            return super().__call__(text, version)

    run(platform_repo, platform_branch(platform_repo), render=Recording())
    assert set(seen) == {PLATFORM}


# ── 2. refusals before any cluster read ─────────────────────────────────────


def test_an_operator_kind_outside_the_read_table_is_refused_before_any_cluster_read(platform_repo: Path) -> None:
    monitor = {"apiVersion": "monitoring.coreos.com/v1", "kind": "ServiceMonitor", "metadata": {"name": "m", "namespace": "keda"}}
    render = OperatorRender().edit("keda", NEW_PLATFORM, lambda docs: docs.append(monitor))
    result = run(platform_repo, platform_branch(platform_repo), render=render, kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("ServiceMonitor" in p and "OPERATOR_READS" in p for p in result["problems"]), result["problems"]


def test_an_operator_secret_is_refused_before_any_cluster_read(platform_repo: Path) -> None:
    secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "s", "namespace": "keda"}}
    render = OperatorRender().edit("keda", PLATFORM, lambda docs: docs.append(secret))
    result = run(platform_repo, platform_branch(platform_repo), render=render, kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("Secret" in p for p in result["problems"]), result["problems"]


def test_an_operator_object_rendered_twice_is_refused(platform_repo: Path) -> None:
    render = OperatorRender().edit("keda", NEW_PLATFORM, lambda docs: docs.append(copy.deepcopy(docs[1])))
    result = run(platform_repo, platform_branch(platform_repo), render=render, kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("rendered twice" in p for p in result["problems"]), result["problems"]


def test_a_failed_operator_render_is_refused(platform_repo: Path) -> None:
    def failing(text: str, version: str) -> list:
        raise pin_dryrun._load("pin_bump").Refused("helm pull failed")

    cluster = verify_handover.Cluster(CONTEXT, runner=NoCluster())
    result = pin_dryrun.dry_run(
        platform_repo, main_sha(platform_repo), platform_branch(platform_repo), cluster,
        render=Render(), platform_of=lambda target: NEW_PLATFORM, render_operator=failing,
    )
    assert result["result"] == "refused"
    assert any("helm pull failed" in p for p in result["problems"]), result["problems"]


# ── 3. the platform-move shape ──────────────────────────────────────────────


def test_a_new_arg_on_an_operator_deployment_is_red(platform_repo: Path) -> None:
    def arg(docs: list) -> None:
        docs[0]["spec"]["template"]["spec"]["containers"][0]["args"].append("--zap-log-level=debug")

    result = run(platform_repo, platform_branch(platform_repo), render=OperatorRender().edit("keda", NEW_PLATFORM, arg))
    assert result["result"] == "red"
    assert any("keda-operator" in p and "args" in p for p in result["problems"]), result["problems"]


def test_an_operator_image_repository_move_is_red(platform_repo: Path) -> None:
    def move(docs: list) -> None:
        docs[0]["spec"]["template"]["spec"]["containers"][0]["image"] = f"docker.io/kedacore/keda:{KEDA_NEW}"

    result = run(platform_repo, platform_branch(platform_repo), render=OperatorRender().edit("keda", NEW_PLATFORM, move))
    assert result["result"] == "red"
    assert any("keda-operator" in p and "repository" in p for p in result["problems"]), result["problems"]


def test_an_added_operator_object_is_red(platform_repo: Path) -> None:
    extra = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "extra", "namespace": "keda"}}
    render = OperatorRender().edit("keda", NEW_PLATFORM, lambda d: d.append(extra))
    live = operator_live(render, edit=lambda live: live.setdefault("configmaps -n keda", {"items": []}))
    result = run(platform_repo, platform_branch(platform_repo), render=render, kubectl=Kubectl(live))
    assert result["result"] == "red"
    assert any("extra" in p and "added" in p for p in result["problems"]), result["problems"]


def webhook(key: str) -> dict:
    return {
        "apiVersion": "admissionregistration.k8s.io/v1", "kind": "ValidatingWebhookConfiguration",
        "metadata": {"name": "cert-manager-webhook"},
        "webhooks": [{"name": "w.example.org", "objectSelector": {"matchExpressions": [{"key": key, "operator": "Exists"}]}}],
    }


def test_an_own_label_a_webhook_in_another_application_reads_is_red(platform_repo: Path) -> None:
    """ADR-1093 in E2: the keda Service's OWN `helm.sh/chart` label moves, and a webhook `objectSelector` in the
    cert-manager Application reads that key."""
    render = OperatorRender()
    for version in (PLATFORM, NEW_PLATFORM):
        render.renders["cert-manager"][version].append(webhook(CHART))
    result = run(platform_repo, platform_branch(platform_repo), render=render)
    assert result["result"] == "red"
    assert any(
        "Service/keda/keda-operator" in p and f"metadata.labels.{CHART}" in p and "inside a selector" in p
        for p in result["problems"]
    ), result["problems"]


def test_an_own_label_no_selector_reads_stays_green(platform_repo: Path) -> None:
    render = OperatorRender()
    for version in (PLATFORM, NEW_PLATFORM):
        render.renders["cert-manager"][version].append(webhook("app"))
    result = run(platform_repo, platform_branch(platform_repo), render=render)
    assert result["result"] == "green", result["problems"]


def test_a_pod_template_label_a_selector_reads_is_still_red(platform_repo: Path) -> None:
    """No regression from part 1: the Deployment's own selector reads the version key on its pod template."""
    def select(docs: list) -> None:
        docs[0]["spec"]["selector"]["matchExpressions"] = [{"key": VERSION, "operator": "Exists"}]

    render = OperatorRender().edit("keda", PLATFORM, select).edit("keda", NEW_PLATFORM, select)
    result = run(platform_repo, platform_branch(platform_repo), render=render)
    assert result["result"] == "red"
    assert any(f"spec.template.metadata.labels.{VERSION}" in p and "inside a selector" in p for p in result["problems"]), result["problems"]


def test_a_selector_in_the_yadgar_render_counts(platform_repo: Path) -> None:
    """The `yadgar` Application is one of the six: its Service reads `helm.sh/chart` in this case."""
    yadgar = Render()
    for documents in yadgar.versions.values():
        service = next(d for d in documents if d["kind"] == "Service")
        service["spec"]["selector"][CHART] = "x"
    render = OperatorRender()
    live = operator_live(render)
    find_live(live, "services -n yadgar", "gateway")["spec"]["selector"][CHART] = "x"
    cluster = verify_handover.Cluster(CONTEXT, runner=Kubectl(live))
    table = table_for(yadgar.versions[TARGET], TARGET)
    sha = platform_branch(platform_repo, extra=lambda r: write(r, "scripts/gates/yadgar_render.sha256", table))
    result = pin_dryrun.dry_run(
        platform_repo, main_sha(platform_repo), sha, cluster,
        render=yadgar, platform_of=lambda target: NEW_PLATFORM, render_operator=render,
    )
    assert any("keda-operator" in p and "inside a selector" in p for p in result["problems"]), result["problems"]


def test_a_pod_label_a_match_label_keys_list_reads_is_red(platform_repo: Path) -> None:
    """ADR-0645 review: a `topologySpreadConstraints[].matchLabelKeys` naming the version key."""
    def spread(docs: list) -> None:
        docs[0]["spec"]["template"]["spec"]["topologySpreadConstraints"] = [{
            "maxSkew": 1, "topologyKey": "kubernetes.io/hostname", "whenUnsatisfiable": "ScheduleAnyway",
            "labelSelector": {"matchLabels": {"app": "keda-operator"}}, "matchLabelKeys": [VERSION],
        }]

    render = OperatorRender().edit("keda", PLATFORM, spread).edit("keda", NEW_PLATFORM, spread)
    result = run(platform_repo, platform_branch(platform_repo), render=render)
    assert result["result"] == "red"
    assert any(f"labels.{VERSION}" in p and "inside a selector" in p for p in result["problems"]), result["problems"]


def test_a_selector_only_on_the_base_side_still_counts(platform_repo: Path) -> None:
    """The E2 twin of E1's collected-once test: a webhook selecting `helm.sh/chart` exists only at main's platform
    (it selects the live objects until the move lands). Its removal is red too; the selector must still count."""
    render = OperatorRender()
    render.renders["cert-manager"][PLATFORM].append(webhook(CHART))
    result = run(platform_repo, platform_branch(platform_repo), render=render)
    assert any(
        "Service/keda/keda-operator" in p and f"metadata.labels.{CHART}" in p and "inside a selector" in p
        for p in result["problems"]
    ), result["problems"]


def test_a_crd_version_change_is_left_to_e1(platform_repo: Path) -> None:
    """A new served version: E2 admits `spec.versions` paths; E1's CRD allowlist judges the schema."""
    def serve(docs: list) -> None:
        docs[2]["spec"]["versions"].append({"name": "v1beta1", "served": True, "storage": False})

    result = run(platform_repo, platform_branch(platform_repo), render=OperatorRender().edit("keda", NEW_PLATFORM, serve))
    assert result["result"] == "green", result["problems"]


def test_a_crd_change_outside_its_versions_is_red(platform_repo: Path) -> None:
    def scope(docs: list) -> None:
        docs[2]["spec"]["scope"] = "Cluster"

    result = run(platform_repo, platform_branch(platform_repo), render=OperatorRender().edit("keda", NEW_PLATFORM, scope))
    assert result["result"] == "red"
    assert any("scaledobjects.keda.sh" in p and "spec.scope" in p for p in result["problems"]), result["problems"]


# ── 4. settledness, the baseline, ownership ─────────────────────────────────


def test_an_operator_application_not_settled_is_red(platform_repo: Path) -> None:
    def drift(live: dict) -> None:
        live["applications.argoproj.io keda -n argocd"]["status"]["sync"]["status"] = "OutOfSync"

    render = OperatorRender()
    result = run(platform_repo, platform_branch(platform_repo), render=render, kubectl=Kubectl(operator_live(render, edit=drift)))
    assert result["result"] == "red"
    assert "not settled: keda sync status OutOfSync" in result["problems"]


def test_an_operator_application_at_another_platform_is_not_settled(platform_repo: Path) -> None:
    def behind(live: dict) -> None:
        live["applications.argoproj.io prometheus -n argocd"]["status"]["sync"]["revision"] = "0.2.5"

    render = OperatorRender()
    result = run(platform_repo, platform_branch(platform_repo), render=render, kubectl=Kubectl(operator_live(render, edit=behind)))
    assert f"not settled: prometheus status.sync.revision 0.2.5, main pins {PLATFORM}" in result["problems"]


def test_operator_drift_from_mains_render_is_baseline_red(platform_repo: Path) -> None:
    def drift(live: dict) -> None:
        find_live(live, "services -n keda", "keda-operator")["spec"]["ports"][0]["port"] = 8443

    render = OperatorRender()
    result = run(platform_repo, platform_branch(platform_repo), render=render, kubectl=Kubectl(operator_live(render, edit=drift)))
    assert result["result"] == "red"
    assert "baseline not empty: keda: /Service/keda/keda-operator spec.ports[0].port" in result["baseline"]


def test_an_operator_object_not_live_is_baseline_red(platform_repo: Path) -> None:
    def gone(live: dict) -> None:
        live["clusterroles.rbac.authorization.k8s.io"]["items"] = []

    render = OperatorRender()
    result = run(platform_repo, platform_branch(platform_repo), render=render, kubectl=Kubectl(operator_live(render, edit=gone)))
    assert "baseline not empty: keda: rbac.authorization.k8s.io/ClusterRole//keda-operator not live" in result["baseline"]


def test_an_operator_object_tracked_by_another_application_is_baseline_red(platform_repo: Path) -> None:
    def other(live: dict) -> None:
        item = find_live(live, "clusterroles.rbac.authorization.k8s.io", "keda-operator")
        item["metadata"]["annotations"][pin_dryrun.TRACKING_ID] = "prometheus:rbac.authorization.k8s.io/ClusterRole:keda/keda-operator"

    render = OperatorRender()
    result = run(platform_repo, platform_branch(platform_repo), render=render, kubectl=Kubectl(operator_live(render, edit=other)))
    assert result["result"] == "red"
    assert any("ClusterRole//keda-operator" in p and "tracking-id" in p for p in result["baseline"]), result["baseline"]


def test_a_crd_missing_from_the_applications_resources_is_baseline_red(platform_repo: Path) -> None:
    """A CRD carries no tracking-id on kind-yadgar (measured 2026-10-31); its owner is the Application whose
    `status.resources` lists it."""
    def unlisted(live: dict) -> None:
        live["applications.argoproj.io keda -n argocd"]["status"]["resources"] = []

    render = OperatorRender()
    result = run(platform_repo, platform_branch(platform_repo), render=render, kubectl=Kubectl(operator_live(render, edit=unlisted)))
    assert result["result"] == "red"
    assert any("scaledobjects.keda.sh" in p and "status.resources" in p for p in result["baseline"]), result["baseline"]


def test_a_crd_listed_under_another_name_is_baseline_red(platform_repo: Path) -> None:
    """The CRD's own row is required, not any CRD row: the Application lists a DIFFERENT CRD."""
    def other(live: dict) -> None:
        live["applications.argoproj.io keda -n argocd"]["status"]["resources"] = [
            {"group": "apiextensions.k8s.io", "kind": "CustomResourceDefinition", "name": "scaledjobs.keda.sh", "version": "v1"}
        ]

    render = OperatorRender()
    result = run(platform_repo, platform_branch(platform_repo), render=render, kubectl=Kubectl(operator_live(render, edit=other)))
    assert result["result"] == "red"
    assert any("scaledobjects.keda.sh" in p and "status.resources" in p for p in result["baseline"]), result["baseline"]


def test_a_namespaceless_render_is_read_in_the_destination_namespace(platform_repo: Path) -> None:
    """mariadb-operator renders no `metadata.namespace`; Argo applies the destination's, and so does the read."""
    render = OperatorRender()
    live = operator_live(render)
    assert find_live(live, "configmaps -n mariadb-operator", "mariadb-operator-config")
    result = run(platform_repo, platform_branch(platform_repo), render=render, kubectl=Kubectl(live))
    assert result["result"] == "green", result["problems"]


def test_an_operator_hook_is_not_read_but_is_judged(platform_repo: Path) -> None:
    def hook(image: str):
        def add(docs: list) -> None:
            docs.append({
                "apiVersion": "batch/v1", "kind": "Job",
                "metadata": {"name": "keda-hook", "namespace": "keda", "annotations": {"helm.sh/hook": "pre-install"}},
                "spec": {"template": {"spec": {"containers": [{"name": "h", "image": image, "args": ["a"]}]}}},
            })
        return add

    sha = platform_branch(platform_repo)
    render = OperatorRender().edit("keda", PLATFORM, hook("x:1")).edit("keda", NEW_PLATFORM, hook("x:2"))
    kubectl = Kubectl(operator_live(render))
    result = run(platform_repo, sha, render=render, kubectl=kubectl)
    assert result["result"] == "green", result["problems"]
    assert "batch/Job/keda/keda-hook" in result["hooks_left_out"]
    assert not any("jobs.batch -n keda" in " ".join(c) for c in kubectl.calls)

    render = OperatorRender().edit("keda", PLATFORM, hook("x:1")).edit("keda", NEW_PLATFORM, hook("y:2"))
    result = run(platform_repo, sha, render=render)
    assert any("keda-hook" in p and "repository" in p for p in result["problems"]), result["problems"]


def test_every_cluster_read_is_a_get_with_the_context(platform_repo: Path) -> None:
    render = OperatorRender()
    kubectl = Kubectl(operator_live(render))
    run(platform_repo, platform_branch(platform_repo), render=render, kubectl=kubectl)
    reads = [c for c in kubectl.calls if "keda" in " ".join(c)]
    assert reads and all(c[:4] == ["kubectl", "--context", CONTEXT, "get"] and c[-2:] == ["-o", "json"] for c in reads)


# ── 5. omitempty zero values ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("rendered", "path"),
    [
        ({"spec": {"template": {"spec": {"hostNetwork": False}}}}, None),
        ({"spec": {"template": {"spec": {"containers": [{"volumeMounts": [{"name": "v", "subPath": ""}]}]}}}}, None),
        ({"spec": {"template": {"spec": {"hostNetwork": True}}}}, "spec.template.spec.hostNetwork"),
        ({"spec": {"template": {"spec": {"automountServiceAccountToken": False}}}}, "spec.template.spec.automountServiceAccountToken"),
        ({"spec": {"template": {"spec": {"containers": [{"volumeMounts": [{"name": "v", "subPath": "x"}]}]}}}},
         "spec.template.spec.containers[0].volumeMounts[0].subPath"),
        ({"spec": {"hostNetwork": False}}, "spec.hostNetwork"),
    ],
)
def test_only_the_two_measured_zero_values_match_an_absent_field(rendered: dict, path: str | None) -> None:
    live = {"spec": {"template": {"spec": {"containers": [{"volumeMounts": [{"name": "v"}]}]}}}}
    assert pin_dryrun.differences(rendered, live) == ([path] if path else [])


def test_the_host_network_rule_is_anchored_at_the_pod_template() -> None:
    """`spec.template.spec.hostNetwork` only, not a path that merely ends in it."""
    rendered = {"x": {"spec": {"template": {"spec": {"hostNetwork": False}}}}}
    live = {"x": {"spec": {"template": {"spec": {}}}}}
    assert pin_dryrun.differences(rendered, live) == ["x.spec.template.spec.hostNetwork"]


# ── 6. the read table and the verifier's ClusterRole ────────────────────────


def test_every_operator_read_is_granted_to_the_verifier() -> None:
    root = SCRIPTS.parent / "verifier" / "manifests"
    role = yaml.safe_load((root / "clusterrole.yaml").read_text())
    granted = {
        (group, resource)
        for rule in role["rules"]
        if {"get", "list"} <= set(rule["verbs"])
        for group in rule["apiGroups"]
        for resource in rule["resources"]
    }
    wanted = {(g, resource.split(".", 1)[0]) for (g, _kind), (resource, _ns) in pin_dryrun.OPERATOR_READS.items()}
    assert sorted(wanted - granted) == []
    binding = yaml.safe_load((root / "clusterrolebinding.yaml").read_text())
    assert binding["kind"] == "ClusterRoleBinding" and binding["roleRef"]["name"] == role["metadata"]["name"]
    assert ("", "secrets") not in wanted


def test_the_operator_read_table_names_no_secret_and_resources_match_their_group() -> None:
    for (group, kind), (resource, _namespaced) in pin_dryrun.OPERATOR_READS.items():
        assert kind != "Secret"
        assert resource == (resource.split(".", 1)[0] + (f".{group}" if group else ""))


def test_the_result_holds_no_value(platform_repo: Path) -> None:
    result = run(platform_repo, platform_branch(platform_repo))
    assert KEDA_NEW not in json.dumps(result["objects"]) and "keda-2.20" not in json.dumps(result)
