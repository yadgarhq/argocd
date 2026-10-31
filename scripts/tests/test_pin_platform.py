"""Car C10, the platform move (ledger 1632, ADR-0946, ADR-0948 R5; Max ruled the platform-move set 2026-10-31).

OFFLINE: every render, registry digest and chart fact is a fixture. One
constructed case per arm of the plan's C10 red list
(`yadgarhq/docs` `plans/auto-deploy-parent-chart.md`, car C10):

- `--write` on a platform move changes exactly the five operator Application
  files plus the three pin files, and refuses an operator file that does not
  pin `chart_pin.json`'s `platform_version`;
- every operator render passes `--include-crds`, and a render without it is
  refused; the `yadgar` E1 render passes it too;
- the operator classifier: a CRD with a removed property, a changed storage
  version, a new property with a default, an operator Deployment with a new
  arg, a digest that is not the registry's tag digest, a tag-only image whose
  tag is not the subchart's `appVersion`, a tag-only image whose subchart did
  not move in `Chart.lock`, a label change inside a selector, a new object, an
  Application that renders nothing, and a Service change each block; a new
  optional property with no default, a new served version with the storage
  version and conversion unchanged, the chart labels moving with the subchart,
  and the #103 shape (platform 0.2.4 -> 0.2.5, image only) pass;
- the consumers take three files, or eight when the platform moves, and
  nothing between: `pin_evidence`, `auto_pin.push`, `auto_pin_merge` and
  `pin_dryrun`'s pure-move refusal;
- ADR-1095 (superseding ADR-1093): a derived label on an object's OWN metadata
  (a Service, a CRD, a Deployment's own labels, not only a pod template) whose
  key any selector in any Application of the estate reads (here: the operator
  renders at both pins and the `yadgar` render; the rest of the estate in
  `test_pin_estate_selectors.py`) blocks; the same move with no selector reading it
  passes; a green platform move carries no hold reason.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
SCRIPTS = REPOSITORY / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

_spec = importlib.util.spec_from_file_location("pin_bump", SCRIPTS / "pin_bump.py")
pb = importlib.util.module_from_spec(_spec)
sys.modules["pin_bump"] = pb
_spec.loader.exec_module(pb)

OLD, NEW = "0.2.4", "0.2.5"
KEDA_OLD, KEDA_NEW = "2.20.1", "2.20.2"
DIGEST_OLD = "sha256:" + "a" * 64
DIGEST_NEW = "sha256:" + "b" * 64


# ── fixtures: the operator render and the platform chart's facts ────────────


def facts(keda: str, *, app_version: str | None = None, tags: tuple[str, ...] = ()) -> dict:
    return {
        "lock": {"keda": keda, "cert-manager": "v1.21.1"},
        "charts": {
            f"keda-{keda}": {"name": "keda", "version": keda, "appVersion": app_version or keda, "tags": set(tags), "top": "keda"},
            "cert-manager-v1.21.1": {
                "name": "cert-manager", "version": "v1.21.1", "appVersion": "v1.21.1", "tags": set(), "top": "cert-manager",
            },
        },
    }


def operator(keda: str, image: str, *, args: list | None = None) -> dict:
    labels = {"app": "keda-operator", "helm.sh/chart": f"keda-{keda}", "app.kubernetes.io/version": keda}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "keda-operator", "namespace": "keda", "labels": dict(labels)},
        "spec": {
            "selector": {"matchLabels": {"app": "keda-operator"}},
            "template": {
                "metadata": {"labels": dict(labels)},
                "spec": {"containers": [{"name": "keda-operator", "image": image, "args": args or ["--leader-elect"]}]},
            },
        },
    }


def crd(keda: str, *, versions: list | None = None) -> dict:
    return {
        "apiVersion": "apiextensions.k8s.io/v1",
        "kind": "CustomResourceDefinition",
        "metadata": {"name": "scaledobjects.keda.sh", "labels": {"helm.sh/chart": f"keda-{keda}", "app.kubernetes.io/version": keda}},
        "spec": {
            "group": "keda.sh",
            "names": {"kind": "ScaledObject", "plural": "scaledobjects"},
            "scope": "Namespaced",
            "versions": versions or [schema_version("v1alpha1", storage=True)],
        },
    }


def schema_version(name: str, *, storage: bool, properties: dict | None = None) -> dict:
    return {
        "name": name,
        "served": True,
        "storage": storage,
        "schema": {
            "openAPIV3Schema": {
                "type": "object",
                "required": ["spec"],
                "properties": {
                    "spec": {
                        "type": "object",
                        "required": ["scaleTargetRef"],
                        "properties": properties or {"scaleTargetRef": {"type": "object"}, "pollingInterval": {"type": "integer"}},
                    }
                },
            }
        },
    }


def service(keda: str, port: int = 443) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": "keda-operator", "namespace": "keda", "labels": {"helm.sh/chart": f"keda-{keda}"}},
        "spec": {"selector": {"app": "keda-operator"}, "ports": [{"port": port}]},
    }


def tag_render(keda: str, tag: str | None = None) -> list:
    return [operator(keda, f"ghcr.io/kedacore/keda:{tag or keda}"), crd(keda), service(keda)]


def two_helms(base: list, head: list, app: str = "keda") -> dict:
    return {"v3.18.4": {app: (base, head)}, "v4.3.0": {app: (copy.deepcopy(base), copy.deepcopy(head))}}


def no_registry(repository: str, tag: str) -> str:
    raise AssertionError(f"a tag-only image needs no registry read ({repository}:{tag})")


def judge(renders: dict, old: dict | None = None, new: dict | None = None, registry=no_registry) -> list[str]:
    return pb.classify_platform(renders, old or facts(KEDA_OLD), new or facts(KEDA_NEW), registry).reasons


# ── the classifier: what passes ─────────────────────────────────────────────


def test_green_the_103_shape_image_and_chart_labels_only() -> None:
    """Platform 0.2.4 -> 0.2.5 carrying keda 2.20.1 -> 2.20.2: tag-only image at the new appVersion, chart labels."""
    assert judge(two_helms(tag_render(KEDA_OLD), tag_render(KEDA_NEW))) == []


def test_green_an_unchanged_render() -> None:
    assert judge(two_helms(tag_render(KEDA_OLD), tag_render(KEDA_OLD)), new=facts(KEDA_OLD)) == []


def test_green_a_new_optional_property_with_no_default() -> None:
    base, head = tag_render(KEDA_OLD), tag_render(KEDA_NEW)
    head[1]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]["fallback"] = {"type": "object"}
    assert judge(two_helms(base, head)) == []


def test_green_a_new_served_version_with_the_storage_version_and_conversion_unchanged() -> None:
    base, head = tag_render(KEDA_OLD), tag_render(KEDA_NEW)
    head[1]["spec"]["versions"].append(schema_version("v1beta1", storage=False))
    assert judge(two_helms(base, head)) == []


def test_green_a_digest_pinned_image_at_the_registry_tag_digest() -> None:
    def render(keda: str, digest: str) -> list:
        return [operator(keda, f"quay.io/jetstack/cert-manager-controller:{keda}@{digest}")]

    seen = []

    def registry(repository: str, tag: str) -> str:
        seen.append((repository, tag))
        return DIGEST_NEW

    assert judge(two_helms(render(KEDA_OLD, DIGEST_OLD), render(KEDA_NEW, DIGEST_NEW)), registry=registry) == []
    assert seen == [("quay.io/jetstack/cert-manager-controller", KEDA_NEW)]


def test_green_a_tag_from_the_subcharts_default_values() -> None:
    base, head = tag_render(KEDA_OLD, "2.20.1-r0"), tag_render(KEDA_NEW, "2.20.2-r0")
    assert judge(two_helms(base, head), new=facts(KEDA_NEW, tags=("2.20.2-r0",))) == []


# ── the classifier: what blocks ─────────────────────────────────────────────


def test_a_crd_with_a_removed_property_blocks() -> None:
    base, head = tag_render(KEDA_OLD), tag_render(KEDA_NEW)
    del head[1]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]["pollingInterval"]
    reasons = judge(two_helms(base, head))
    assert any("scaledobjects.keda.sh" in r and "pollingInterval" in r and "removed" in r for r in reasons), reasons


def test_a_changed_storage_version_blocks() -> None:
    base = tag_render(KEDA_OLD)
    base[1]["spec"]["versions"].append(schema_version("v1beta1", storage=False))
    head = tag_render(KEDA_NEW)
    head[1]["spec"]["versions"] = [schema_version("v1alpha1", storage=False), schema_version("v1beta1", storage=True)]
    reasons = judge(two_helms(base, head))
    assert any("storage version" in r for r in reasons), reasons


def test_a_new_property_with_a_default_blocks() -> None:
    base, head = tag_render(KEDA_OLD), tag_render(KEDA_NEW)
    head[1]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]["cooldown"] = {
        "type": "integer", "default": 300,
    }
    reasons = judge(two_helms(base, head))
    assert any("cooldown" in r and "default" in r for r in reasons), reasons


def test_a_new_required_field_blocks() -> None:
    base, head = tag_render(KEDA_OLD), tag_render(KEDA_NEW)
    spec = head[1]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]
    spec["properties"]["triggers"] = {"type": "array"}
    spec["required"] = ["scaleTargetRef", "triggers"]
    reasons = judge(two_helms(base, head))
    assert any("required" in r for r in reasons), reasons


def test_a_type_change_blocks() -> None:
    base, head = tag_render(KEDA_OLD), tag_render(KEDA_NEW)
    head[1]["spec"]["versions"][0]["schema"]["openAPIV3Schema"]["properties"]["spec"]["properties"]["pollingInterval"]["type"] = "string"
    reasons = judge(two_helms(base, head))
    assert any("pollingInterval" in r and "type" in r for r in reasons), reasons


def test_a_conversion_change_blocks() -> None:
    base, head = tag_render(KEDA_OLD), tag_render(KEDA_NEW)
    head[1]["spec"]["conversion"] = {"strategy": "Webhook"}
    reasons = judge(two_helms(base, head))
    assert any("conversion" in r for r in reasons), reasons


def test_a_removed_crd_version_blocks() -> None:
    base = tag_render(KEDA_OLD)
    base[1]["spec"]["versions"].append(schema_version("v1beta1", storage=False))
    reasons = judge(two_helms(base, tag_render(KEDA_NEW)))
    assert any("v1beta1" in r and "removed" in r for r in reasons), reasons


def test_an_operator_deployment_with_a_new_arg_blocks() -> None:
    """The #103 shape with an extra arg."""
    head = tag_render(KEDA_NEW)
    head[0]["spec"]["template"]["spec"]["containers"][0]["args"].append("--zap-log-level=debug")
    reasons = judge(two_helms(tag_render(KEDA_OLD), head))
    assert any("keda-operator" in r and "args" in r for r in reasons), reasons


def test_a_digest_that_is_not_the_registry_tag_digest_blocks() -> None:
    def render(keda: str, digest: str) -> list:
        return [operator(keda, f"quay.io/jetstack/cert-manager-controller:{keda}@{digest}")]

    reasons = judge(two_helms(render(KEDA_OLD, DIGEST_OLD), render(KEDA_NEW, DIGEST_NEW)), registry=lambda r, t: DIGEST_OLD)
    assert any("digest" in r and "quay.io/jetstack/cert-manager-controller" in r for r in reasons), reasons


def test_an_unreadable_registry_blocks() -> None:
    def render(keda: str, digest: str) -> list:
        return [operator(keda, f"quay.io/jetstack/cert-manager-controller:{keda}@{digest}")]

    def registry(repository: str, tag: str) -> str:
        raise OSError("no route")

    reasons = judge(two_helms(render(KEDA_OLD, DIGEST_OLD), render(KEDA_NEW, DIGEST_NEW)), registry=registry)
    assert any("could not be read" in r for r in reasons), reasons


def test_a_digest_pinned_image_with_no_tag_blocks() -> None:
    def render(digest: str) -> list:
        return [operator(KEDA_OLD, f"quay.io/jetstack/cert-manager-controller@{digest}")]

    reasons = judge(two_helms(render(DIGEST_OLD), render(DIGEST_NEW)), new=facts(KEDA_OLD), registry=lambda r, t: DIGEST_NEW)
    assert any("no tag" in r for r in reasons), reasons


def test_a_tag_only_image_whose_tag_is_not_the_subchart_app_version_blocks() -> None:
    reasons = judge(two_helms(tag_render(KEDA_OLD), tag_render(KEDA_NEW, "2.21.0")))
    assert any("2.21.0" in r and "appVersion" in r for r in reasons), reasons


def test_a_tag_only_image_whose_subchart_did_not_move_in_chart_lock_blocks() -> None:
    """The tag is the new appVersion, but `Chart.lock` still pins the same keda."""
    new = facts(KEDA_NEW)
    new["lock"]["keda"] = KEDA_OLD
    reasons = judge(two_helms(tag_render(KEDA_OLD), tag_render(KEDA_NEW)), new=new)
    assert any("Chart.lock" in r for r in reasons), reasons


def test_a_repository_change_blocks() -> None:
    head = tag_render(KEDA_NEW)
    head[0]["spec"]["template"]["spec"]["containers"][0]["image"] = f"docker.io/kedacore/keda:{KEDA_NEW}"
    reasons = judge(two_helms(tag_render(KEDA_OLD), head))
    assert any("repository" in r for r in reasons), reasons


def test_a_label_change_inside_a_selector_blocks() -> None:
    """A pod template label the Deployment's selector matches."""
    def render(keda: str) -> list:
        documents = tag_render(keda)
        documents[0]["spec"]["selector"]["matchLabels"]["helm.sh/chart"] = f"keda-{keda}"
        return documents

    reasons = judge(two_helms(render(KEDA_OLD), render(KEDA_NEW)))
    assert any("selector" in r for r in reasons), reasons


def test_a_pod_label_an_unchanged_selector_matches_blocks() -> None:
    """The selector itself does not move (a `matchExpressions` `Exists` on the version label), but the pod template
    label it matches does: still inside a selector."""
    def render(keda: str) -> list:
        documents = tag_render(keda)
        documents[0]["spec"]["selector"]["matchExpressions"] = [{"key": "app.kubernetes.io/version", "operator": "Exists"}]
        return documents

    reasons = judge(two_helms(render(KEDA_OLD), render(KEDA_NEW)))
    assert any("inside a selector" in r and "app.kubernetes.io/version" in r for r in reasons), reasons


EXISTS_VERSION = [{"key": "app.kubernetes.io/version", "operator": "Exists"}]
ELSEWHERE_SELECTORS = {
    "pdb-match-expressions": {
        "apiVersion": "policy/v1", "kind": "PodDisruptionBudget", "metadata": {"name": "keda-operator", "namespace": "keda"},
        "spec": {"minAvailable": 1, "selector": {"matchExpressions": EXISTS_VERSION}},
    },
    "networkpolicy-pod-selector": {
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "keda", "namespace": "keda"},
        "spec": {"podSelector": {"matchLabels": {"helm.sh/chart": "keda"}}, "policyTypes": ["Ingress"]},
    },
    "networkpolicy-namespace-selector": {
        "apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "keda", "namespace": "keda"},
        "spec": {"podSelector": {}, "ingress": [{"from": [{"namespaceSelector": {"matchExpressions": EXISTS_VERSION}}]}]},
    },
    "servicemonitor-selector": {
        "apiVersion": "monitoring.coreos.com/v1", "kind": "ServiceMonitor", "metadata": {"name": "keda", "namespace": "keda"},
        "spec": {"selector": {"matchExpressions": EXISTS_VERSION}, "endpoints": [{"port": "metrics"}]},
    },
    "podmonitor-selector": {
        "apiVersion": "monitoring.coreos.com/v1", "kind": "PodMonitor", "metadata": {"name": "keda", "namespace": "keda"},
        "spec": {"selector": {"matchLabels": {"helm.sh/chart": "keda"}}, "podMetricsEndpoints": [{"port": "metrics"}]},
    },
    "webhook-object-selector": {
        "apiVersion": "admissionregistration.k8s.io/v1", "kind": "ValidatingWebhookConfiguration",
        "metadata": {"name": "keda-admission"},
        "webhooks": [{"name": "vscaledobject.kb.io", "objectSelector": {"matchExpressions": EXISTS_VERSION}}],
    },
    "service-selector": {
        "apiVersion": "v1", "kind": "Service", "metadata": {"name": "keda-metrics", "namespace": "keda"},
        "spec": {"selector": {"app.kubernetes.io/version": "pinned"}, "ports": [{"port": 9022}]},
    },
}


@pytest.mark.parametrize("name", sorted(ELSEWHERE_SELECTORS))
def test_a_pod_label_another_objects_selector_reads_blocks(name: str) -> None:
    """Fail-open B: the Deployment's own selector reads only `app`, but another object in the same render selects on
    the moved label's key. That object is identical at both pins, so only the pod template label moves."""
    selecting = ELSEWHERE_SELECTORS[name]
    key = "helm.sh/chart" if "helm.sh/chart" in json.dumps(selecting) else "app.kubernetes.io/version"

    def render(keda: str) -> list:
        return [*tag_render(keda), copy.deepcopy(selecting)]

    reasons = judge(two_helms(render(KEDA_OLD), render(KEDA_NEW)))
    assert any("keda-operator" in r and "inside a selector" in r and key in r for r in reasons), reasons


# ── ADR-1095: an object's OWN derived label that any selector in any Application of the estate reads ──


def reads_chart_label(kind: str = "ServiceMonitor") -> dict:
    """An object that selects on `helm.sh/chart` and is identical at both pins."""
    if kind == "ServiceMonitor":
        return {
            "apiVersion": "monitoring.coreos.com/v1", "kind": "ServiceMonitor", "metadata": {"name": "keda", "namespace": "observability"},
            "spec": {"selector": {"matchExpressions": [{"key": "helm.sh/chart", "operator": "Exists"}]}, "endpoints": [{"port": "metrics"}]},
        }
    return {
        "apiVersion": "admissionregistration.k8s.io/v1", "kind": "ValidatingWebhookConfiguration", "metadata": {"name": "guard"},
        "webhooks": [{"name": "guard.example.org", "objectSelector": {"matchExpressions": [{"key": "app.kubernetes.io/version", "operator": "Exists"}]}}],
    }


def apps(**renders: tuple[list, list]) -> dict:
    """Several Applications on two helms."""
    return {
        helm: {name: (copy.deepcopy(base), copy.deepcopy(head)) for name, (base, head) in renders.items()}
        for helm in ("v3.18.4", "v4.3.0")
    }


def test_an_own_label_a_servicemonitor_in_another_application_reads_blocks() -> None:
    """The Service's OWN `helm.sh/chart` label moves with keda; a ServiceMonitor in the `prometheus` Application reads
    that key. Under ADR-1095 that is inside a selector: NEEDS-HUMAN, though the Service has no pod template."""
    selecting = [reads_chart_label()]
    reasons = judge(apps(keda=(tag_render(KEDA_OLD), tag_render(KEDA_NEW)), prometheus=(selecting, selecting)))
    assert any(
        r.startswith("keda: ") and "Service/keda/keda-operator" in r and "metadata.labels.helm.sh/chart" in r and "inside a selector" in r
        for r in reasons
    ), reasons


def test_an_own_label_no_selector_reads_is_allowed() -> None:
    """The same move with a ServiceMonitor in another Application that reads only `app`: still green."""
    selecting = [reads_chart_label()]
    selecting[0]["spec"]["selector"] = {"matchLabels": {"app": "keda-operator"}}
    assert judge(apps(keda=(tag_render(KEDA_OLD), tag_render(KEDA_NEW)), prometheus=(selecting, selecting))) == []


def test_an_own_label_a_webhook_object_selector_in_another_application_reads_blocks() -> None:
    """A webhook `objectSelector` in another Application reads `app.kubernetes.io/version`; the keda Deployment's own
    metadata label of that key moves."""
    selecting = [reads_chart_label("webhook")]
    reasons = judge(apps(keda=(tag_render(KEDA_OLD), tag_render(KEDA_NEW)), cert_manager=(selecting, selecting)))
    assert any(
        "Deployment/keda/keda-operator" in r and "metadata.labels.app.kubernetes.io/version" in r and "inside a selector" in r
        and not r.split("metadata.labels")[0].endswith("template.")
        for r in reasons
    ), reasons


def test_an_own_label_a_selector_in_the_yadgar_render_reads_blocks() -> None:
    """`others` carries the `yadgar` Application's render: its selectors count too."""
    reasons = pb.classify_platform(
        two_helms(tag_render(KEDA_OLD), tag_render(KEDA_NEW)), facts(KEDA_OLD), facts(KEDA_NEW), no_registry,
        others=[reads_chart_label()],
    ).reasons
    assert any("Service/keda/keda-operator" in r and "inside a selector" in r for r in reasons), reasons


def test_a_crd_own_label_a_selector_reads_blocks() -> None:
    """A CRD's own `helm.sh/chart` label, read by a selector elsewhere: a webhook objectSelector can match a CRD."""
    selecting = [reads_chart_label()]
    reasons = judge(apps(keda=(tag_render(KEDA_OLD), tag_render(KEDA_NEW)), prometheus=(selecting, selecting)))
    assert any("CustomResourceDefinition" in r and "inside a selector" in r for r in reasons), reasons


def test_the_selector_keys_are_collected_once_across_applications() -> None:
    """The collector reads every Application at both pins: a selector present only in the BASE of another Application
    still counts (it selects the live objects until the move lands)."""
    selecting = [reads_chart_label()]
    reasons = judge(apps(keda=(tag_render(KEDA_OLD), tag_render(KEDA_NEW)), prometheus=(selecting, [])))
    assert any("Service/keda/keda-operator" in r and "inside a selector" in r for r in reasons), reasons


@pytest.mark.parametrize("field", ["matchLabelKeys", "mismatchLabelKeys"])
@pytest.mark.parametrize("where", ["topologySpreadConstraints", "podAntiAffinity"])
def test_a_pod_label_a_label_keys_list_reads_blocks(field: str, where: str) -> None:
    """ADR-0645 review: `matchLabelKeys`/`mismatchLabelKeys` name pod label keys the scheduler reads from the pod's own
    labels; a moved derived label so named is inside a selector."""
    def render(keda: str) -> list:
        documents = tag_render(keda)
        pod = documents[0]["spec"]["template"]["spec"]
        if where == "topologySpreadConstraints":
            pod[where] = [{"maxSkew": 1, "topologyKey": "kubernetes.io/hostname", "whenUnsatisfiable": "ScheduleAnyway",
                           "labelSelector": {"matchLabels": {"app": "keda-operator"}}, field: ["app.kubernetes.io/version"]}]
        else:
            pod["affinity"] = {where: {"requiredDuringSchedulingIgnoredDuringExecution": [
                {"topologyKey": "kubernetes.io/hostname", "labelSelector": {"matchLabels": {"app": "keda-operator"}},
                 field: ["app.kubernetes.io/version"]}]}}
        return documents

    assert "app.kubernetes.io/version" in pb.render_selector_keys(render(KEDA_OLD))
    reasons = judge(two_helms(render(KEDA_OLD), render(KEDA_NEW)))
    assert any("app.kubernetes.io/version" in r and "inside a selector" in r for r in reasons), reasons


def test_a_chart_label_that_is_not_the_subchart_version_blocks() -> None:
    head = tag_render(KEDA_NEW)
    head[2]["metadata"]["labels"]["helm.sh/chart"] = "keda-9.9.9"
    reasons = judge(two_helms(tag_render(KEDA_OLD), head))
    assert any("helm.sh/chart" in r for r in reasons), reasons


def test_a_service_change_blocks() -> None:
    head = tag_render(KEDA_NEW)
    head[2] = service(KEDA_NEW, port=9443)
    reasons = judge(two_helms(tag_render(KEDA_OLD), head))
    assert any("/Service/keda/keda-operator" in r and "ports" in r for r in reasons), reasons


def test_a_new_object_in_the_platform_subchart_blocks() -> None:
    head = tag_render(KEDA_NEW) + [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "extra", "namespace": "keda"}}]
    reasons = judge(two_helms(tag_render(KEDA_OLD), head))
    assert any("added" in r and "extra" in r for r in reasons), reasons


def test_an_application_that_renders_nothing_blocks() -> None:
    """An empty render is an empty diff: it must never read as a green move."""
    reasons = judge(two_helms([], []), new=facts(KEDA_OLD))
    assert any("renders no object" in r for r in reasons), reasons


def test_a_single_helm_blocks() -> None:
    reasons = judge({"v3.18.4": {"keda": (tag_render(KEDA_OLD), tag_render(KEDA_NEW))}})
    assert any("one helm" in r for r in reasons), reasons


def test_the_two_helms_disagreeing_blocks() -> None:
    renders = two_helms(tag_render(KEDA_OLD), tag_render(KEDA_NEW))
    renders["v4.3.0"]["keda"][1][2]["spec"]["ports"][0]["port"] = 1
    reasons = judge(renders)
    assert any("disagree" in r for r in reasons), reasons


def test_an_init_container_image_blocks() -> None:
    def render(keda: str) -> list:
        documents = tag_render(keda)
        pod = documents[0]["spec"]["template"]["spec"]
        pod["initContainers"] = [{"name": "init", "image": f"ghcr.io/kedacore/keda:{keda}"}]
        return documents

    reasons = judge(two_helms(render(KEDA_OLD), render(KEDA_NEW)))
    assert any("initContainers" in r for r in reasons), reasons


# ── the yadgar classifier no longer blocks a platform move by itself ────────


def test_the_yadgar_classifier_leaves_the_platform_move_to_the_operator_evidence() -> None:
    """Rewritten from C1's "a platform move is needs-human": the categorical block is gone (ruled 2026-10-31)."""
    old = {"gateway": "0.12.10", "platform": OLD}
    new = {"gateway": "0.12.10", "platform": NEW}
    verdict = pb.classify({"v3.18.4": ([], []), "v4.3.0": ([], [])}, old, new, lambda m, v: "")
    assert not any("platform" in r for r in verdict.reasons), verdict.reasons


def test_a_platform_move_with_no_operator_render_blocks() -> None:
    """The check refuses to call a platform move green when no operator Application was rendered."""
    reasons = pb.classify_platform({"v3.18.4": {}, "v4.3.0": {}}, facts(KEDA_OLD), facts(KEDA_NEW), no_registry).reasons
    assert any("no operator Application" in r for r in reasons), reasons


def test_a_green_platform_move_carries_no_hold_reason(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Rewritten from C10 part 1's "stays needs-human until the operator comparator exists": part 2 builds that
    comparator in `pin_dryrun.py`, so a ruled-set diff with no selector read clears E1 with no reason at all. The
    tree carries every Application and root (ADR-1095); the chart Applications outside the operators render nothing."""
    for directory in ("applications", "manifests", "verifier", "install", "projects"):
        shutil.copytree(REPOSITORY / directory, tmp_path / directory)
    monkeypatch.setattr(pb, "render_chart_source", lambda name, namespace, source, value_files, binary: [])
    monkeypatch.setattr(pb, "render_operator", lambda text, version, binary: tag_render(KEDA_OLD if version == OLD else KEDA_NEW))
    monkeypatch.setattr(pb, "platform_facts", lambda version, binary: facts(KEDA_OLD if version == OLD else KEDA_NEW))
    found = pb.platform_evidence(tmp_path, OLD, NEW, {"v3.18.4": "/h3", "v4.3.0": "/h4"}, pb.Net(), [])
    assert found["reasons"] == []
    assert not hasattr(pb, "PLATFORM_HOLD")


def test_platform_evidence_counts_the_yadgar_renders_selectors(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ADR-1095: the `yadgar` Application is in the estate; a selector in its render that reads a derived label key
    holds a move of that label on an operator object's own metadata."""
    for name in pb.OPERATOR_APPLICATIONS:
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes((REPOSITORY / name).read_bytes())
    monkeypatch.setattr(pb, "render_operator", lambda text, version, binary: tag_render(KEDA_OLD if version == OLD else KEDA_NEW))
    monkeypatch.setattr(pb, "platform_facts", lambda version, binary: facts(KEDA_OLD if version == OLD else KEDA_NEW))
    found = pb.platform_evidence(tmp_path, OLD, NEW, {"v3.18.4": "/h3", "v4.3.0": "/h4"}, pb.Net(), [reads_chart_label()])
    assert any("Service/keda/keda-operator" in r and "inside a selector" in r for r in found["reasons"]), found["reasons"]


def test_check_hands_the_yadgar_renders_to_the_platform_evidence(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """`check` passes every `yadgar` render it made (both pins, every helm) to `platform_evidence`."""
    (tmp_path / "applications").mkdir()
    (tmp_path / pb.APPLICATION).write_bytes((REPOSITORY / pb.APPLICATION).read_bytes())
    pin = pb.current_pin((tmp_path / pb.APPLICATION).read_text())
    marker = {"kind": "Service", "metadata": {"name": "marker"}, "spec": {"selector": {"x": "y"}}}

    class FakeNet:
        def chart_yaml(self, version: str) -> str:
            platform = OLD if version == pin else NEW
            return f"dependencies:\n  - name: platform\n    version: {platform}\n"

        def commits(self, old: str, new: str) -> list:
            return []

        def ghcr_digest(self, module: str, tag: str) -> str:
            raise AssertionError("classify is stubbed")

    seen = {}
    monkeypatch.setattr(pb, "render", lambda text, version, binary: [dict(marker, version=version, binary=binary)])
    monkeypatch.setattr(pb, "classify", lambda renders, old, new, digest: pb.Verdict(accept=True, helms=sorted(renders)))
    monkeypatch.setattr(pb, "audits", lambda head, old, new, net: ({}, {}))

    def evidence(repo, old, new, helms, net, others):
        seen["others"] = others
        return {"from": old, "to": new, "objects": {}, "changed": [], "reasons": []}

    monkeypatch.setattr(pb, "platform_evidence", evidence)
    pb.check(tmp_path, "999.0.0", FakeNet(), {"v3.18.4": "/h3", "v4.3.0": "/h4"})
    assert sorted((d["version"], d["binary"]) for d in seen["others"]) == sorted(
        (v, b) for v in (pin, "999.0.0") for b in ("/h3", "/h4")
    )


# ── renders: --include-crds, and the operator Application's shape ───────────

OPERATOR_APP = """\
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: keda
  namespace: argocd
spec:
  project: default
  source:
    repoURL: ghcr.io/yadgarhq/charts
    chart: platform
    targetRevision: {version}
    helm:
      valuesObject:
        operators:
          keda:
            create: true
  destination:
    server: https://kubernetes.default.svc
    namespace: keda
"""


def test_every_operator_render_passes_include_crds() -> None:
    arguments = pb.operator_template_arguments("keda", "0.2.5", "keda", ["/v.yaml"])
    assert "--include-crds" in arguments
    assert arguments[:3] == ["template", "keda", pb.PLATFORM_CHART]


def test_a_render_without_include_crds_is_refused() -> None:
    with pytest.raises(pb.Refused, match="--include-crds"):
        pb.helm_template(["template", "keda", pb.PLATFORM_CHART, "--version", "0.2.5"])


def test_the_yadgar_e1_render_passes_include_crds(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = []

    class Gate:
        @staticmethod
        def parent_render(tree, overrides=()):
            seen.append(tuple(overrides))
            return []

    monkeypatch.setattr(pb, "gate", lambda name: Gate)
    monkeypatch.setattr(pb, "helm_on_path", lambda binary: __import__("contextlib").nullcontext())
    application = (REPOSITORY / pb.APPLICATION).read_text()
    pb.render(application, "0.49.30", "/bin/helm")
    assert seen == [("--include-crds",)]


@pytest.mark.parametrize(
    "edit",
    [
        lambda t: t.replace("    helm:\n", "    helm:\n      valueFiles: [extra.yaml]\n"),
        lambda t: t.replace("chart: platform", "chart: other"),
        lambda t: t.replace("  source:\n", "  sources: []\n  source:\n"),
        lambda t: t.replace("    helm:\n", "    helm:\n      releaseName: other\n"),
    ],
)
def test_an_operator_application_of_another_shape_is_refused(edit) -> None:
    with pytest.raises(pb.Refused):
        pb.operator_inputs(edit(OPERATOR_APP.format(version=OLD)))


def test_values_and_values_object_are_both_rendered_values_object_last() -> None:
    text = OPERATOR_APP.format(version=OLD).replace("      valuesObject:\n", "      values: |\n        a: 1\n      valuesObject:\n")
    name, namespace, layers = pb.operator_inputs(text)
    assert (name, namespace) == ("keda", "keda")
    assert layers == ["a: 1\n", {"operators": {"keda": {"create": True}}}]


def test_default_tags_drops_an_empty_tag() -> None:
    """Most vendored subcharts carry `tag: ""` (meaning: use appVersion); an empty tag is no tag to match."""
    assert pb.default_tags({"image": {"tag": ""}, "webhooks": {"image": {"tag": "v2.20.2"}}, "other": [{"tag": ""}]}) == {"v2.20.2"}


def untagged_facts(how: str) -> dict:
    found = facts(KEDA_NEW, tags=("",) if how == "empty-default-tag" else ())
    if how == "empty-app-version":
        found["charts"][f"keda-{KEDA_NEW}"]["appVersion"] = ""
    return found


@pytest.mark.parametrize("how", ["empty-default-tag", "empty-app-version"])
def test_an_untagged_image_blocks(how: str) -> None:
    """Fail-open A: an image with no tag and no digest runs whatever `:latest` is; it must never match an empty
    default `tag` value or an empty `appVersion`."""
    head = tag_render(KEDA_NEW)
    head[0]["spec"]["template"]["spec"]["containers"][0]["image"] = "ghcr.io/kedacore/keda"
    reasons = judge(two_helms(tag_render(KEDA_OLD), head), new=untagged_facts(how))
    assert any("keda-operator" in r and "no tag" in r for r in reasons), reasons


# ── --write moves the platform ──────────────────────────────────────────────


TARGET_DIGEST = "sha256:" + "9" * 64


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The three pin files and the five operator Applications, copied from this checkout; the K3 writer stubbed."""
    for name in (*pb.MECHANICAL_FILES, *pb.OPERATOR_APPLICATIONS):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_bytes((REPOSITORY / name).read_bytes())
    monkeypatch.setattr(pb, "writer_helm", lambda: "/bin/true")
    monkeypatch.setattr(pb, "run_writer", lambda writer: (tmp_path / pb.TABLE).write_text("stub\n"))
    monkeypatch.setattr(pb, "chart_digest", lambda version: TARGET_DIGEST)
    return tmp_path


def current_platform() -> str:
    return json.loads((REPOSITORY / pb.CHART_PIN).read_text())["platform_version"]


def changed(tree: Path) -> set[str]:
    return {n for n in (*pb.MECHANICAL_FILES, *pb.OPERATOR_APPLICATIONS) if (tree / n).read_bytes() != (REPOSITORY / n).read_bytes()}


def test_write_on_a_platform_move_changes_the_five_operator_files_and_the_three(tree: Path) -> None:
    pb.write_files(tree, "99.0.0", platform="9.9.9")
    assert changed(tree) == set(pb.pin_files(platform_moved=True))
    assert json.loads((tree / pb.CHART_PIN).read_text()) == {
        "chart_tag": "v99.0.0",
        "chart_digest": TARGET_DIGEST,
        "platform_version": "9.9.9",
    }
    for name in pb.OPERATOR_APPLICATIONS:
        before, after = (REPOSITORY / name).read_text(), (tree / name).read_text()
        assert after == before.replace(f"targetRevision: {current_platform()}\n", "targetRevision: 9.9.9\n"), name


def test_write_without_a_platform_move_changes_the_three_only(tree: Path) -> None:
    pb.write_files(tree, "99.0.0", platform=current_platform())
    assert changed(tree) == set(pb.MECHANICAL_FILES) == set(pb.pin_files(platform_moved=False))


def test_write_reads_the_targets_platform_when_not_given(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pb, "target_platform", lambda target: "9.9.9" if target == "99.0.0" else pytest.fail(target))
    pb.write_files(tree, "99.0.0")
    assert changed(tree) == set(pb.pin_files(platform_moved=True))


def test_write_refuses_an_operator_file_that_does_not_pin_platform_version(tree: Path) -> None:
    name = pb.OPERATOR_APPLICATIONS[2]
    text = (tree / name).read_text().replace(f"targetRevision: {current_platform()}\n", "targetRevision: 0.0.1\n")
    (tree / name).write_text(text)
    before = {n: (tree / n).read_bytes() for n in (*pb.MECHANICAL_FILES, *pb.OPERATOR_APPLICATIONS)}
    with pytest.raises(pb.Refused, match=name):
        pb.write_files(tree, "99.0.0", platform="9.9.9")
    assert {n: (tree / n).read_bytes() for n in before} == before, "a refused write must leave every file as it was"


DIGEST_OLD, DIGEST_NEW = "sha256:" + "1" * 64, "sha256:" + "2" * 64


def test_write_reads_the_chart_digest_before_it_writes_any_file(tree: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Yaadgaar 250 with car C10: an unreadable digest leaves all eight files as they were."""
    def unreadable(version: str) -> str:
        raise pb.Refused(f"the digest of {version} could not be read")

    monkeypatch.setattr(pb, "chart_digest", unreadable)
    before = {n: (tree / n).read_bytes() for n in (*pb.MECHANICAL_FILES, *pb.OPERATOR_APPLICATIONS)}
    with pytest.raises(pb.Refused, match="digest"):
        pb.write_files(tree, "99.0.0", platform="9.9.9")
    assert {n: (tree / n).read_bytes() for n in before} == before


def test_the_chart_pin_moves_its_platform_version_by_a_line_edit() -> None:
    text = f'{{\n  "chart_tag": "v0.25.14",\n  "chart_digest": "{DIGEST_OLD}",\n  "platform_version": "0.2.6"\n}}\n'
    assert pb.rewrite_chart_pin(text, "0.25.27", digest=DIGEST_NEW, platform="0.2.7") == (
        f'{{\n  "chart_tag": "v0.25.27",\n  "chart_digest": "{DIGEST_NEW}",\n  "platform_version": "0.2.7"\n}}\n'
    )


def test_the_chart_pin_digest_and_platform_are_keyword_only() -> None:
    """A positional third argument cannot be read as the digest by one caller and the platform by another."""
    text = f'{{\n  "chart_tag": "v0.25.14",\n  "chart_digest": "{DIGEST_OLD}",\n  "platform_version": "0.2.6"\n}}\n'
    with pytest.raises(TypeError):
        pb.rewrite_chart_pin(text, "0.25.27", DIGEST_NEW)  # type: ignore[misc]


def test_the_store_block_is_not_lifted_by_c10() -> None:
    """Yaadgaar 493: the plan keeps a moved `yadgar-store` NEEDS-HUMAN after C10 (plan :486-492)."""
    source = (SCRIPTS / "pin_bump.py").read_text()
    store = [line for line in source.splitlines() if "store's migrate.rs owns the ledger DDL" in line]
    assert store and not any("lifted by car C10" in line for line in store), store


# ── the consumers: three files, or eight on a platform move ─────────────────

EIGHT = list(pb.pin_files(platform_moved=True))


def module(name: str):
    """`auto_pin`, `auto_pin_merge` or `pin_evidence`, imported at test time, never at collection: the other test
    files load their own copies into `sys.modules` while collecting, and a copy bound earlier here would pin a
    stale `auto_pin` inside `auto_pin_merge` for them."""
    return __import__(name)


def auto_pr():
    return module("pin_evidence").Pr("b" * 40, "auto/pin-99.0.0", True, "yadgarhq/argocd", "a" * 40)


def test_pin_evidence_takes_the_eight_files_of_a_platform_move_on_an_automatic_head() -> None:
    pe = module("pin_evidence")
    assert pe.automatic_problem(auto_pr(), ("0.49.27", "99.0.0"), EIGHT) is None
    assert pe.automatic_problem(auto_pr(), ("0.49.27", "99.0.0"), list(pb.MECHANICAL_FILES)) is None


@pytest.mark.parametrize(
    "files",
    [[*pb.MECHANICAL_FILES, pb.OPERATOR_APPLICATIONS[0]], EIGHT[:-1], [*EIGHT, "applications/tls.yaml"]],
    ids=["one-operator-file", "four-operator-files", "a-ninth-file"],
)
def test_pin_evidence_refuses_an_automatic_head_with_part_of_the_operator_files(files: list) -> None:
    pe = module("pin_evidence")
    problem = pe.automatic_problem(auto_pr(), ("0.49.27", "99.0.0"), files)
    assert problem and "exactly" in problem, problem


PLATFORM_MOVE_EVIDENCE = {
    "accept": True, "reasons": [], "helms": ["v3.18.4", "v4.3.0"], "changed": [], "boot_audit": {}, "migrations": {},
    "modules": {"old": {"platform": "0.2.14"}, "new": {"platform": "0.2.17"}},
    "platform": {"from": "0.2.14", "to": "0.2.17", "objects": {}, "changed": [], "reasons": []},
}


@pytest.mark.parametrize("automatic", [False, True], ids=["human", "automatic"])
@pytest.mark.parametrize("files", [list(pb.MECHANICAL_FILES), EIGHT[:-1]], ids=["three-files", "seven-files"])
def test_pin_evidence_fails_a_platform_move_that_leaves_operator_files_behind(automatic: bool, files: list) -> None:
    """Carry-forward gap: the target chart moved `platform`, but the head leaves operator Applications on the old one.
    A FAILURE, not a NEEDS-HUMAN reason, so no acknowledgement clears it (a human head only WARNs on regeneration)."""
    pe = module("pin_evidence")
    found = judge_pin(pe, automatic, files, PLATFORM_MOVE_EVIDENCE)
    assert any("platform" in f and pb.OPERATOR_APPLICATIONS[-1] in f and not f.startswith("NEEDS-HUMAN") for f in found.failures), (
        found.failures
    )


def test_pin_evidence_takes_a_platform_move_that_moves_all_eight_files() -> None:
    pe = module("pin_evidence")
    found = judge_pin(pe, False, EIGHT, PLATFORM_MOVE_EVIDENCE)
    assert not any("left behind" in f for f in found.failures), found.failures


def judge_pin(pe, automatic: bool, files: list, evidence: dict):
    application = (REPOSITORY / pb.APPLICATION).read_text()
    pin = pb.current_pin(application)
    target = "99.0.0"
    pr = pe.Pr("b" * 40, "auto/pin-99.0.0" if automatic else "fix/pin-99.0.0", automatic, "yadgarhq/argocd", "a" * 40)
    return pe.judge(
        pr, application, pb.rewrite_target_revision(application, target)[0],
        evidence_of=lambda *a: copy.deepcopy({**evidence, "pin": pin, "target": target}),
        statuses_of=lambda sha: [], helms={v: "/bin/true" for v in pe.HELMS}, changed=list(files),
        regenerated_of=lambda t: [], acks={}, base_acks={},
    )


def test_regenerated_differences_binds_the_operator_files_too(tmp_path: Path) -> None:
    """The base's eight files are seeded; an operator file the head carries that `write_files` would not write is named."""
    pe = module("pin_evidence")
    head = tmp_path / "head"
    for path in EIGHT:
        (head / path).parent.mkdir(parents=True, exist_ok=True)
        (head / path).write_text(f"{path}@target")
    for writer in pe.WRITERS:
        (head / "scripts" / "gates" / writer).write_text(f"# {writer}")
    (head / pb.OPERATOR_APPLICATIONS[1]).write_text("a values change beside the platform move")
    seen = {}

    def write(tree: Path, target: str) -> None:
        seen.update({path: (tree / path).read_text() for path in EIGHT})
        for path in EIGHT:
            (tree / path).write_text(f"{path}@target")

    differs = pe.regenerated_differences(head, "99.0.0", base_of=lambda path: f"base {path}".encode(), write=write)
    assert differs == [pb.OPERATOR_APPLICATIONS[1]]
    assert seen == {path: f"base {path}" for path in EIGHT}


def test_auto_pin_push_takes_the_eight_files_and_refuses_part_of_them() -> None:
    ap = module("auto_pin")
    assert sorted(ap.OPERATOR_APPLICATIONS) == sorted(pb.OPERATOR_APPLICATIONS)
    assert ap.push_problem(EIGHT) is None
    assert ap.push_problem(list(ap.PIN_FILES)) is None
    problem = ap.push_problem([*ap.PIN_FILES, ap.OPERATOR_APPLICATIONS[0]])
    assert problem and "all five" in problem, problem


def test_auto_pin_merge_takes_the_eight_files_only_from_mains_set() -> None:
    am = module("auto_pin_merge")
    reads = am.MergeReads(epoch=None, e2_run=None, mechanical=pb.MECHANICAL_FILES, operators=pb.OPERATOR_APPLICATIONS, sleep=None)
    assert am.files_allowed(sorted(EIGHT), reads)
    assert am.files_allowed(sorted(pb.MECHANICAL_FILES), reads)
    assert not am.files_allowed(sorted([*pb.MECHANICAL_FILES, pb.OPERATOR_APPLICATIONS[0]]), reads)
    narrower = am.MergeReads(epoch=None, e2_run=None, mechanical=pb.MECHANICAL_FILES, sleep=None)
    assert not am.files_allowed(sorted(EIGHT), narrower), "with no operator set from main, the eight are refused"


def test_the_body_names_the_platform_move() -> None:
    evidence = {
        "pin": "0.49.27", "target": "0.49.78", "modules": {"old": {"platform": "0.2.14"}, "new": {"platform": "0.2.17"}},
        "commits": [], "helms": ["v3.18.4", "v4.3.0"], "objects": 94, "changed": [], "boot_audit": {}, "migrations": {},
        "platform": {"from": "0.2.14", "to": "0.2.17", "objects": {"keda": 29}, "changed": [], "reasons": []},
        "accept": True, "reasons": [],
    }
    body = pb.pr_body(evidence)
    assert "five operator Applications from platform 0.2.14 to **0.2.17**" in body
    assert "E1 platform move 0.2.14 -> 0.2.17" in body and "keda 29" in body
