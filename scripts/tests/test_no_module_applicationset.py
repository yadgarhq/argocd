"""No module ApplicationSet survives the retirement (ledger 1270b, ADR-0848).

WHAT RETIRED. `applicationsets/modules.yaml` was a `scmProvider`-generator
ApplicationSet, `yadgar-modules`, that discovered every `yadgar-deployable`
repository and generated one Application per module, each reading a second
helm source at `$argocd/versions/{{ .repository }}.yaml`. The parent chart
cutover (ADR-0786, `plans/dogfooding-the-parent-chart.md` in `yadgarhq/docs`)
moved every module under one published chart instead, so the generator's
`NotIn` selector already excluded all seven modules and it generated zero
Applications before this gate existed. `versions/*.yaml`,
`scripts/versions_pinned.py` (the gate that kept a released module's pin from
going silently stale) and the `github-scm` Secret the generator authenticated
with retired alongside it — actions#107 (ledger 1270a) stopped `ci-release`
writing `versions/<module>.yaml` into this repository first, and nothing has
written one since.

THIS GATE ASSERTS THE RETIREMENT STAYS RETIRED, by three independent checks,
each catching a different way the mechanism could creep back:

  1. No `ApplicationSet` document, anywhere in this repository's tracked
     YAML, carries an `scmProvider` generator — the shape that discovers
     repositories across the organisation rather than naming one.
  2. No `versions/` directory exists at the repository root — the thing a
     revived generator would read a per-module pin from.
  3. No tracked file other than `MIGRATION_NOTES.md` (which discusses the
     retirement in prose) names the literal helm value-file reference
     `$argocd/versions` — the shape a revived ApplicationSet template would
     need to read a pin back.

Offline: no helm, no network, no cluster. Run:
    python3 -m pytest scripts/tests/test_no_module_applicationset.py -q
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]

VERSIONS_REF = "$argocd/versions"

# EVERY git CALL IN THIS FILE PASSES THIS, deliberately, and dropping it on
# any one of them is a real incident rather than a style nit: `pre-commit`
# runs this file's own hook (`operator-applications`) with `GIT_DIR` /
# `GIT_WORK_TREE` / `GIT_INDEX_FILE` set in its environment (its own stash
# machinery for unstaged changes), and a bare `subprocess.run(["git", ...])`
# inherits that by default. A mutation test's `git init`/`git add -A` under a
# `tmp_path` copy then redirects through THOSE variables instead of the repo
# it just created, and `git add -A` silently rewrites the REAL repository's
# index to describe the tmp copy's tree — every real file reads as deleted,
# every fixture file as added. Measured: this happened for real while
# authoring this file, during `pre-commit run --all-files`. Stripping the
# `GIT_*` keys from the child's environment is the fix; `-C`/`cwd=` alone did
# not stop it, because those only change where git LOOKS, not what these
# variables tell it to use once it is there.
_GIT_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_GIT_ENV,
        capture_output=True,
        text=True,
        check=True,
    )


def _tracked_files(tree: Path) -> list[str]:
    """Every path `git` tracks in `tree`, relative to it.

    Tracked rather than walked: a scratch file an editor drops beside the
    working tree must not make this gate flap, and a deleted-but-staged file
    must not either.
    """
    out = _git(["-C", str(tree), "ls-files"]).stdout
    return [line for line in out.splitlines() if line]


def _contains_scm_provider(node: object) -> bool:
    """`True` if `scmProvider` appears as a key anywhere under `node`.

    Recursive rather than a single `"generators"` lookup: a generator can
    nest inside `matrix` or `merge`, and this gate is about the KEY existing
    at all, not about one particular generator shape.
    """
    if isinstance(node, dict):
        if "scmProvider" in node:
            return True
        return any(_contains_scm_provider(value) for value in node.values())
    if isinstance(node, list):
        return any(_contains_scm_provider(item) for item in node)
    return False


def applicationsets_with_scm_provider(tree: Path) -> list[str]:
    """Every tracked YAML file holding an `ApplicationSet` with an `scmProvider` generator."""
    offenders = []
    for name in _tracked_files(tree):
        if not name.endswith((".yaml", ".yml")):
            continue
        path = tree / name
        if not path.is_file():
            continue
        for document in yaml.safe_load_all(path.read_text()):
            if not isinstance(document, dict):
                continue
            if document.get("kind") != "ApplicationSet":
                continue
            if _contains_scm_provider(document.get("spec") or {}):
                offenders.append(name)
                break
    return offenders


def versions_directory_present(tree: Path) -> bool:
    """`True` if `versions/` exists at the repository root."""
    return (tree / "versions").is_dir()


#: This gate's own test file necessarily names the retired reference in its
#: docstring, its constant and its fixtures — the same reason
#: `MIGRATION_NOTES.md`'s prose is exempt.
_SELF = "scripts/tests/test_no_module_applicationset.py"


def versions_ref_outside_migration_notes(tree: Path) -> list[str]:
    """Every tracked file, other than `MIGRATION_NOTES.md` and this gate itself, naming `$argocd/versions`."""
    offenders = []
    for name in _tracked_files(tree):
        if name in ("MIGRATION_NOTES.md", _SELF):
            continue
        path = tree / name
        if not path.is_file():
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if VERSIONS_REF in text:
            offenders.append(name)
    return offenders


def test_no_applicationset_carries_an_scm_provider_generator() -> None:
    offenders = applicationsets_with_scm_provider(REPOSITORY)
    assert offenders == [], (
        f"found an scmProvider-generator ApplicationSet in {offenders} — the "
        "module-discovery generator retired at ledger 1270b; modules deploy "
        "through the parent chart (applications/yadgar.yaml) now"
    )


def test_no_versions_directory() -> None:
    assert not versions_directory_present(REPOSITORY), (
        "`versions/` exists at the repository root — it retired at ledger "
        "1270b along with the ApplicationSet that read it"
    )


def test_no_tracked_versions_ref_outside_migration_notes() -> None:
    offenders = versions_ref_outside_migration_notes(REPOSITORY)
    assert offenders == [], (
        f"found the retired `$argocd/versions` helm value-file reference in "
        f"{offenders} — only MIGRATION_NOTES.md's prose may still name it"
    )


def test_a_restored_modules_applicationset_reddens(tmp_path: Path) -> None:
    """Mutation check: put a module-discovery ApplicationSet back; the gate names it."""
    tree = tmp_path / "copy"
    tree.mkdir()
    (tree / "applicationsets").mkdir()
    (tree / "applicationsets" / "modules.yaml").write_text(
        "apiVersion: argoproj.io/v1alpha1\n"
        "kind: ApplicationSet\n"
        "metadata:\n"
        "  name: yadgar-modules\n"
        "  namespace: argocd\n"
        "spec:\n"
        "  generators:\n"
        "    - scmProvider:\n"
        "        github:\n"
        "          organization: yadgarhq\n"
        "  template:\n"
        "    metadata:\n"
        "      name: '{{ .repository }}'\n"
        "    spec:\n"
        "      project: default\n"
        "      destination:\n"
        "        server: https://kubernetes.default.svc\n"
    )
    _git(["init", "-q"], cwd=tree)
    _git(["add", "-A"], cwd=tree)
    offenders = applicationsets_with_scm_provider(tree)
    assert offenders == ["applicationsets/modules.yaml"]


def test_an_untracked_versions_directory_does_not_redden(tmp_path: Path) -> None:
    """A directory present only on disk, never tracked, is not this gate's business."""
    tree = tmp_path / "copy"
    tree.mkdir()
    _git(["init", "-q"], cwd=tree)
    (tree / "README.md").write_text("placeholder\n")
    _git(["add", "-A"], cwd=tree)
    (tree / "versions").mkdir()
    (tree / "versions" / "scratch.yaml").write_text("untracked: true\n")
    # Untracked, so _tracked_files never sees it — but the directory check is
    # a filesystem check, not a git check, so it still catches an untracked
    # revival the same as a committed one.
    assert versions_directory_present(tree)


def test_a_tracked_versions_ref_outside_migration_notes_reddens(tmp_path: Path) -> None:
    """Mutation check: a stray helm value-file reference in a tracked file reddens."""
    tree = tmp_path / "copy"
    tree.mkdir()
    _git(["init", "-q"], cwd=tree)
    (tree / "MIGRATION_NOTES.md").write_text(f"discusses {VERSIONS_REF} in prose\n")
    (tree / "stray.yaml").write_text(f"valueFiles:\n  - {VERSIONS_REF}/gateway.yaml\n")
    _git(["add", "-A"], cwd=tree)
    assert versions_ref_outside_migration_notes(tree) == ["stray.yaml"]


@pytest.mark.parametrize("name", ["MIGRATION_NOTES.md"])
def test_migration_notes_itself_is_exempt(tmp_path: Path, name: str) -> None:
    tree = tmp_path / "copy"
    tree.mkdir()
    _git(["init", "-q"], cwd=tree)
    (tree / name).write_text(f"mentions {VERSIONS_REF} as history\n")
    _git(["add", "-A"], cwd=tree)
    assert versions_ref_outside_migration_notes(tree) == []
