"""ADR-1095: E1 collects selector keys from EVERY Application of the estate, not six (yaadgaar 503).

OFFLINE: no helm and no network. The tree is a copy of this checkout's
Application files (`applications/`, root at `projects/root.yaml`) and the
directories they source (`manifests/`, `verifier/manifests/`, `install/`). A
chart source is rendered by `pin_bump.render_chart_source`, replaced here by a
recorder; a directory source is read from the tree for real. The operator
render and the platform facts are the `test_pin_platform` fixtures.

WHAT IS ASSERTED, each with a red case:

  1. Every Application root selects is in scope, found by enumerating root's
     directory source, so a new Application file is collected with no code
     change; `yadgar` and the five operator Applications are not rendered
     again (their renders at both pins are in hand).
  2. A selector in a non-operator Application (`tls`, `estate-front`, a new
     one) that reads `app.kubernetes.io/version` makes a moved version label
     NEEDS-HUMAN; the same tree with no such selector carries no reason.
  3. A source the collector cannot reproduce is NEEDS-HUMAN with a reason,
     never skipped: a kustomize or helm directory, another repository's path,
     helm `parameters`, a chart version range, an unresolvable `valueFiles`,
     an ApplicationSet, a nested Application, a missing root, a missing path.
  4. The chart render is Argo CD's: OCI without a scheme, `--repo` with one,
     release = Application name (or `releaseName`), the destination
     namespace, CRDs included, values layered valueFiles < values <
     valuesObject.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

TESTS = Path(__file__).resolve().parent
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from test_pin_platform import KEDA_NEW, KEDA_OLD, NEW, OLD, REPOSITORY, facts, pb, tag_render  # noqa: E402

VERSION = "app.kubernetes.io/version"
HELMS = {"v3.18.4": "/h3", "v4.3.0": "/h4"}
# The estate on 2026-10-31: 13 Applications under `applications/` and root (ADR-1095's context).
ESTATE = sorted(
    {
        "arc", "argocd", "cert-manager", "envoy-gateway", "estate-front", "estate-front-runner", "keda", "mariadb-operator",
        "post-merge-verifier", "post-merge-verifier-runner", "prometheus", "root", "tls", "yadgar",
    }
)
CHART_APPLICATIONS = ("arc", "argocd", "estate-front-runner", "post-merge-verifier-runner")


def estate_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    for directory in ("applications", "manifests", "verifier", "install"):
        shutil.copytree(REPOSITORY / directory, tree / directory)
    (tree / "projects").mkdir()
    shutil.copyfile(REPOSITORY / pb.ROOT_APPLICATION, tree / pb.ROOT_APPLICATION)
    return tree


class Charts:
    """`render_chart_source` stand-in: one ConfigMap per chart Application, every call recorded."""

    def __init__(self, extra: dict | None = None) -> None:
        self.calls: list[tuple[str, str]] = []
        self.extra = extra or {}

    def __call__(self, name: str, namespace: str, source: dict, value_files: list, binary: str) -> list:
        self.calls.append((name, binary))
        return [{"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name, "namespace": namespace}}, *self.extra.get(name, [])]


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> Charts:
    charts = Charts()
    monkeypatch.setattr(pb, "render_chart_source", charts)
    monkeypatch.setattr(pb, "render_operator", lambda text, version, binary: tag_render(KEDA_OLD if version == OLD else KEDA_NEW))
    monkeypatch.setattr(pb, "platform_facts", lambda version, binary: facts(KEDA_OLD if version == OLD else KEDA_NEW))
    return charts


def evidence(tree: Path) -> dict:
    return pb.platform_evidence(tree, OLD, NEW, HELMS, pb.Net(), [])


def reads_version(kind: str = "NetworkPolicy", name: str = "reads-version", namespace: str = "yadgar") -> str:
    """A manifest whose selector reads `app.kubernetes.io/version`."""
    if kind == "Service":
        spec = {"selector": {VERSION: "pinned"}, "ports": [{"port": 80}]}
        return yaml.safe_dump({"apiVersion": "v1", "kind": "Service", "metadata": {"name": name, "namespace": namespace}, "spec": spec})
    spec = {"podSelector": {"matchExpressions": [{"key": VERSION, "operator": "Exists"}]}, "policyTypes": ["Ingress"]}
    return yaml.safe_dump(
        {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": name, "namespace": namespace}, "spec": spec}
    )


def version_label_held(reasons: list[str]) -> bool:
    return any("keda-operator" in r and f"labels.{VERSION}" in r and "inside a selector" in r for r in reasons)


def application(name: str, source: dict, namespace: str = "estate-front") -> str:
    return yaml.safe_dump(
        {
            "apiVersion": "argoproj.io/v1alpha1", "kind": "Application", "metadata": {"name": name, "namespace": "argocd"},
            "spec": {"project": "default", "source": source, "destination": {"server": "https://kubernetes.default.svc", "namespace": namespace}},
        }
    )


# ── 1. every Application root selects ───────────────────────────────────────


def test_every_application_root_selects_is_in_scope(tmp_path: Path, offline: Charts) -> None:
    found = evidence(estate_tree(tmp_path))
    assert found["reasons"] == []
    assert found["applications"] == ESTATE
    # each chart Application rendered once per helm; yadgar and the operators are never rendered again here
    assert sorted(offline.calls) == sorted((name, binary) for name in CHART_APPLICATIONS for binary in HELMS.values())


def test_a_new_application_file_is_collected_without_code_change(tmp_path: Path, offline: Charts) -> None:
    tree = estate_tree(tmp_path)
    (tree / "manifests" / "newcomer").mkdir()
    (tree / "manifests" / "newcomer" / "service.yaml").write_text(reads_version("Service", namespace="newcomer"))
    source = {"repoURL": pb.THIS_REPOSITORY, "targetRevision": "main", "path": "manifests/newcomer"}
    (tree / "applications" / "newcomer.yaml").write_text(application("newcomer", source, "newcomer"))
    found = evidence(tree)
    assert "newcomer" in found["applications"]
    assert VERSION in found["selector_keys"]["newcomer"]
    assert version_label_held(found["reasons"]), found["reasons"]


# ── 2. a selector in a non-operator Application ─────────────────────────────


@pytest.mark.parametrize(("directory", "application_name"), [("manifests/tls", "tls"), ("manifests/estate-front", "estate-front")])
def test_a_version_selector_in_a_non_operator_application_holds_the_move(
    tmp_path: Path, offline: Charts, directory: str, application_name: str
) -> None:
    tree = estate_tree(tmp_path)
    (tree / directory / "reads-version.yaml").write_text(reads_version())
    found = evidence(tree)
    assert VERSION in found["selector_keys"][application_name]
    assert version_label_held(found["reasons"]), found["reasons"]


def test_a_version_selector_in_a_chart_application_holds_the_move(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Charts) -> None:
    charts = Charts({"arc": [yaml.safe_load(reads_version(namespace="arc-systems"))]})
    monkeypatch.setattr(pb, "render_chart_source", charts)
    found = evidence(estate_tree(tmp_path))
    assert VERSION in found["selector_keys"]["arc"]
    assert version_label_held(found["reasons"]), found["reasons"]


def test_the_estate_today_reads_no_derived_key(tmp_path: Path, offline: Charts) -> None:
    """This checkout's directory sources select on neither derived key (the chart renders are stubbed here; the
    replay gate renders them for real)."""
    found = evidence(estate_tree(tmp_path))
    assert not any(pb.DERIVED_LABELS & set(keys) for keys in found["selector_keys"].values()), found["selector_keys"]


# ── 3. a source the collector cannot reproduce is NEEDS-HUMAN ───────────────


def _kustomize(tree: Path) -> None:
    (tree / "manifests" / "tls" / "kustomization.yaml").write_text("resources: [clusterissuer.yaml]\n")


def _helm_directory(tree: Path) -> None:
    (tree / "manifests" / "tls" / "Chart.yaml").write_text("apiVersion: v2\nname: tls\nversion: 0.1.0\n")


def _jsonnet(tree: Path) -> None:
    (tree / "manifests" / "tls" / "extra.jsonnet").write_text("{}\n")


def _foreign_path(tree: Path) -> None:
    source = {"repoURL": "https://github.com/someone/else", "targetRevision": "main", "path": "manifests"}
    (tree / "applications" / "foreign.yaml").write_text(application("foreign", source))


def _another_revision(tree: Path) -> None:
    source = {"repoURL": pb.THIS_REPOSITORY, "targetRevision": "v1", "path": "manifests/tls"}
    (tree / "applications" / "pinned.yaml").write_text(application("pinned", source))


def _missing_path(tree: Path) -> None:
    source = {"repoURL": pb.THIS_REPOSITORY, "targetRevision": "main", "path": "manifests/absent"}
    (tree / "applications" / "absent.yaml").write_text(application("absent", source))


def _directory_option(tree: Path) -> None:
    source = {"repoURL": pb.THIS_REPOSITORY, "targetRevision": "main", "path": "manifests/tls", "directory": {"exclude": "x.yaml"}}
    (tree / "applications" / "excluding.yaml").write_text(application("excluding", source))


def _parameters(tree: Path) -> None:
    source = {
        "repoURL": "ghcr.io/example/charts", "chart": "thing", "targetRevision": "1.0.0",
        "helm": {"parameters": [{"name": "image.tag", "value": "x"}]},
    }
    (tree / "applications" / "parameters.yaml").write_text(application("parameters", source))


def _version_range(tree: Path) -> None:
    source = {"repoURL": "ghcr.io/example/charts", "chart": "thing", "targetRevision": ">=1.0.0"}
    (tree / "applications" / "ranged.yaml").write_text(application("ranged", source))


def _foreign_value_file(tree: Path) -> None:
    source = {"repoURL": "ghcr.io/example/charts", "chart": "thing", "targetRevision": "1.0.0", "helm": {"valueFiles": ["values.yaml"]}}
    (tree / "applications" / "valuefile.yaml").write_text(application("valuefile", source))


def _missing_value_file(tree: Path) -> None:
    (tree / "install" / "values.yaml").unlink()


def _no_source(tree: Path) -> None:
    (tree / "applications" / "empty.yaml").write_text(
        yaml.safe_dump({"apiVersion": "argoproj.io/v1alpha1", "kind": "Application", "metadata": {"name": "empty"}, "spec": {}})
    )


def _plugin(tree: Path) -> None:
    source = {"repoURL": pb.THIS_REPOSITORY, "targetRevision": "main", "path": "manifests/tls", "plugin": {"name": "x"}}
    (tree / "applications" / "plugin.yaml").write_text(application("plugin", source))


def _application_set(tree: Path) -> None:
    (tree / "applications" / "generated.yaml").write_text(
        yaml.safe_dump({"apiVersion": "argoproj.io/v1alpha1", "kind": "ApplicationSet", "metadata": {"name": "generated"}, "spec": {}})
    )


def _nested_application(tree: Path) -> None:
    source = {"repoURL": pb.THIS_REPOSITORY, "targetRevision": "main", "path": "manifests/tls"}
    (tree / "manifests" / "tls" / "child.yaml").write_text(application("child", source))


def _no_root(tree: Path) -> None:
    (tree / pb.ROOT_APPLICATION).unlink()


def _root_exclude(tree: Path) -> None:
    root = yaml.safe_load((tree / pb.ROOT_APPLICATION).read_text())
    root["spec"]["source"]["directory"]["exclude"] = "applications/tls.yaml"
    (tree / pb.ROOT_APPLICATION).write_text(yaml.safe_dump(root))


def _values_and_values_object(tree: Path) -> None:
    """Argo CD v3.1.8 `ValuesYAML()` takes `valuesObject` alone when set and drops `values`; layering both differs."""
    source = {"repoURL": "ghcr.io/example/charts", "chart": "thing", "targetRevision": "1.0.0", "helm": {"values": "a: 1\n", "valuesObject": {"b": 2}}}
    (tree / "applications" / "both.yaml").write_text(application("both", source))


def _application_in_a_list(tree: Path) -> None:
    """Argo CD expands a `kind: List`: an Application inside one is applied by root."""
    source = {"repoURL": pb.THIS_REPOSITORY, "targetRevision": "main", "path": "manifests/tls"}
    listed = {"apiVersion": "v1", "kind": "List", "items": [yaml.safe_load(application("hidden", source))]}
    (tree / "applications" / "zlist.yaml").write_text(yaml.safe_dump(listed))


def _list_in_a_directory(tree: Path) -> None:
    listed = {"apiVersion": "v1", "kind": "ServiceList", "items": [yaml.safe_load(reads_version("Service"))]}
    (tree / "manifests" / "tls" / "zlist.yaml").write_text(yaml.safe_dump(listed))


def _include_backslash(tree: Path) -> None:
    root = yaml.safe_load((tree / pb.ROOT_APPLICATION).read_text())
    root["spec"]["source"]["directory"]["include"] = "applications/\\*.yaml"
    (tree / pb.ROOT_APPLICATION).write_text(yaml.safe_dump(root))


UNREADABLE = {
    "values-and-values-object": (_values_and_values_object, "both"),
    "application-in-a-list": (_application_in_a_list, "root"),
    "list-in-a-directory": (_list_in_a_directory, "tls"),
    "include-backslash": (_include_backslash, "root"),
    "kustomize": (_kustomize, "tls"),
    "helm-directory": (_helm_directory, "tls"),
    "jsonnet": (_jsonnet, "tls"),
    "foreign-path": (_foreign_path, "foreign"),
    "another-revision": (_another_revision, "pinned"),
    "missing-path": (_missing_path, "absent"),
    "directory-option": (_directory_option, "excluding"),
    "parameters": (_parameters, "parameters"),
    "version-range": (_version_range, "ranged"),
    "foreign-value-file": (_foreign_value_file, "valuefile"),
    "missing-value-file": (_missing_value_file, "argocd"),
    "no-source": (_no_source, "empty"),
    "plugin": (_plugin, "plugin"),
    "application-set": (_application_set, "root"),
    "nested-application": (_nested_application, "tls"),
    "no-root": (_no_root, "root"),
    "root-exclude": (_root_exclude, "root"),
}


@pytest.mark.parametrize("case", sorted(UNREADABLE))
def test_a_source_that_cannot_be_read_for_selectors_is_needs_human(tmp_path: Path, offline: Charts, case: str) -> None:
    tree = estate_tree(tmp_path)
    change, culprit = UNREADABLE[case]
    change(tree)
    reasons = evidence(tree)["reasons"]
    assert any(r.startswith(f"selectors: Application {culprit}: ") and "cannot be read for selectors" in r for r in reasons), reasons


def test_a_failed_chart_render_is_needs_human(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, offline: Charts) -> None:
    def failing(name: str, namespace: str, source: dict, value_files: list, binary: str) -> list:
        raise pb.Refused("helm template exited 1")

    monkeypatch.setattr(pb, "render_chart_source", failing)
    reasons = evidence(estate_tree(tmp_path))["reasons"]
    assert any(r.startswith("selectors: Application arc: ") and "helm template exited 1" in r for r in reasons), reasons


# ── 4. the chart render is Argo CD's ────────────────────────────────────────


def test_an_oci_chart_is_rendered_as_argo_renders_it() -> None:
    source = {"repoURL": "ghcr.io/actions/actions-runner-controller-charts", "chart": "gha-runner-scale-set-controller", "targetRevision": "0.14.2"}
    arguments = pb.chart_source_arguments("arc", "arc-systems", source, ["/v/0", "/v/1"])
    assert arguments[:3] == ["template", "arc", "oci://ghcr.io/actions/actions-runner-controller-charts/gha-runner-scale-set-controller"]
    assert arguments[arguments.index("--version") + 1] == "0.14.2"
    assert arguments[arguments.index("--namespace") + 1] == "arc-systems"
    assert pb.INCLUDE_CRDS in arguments and "--repo" not in arguments
    assert [arguments[i + 1] for i, a in enumerate(arguments) if a == "--values"] == ["/v/0", "/v/1"]


def test_a_https_chart_is_rendered_with_its_repository_and_release_name() -> None:
    source = {"repoURL": "https://argoproj.github.io/argo-helm", "chart": "argo-cd", "targetRevision": "8.6.1", "helm": {"releaseName": "argo"}}
    arguments = pb.chart_source_arguments("argocd", "argocd", source, [])
    assert arguments[:3] == ["template", "argo", "argo-cd"]
    assert arguments[arguments.index("--repo") + 1] == "https://argoproj.github.io/argo-helm"


def test_the_value_layers_are_value_files_then_values_object(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tree = estate_tree(tmp_path)
    seen = {}

    def recording(name: str, namespace: str, source: dict, value_files: list, binary: str) -> list:
        seen[name] = [Path(f).read_text() for f in value_files]
        return []

    monkeypatch.setattr(pb, "render_chart_source", recording)
    source = {
        "repoURL": "ghcr.io/example/charts", "chart": "thing", "targetRevision": "1.0.0",
        "helm": {"valueFiles": ["$self/install/values.yaml"], "valuesObject": {"layer": "object"}},
    }
    document = yaml.safe_load(application("layered", source))
    document["spec"]["sources"] = [document["spec"].pop("source"), {"repoURL": pb.THIS_REPOSITORY, "targetRevision": "main", "ref": "self"}]
    pb.application_documents(tree, document, "/h3")
    assert seen["layered"] == [(tree / "install" / "values.yaml").read_text(), "layer: object\n"]


# ── review of argocd#212: forms root applies that must never be skipped ──────


def test_a_nested_application_file_is_collected(tmp_path: Path, offline: Charts) -> None:
    """Argo CD v3.1.8 matches `directory.include` with gobwas `glob.Match` and no separators, so `*` crosses `/`:
    root (recurse, `applications/*.yaml`) applies `applications/sub/x.yaml`, and E1 must read it."""
    tree = estate_tree(tmp_path)
    (tree / "manifests" / "nested").mkdir()
    (tree / "manifests" / "nested" / "service.yaml").write_text(reads_version("Service", namespace="nested"))
    source = {"repoURL": pb.THIS_REPOSITORY, "targetRevision": "main", "path": "manifests/nested"}
    (tree / "applications" / "sub").mkdir()
    (tree / "applications" / "sub" / "nested.yaml").write_text(application("nested", source, "nested"))
    found = evidence(tree)
    assert "nested" in found["applications"]
    assert version_label_held(found["reasons"]), found["reasons"]


def test_a_second_file_reusing_an_in_hand_name_is_read(tmp_path: Path, offline: Charts) -> None:
    """Root applies `applications/zz.yaml` named `keda` with another source; it is no in-hand render, so E1 reads it."""
    tree = estate_tree(tmp_path)
    (tree / "manifests" / "impostor").mkdir()
    (tree / "manifests" / "impostor" / "service.yaml").write_text(reads_version("Service", namespace="keda"))
    source = {"repoURL": pb.THIS_REPOSITORY, "targetRevision": "main", "path": "manifests/impostor"}
    (tree / "applications" / "zz.yaml").write_text(application("keda", source, "keda"))
    found = evidence(tree)
    assert version_label_held(found["reasons"]), found["reasons"]
