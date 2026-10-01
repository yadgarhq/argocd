"""The six operator Applications root adopts at E3 are the ones `deploy` released.

E3 OF THE OPERATORS HANDOVER (ADR-0824). The six operator Applications —
`cert-manager`, `keda`, `mariadb-operator`, `mariadb-operator-crds`,
`envoy-gateway` and `prometheus` — were declared in `yadgarhq/deploy`'s
`infra/`. E1 (`deploy#76`) put `argocd.argoproj.io/sync-options: Prune=false`
on each live object, and E2 deleted the six files, so the live objects run
unowned. This repository's `root` Application now declares them under
`applications/`. Root adopts each live object by its identity — group, kind,
namespace and name — so the uid does not change.

WHAT IS ASSERTED, and each has a red case below:

  1. Each `spec` is identical to deploy's last copy, with two named
     exceptions (S0 of the operators handover, ADR-0824): `automated.prune`
     is absent, and `retry` equals the chart's own example at the tag named
     by CHART_EXAMPLE_SOURCE below. Everything else is hashed and pinned to
     `yadgarhq/deploy` at `fa7ccb5`, the E1 merge and the last commit that
     declared them. A changed spec is a changed operator, which a handover
     must not carry.
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
     `envoy-gateway` and `prometheus` no longer pin to deploy's copy. Each sources `yadgarhq`'s `platform` chart at
     the version embedded at `yadgarhq/chart` v0.3.15, with its own
     `operators.<op>.create` true and every other operator, Argo CD included,
     explicitly false. Deploy's values are carried over under the operator's
     subchart key. Each name, destination, `syncPolicy` and release name is
     pinned unchanged, so the release instance label and every immutable
     selector stay as they are. PLATFORM_SOURCED holds one row per operator.
     Checks 2 to 7 still cover all four.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
ROOT = REPOSITORY / "projects" / "root.yaml"
APPLICATIONS = REPOSITORY / "applications"

# sha256 of each file's text from the `spec:` line to the end, taken from
# `git -C deploy show fa7ccb5:infra/<name>.yaml | sed -n '/^spec:/,$p' | sha256sum`.
SOURCE = "yadgarhq/deploy@fa7ccb529fd12911a7ccca1dc53f10490063f446"
# `keda`, `cert-manager`, `envoy-gateway` and `prometheus` left this table at
# D4 (ADR-0824): each now sources `platform`, which PLATFORM_SOURCED below
# pins. Their deploy-era hashes were
# keda 19e6856ba5ebc99ba0f24702cc5840b1d94f19a5f0e0d38115b0d280f666f6b6,
# cert-manager f402c7c04defb9ba09118f243357d16dc2df91eff35ea47f5e52dd0b19d579cb,
# envoy-gateway 1d8b30ab563c10c623bd0e0bebd502273c8a8fab833d9c58661b82d6aa0059b4 and
# prometheus c03d2900b2f5f23f76e20c4252f98243984aeb0dfd156aa43913949bb5204a79.
PINNED_SPECS: dict[str, str] = {
    "mariadb-operator": "485a08bcc620570e35ea6c216872e0d64c757d47ea43cc6479de9edf407c65be",
    "mariadb-operator-crds": "bd68b4915105319e1fdf3dcc10f7d6651955cf9b546db944ce4172be9d90536e",
}

SYNC_OPTIONS = "argocd.argoproj.io/sync-options"
PRUNE_FALSE = "Prune=false"

# S0 OF THE OPERATORS HANDOVER (ADR-0824). `retry` below is copied verbatim from
# `yadgarhq/chart`'s own `example/operators-application.yaml` at its latest
# release tag, read with `gh-personal api repos/yadgarhq/chart/contents/...`.
# That example also omits `automated.prune`, which is the other half of S0.
CHART_EXAMPLE_SOURCE = "yadgarhq/chart@v0.3.15:example/operators-application.yaml"
EXPECTED_RETRY: dict = {
    "limit": 6,
    "backoff": {
        "duration": "15s",
        "factor": 2,
        "maxDuration": "5m",
    },
}

# The literal `retry:` block S0 appends at the end of each file (nothing
# follows it there). Anchored to end-of-file ON PURPOSE: a file is required to
# end with exactly this text, so anything appended after it, or any deviation
# inside it, fails the match below rather than being silently discarded.
RETRY_BLOCK_TEXT = (
    "    retry:\n"
    "      limit: 6\n"
    "      backoff:\n"
    "        duration: 15s\n"
    "        factor: 2\n"
    "        maxDuration: 5m\n"
)
RETRY_BLOCK_SUFFIX = "\n" + RETRY_BLOCK_TEXT

# D4 OF THE OPERATORS HANDOVER (ADR-0824). Each Application here sources the
# `platform` chart with exactly one operator on. The version is the `platform`
# dependency embedded at `yadgarhq/chart` v0.3.15 (its `chart/Chart.yaml`),
# which is also the `targetRevision` of that tag's
# `example/operators-application.yaml`. This is a hardcoded copy: no test yet
# holds it equal to the parent chart's embedded version (ledger 1206).
PLATFORM_SOURCE = "yadgarhq/chart@v0.3.15:chart/Chart.yaml"
PLATFORM_VERSION = "0.1.21"
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
#   values     the `platform` subchart key that carries deploy's values over.
#   form       optional. The `helm` key that holds the values: `valuesObject`
#              when absent, or `values`, the YAML string, which is parsed.
#              Only `prometheus` uses `values`, because its `null` must reach
#              helm and a `null` inside `valuesObject` can be dropped on apply
#              (reasoned from client-side apply's merge patch, not measured).
#   digests    optional. Keys under `values` pinned by the sha256 of their
#              canonical JSON (`json.dumps(..., sort_keys=True)`) rather than
#              written out here. `carried` holds every other key.
#   carried    exactly what sits under `values`, less `digests`: deploy's
#              copy, moved under that key. Measured 2026-10-01: without a row's sizing blocks
#              the render's pod templates change and the Deployments roll.
#              cert-manager's `crds` block changes nothing in today's render,
#              because `platform` and cert-manager v1.21.1 already default to
#              `enabled: true` and `keep: true`. It is pinned as a defence
#              against a later change to either default.
#   unchanged  every field of `spec` other than `source`, as deploy's copy
#              declared it with S0's two changes. A change here is a change to
#              the release.
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
    },
    "prometheus": {
        "toggle": "prometheus",
        "values": "prometheus",
        "form": "values",
        # deploy's alerting rules, verbatim: `serverFiles` parsed from deploy's
        # copy at SOURCE equals this file's. `platform` carries no rules.
        "digests": {"serverFiles": "fc7255aec26edb02827487ecb30f4780343d67504390ba5c3f55fc7948b35435"},
        # deploy's PVC (`platform` defaults `enabled` to false, which renders an
        # emptyDir), and `null` on the reload sidecar's resources, which deletes
        # `platform`'s 10m/32Mi requests. Measured 2026-10-01: without either the
        # pod template changes and the pod rolls; without the PVC the TSDB is
        # empty. An empty map `{}` does not delete the requests.
        "carried": {
            "server": {"persistentVolume": {"enabled": True, "size": "2Gi"}},
            "configmapReload": {"prometheus": {"resources": None}},
        },
        "unchanged": {
            "project": "default",
            "destination": {"server": "https://kubernetes.default.svc", "namespace": "observability"},
            # deploy declared no `ServerSideApply=true` for prometheus.
            "syncPolicy": {**PLATFORM_SYNC_POLICY, "syncOptions": ["CreateNamespace=true"]},
        },
    },
}

ADOPTED: tuple[str, ...] = (*PINNED_SPECS, *PLATFORM_SOURCED)


def spec_text(path: Path) -> str:
    """The file's text from the `spec:` line to the end, exactly as stored."""
    text = path.read_text()
    match = re.search(r"^spec:\n", text, flags=re.MULTILINE)
    return text[match.start() :] if match else ""


def normalize_for_pin(text: str) -> str | None:
    """Undo S0's two `syncPolicy` changes (ADR-0824) so the rest still pins to deploy's copy.

    Returns None — a guaranteed mismatch below — unless `text` ends with
    EXACTLY `RETRY_BLOCK_TEXT` and `automated` carries no `prune` key: S0's
    shape is required, not merely tolerated, so a file that still has the
    deploy-era shape (prune present, no retry), or that has anything other
    than the chart's own retry block appended, or that has extra content
    after the retry block, is treated as drift rather than silently accepted.

    Each `automated` literal below must occur EXACTLY ONCE to be acted on. A
    text carrying it twice is ambiguous about which copy is the real
    `syncPolicy.automated`, so it is refused (None) rather than resolved by
    blindly replacing whichever occurrence comes first.
    """
    if not text.endswith(RETRY_BLOCK_SUFFIX):
        return None
    text = text[: -len(RETRY_BLOCK_TEXT)]
    inline = "    automated: { selfHeal: true }"
    block = "    automated:\n      selfHeal: true\n"
    if text.count(inline) == 1:
        return text.replace(inline, "    automated: { prune: true, selfHeal: true }", 1)
    if text.count(block) == 1:
        return text.replace(block, "    automated:\n      prune: true\n      selfHeal: true\n", 1)
    return None


def spec_drift(tree: Path) -> list[str]:
    """Every pinned name whose file is missing or whose normalized spec hash differs."""
    drift = []
    for name, expected in PINNED_SPECS.items():
        path = tree / "applications" / f"{name}.yaml"
        if not path.is_file():
            drift.append(name)
            continue
        normalized = normalize_for_pin(spec_text(path))
        if normalized is None or hashlib.sha256(normalized.encode()).hexdigest() != expected:
            drift.append(name)
    return drift


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
    """Every `applications/*.yaml` carrying `Prune=false`, and how many files were read."""
    found, read = [], 0
    for path in sorted((tree / "applications").glob("*.yaml")):
        read += 1
        for document in yaml.safe_load_all(path.read_text()):
            annotations = ((document or {}).get("metadata") or {}).get("annotations") or {}
            options = [o.strip() for o in str(annotations.get(SYNC_OPTIONS, "")).split(",")]
            if PRUNE_FALSE in options:
                found.append(path.name)
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
      value-keys       the values hold `operators` and the row's subchart key only
      carried          the row's subchart key, less `digests`, holds exactly
                       `carried`
      carried-digest   each key in the row's `digests` hashes to its value
      unchanged        every field of `spec` outside `source` equals `unchanged`
    """
    errors = []
    for name, row in PLATFORM_SOURCED.items():
        path = tree / "applications" / f"{name}.yaml"
        document = (yaml.safe_load(path.read_text()) if path.is_file() else None) or {}
        spec = document.get("spec") or {}
        source = spec.get("source") or {}
        helm = source.get("helm") or {}
        form = row.get("form", "valuesObject")
        values = parsed_values(helm.get(form)) if form == "values" else helm.get(form)
        values = values if isinstance(values, dict) else {}
        subchart = values.get(row["values"])
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
            "value-keys": set(values) == {"operators", row["values"]},
            "carried": row["values"] in values
            and {key: value for key, value in subchart.items() if key not in digests} == row["carried"],
            "carried-digest": all(
                key in subchart and canonical_digest(subchart[key]) == digest for key, digest in digests.items()
            ),
            "unchanged": rest == row["unchanged"],
        }
        errors.extend((name, clause) for clause, passed in checks.items() if not passed)
    return errors


def platform_errors(tree: Path) -> list[str]:
    """Every platform-sourced name with at least one clause of `platform_clause_errors` failing."""
    return list(dict.fromkeys(name for name, _ in platform_clause_errors(tree)))


@pytest.fixture
def copy(tmp_path: Path) -> Path:
    """A writable copy of the two directories these gates read."""
    tree = tmp_path / "tree"
    shutil.copytree(APPLICATIONS, tree / "applications")
    shutil.copytree(REPOSITORY / "projects", tree / "projects")
    return tree


def test_every_spec_equals_deploys_last_copy() -> None:
    print(f"[E3] {len(PINNED_SPECS)} spec(s) compared against {SOURCE}")
    assert spec_drift(REPOSITORY) == []


def test_every_platform_sourced_application_is_the_d4_shape() -> None:
    print(
        f"[D4] {len(PLATFORM_SOURCED)} Application(s) ({', '.join(PLATFORM_SOURCED)}) pinned to platform"
        f" {PLATFORM_VERSION} from {PLATFORM_SOURCE}"
    )
    assert platform_errors(REPOSITORY) == []


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
    assert spec_drift(copy) == ["mariadb-operator"]


def test_a_missing_file_reddens(copy: Path) -> None:
    (copy / "applications" / "mariadb-operator.yaml").unlink()
    assert spec_drift(copy) == ["mariadb-operator"]


def test_a_renamed_application_reddens(copy: Path) -> None:
    path = copy / "applications" / "envoy-gateway.yaml"
    text = path.read_text()
    assert "  name: envoy-gateway\n" in text
    path.write_text(text.replace("  name: envoy-gateway\n", "  name: envoy-gateway-new\n", 1))
    assert identity_errors(copy) == ["envoy-gateway"]


def test_a_narrowed_root_include_reddens(copy: Path) -> None:
    path = copy / "projects" / "root.yaml"
    text = path.read_text()
    assert "{applications,applicationsets}/*.yaml" in text
    path.write_text(text.replace("{applications,applicationsets}/*.yaml", "applicationsets/*.yaml"))
    assert outside_root(copy) == list(ADOPTED)


def test_prune_false_on_one_copy_reddens(copy: Path) -> None:
    path = copy / "applications" / "cert-manager.yaml"
    text = path.read_text()
    anchor = '    argocd.argoproj.io/sync-wave: "-10"\n'
    assert anchor in text
    path.write_text(text.replace(anchor, anchor + "    argocd.argoproj.io/sync-options: Prune=false\n", 1))
    found, _ = manifests_with_prune_false(copy)
    assert found == ["cert-manager.yaml"]


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
    """A line appended after the retry block must not vanish from the pinned hash."""
    path = copy / "applications" / "mariadb-operator.yaml"
    text = path.read_text()
    assert text.endswith(RETRY_BLOCK_SUFFIX)
    path.write_text(text + "  ignoreDifferences: []\n")
    assert spec_drift(copy) == ["mariadb-operator"]


def test_normalize_for_pin_refuses_a_repeated_automated_literal() -> None:
    """A text with the flow-style `automated` literal twice must not pick the first match.

    `normalize_for_pin` used to call `text.replace(literal, replacement, 1)` as
    soon as the literal appeared `in text` at all, silently acting on whichever
    copy comes first. A text carrying it twice is ambiguous and must come back
    `None` (drift) rather than a guess.
    """
    text = (
        "spec:\n"
        "  syncPolicy:\n"
        "    automated: { selfHeal: true }\n"
        "    automated: { selfHeal: true }\n"  # deliberately ambiguous duplicate
    ) + RETRY_BLOCK_TEXT
    assert normalize_for_pin(text) is None


def test_normalize_for_pin_refuses_a_repeated_block_style_literal() -> None:
    """Same fail-closed requirement for the block-style `automated` form."""
    text = (
        "spec:\n"
        "  syncPolicy:\n"
        "    automated:\n"
        "      selfHeal: true\n"
        "    automated:\n"
        "      selfHeal: true\n"  # deliberately ambiguous duplicate
    ) + RETRY_BLOCK_TEXT
    assert normalize_for_pin(text) is None


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
