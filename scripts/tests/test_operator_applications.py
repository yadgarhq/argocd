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
"""

from __future__ import annotations

import fnmatch
import hashlib
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
PINNED_SPECS: dict[str, str] = {
    "cert-manager": "f402c7c04defb9ba09118f243357d16dc2df91eff35ea47f5e52dd0b19d579cb",
    "keda": "19e6856ba5ebc99ba0f24702cc5840b1d94f19a5f0e0d38115b0d280f666f6b6",
    "mariadb-operator": "485a08bcc620570e35ea6c216872e0d64c757d47ea43cc6479de9edf407c65be",
    "mariadb-operator-crds": "bd68b4915105319e1fdf3dcc10f7d6651955cf9b546db944ce4172be9d90536e",
    "envoy-gateway": "1d8b30ab563c10c623bd0e0bebd502273c8a8fab833d9c58661b82d6aa0059b4",
    "prometheus": "c03d2900b2f5f23f76e20c4252f98243984aeb0dfd156aa43913949bb5204a79",
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
    """
    if not text.endswith(RETRY_BLOCK_SUFFIX):
        return None
    text = text[: -len(RETRY_BLOCK_TEXT)]
    if "    automated: { selfHeal: true }" in text:
        return text.replace(
            "    automated: { selfHeal: true }",
            "    automated: { prune: true, selfHeal: true }",
            1,
        )
    if "    automated:\n      selfHeal: true\n" in text:
        return text.replace(
            "    automated:\n      selfHeal: true\n",
            "    automated:\n      prune: true\n      selfHeal: true\n",
            1,
        )
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
    for name in PINNED_SPECS:
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
    for name in PINNED_SPECS:
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
    for name in PINNED_SPECS:
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
    for name in PINNED_SPECS:
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
    for name in PINNED_SPECS:
        path = tree / "applications" / f"{name}.yaml"
        for document in yaml.safe_load_all(path.read_text()):
            if ((document or {}).get("metadata") or {}).get("finalizers"):
                names.append(name)
    return names


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


def test_every_file_is_the_application_root_adopts_by_name() -> None:
    assert identity_errors(REPOSITORY) == []


def test_root_selects_every_file() -> None:
    assert outside_root(REPOSITORY) == []


def test_no_application_carries_prune_false() -> None:
    found, read = manifests_with_prune_false(REPOSITORY)
    print(f"[E3] {read} manifest(s) under applications/ read, {len(found)} carry {PRUNE_FALSE}")
    assert read >= len(PINNED_SPECS)
    assert found == []


def test_no_application_carries_a_finalizer() -> None:
    assert finalized(REPOSITORY) == []


def test_no_application_automated_block_carries_prune() -> None:
    assert automated_prune_present(REPOSITORY) == []


def test_every_application_retry_matches_the_chart_example() -> None:
    print(f"[S0] {len(PINNED_SPECS)} retry block(s) compared against {CHART_EXAMPLE_SOURCE}")
    assert retry_errors(REPOSITORY) == []


def test_a_changed_spec_reddens(copy: Path) -> None:
    path = copy / "applications" / "keda.yaml"
    text = path.read_text()
    assert "selfHeal: true" in text
    path.write_text(text.replace("selfHeal: true", "selfHeal: false", 1))
    assert spec_drift(copy) == ["keda"]


def test_a_missing_file_reddens(copy: Path) -> None:
    (copy / "applications" / "prometheus.yaml").unlink()
    assert spec_drift(copy) == ["prometheus"]


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
    assert outside_root(copy) == list(PINNED_SPECS)


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
    path = copy / "applications" / "prometheus.yaml"
    text = path.read_text()
    assert text.endswith(RETRY_BLOCK_SUFFIX)
    path.write_text(text + "  ignoreDifferences: []\n")
    assert spec_drift(copy) == ["prometheus"]
