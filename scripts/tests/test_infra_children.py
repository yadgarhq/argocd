"""The five Applications root adopted when `infra` retired are pinned (ADR-0824).

RETIRING `infra`, OPTION A. `yadgarhq/deploy`'s `infra` app-of-apps declared
five Applications beside itself: `arc`, `estate-front`, `estate-front-runner`,
`tls` and `yadgar`. M1 (`deploy#79`) put `Prune=false` on each live object, and
M2 deleted the five files, so the live objects ran unowned. This repository's
root now declares them under `applications/` and adopts each live object by its
identity, so the uid does not change. The two directory sources moved with them,
byte-identical, to `manifests/` — outside root's include glob.

WHAT IS ASSERTED, and each has a red case below. Offline: no helm, no network.

  1. Each file is `argoproj.io/Application` `argocd/<name>`, root selects it,
     and it carries no finalizer. Root's own `Prune=false` census in
     `test_operator_applications.py` already recurses `applications/`, so it
     covers these five too.
  2. Per row, one clause per requirement (`child_clause_errors`):
       annotations   `metadata.annotations` is exactly the row's: the sync-wave
                     deploy declared, and no `Prune=false` (root's apply
                     removes the one M1 put on the live object).
       sync-policy   S0: `automated` is `{selfHeal: true}` with no `prune` key,
                     `retry` is the chart example's block, and `syncOptions`
                     are deploy's. `tls` included: its retry is 6, not 60.
       source        `spec.source` equals the row's, with the helm values
                     pinned by digest where they are long. No `sources`.
       unchanged     every other field of `spec` equals the row's.
  3. The directory sources point at THIS repository, under `manifests/`, and
     each file there is byte-identical to the copy deploy last applied
     (`MANIFEST_DIGESTS`). Changing one is a live change: the tls Job's pod
     template, for one, recreates the Job.
  4. No Application under `applications/` sources a path under
     `applications/` or `applicationsets/` of this repository: root's include
     glob lets `*` cross `/`, so root would apply that file itself, a second
     owner. And no Application sources `yadgarhq/deploy` any more.
  5. `estate-front-runner`'s `controllerServiceAccount` names `arc`'s release:
     Argo uses the Application name as the release name, and the chart names
     its ServiceAccount `<release>-gha-rs-controller`. Renaming `arc` breaks
     the runner's RoleBinding silently.

WHAT IS NOT HERE, AND WHERE IT IS. `yadgar` equal to the chart's own
`example/application.yaml` outside `syncPolicy` and `valuesObject`, and its
render equal to the committed per-object digests (K3), need the network and
helm. They live in `scripts/gates/`, run by the `two-owners` CI job, never by
the offline pre-commit hook.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from test_operator_applications import EXPECTED_RETRY, expand_braces

REPOSITORY = Path(__file__).resolve().parents[2]
SOURCE = "yadgarhq/deploy@05b160bf2dbd6a036c7e1cf0390ab72b8283f6e5"
THIS_REPOSITORY = "https://github.com/yadgarhq/argocd"
RETIRED_REPOSITORY = "yadgarhq/deploy"
DESTINATION = "https://kubernetes.default.svc"


def s0(*sync_options: str) -> dict:
    """S0's `syncPolicy`: no `automated.prune`, the chart example's `retry`."""
    return {"automated": {"selfHeal": True}, "syncOptions": list(sync_options), "retry": EXPECTED_RETRY}


def canonical_digest(value: object) -> str:
    """sha256 of `value` as canonical JSON, so formatting and comments do not count."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


# sha256 of each file's bytes, equal to `git -C deploy show 05b160b:infra/<x>`.
MANIFEST_DIGESTS: dict[str, str] = {
    "manifests/estate-front/edge-service.yaml": "0cb712e37ea851ef7cb8c7c824f1dd00b6b83b76e5024206122316f43ae02ee1",
    "manifests/estate-front/networkpolicy.yaml": "cbf75b1456c741f2a927d9e50e4ae36068df41d6ff7431c2daadabe1c7158fe7",
    "manifests/tls/ca-preflight.yaml": "e0ea5280ed08c4926a11de54d4e64f54f62450ab166ca98dfb7abb1b1b229429",
    "manifests/tls/clusterissuer.yaml": "ceaea049f75d527493dd1d7fd70d17ec119e4d6cca339a27147fd9c1c5964e68",
}

# One row per adopted child. Each value is what deploy's copy at SOURCE
# declared, with S0's two `syncPolicy` changes and, for the two directory
# sources, the source moved to this repository.
#
#   annotations     exactly `metadata.annotations`.
#   source          exactly `spec.source`, except under `values_digest`.
#   values_digest   optional. `source.helm.valuesObject` is pinned by
#                   `canonical_digest` rather than written out. It equals the
#                   digest of deploy's `helm.values` string parsed.
#   unchanged       every field of `spec` other than `source` and `syncPolicy`.
#   sync_policy     exactly `spec.syncPolicy`.
CHILDREN: dict[str, dict] = {
    "arc": {
        # With the other operators: the scale set's CRDs ship in this chart.
        "annotations": {"argocd.argoproj.io/sync-wave": "-10"},
        "source": {
            "repoURL": "ghcr.io/actions/actions-runner-controller-charts",
            "chart": "gha-runner-scale-set-controller",
            "targetRevision": "0.14.2",
            # Development sizing (D55), deploy's copy.
            "helm": {
                "valuesObject": {
                    "resources": {"requests": {"cpu": "20m", "memory": "64Mi"}, "limits": {"memory": "256Mi"}}
                }
            },
        },
        "unchanged": {"project": "default", "destination": {"server": DESTINATION, "namespace": "arc-systems"}},
        "sync_policy": s0("CreateNamespace=true", "ServerSideApply=true"),
    },
    "estate-front": {
        "annotations": {"argocd.argoproj.io/sync-wave": "-6"},
        "source": {"repoURL": THIS_REPOSITORY, "targetRevision": "main", "path": "manifests/estate-front"},
        "unchanged": {"project": "default", "destination": {"server": DESTINATION, "namespace": "estate-front"}},
        "sync_policy": s0("CreateNamespace=true"),
    },
    "estate-front-runner": {
        # After `arc` (-10, the CRD) and `estate-front` (-6, the namespace).
        "annotations": {"argocd.argoproj.io/sync-wave": "-5"},
        "source": {
            "repoURL": "ghcr.io/actions/actions-runner-controller-charts",
            "chart": "gha-runner-scale-set",
            "targetRevision": "0.14.2",
        },
        "values_digest": "270725924f4a46d6368f246e04e751096b3ca396269b44fb88f416138bb6a067",
        "unchanged": {"project": "default", "destination": {"server": DESTINATION, "namespace": "estate-front"}},
        "sync_policy": s0("CreateNamespace=true"),
    },
    "tls": {
        "annotations": {"argocd.argoproj.io/sync-wave": "-5"},
        "source": {"repoURL": THIS_REPOSITORY, "targetRevision": "main", "path": "manifests/tls"},
        "unchanged": {"project": "default", "destination": {"server": DESTINATION, "namespace": "yadgar"}},
        # 6, NOT deploy's 60 (S0). The Application's own comment says what that costs.
        "sync_policy": s0("CreateNamespace=true"),
    },
    "yadgar": {
        # No wave, as in deploy and in the chart's example.
        "annotations": {},
        # `valuesObject` is the K3 gate's subject (`scripts/gates/`), not this row's.
        "source": {"repoURL": "ghcr.io/yadgarhq/charts", "chart": "yadgar"},
        "unchanged": {
            "project": "default",
            "destination": {"server": DESTINATION, "namespace": "yadgar"},
            "ignoreDifferences": [
                {
                    "group": "k8s.mariadb.com",
                    "kind": "MariaDB",
                    "jsonPointers": [
                        "/spec/rootPasswordSecretKeyRef/generate",
                        "/spec/passwordSecretKeyRef/generate",
                    ],
                }
            ],
        },
        "sync_policy": s0("CreateNamespace=true"),
    },
}

# `yadgar`'s `source` keys besides the two pinned above. `targetRevision` is
# held equal to `chart_pin.json`'s `chart_tag` by `scripts/check_chart_pin.py`;
# `helm` must hold `valuesObject` alone (no `valueFiles`, no `$self`).
YADGAR_SOURCE_KEYS = {"repoURL", "chart", "targetRevision", "helm"}


def load(tree: Path, name: str) -> dict:
    path = tree / "applications" / f"{name}.yaml"
    return (yaml.safe_load(path.read_text()) if path.is_file() else None) or {}


def child_clause_errors(tree: Path) -> list[tuple[str, str]]:
    """Every (name, clause) where an adopted child is not its row. See the module docstring."""
    errors = []
    for name, row in CHILDREN.items():
        document = load(tree, name)
        metadata = document.get("metadata") or {}
        spec = document.get("spec") or {}
        source = spec.get("source")
        source = source if isinstance(source, dict) else {}
        if name == "yadgar":
            helm = source.get("helm") or {}
            source_ok = (
                {key: source.get(key) for key in row["source"]} == row["source"]
                and set(source) == YADGAR_SOURCE_KEYS
                and set(helm) == {"valuesObject"}
                and isinstance(helm.get("valuesObject"), dict)
            )
        elif "values_digest" in row:
            helm = source.get("helm") or {}
            source_ok = (
                {key: value for key, value in source.items() if key != "helm"} == row["source"]
                and set(helm) == {"valuesObject"}
                and canonical_digest(helm.get("valuesObject")) == row["values_digest"]
            )
        else:
            source_ok = source == row["source"]
        checks = {
            "annotations": (metadata.get("annotations") or {}) == row["annotations"],
            "sync-policy": spec.get("syncPolicy") == row["sync_policy"],
            "source": source_ok and "sources" not in spec,
            "unchanged": {k: v for k, v in spec.items() if k not in ("source", "sources", "syncPolicy")}
            == row["unchanged"],
        }
        errors.extend((name, clause) for clause, passed in checks.items() if not passed)
    return errors


def identity_errors(tree: Path) -> list[str]:
    """Every child whose file is not exactly one `argoproj.io/Application` `argocd/<name>`."""
    errors = []
    for name in CHILDREN:
        path = tree / "applications" / f"{name}.yaml"
        documents = [d for d in yaml.safe_load_all(path.read_text()) if d] if path.is_file() else []
        identity = [
            (
                str(d.get("apiVersion", "")).split("/")[0],
                d.get("kind"),
                (d.get("metadata") or {}).get("namespace"),
                (d.get("metadata") or {}).get("name"),
            )
            for d in documents
        ]
        if identity != [("argoproj.io", "Application", "argocd", name)]:
            errors.append(name)
    return errors


def outside_root(tree: Path) -> list[str]:
    """Every child whose file root's `path` + `directory.include` does not select."""
    import fnmatch

    root = yaml.safe_load((tree / "projects" / "root.yaml").read_text())
    source = root["spec"]["source"]
    base = (tree / source.get("path", ".")).resolve()
    patterns = expand_braces((source.get("directory") or {}).get("include", "*"))
    missing = []
    for name in CHILDREN:
        path = (tree / "applications" / f"{name}.yaml").resolve()
        relative = path.relative_to(base).as_posix() if path.is_relative_to(base) else None
        if relative is None or not path.is_file() or not any(fnmatch.fnmatchcase(relative, p) for p in patterns):
            missing.append(name)
    return missing


def finalized(tree: Path) -> list[str]:
    return [name for name in CHILDREN if (load(tree, name).get("metadata") or {}).get("finalizers")]


def manifest_errors(tree: Path) -> list[str]:
    """Every `MANIFEST_DIGESTS` file missing or changed, and every extra file beside them."""
    errors = [
        relative
        for relative, digest in MANIFEST_DIGESTS.items()
        if not (tree / relative).is_file() or hashlib.sha256((tree / relative).read_bytes()).hexdigest() != digest
    ]
    directories = {(tree / relative).parent for relative in MANIFEST_DIGESTS}
    for directory in sorted(directories):
        for path in sorted(directory.glob("*")) if directory.is_dir() else []:
            relative = path.relative_to(tree).as_posix()
            if relative not in MANIFEST_DIGESTS:
                errors.append(relative)
    return sorted(errors)


def sources_of(document: dict) -> list[dict]:
    spec = document.get("spec") or {}
    listed = spec.get("sources") if isinstance(spec.get("sources"), list) else []
    single = [spec["source"]] if isinstance(spec.get("source"), dict) else []
    return [s for s in [*single, *listed] if isinstance(s, dict)]


def application_documents(tree: Path):
    """Every Application document under `applications/`, recursively (root recurses)."""
    for path in sorted((tree / "applications").rglob("*.yaml")):
        for document in yaml.safe_load_all(path.read_text()):
            if isinstance(document, dict) and document.get("kind") == "Application":
                yield path.relative_to(tree).as_posix(), document


def self_sourced_inside_root(tree: Path) -> list[str]:
    """Every `file: path` whose source reads this repository under root's own directories."""
    found = []
    for relative, document in application_documents(tree):
        for source in sources_of(document):
            path = str(source.get("path") or "").strip("/").removeprefix("./")
            if source.get("repoURL", "").rstrip("/").removesuffix(".git") != THIS_REPOSITORY or not path:
                continue
            if path.split("/")[0] in ("applications", "applicationsets"):
                found.append(f"{relative}: {path}")
    return found


def retired_repository_sources(tree: Path) -> list[str]:
    """Every `file: repoURL` still sourcing `yadgarhq/deploy`."""
    return [
        f"{relative}: {source.get('repoURL')}"
        for relative, document in application_documents(tree)
        for source in sources_of(document)
        if RETIRED_REPOSITORY in str(source.get("repoURL", ""))
    ]


def release_coupling_errors(tree: Path) -> list[str]:
    """`estate-front-runner`'s controller ServiceAccount must name `arc`'s release and namespace."""
    arc = load(tree, "arc")
    runner = load(tree, "estate-front-runner")
    values = (((runner.get("spec") or {}).get("source") or {}).get("helm") or {}).get("valuesObject") or {}
    expected = {
        "namespace": ((arc.get("spec") or {}).get("destination") or {}).get("namespace"),
        "name": f"{(arc.get('metadata') or {}).get('name')}-gha-rs-controller",
    }
    return [] if values.get("controllerServiceAccount") == expected else ["estate-front-runner"]


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    """A writable copy of the directories these gates read."""
    tree = tmp_path / "tree"
    for directory in ("applications", "applicationsets", "projects", "manifests"):
        shutil.copytree(REPOSITORY / directory, tree / directory)
    return tree


def mutate(tree: Path, name: str, change) -> None:
    path = tree / "applications" / f"{name}.yaml"
    document = yaml.safe_load(path.read_text())
    change(document)
    path.write_text(yaml.safe_dump(document))


# ── green on the repository ──────────────────────────────────────────────────


def test_every_child_is_its_row() -> None:
    print(f"[infra retire] {len(CHILDREN)} adopted child(ren) pinned to {SOURCE} + S0: {', '.join(CHILDREN)}")
    assert child_clause_errors(REPOSITORY) == []


def test_every_child_is_the_application_root_adopts_by_name() -> None:
    assert identity_errors(REPOSITORY) == []


def test_root_selects_every_child() -> None:
    assert outside_root(REPOSITORY) == []


def test_no_child_carries_a_finalizer() -> None:
    assert finalized(REPOSITORY) == []


def test_the_directory_sources_are_deploys_bytes() -> None:
    print(f"[infra retire] {len(MANIFEST_DIGESTS)} manifest(s) under manifests/ compared to {SOURCE}")
    assert manifest_errors(REPOSITORY) == []


def test_no_application_sources_a_path_root_applies_itself() -> None:
    assert self_sourced_inside_root(REPOSITORY) == []


def test_no_application_sources_the_retired_repository() -> None:
    count = sum(len(sources_of(d)) for _, d in application_documents(REPOSITORY))
    print(f"[infra retire] {count} source(s) read under applications/, none may be {RETIRED_REPOSITORY}")
    assert count > 0
    assert retired_repository_sources(REPOSITORY) == []


def test_the_runner_names_arcs_release() -> None:
    assert release_coupling_errors(REPOSITORY) == []


# ── red cases ────────────────────────────────────────────────────────────────


def test_a_reintroduced_prune_reddens(copy: Path) -> None:
    mutate(copy, "yadgar", lambda d: d["spec"]["syncPolicy"]["automated"].update(prune=True))
    assert child_clause_errors(copy) == [("yadgar", "sync-policy")]


def test_tls_back_on_sixty_retries_reddens(copy: Path) -> None:
    mutate(copy, "tls", lambda d: d["spec"]["syncPolicy"]["retry"].update(limit=60))
    assert child_clause_errors(copy) == [("tls", "sync-policy")]


def test_a_dropped_retry_reddens(copy: Path) -> None:
    mutate(copy, "arc", lambda d: d["spec"]["syncPolicy"].pop("retry"))
    assert child_clause_errors(copy) == [("arc", "sync-policy")]


def test_a_dropped_sync_option_reddens(copy: Path) -> None:
    """`ServerSideApply=true` is what lets arc's four large CRDs apply at all."""
    mutate(copy, "arc", lambda d: d["spec"]["syncPolicy"]["syncOptions"].remove("ServerSideApply=true"))
    assert child_clause_errors(copy) == [("arc", "sync-policy")]


def test_prune_false_carried_over_reddens(copy: Path) -> None:
    mutate(
        copy,
        "estate-front",
        lambda d: d["metadata"]["annotations"].update({"argocd.argoproj.io/sync-options": "Prune=false"}),
    )
    assert child_clause_errors(copy) == [("estate-front", "annotations")]


def test_a_wave_on_yadgar_reddens(copy: Path) -> None:
    mutate(copy, "yadgar", lambda d: d["metadata"].update(annotations={"argocd.argoproj.io/sync-wave": "10"}))
    assert child_clause_errors(copy) == [("yadgar", "annotations")]


def test_a_moved_wave_reddens(copy: Path) -> None:
    mutate(copy, "estate-front-runner", lambda d: d["metadata"]["annotations"].update({"argocd.argoproj.io/sync-wave": "-10"}))
    assert child_clause_errors(copy) == [("estate-front-runner", "annotations")]


def test_tls_pointed_back_at_deploy_reddens(copy: Path) -> None:
    def back(d: dict) -> None:
        d["spec"]["source"].update(repoURL="https://github.com/yadgarhq/deploy", path="infra/tls")

    mutate(copy, "tls", back)
    assert child_clause_errors(copy) == [("tls", "source")]
    assert retired_repository_sources(copy) == ["applications/tls.yaml: https://github.com/yadgarhq/deploy"]


def test_yadgar_back_on_two_sources_reddens(copy: Path) -> None:
    def two(d: dict) -> None:
        chart = d["spec"].pop("source")
        chart["helm"] = {"valueFiles": ["$self/infra/yadgar/values.yaml"]}
        d["spec"]["sources"] = [
            chart,
            {"repoURL": "https://github.com/yadgarhq/deploy", "targetRevision": "main", "ref": "self"},
        ]

    mutate(copy, "yadgar", two)
    assert child_clause_errors(copy) == [("yadgar", "source")]
    assert retired_repository_sources(copy) == ["applications/yadgar.yaml: https://github.com/yadgarhq/deploy"]


def test_yadgar_with_a_value_file_beside_the_object_reddens(copy: Path) -> None:
    mutate(copy, "yadgar", lambda d: d["spec"]["source"]["helm"].update(valueFiles=["values.yaml"]))
    assert child_clause_errors(copy) == [("yadgar", "source")]


def test_yadgar_without_ignore_differences_reddens(copy: Path) -> None:
    """Without it the operator's writeback reads as drift, and selfHeal loops against it."""
    mutate(copy, "yadgar", lambda d: d["spec"].pop("ignoreDifferences"))
    assert child_clause_errors(copy) == [("yadgar", "unchanged")]


def test_a_moved_destination_reddens(copy: Path) -> None:
    mutate(copy, "estate-front", lambda d: d["spec"]["destination"].update(namespace="yadgar"))
    assert child_clause_errors(copy) == [("estate-front", "unchanged")]


def test_a_changed_runner_value_reddens(copy: Path) -> None:
    mutate(copy, "estate-front-runner", lambda d: d["spec"]["source"]["helm"]["valuesObject"].update(maxRunners=3))
    assert child_clause_errors(copy) == [("estate-front-runner", "source")]


def test_runner_values_back_in_a_string_reddens(copy: Path) -> None:
    def string(d: dict) -> None:
        helm = d["spec"]["source"]["helm"]
        helm["values"] = yaml.safe_dump(helm.pop("valuesObject"))

    mutate(copy, "estate-front-runner", string)
    assert child_clause_errors(copy) == [("estate-front-runner", "source")]


def test_changed_arc_sizing_reddens(copy: Path) -> None:
    mutate(
        copy,
        "arc",
        lambda d: d["spec"]["source"]["helm"]["valuesObject"]["resources"]["limits"].update(memory="512Mi"),
    )
    assert child_clause_errors(copy) == [("arc", "source")]


def test_a_missing_child_reddens(copy: Path) -> None:
    (copy / "applications" / "tls.yaml").unlink()
    assert identity_errors(copy) == ["tls"]
    assert outside_root(copy) == ["tls"]
    assert {clause for name, clause in child_clause_errors(copy) if name == "tls"} == {
        "annotations",
        "sync-policy",
        "source",
        "unchanged",
    }


def test_a_renamed_child_reddens(copy: Path) -> None:
    mutate(copy, "arc", lambda d: d["metadata"].update(name="arc-controller"))
    assert identity_errors(copy) == ["arc"]
    assert release_coupling_errors(copy) == ["estate-front-runner"]


def test_a_finalizer_reddens(copy: Path) -> None:
    mutate(copy, "yadgar", lambda d: d["metadata"].update(finalizers=["resources-finalizer.argocd.argoproj.io"]))
    assert finalized(copy) == ["yadgar"]


def test_an_edited_manifest_reddens(copy: Path) -> None:
    """A byte, not a field: prettier or end-of-file-fixer rewriting the copy is caught too."""
    path = copy / "manifests" / "tls" / "ca-preflight.yaml"
    path.write_bytes(path.read_bytes() + b"\n")
    assert manifest_errors(copy) == ["manifests/tls/ca-preflight.yaml"]


def test_an_extra_manifest_reddens(copy: Path) -> None:
    (copy / "manifests" / "estate-front" / "extra.yaml").write_text("apiVersion: v1\nkind: ConfigMap\n")
    assert manifest_errors(copy) == ["manifests/estate-front/extra.yaml"]


def test_a_directory_source_under_applications_reddens(copy: Path) -> None:
    """Root's include glob lets `*` cross `/`, so root would apply `applications/tls/*` itself."""
    shutil.copytree(copy / "manifests" / "tls", copy / "applications" / "tls")
    mutate(copy, "tls", lambda d: d["spec"]["source"].update(path="applications/tls"))
    assert self_sourced_inside_root(copy) == ["applications/tls.yaml: applications/tls"]


def test_a_nested_application_sourcing_applicationsets_reddens(copy: Path) -> None:
    (copy / "applications" / "sub").mkdir()
    (copy / "applications" / "sub" / "x.yaml").write_text(
        "apiVersion: argoproj.io/v1alpha1\nkind: Application\nmetadata:\n  name: x\n  namespace: argocd\n"
        "spec:\n  sources:\n    - repoURL: https://github.com/yadgarhq/argocd.git\n"
        "      targetRevision: main\n      path: ./applicationsets\n"
    )
    assert self_sourced_inside_root(copy) == ["applications/sub/x.yaml: applicationsets"]


def test_a_runner_pointed_at_another_controller_reddens(copy: Path) -> None:
    mutate(
        copy,
        "estate-front-runner",
        lambda d: d["spec"]["source"]["helm"]["valuesObject"]["controllerServiceAccount"].update(namespace="arc"),
    )
    assert release_coupling_errors(copy) == ["estate-front-runner"]
