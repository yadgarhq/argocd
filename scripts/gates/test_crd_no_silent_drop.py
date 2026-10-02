"""A chart bump — or a values change, or a deleted Application — never drops a
CRD silently (ledger 1217).

Argo CD v3.1.8 never puts a tracking id on a `CustomResourceDefinition`
(`repository.go:1584`, `!IsCRD`), so it never prunes one either. A pull
request that bumps a chart pin, flips a values toggle, or deletes an
Application outright can remove a CRD from what this organisation renders,
and the cluster keeps it — with any custom resource it still admits — with
no signal from Argo on either side of the change.

WHY THIS REPOSITORY, AND NOT `yadgarhq/chart` OR `yadgarhq/platform`. A chart
or platform release is a tag; nothing there runs this organisation's values,
and `yadgarhq/chart`'s own `scripts/tests/test_parent_chart.py` already
renders the parent at ITS OWN example values, not this organisation's. A pin
BUMP — the event this gate exists to catch — is a diff in exactly one place:
this repository's `applications/*.yaml`, which is where `scripts/chart_pin.json`
and every operator Application's `targetRevision` actually move. That is also
the render Argo itself performs, so a CRD this gate sees drop is a CRD the
cluster actually loses.

WHAT THIS GATE COMPARES. Every `applications/*.yaml` Application whose chart
source supplies its values inline (`helm.valuesObject` or `helm.values`,
never `helm.valueFiles`), rendered with `helm template --include-crds` at
this pull request's BASE commit and again at HEAD, UNIONED across every such
Application rather than compared one at a time — a CRD that moves from one
Application to another between base and head is not a false positive this
way. Measured 2026-10-02 at head (this organisation's current pins): the
five operator Applications (`cert-manager.yaml`, `envoy-gateway.yaml`,
`keda.yaml`, `mariadb-operator.yaml`, `prometheus.yaml`) each source the SAME
`platform` chart from `ghcr.io/yadgarhq/charts` with a different
`operators.<name>.create: true`, so the operators' CRDs are real wherever the
chart renders them — NOT, as an earlier framing of this ledger item assumed,
vendored inside `yadgarhq/platform` alone; `yadgarhq/platform` publishes the
chart, this repository is what actually turns it on, per Application, at a
pin this repository owns.

ONE DOCUMENTED EXCLUSION: `applications/argocd.yaml`. Its chart source reads
`helm.valueFiles: [$self/install/values.yaml]`, a file from this
repository's OWN tree rather than an inline value — rendering it at an
arbitrary base commit would need a second contents-API read this gate does
not yet make. Argo CD's own CRDs (`Application`, `AppProject`,
`ApplicationSet`) are therefore NOT covered here. This is a stated gap, not
an oversight (`renderable` below is where it is enforced); closing it is
"read `install/values.yaml` at the same ref too" when somebody has reason to.

WHY `scripts/gates/`, NOT A NEW CI JOB. The `two-owners` job already needs
helm and the registry and already runs `pytest scripts/gates -q` — see
`.github/workflows/ci.yaml`. Adding this module there folds the gate into the
existing `ci / passed` aggregate (`ci`'s `needs: [signature, two-owners]`)
with no ruleset or workflow-shape change, and with no new required check for
`main`'s branch ruleset to learn.

WHY THE GITHUB CONTENTS API FOR THE BASE SIDE, NOT GIT. CI clones at depth 1
(`scripts/gates/test_no_two_owners.py`'s own note: `git show origin/main:…`
errors on the runner because of it). `scripts/check_chart_pin.py` already
reads ANOTHER repository this way; this reads THIS repository's own tree at
`PR_BASE_SHA`, the same shape, over the same public, anonymous-capable API.

WHY NO `pytest.skip` ANYWHERE HERE. `yadgarhq/actions`' `hooks/no_test_skips.py`
forbids it outright, and the step that runs this suite
(`.github/workflows/ci.yaml`'s `two-owners` job) only ever executes on
`pull_request`, so `PR_BASE_SHA` is always set when this module runs in CI. A
local run without it fails loudly with a `KeyError` rather than silently
skipping — the discipline this gate exists to apply to a chart bump applies to
itself (ADR-0645: an audit over nothing must not report green).

Helm invocations below were exercised locally on helm v4.3.0; CI's
`two-owners` job pins v3.18.4, the version Argo CD v3.1.8 bundles.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import yaml

from test_no_two_owners import APPLICATIONS, REPOSITORY, api_version_flags, helm, sources_of

OWNER_REPO = "yadgarhq/argocd"
CONTENTS_FILE_API = "https://api.github.com/repos/{repo}/contents/{path}?ref={ref}"
CONTENTS_DIR_API = "https://api.github.com/repos/{repo}/contents/{dir}?ref={ref}"

ACK_FILE = REPOSITORY / "scripts" / "crd_removals_ack.json"
REQUIRED_ACK_FIELDS = ("name", "reason", "cleanup")

# THE ONE DOCUMENTED EXCLUSION. See the module docstring.
EXCLUDED_FROM_RENDER = frozenset({"argocd.yaml"})


# ── reading an Application's chart source ──────────────────────────────────


def chart_source(document: dict | None) -> dict | None:
    """The one chart source of an Application document, or `None`."""
    if not document:
        return None
    spec = document.get("spec") or {}
    return next((source for source in sources_of(spec) if source.get("chart")), None)


def renderable(source: dict | None) -> bool:
    """`False` for a chart source whose values live in a file this gate cannot read at an arbitrary ref."""
    if source is None:
        return False
    return "valueFiles" not in (source.get("helm") or {})


def values_of(source: dict) -> dict:
    """A chart source's values, whichever of the two inline forms it uses.

    `applications/prometheus.yaml` uses `helm.values` (a raw YAML string); every
    other in-scope Application uses `helm.valuesObject` (an inline mapping).
    Measured 2026-10-02 across every `applications/*.yaml` at head.
    """
    helm_block = source.get("helm") or {}
    if "valuesObject" in helm_block:
        return helm_block["valuesObject"] or {}
    if "values" in helm_block:
        return yaml.safe_load(helm_block["values"]) or {}
    return {}


# ── the GitHub contents API, for the base side of a depth-1 checkout ───────


def github_request(url: str):
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read())


def base_application_filenames(ref: str) -> set[str]:
    """Every `*.yaml` under `applications/` at `ref`, or empty if the directory never existed there."""
    url = CONTENTS_DIR_API.format(repo=OWNER_REPO, dir=APPLICATIONS, ref=ref)
    try:
        entries = github_request(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return set()
        raise
    return {entry["name"] for entry in entries if entry["name"].endswith(".yaml")}


def base_application(filename: str, ref: str) -> dict | None:
    """The Application document at `applications/<filename>` as it read at `ref`, or `None` if absent there."""
    path = f"{APPLICATIONS}/{filename}"
    url = CONTENTS_FILE_API.format(repo=OWNER_REPO, path=path, ref=ref)
    try:
        payload = github_request(url)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    return yaml.safe_load(base64.b64decode(payload["content"]).decode())


# ── rendering, and reading CRDs out of what rendered ───────────────────────


def crd_names(documents) -> set[str]:
    """Every `CustomResourceDefinition` name in a rendered manifest stream."""
    names: set[str] = set()
    for document in documents:
        if isinstance(document, dict) and document.get("kind") == "CustomResourceDefinition":
            name = (document.get("metadata") or {}).get("name")
            if name:
                names.add(name)
    return names


def crds_of_chart(
    path: Path,
    values: dict,
    release: str = "fixture",
    namespace: str = "default",
    extra_flags: tuple[str, ...] = (),
) -> set[str]:
    """Every CRD `helm template --include-crds` renders for a LOCAL chart directory.

    No network: `path` is already on disk, either a fixture (the tests below)
    or an already-`helm pull --untar`red real chart (`crds_rendered`).
    """
    with tempfile.TemporaryDirectory() as scratch:
        values_file = Path(scratch) / "values.yaml"
        values_file.write_text(yaml.safe_dump(values))
        rendered = helm(
            "template",
            release,
            str(path),
            "--namespace",
            namespace,
            "--values",
            str(values_file),
            "--include-crds",
            *extra_flags,
        )
    return crd_names(yaml.safe_load_all(rendered))


def crds_rendered(release: str, source: dict, namespace: str) -> set[str]:
    """Every CRD a REAL OCI chart source renders: `helm pull --untar`, then `crds_of_chart`."""
    with tempfile.TemporaryDirectory() as scratch:
        helm(
            "pull",
            f"oci://{source['repoURL']}/{source['chart']}",
            "--version",
            source["targetRevision"],
            "--untar",
            "--untardir",
            scratch,
        )
        return crds_of_chart(
            Path(scratch) / source["chart"],
            values_of(source),
            release,
            namespace,
            tuple(api_version_flags()),
        )


def crds_at(side: str, base_ref: str) -> tuple[set[str], dict[str, set[str]]]:
    """The union of CRD names across every in-scope Application on `side` ("base" or "head").

    `side == "head"` reads this working tree directly; `side == "base"` reads
    the GitHub contents API at `base_ref`. The filename set compared is the
    UNION of what exists on both sides, so an Application added or deleted by
    this pull request is seen rather than silently skipped on the side it is
    absent from.
    """
    head_filenames = {path.name for path in (REPOSITORY / APPLICATIONS).glob("*.yaml")}
    base_filenames = base_application_filenames(base_ref)
    filenames = (head_filenames | base_filenames) - EXCLUDED_FROM_RENDER

    names: set[str] = set()
    per_file: dict[str, set[str]] = {}
    for filename in sorted(filenames):
        if side == "head":
            path = REPOSITORY / APPLICATIONS / filename
            document = yaml.safe_load(path.read_text()) if path.exists() else None
        else:
            document = base_application(filename, base_ref)

        source = chart_source(document)
        if not renderable(source):
            continue

        spec = document.get("spec") or {}
        namespace = (spec.get("destination") or {}).get("namespace") or "default"
        release = (document.get("metadata") or {}).get("name", filename)

        found = crds_rendered(release, source, namespace)
        if found:
            per_file[filename] = found
        names |= found
    return names, per_file


# ── the acknowledgement file ────────────────────────────────────────────────


def acknowledged_removals(path: Path = ACK_FILE) -> dict[str, dict]:
    """`scripts/crd_removals_ack.json`'s entries keyed by CRD name.

    Every entry MUST carry a non-empty `name`, `reason` and `cleanup` (a
    manual command to delete the orphaned CRD) — anything less is refused
    rather than silently accepted, because a half-filled acknowledgement is
    worse than none: it reads as reviewed when it was not.
    """
    if not path.exists():
        return {}
    entries = json.loads(path.read_text())
    for entry in entries:
        missing = [field for field in REQUIRED_ACK_FIELDS if not entry.get(field)]
        if missing:
            raise AssertionError(
                f"{path.name} entry {entry!r} is missing {missing} — every acknowledgement "
                "needs a non-empty name, reason and manual cleanup command."
            )
    return {entry["name"]: entry for entry in entries}


def unacknowledged_drops(base: set[str], head: set[str], acks: dict[str, dict]) -> list[str]:
    """CRD(s) the base side rendered, the head side does not, and no ack names."""
    return sorted((base - head) - set(acks))


def stale_acknowledgements(base: set[str], head: set[str], acks: dict[str, dict]) -> list[str]:
    """Ack entries naming a CRD that was NOT actually dropped — a standing lie otherwise."""
    dropped = base - head
    return sorted(name for name in acks if name not in dropped)


# ── fixtures: two minimal helm charts, built at test time, never committed ──
#
# NEVER AS `.yaml` FILES ON DISK IN THIS REPOSITORY. `scripts/policy_sources_named.py`
# parses every `*.yaml` under the repository root, and `check-yaml` / `prettier`
# act on every one too — a fixture chart checked in picks up obligations it was
# never meant to carry. `scripts/gates/test_no_two_owners.py`'s own
# `RESTORED_VALKEY_APPLICATION` et al. are the precedent: fixtures are string
# constants here, written to `tmp_path` only for the duration of one test.

FIXTURE_CHART_YAML = "apiVersion: v2\nname: crd-pair-fixture\nversion: 0.1.0\n"

WIDGET_CRD = """\
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: widgets.fixture.yadgarhq.io
spec:
  group: fixture.yadgarhq.io
  names:
    kind: Widget
    plural: widgets
  scope: Namespaced
  versions:
    - name: v1
      served: true
      storage: true
      schema:
        openAPIV3Schema:
          type: object
"""

GADGET_CRD = """\
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: gadgets.fixture.yadgarhq.io
spec:
  group: fixture.yadgarhq.io
  names:
    kind: Gadget
    plural: gadgets
  scope: Namespaced
  versions:
    - name: v1
      served: true
      storage: true
      schema:
        openAPIV3Schema:
          type: object
"""


def write_fixture_chart(root: Path, crds: tuple[str, ...]) -> Path:
    """A minimal helm chart at `root`, carrying exactly these CRD manifests."""
    (root / "crds").mkdir(parents=True)
    (root / "Chart.yaml").write_text(FIXTURE_CHART_YAML)
    for index, crd in enumerate(crds):
        (root / "crds" / f"crd-{index}.yaml").write_text(crd)
    return root


# ── TDD: the fixture pair, red / green / ack / no-removal / stale-ack ──────


def test_a_dropped_crd_between_two_real_renders_is_detected(tmp_path: Path) -> None:
    """THE RED CASE. A real `helm template --include-crds` on each side, diffed."""
    base = write_fixture_chart(tmp_path / "base", (WIDGET_CRD, GADGET_CRD))
    head = write_fixture_chart(tmp_path / "head", (GADGET_CRD,))
    base_crds = crds_of_chart(base, {})
    head_crds = crds_of_chart(head, {})
    assert base_crds == {"widgets.fixture.yadgarhq.io", "gadgets.fixture.yadgarhq.io"}
    assert head_crds == {"gadgets.fixture.yadgarhq.io"}
    assert base_crds - head_crds == {"widgets.fixture.yadgarhq.io"}


def test_an_unacknowledged_drop_is_unacknowledged(tmp_path: Path) -> None:
    """The exact predicate the production gate asserts is empty."""
    base = write_fixture_chart(tmp_path / "base", (WIDGET_CRD, GADGET_CRD))
    head = write_fixture_chart(tmp_path / "head", (GADGET_CRD,))
    base_crds = crds_of_chart(base, {})
    head_crds = crds_of_chart(head, {})
    assert unacknowledged_drops(base_crds, head_crds, {}) == ["widgets.fixture.yadgarhq.io"]


def test_an_acknowledged_drop_has_nothing_unacknowledged(tmp_path: Path) -> None:
    """THE ACK CASE. An ack naming the dropped CRD clears it."""
    base = write_fixture_chart(tmp_path / "base", (WIDGET_CRD, GADGET_CRD))
    head = write_fixture_chart(tmp_path / "head", (GADGET_CRD,))
    base_crds = crds_of_chart(base, {})
    head_crds = crds_of_chart(head, {})

    ack_file = tmp_path / "ack.json"
    ack_file.write_text(
        json.dumps(
            [
                {
                    "name": "widgets.fixture.yadgarhq.io",
                    "reason": "retired fixture CRD",
                    "cleanup": "kubectl --context kind-yadgar delete crd widgets.fixture.yadgarhq.io",
                }
            ]
        )
    )
    acks = acknowledged_removals(ack_file)
    assert unacknowledged_drops(base_crds, head_crds, acks) == []
    assert stale_acknowledgements(base_crds, head_crds, acks) == []


def test_the_same_chart_drops_nothing(tmp_path: Path) -> None:
    """THE NO-REMOVAL CASE. Base compared against itself needs no acknowledgement."""
    base = write_fixture_chart(tmp_path / "base", (WIDGET_CRD, GADGET_CRD))
    base_crds = crds_of_chart(base, {})
    assert unacknowledged_drops(base_crds, base_crds, {}) == []


def test_an_ack_naming_a_crd_still_present_is_stale(tmp_path: Path) -> None:
    """An ack that does not match a real removal is a standing lie, not a pass."""
    base = write_fixture_chart(tmp_path / "base", (WIDGET_CRD, GADGET_CRD))
    base_crds = crds_of_chart(base, {})
    ack_file = tmp_path / "ack.json"
    ack_file.write_text(
        json.dumps(
            [{"name": "widgets.fixture.yadgarhq.io", "reason": "pre-emptive", "cleanup": "kubectl ... delete crd ..."}]
        )
    )
    acks = acknowledged_removals(ack_file)
    assert stale_acknowledgements(base_crds, base_crds, acks) == ["widgets.fixture.yadgarhq.io"]


def test_an_ack_missing_a_required_field_refuses(tmp_path: Path) -> None:
    ack_file = tmp_path / "ack.json"
    ack_file.write_text(json.dumps([{"name": "widgets.fixture.yadgarhq.io", "reason": "retired"}]))
    with pytest.raises(AssertionError, match="cleanup"):
        acknowledged_removals(ack_file)


def test_the_committed_ack_file_is_well_formed() -> None:
    """`scripts/crd_removals_ack.json` itself, read the same way the production gate reads it."""
    acknowledged_removals(ACK_FILE)


def test_argocd_yaml_is_excluded_from_render() -> None:
    """The one documented gap: `valueFiles` makes a source unrenderable at an arbitrary ref."""
    document = yaml.safe_load((REPOSITORY / APPLICATIONS / "argocd.yaml").read_text())
    source = next(s for s in sources_of(document["spec"]) if s.get("chart"))
    assert not renderable(source)


# ── the live gate: this organisation's real Applications, base vs head ─────


def test_a_chart_bump_or_values_change_does_not_silently_drop_a_crd() -> None:
    """LEDGER 1217, THE PRODUCTION GATE. See the module docstring for the shape."""
    base_ref = os.environ["PR_BASE_SHA"]
    base_crds, base_by_file = crds_at("base", base_ref)
    head_crds, _ = crds_at("head", base_ref)

    # ADR-0645: an audit that examined no CRD anywhere must not report green.
    assert base_crds, (
        "the base side rendered zero CRDs across every in-scope Application "
        f"({sorted(base_by_file) or 'none'}). Either nothing in scope renders a "
        "CRD any more (update this gate's module docstring to match) or this "
        "gate measured nothing."
    )

    acks = acknowledged_removals()

    stale = stale_acknowledgements(base_crds, head_crds, acks)
    assert not stale, (
        f"{ACK_FILE.relative_to(REPOSITORY)} acknowledges {stale}, but the head render still "
        "carries it (or never dropped it). Remove the stale acknowledgement — one that does "
        "not match a real removal is a standing lie, not a reviewed decision."
    )

    missing = unacknowledged_drops(base_crds, head_crds, acks)
    assert not missing, (
        f"this pull request drops CustomResourceDefinition(s) {missing} that the base commit "
        "rendered and HEAD does not. Argo CD never tracks or prunes a CRD "
        "(Argo CD v3.1.8 repository.go:1584, `!IsCRD`), so each one stays in the cluster "
        "silently, with any custom resource it still admits. Either restore it in the chart "
        f"or values being pinned, or acknowledge it in {ACK_FILE.relative_to(REPOSITORY)} "
        "(name, reason, and the manual `kubectl delete crd` to clean it up)."
    )
