"""The Makefile's one-time install pin must match this Application's own.

ARGO MANAGES ARGO: `make bootstrap` installs Argo CD once by hand at
`ARGOCD_CHART_VERSION`, then hands control to `applications/argocd.yaml`,
whose own `targetRevision` on the `argo-cd` chart source is the version Argo
reconciles ITSELF to on every sync after that first one. `argocd.yaml`'s own
comment says so: "The chart version lives here rather than in the Makefile
once this is syncing: upgrading Argo becomes a bump in this file." If the
two values diverge, `make bootstrap` installs one version and the very
first self-managed sync moves it to the other — a surprise upgrade (or
downgrade) nobody asked for, and nothing short of reading both files by hand
would have caught it.

NO GATE CAUGHT THIS BEFORE THIS FILE. The two values happen to agree
(`8.6.1`) only because `argocd#59` copied the Makefile's existing pin
byte-faithfully from `yadgarhq/deploy` and nobody separately checked it
against this repository's own `applications/argocd.yaml`. Confirmed red-first
by construction: `test_a_diverged_pin_reddens` mutates a COPY of the real
tree to diverge the two values, and must fail before this file existed —
there was no test here to catch a hand-edited drift at all.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[2]

_ARGOCD_UPSTREAM_REPO_URL = "https://argoproj.github.io/argo-helm"


def makefile_argocd_chart_version(tree: Path) -> str:
    """The value `ARGOCD_CHART_VERSION` is assigned to in the Makefile."""
    text = (tree / "Makefile").read_text()
    match = re.search(r"^ARGOCD_CHART_VERSION\s*:=\s*(\S+)\s*$", text, re.MULTILINE)
    assert match, "ARGOCD_CHART_VERSION assignment not found in Makefile — update this gate"
    return match.group(1)


def application_target_revision(tree: Path) -> str:
    """The `targetRevision` the `argo-cd` chart source names in `applications/argocd.yaml`."""
    documents = list(yaml.safe_load_all((tree / "applications" / "argocd.yaml").read_text()))
    assert len(documents) == 1, "applications/argocd.yaml is no longer a single document"
    sources = documents[0]["spec"]["sources"]
    chart_source = next(
        (s for s in sources if s.get("repoURL") == _ARGOCD_UPSTREAM_REPO_URL),
        None,
    )
    assert chart_source is not None, (
        f"no source in applications/argocd.yaml names {_ARGOCD_UPSTREAM_REPO_URL!r} — "
        "update this gate's repoURL constant"
    )
    return chart_source["targetRevision"]


def a_copy_of_the_tree(tmp_path: Path) -> Path:
    """The repository, copied, so no red case can touch the working tree."""
    tree = tmp_path / "argocd"
    shutil.copytree(REPOSITORY, tree, ignore=shutil.ignore_patterns(".git"))
    return tree


def test_makefile_pin_matches_application_target_revision() -> None:
    """`make bootstrap`'s one-time install lands on the version self-management keeps."""
    makefile_version = makefile_argocd_chart_version(REPOSITORY)
    application_version = application_target_revision(REPOSITORY)
    assert makefile_version == application_version, (
        f"Makefile's ARGOCD_CHART_VERSION ({makefile_version!r}) does not match "
        f"applications/argocd.yaml's targetRevision ({application_version!r}) — "
        "make bootstrap would install one version, and the first self-managed "
        "sync after install/values.yaml applies would immediately move Argo "
        "to the other."
    )


def test_a_diverged_pin_reddens(tmp_path: Path) -> None:
    """Mutation check: bump the Makefile's pin alone; the gate names both values."""
    tree = a_copy_of_the_tree(tmp_path)
    makefile = tree / "Makefile"
    text = makefile.read_text()
    anchor = "ARGOCD_CHART_VERSION := 8.6.1"
    assert anchor in text, "the ARGOCD_CHART_VERSION line moved — update this test"
    mutated = text.replace(anchor, "ARGOCD_CHART_VERSION := 8.6.2", 1)
    assert mutated != text
    makefile.write_text(mutated)
    makefile_version = makefile_argocd_chart_version(tree)
    application_version = application_target_revision(tree)
    assert makefile_version != application_version, (
        "mutating the Makefile's pin alone did not diverge it from "
        "applications/argocd.yaml — update this test"
    )


def test_a_diverged_application_target_revision_reddens(tmp_path: Path) -> None:
    """Mutation check: bump the Application's targetRevision alone; the gate names both values."""
    tree = a_copy_of_the_tree(tmp_path)
    application = tree / "applications" / "argocd.yaml"
    text = application.read_text()
    anchor = "      targetRevision: 8.6.1\n"
    assert anchor in text, "the argo-cd source's targetRevision line moved — update this test"
    mutated = text.replace(anchor, "      targetRevision: 8.7.0\n", 1)
    assert mutated != text
    application.write_text(mutated)
    makefile_version = makefile_argocd_chart_version(tree)
    application_version = application_target_revision(tree)
    assert makefile_version != application_version, (
        "mutating the Application's targetRevision alone did not diverge it "
        "from the Makefile's pin — update this test"
    )
