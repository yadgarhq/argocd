"""A chart bump — or a values change, or a deleted Application — never drops a
CRD silently (ledger 1217).

Argo CD v3.1.8 never puts a tracking id on a `CustomResourceDefinition`
(`repository.go:1584`, `!IsCRD`), so it never prunes one either. A pull
request that bumps a chart pin, flips a values toggle, or deletes an
Application outright can remove a CRD from what this organisation renders. The
CLUSTER KEEPS THE CRD — Argo CD simply stops MANAGING it, silently, on either
side of the change, with any custom resource it still admits left orphaned.

WHY THIS REPOSITORY, AND NOT `yadgarhq/chart` OR `yadgarhq/platform`. A chart
or platform release is a tag; nothing there runs this organisation's values,
and `yadgarhq/chart`'s own `scripts/tests/test_parent_chart.py` already
renders the parent at ITS OWN example values, not this organisation's. A pin
BUMP — the event this gate exists to catch — is a diff in exactly one place:
this repository's `applications/*.yaml`, which is where `scripts/chart_pin.json`
and every operator Application's `targetRevision` actually move. That is also
the render Argo itself performs.

WHAT THIS GATE COMPARES. Every `applications/*.yaml` Application carrying
EXACTLY ONE chart source (`chart_source` fails red on a second one — this
gate models one chart per Application, and the day that stops being true it
must say so loudly rather than silently render the first and ignore the
rest), rendered with `helm template --include-crds` at this pull request's
BASE commit and again at HEAD, UNIONED across every such Application rather
than compared one at a time — a CRD that moves from one Application to
another between base and head is not a false positive this way. Measured
2026-10-02 at head: the five operator Applications (`cert-manager.yaml`,
`envoy-gateway.yaml`, `keda.yaml`, `mariadb-operator.yaml`,
`prometheus.yaml`) each source the SAME `platform` chart from
`ghcr.io/yadgarhq/charts` with a different `operators.<name>.create: true`,
so the operators' CRDs are real wherever the chart renders them — NOT, as an
earlier framing of this ledger item assumed, vendored inside
`yadgarhq/platform` alone; `yadgarhq/platform` publishes the chart, this
repository is what actually turns it on, per Application, at a pin this
repository owns.

A CHART SOURCE'S `helm` BLOCK IS FAIL-CLOSED, NOT BEST-EFFORT. `MODELLED_HELM_KEYS`
is the allowlist (`valuesObject`, `values`, `releaseName`, `skipCrds`), plus a
single `$self/<path>` `valueFiles` entry (`self_value_file_path`). Any OTHER
helm key on an in-scope source — `parameters`, `fileParameters`, `kubeVersion`,
`apiVersions`, `namespace`, `passCredentials`, `skipSchemaValidation`,
`skipTests`, `ignoreMissingValueFiles`, or more than one `valueFiles` entry —
raises rather than renders. The reason this is not merely documented: a
`helm.parameters` entry setting `crds.enabled=false`, or `helm.skipCrds: true`,
changes what Argo CD actually installs exactly as much as a chart-pin bump
does, and a gate that rendered past it without those flags would report green
on exactly the silent drop ledger 1217 exists to catch. `skipCrds` IS modelled
(Argo's own description: "skips custom resource definition installation step,
Helm's `--skip-crds`") — a source with `skipCrds: true` contributes the EMPTY
set, matching what Argo actually applies, without even pulling the chart.
`releaseName` is honoured the same way Argo resolves it: the chart source's
own value if set, else the Application's `metadata.name` (`release_name_of`).
Every key name here was read off `yadgarhq/chart`'s own pinned argo-cd CRD
(`crd-application.yaml`), not assumed.

`applications/argocd.yaml` IS COVERED. Its first source is a CLASSIC (non-OCI)
helm repository (`https://argoproj.github.io/argo-helm`) — `pull_chart`
branches on `repoURL`'s scheme and uses `helm pull <chart> --repo <url>`
rather than hardcoding `oci://`. Its `helm.valueFiles: ["$self/install/values.yaml"]`
is the one `valueFiles` shape this gate models: `self_value_file_path` resolves
it, and `self_value_file_content` reads `install/values.yaml` from this
working tree for HEAD and through the GitHub contents API at `PR_BASE_SHA` for
BASE, passed to `helm template` as a second `--values` file. Measured
2026-10-02: this adds Argo CD's own 3 CRDs (`applications.argoproj.io`,
`applicationsets.argoproj.io`, `appprojects.argoproj.io`) to the base-side
union. Its second source (`ref: self`, no `chart` key) contributes nothing —
`sources_of`/`chart_source` only look at sources carrying a `chart` key.

WHAT IS STILL NOT RENDERED, A STATED SCOPE LIMIT RATHER THAN AN OVERSIGHT.
`applications/estate-front.yaml`, `applications/tls.yaml` and
`applications/post-merge-verifier.yaml` are `path:`-sourced (directory)
Applications with no `chart` key at all — `chart_source` returns `None` for
them and they are skipped, the same way they are skipped by
`scripts/gates/test_no_two_owners.py`'s own D side. Nothing under
`applicationsets/` is rendered either: an ApplicationSet is a template
generating Applications, not a chart source itself, and module charts
(`yadgarhq/applicationsets/yadgar-modules.yaml`'s subject) carry no CRDs
today. Closing either gap is a different, larger gate than this one.

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

WHY AN ACKNOWLEDGEMENT NEVER GOES STALE ON ITS OWN. `stale_acknowledgements`
takes the HEAD side alone: an ack is stale only when the CRD it names is
RENDERED AGAIN at head — a resurrection that contradicts the ack's own claim.
An earlier version of this gate computed staleness from `base - head` of the
SAME pull request, which made every acknowledgement single-use: the pull
request that merges the ack has a real drop to show for it, but the very next
pull request compares an unchanged base against an unchanged head, finds
nothing freshly dropped, and therefore called the now-permanent ack "stale" —
reddening every PR after the first forever. `scripts/crd_removals_ack.json`
is a permanent cleanup record, not a per-PR diff, and this gate reads it that
way now.

WHY NO `pytest.skip` ANYWHERE HERE. `yadgarhq/actions`' `hooks/no_test_skips.py`
forbids it outright, and the step that runs this suite
(`.github/workflows/ci.yaml`'s `two-owners` job) only ever executes on
`pull_request`, so `PR_BASE_SHA` is always set when this module runs in CI. A
local run without it fails loudly with a `KeyError` rather than silently
skipping — the discipline this gate exists to apply to a chart bump applies to
itself (ADR-0645: an audit over nothing must not report green).

THE LIVE PRODUCTION PATH (`gate`) IS WIRING-TESTED, NOT ONLY LOGIC-TESTED.
`test_a_chart_bump_or_values_change_does_not_silently_drop_a_crd` runs
against this organisation's REAL data, where base and head are usually
identical — which means a bug in `gate()` itself (the floor check removed,
HEAD accidentally rendered from the BASE ref, either final `assert`
commented out) would not be caught by that one test on an ordinary pull
request, because the real render passes either way. The four
`test_the_production_path_*` tests below monkeypatch
`base_application_filenames` / `base_application` / `crds_rendered` to feed
`gate()` a fabricated toggle-off pair, an empty base, a deleted Application,
and a stale acknowledgement, and assert it raises in each case — every one of
those four mutations above is caught by at least one of them.

Helm invocations below were exercised locally on helm v4.3.0 AND on v3.18.4
(installed at the exact version CI's `two-owners` job pins, the version Argo
CD v3.1.8 bundles) — both gave the same CRD counts.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
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

# THE ALLOWLIST. See the module docstring's "FAIL-CLOSED" paragraph. A single
# `$self/<path>` `valueFiles` entry is modelled too, but through
# `self_value_file_path` rather than a bare key name, because the FORM of the
# entry is what makes it safe to resolve, not merely its key.
MODELLED_HELM_KEYS = frozenset({"valuesObject", "values", "releaseName", "skipCrds"})

SELF_VALUE_FILE = re.compile(r"^\$self/(?P<path>.+)$")

# So a test in this file can monkeypatch this module's own top-level names
# (`base_application_filenames`, `base_application`, `crds_rendered`,
# `REPOSITORY`, `ACK_FILE`, `helm`, `pull_chart`) the way the review asked —
# patching the SAME module the code under test actually calls through.
THIS_MODULE = sys.modules[__name__]


# ── reading an Application's chart source ──────────────────────────────────


def chart_source(document: dict | None) -> dict | None:
    """The one chart source of an Application document, or `None` if it has none.

    Raises if it has MORE THAN ONE — this gate models exactly one chart per
    Application (finding 7 of the 2026-10-02 review); a second one would
    otherwise be silently ignored rather than rendered or refused.
    """
    if not document:
        return None
    spec = document.get("spec") or {}
    chart_sources = [source for source in sources_of(spec) if source.get("chart")]
    if len(chart_sources) > 1:
        raise AssertionError(
            f"an Application has {len(chart_sources)} chart sources; this gate renders exactly "
            "one per Application and must be extended before it can trust a render past this."
        )
    return chart_sources[0] if chart_sources else None


def unmodelled_helm_keys(source: dict) -> set[str]:
    """Helm keys on `source` this gate does not model. Non-empty means `crds_at` must refuse, not approximate."""
    keys = set((source.get("helm") or {}).keys())
    if "valueFiles" in keys and self_value_file_path(source) is not None:
        keys.discard("valueFiles")
    return keys - MODELLED_HELM_KEYS


def self_value_file_path(source: dict) -> str | None:
    """The repo-relative path of a single `$self/<path>` `valueFiles` entry, or `None`.

    `None` for zero entries, for more than one, or for an entry that is not
    `$self`-relative — every one of those stays inside `unmodelled_helm_keys`'s
    result instead of being silently approximated.
    """
    value_files = (source.get("helm") or {}).get("valueFiles") or []
    if len(value_files) != 1:
        return None
    match = SELF_VALUE_FILE.match(value_files[0])
    return match.group("path") if match else None


def release_name_of(document: dict, source: dict, filename: str) -> str:
    """Argo's own release-name resolution: `helm.releaseName` if set, else the Application's `metadata.name`."""
    helm_block = source.get("helm") or {}
    if helm_block.get("releaseName"):
        return helm_block["releaseName"]
    return (document.get("metadata") or {}).get("name", filename)


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


def self_value_file_content(path: str, side: str, base_ref: str) -> str:
    """The content of a `ref: self`, `$self/<path>` `valueFiles` entry, read from THIS repository.

    HEAD reads the working tree directly; BASE reads the same contents API
    `base_application` uses, at `base_ref`, so a change to `install/values.yaml`
    itself is seen exactly like a change to the Application manifest is.
    """
    if side == "head":
        return (REPOSITORY / path).read_text()
    payload = github_request(CONTENTS_FILE_API.format(repo=OWNER_REPO, path=path, ref=base_ref))
    return base64.b64decode(payload["content"]).decode()


# ── pulling and rendering, and reading CRDs out of what rendered ───────────


def pull_chart(source: dict, scratch: str) -> Path:
    """`helm pull --untar` into `scratch`, OCI or classic repo depending on `repoURL`'s scheme.

    A scheme-less `repoURL` (`ghcr.io/yadgarhq/charts`, every in-scope
    Application but `argocd.yaml`'s first source) is OCI, `helm pull
    oci://<repoURL>/<chart>`. An `http(s)://` one (`argocd.yaml`'s
    `https://argoproj.github.io/argo-helm`) is a classic repository, `helm
    pull <chart> --repo <repoURL>` — the earlier version of this gate
    hardcoded the OCI form and would have crashed on this shape (finding 5 of
    the 2026-10-02 review).
    """
    repo_url = source["repoURL"]
    chart = source["chart"]
    version = source["targetRevision"]
    if repo_url.startswith("http://") or repo_url.startswith("https://"):
        helm("pull", chart, "--repo", repo_url, "--version", version, "--untar", "--untardir", scratch)
    else:
        helm("pull", f"oci://{repo_url}/{chart}", "--version", version, "--untar", "--untardir", scratch)
    return Path(scratch) / chart


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
    extra_values_text: str | None = None,
) -> set[str]:
    """Every CRD `helm template --include-crds` renders for a LOCAL chart directory.

    No network: `path` is already on disk, either a fixture (the tests below)
    or an already-`helm pull --untar`red real chart (`crds_rendered`).
    `extra_values_text`, when given, is a SECOND `--values` file — the
    resolved content of a `$self/<path>` `valueFiles` entry, applied after
    (so it can override) `values`.
    """
    with tempfile.TemporaryDirectory() as scratch:
        values_file = Path(scratch) / "values.yaml"
        values_file.write_text(yaml.safe_dump(values))
        value_flags = ["--values", str(values_file)]
        if extra_values_text is not None:
            extra_file = Path(scratch) / "self-values.yaml"
            extra_file.write_text(extra_values_text)
            value_flags += ["--values", str(extra_file)]
        rendered = helm(
            "template",
            release,
            str(path),
            "--namespace",
            namespace,
            *value_flags,
            "--include-crds",
            *extra_flags,
        )
    return crd_names(yaml.safe_load_all(rendered))


def crds_rendered(
    release: str,
    source: dict,
    namespace: str,
    extra_values_text: str | None = None,
) -> set[str]:
    """Every CRD a real chart source renders — the EMPTY set outright if `skipCrds` is set.

    `skipCrds: true` means Argo never applies this chart's CRDs at all (its
    own description: "skips custom resource definition installation step,
    Helm's `--skip-crds`") — modelling it here, rather than ignoring it, is
    findings 2+3 of the 2026-10-02 review.
    """
    if (source.get("helm") or {}).get("skipCrds"):
        return set()
    with tempfile.TemporaryDirectory() as scratch:
        chart_dir = pull_chart(source, scratch)
        return crds_of_chart(
            chart_dir,
            values_of(source),
            release,
            namespace,
            tuple(api_version_flags()),
            extra_values_text,
        )


def crds_at(side: str, base_ref: str) -> tuple[set[str], dict[str, set[str]]]:
    """The union of CRD names across every in-scope Application on `side` ("base" or "head").

    `side == "head"` reads this working tree directly; `side == "base"` reads
    the GitHub contents API at `base_ref`. The filename set compared is the
    UNION of what exists on both sides, so an Application added or deleted by
    this pull request is seen rather than silently skipped on the side it is
    absent from.

    RAISES, rather than skips, the moment an in-scope chart source carries an
    unmodelled helm key — see the module docstring's "FAIL-CLOSED" paragraph.
    """
    head_filenames = {path.name for path in (REPOSITORY / APPLICATIONS).glob("*.yaml")}
    base_filenames = base_application_filenames(base_ref)
    filenames = head_filenames | base_filenames

    names: set[str] = set()
    per_file: dict[str, set[str]] = {}
    for filename in sorted(filenames):
        if side == "head":
            path = REPOSITORY / APPLICATIONS / filename
            document = yaml.safe_load(path.read_text()) if path.exists() else None
        else:
            document = base_application(filename, base_ref)

        source = chart_source(document)
        if source is None:
            continue  # no chart source at all on this side: path-only Application, or absent here

        unmodelled = unmodelled_helm_keys(source)
        if unmodelled:
            raise AssertionError(
                f"{filename}'s chart source on the {side} side uses helm key(s) {sorted(unmodelled)} that "
                "this gate does not model. An unmodelled key can change what Argo actually renders "
                "without this gate noticing — ledger 1217's own failure mode — so extend "
                "`values_of` / `crds_rendered` / `MODELLED_HELM_KEYS` with a justified reason before "
                "this gate may trust its own render of this Application again."
            )

        spec = document.get("spec") or {}
        namespace = (spec.get("destination") or {}).get("namespace") or "default"
        release = release_name_of(document, source, filename)

        extra_values_text = None
        self_path = self_value_file_path(source)
        if self_path is not None:
            extra_values_text = self_value_file_content(self_path, side, base_ref)

        found = crds_rendered(release, source, namespace, extra_values_text)
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


def stale_acknowledgements(head: set[str], acks: dict[str, dict]) -> list[str]:
    """Ack entries naming a CRD the HEAD side still renders — a resurrection, which makes the ack wrong.

    Takes `head` ALONE, deliberately — see the module docstring's "WHY AN
    ACKNOWLEDGEMENT NEVER GOES STALE ON ITS OWN" paragraph. Computing this
    from `base - head` of one pull request made every acknowledgement
    single-use: the very next pull request, with nothing newly dropped, would
    call the committed, correct, permanent ack "stale" and redden forever.
    """
    return sorted(name for name in acks if name in head)


# ── the live gate, pulled out of the test so wiring itself can be exercised ─


def gate(base_ref: str) -> None:
    """LEDGER 1217, THE PRODUCTION GATE. Raises `AssertionError` on anything it exists to catch.

    A plain function rather than inlined in a test, so
    `test_the_production_path_*` below can call it with
    `base_application_filenames` / `base_application` / `crds_rendered`
    monkeypatched and prove the WIRING works, not only the pure
    `unacknowledged_drops` / `stale_acknowledgements` logic in isolation.
    """
    base_crds, base_by_file = crds_at("base", base_ref)
    head_crds, _ = crds_at("head", base_ref)

    # ADR-0645: an audit that examined no CRD anywhere must not report green.
    assert base_crds, (
        "the base side rendered zero CRDs across every in-scope Application "
        f"({sorted(base_by_file) or 'none'}). Either nothing in scope renders a CRD any more "
        "(update this gate's module docstring to match) or this gate measured nothing."
    )

    acks = acknowledged_removals(ACK_FILE)

    stale = stale_acknowledgements(head_crds, acks)
    assert not stale, (
        f"{ACK_FILE.name} acknowledges {stale} as removed, but the head render still carries "
        "it. Remove the stale acknowledgement(s) — once a CRD is rendered again, the old ack is "
        "wrong, not merely out of date."
    )

    missing = unacknowledged_drops(base_crds, head_crds, acks)
    assert not missing, (
        f"this pull request drops CustomResourceDefinition(s) {missing} that the base commit "
        "rendered and HEAD does not. Argo CD never tracks or prunes a CRD "
        "(Argo CD v3.1.8 repository.go:1584, `!IsCRD`), so Argo stops managing each one while the "
        "cluster keeps it — and any custom resource it still admits — forever. Either restore it "
        f"in the chart or values being pinned, or acknowledge it in {ACK_FILE.name} (name, reason, "
        "and the manual `kubectl delete crd` to clean it up)."
    )


# ── fixtures: minimal helm charts, built at test time, never committed ─────
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


def fake_application(
    chart: str = "fake",
    repo_url: str = "ghcr.io/example",
    revision: str = "1.0.0",
    helm_block: dict | None = None,
    namespace: str = "fake",
    name: str = "fake",
) -> dict:
    """A minimal Application document, just enough shape for `chart_source` / `crds_at` to walk."""
    return {
        "metadata": {"name": name},
        "spec": {
            "source": {
                "repoURL": repo_url,
                "chart": chart,
                "targetRevision": revision,
                "helm": helm_block or {"valuesObject": {}},
            },
            "destination": {"namespace": namespace},
        },
    }


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
    assert stale_acknowledgements(head_crds, acks) == []


def test_the_same_chart_drops_nothing(tmp_path: Path) -> None:
    """THE NO-REMOVAL CASE. Base compared against itself needs no acknowledgement."""
    base = write_fixture_chart(tmp_path / "base", (WIDGET_CRD, GADGET_CRD))
    base_crds = crds_of_chart(base, {})
    assert unacknowledged_drops(base_crds, base_crds, {}) == []


def test_an_ack_naming_a_crd_still_present_is_stale(tmp_path: Path) -> None:
    """An ack naming a CRD the current render still carries is wrong, not a pass."""
    chart = write_fixture_chart(tmp_path / "chart", (WIDGET_CRD, GADGET_CRD))
    crds = crds_of_chart(chart, {})
    ack_file = tmp_path / "ack.json"
    ack_file.write_text(
        json.dumps(
            [{"name": "widgets.fixture.yadgarhq.io", "reason": "pre-emptive", "cleanup": "kubectl ... delete crd ..."}]
        )
    )
    acks = acknowledged_removals(ack_file)
    assert stale_acknowledgements(crds, acks) == ["widgets.fixture.yadgarhq.io"]


def test_an_ack_survives_once_the_crd_is_gone_from_both_sides(tmp_path: Path) -> None:
    """FIX FOR FINDING 1 (the single-use bug): after the merge that removed it, the ack must not go stale.

    Red under the review's reported code: the old `stale_acknowledgements(base,
    head, acks)` computed `dropped = base - head` of THIS comparison; with the
    CRD already gone from both sides (the normal shape of every pull request
    after the one that acknowledged the removal), `dropped` is empty and the
    permanent, correct ack was flagged as stale. Green now: staleness reads
    `head` alone.
    """
    head = write_fixture_chart(tmp_path / "head", (GADGET_CRD,))  # widget already gone, as after a merged ack
    head_crds = crds_of_chart(head, {})
    ack_file = tmp_path / "ack.json"
    ack_file.write_text(
        json.dumps(
            [{"name": "widgets.fixture.yadgarhq.io", "reason": "retired", "cleanup": "kubectl ... delete crd ..."}]
        )
    )
    acks = acknowledged_removals(ack_file)
    assert stale_acknowledgements(head_crds, acks) == []


def test_an_ack_missing_a_required_field_refuses(tmp_path: Path) -> None:
    ack_file = tmp_path / "ack.json"
    ack_file.write_text(json.dumps([{"name": "widgets.fixture.yadgarhq.io", "reason": "retired"}]))
    with pytest.raises(AssertionError, match="cleanup"):
        acknowledged_removals(ack_file)


def test_the_committed_ack_file_is_well_formed() -> None:
    """`scripts/crd_removals_ack.json` itself, read the same way the production gate reads it."""
    acknowledged_removals(ACK_FILE)


# ── TDD: the three unmodelled-helm-key holes (findings 2 + 3) ──────────────


def test_an_unmodelled_helm_key_is_not_silently_ignored() -> None:
    """RED CASE for findings 2+3: `parameters` is not in the allowlist."""
    source = {"helm": {"valuesObject": {}, "parameters": [{"name": "crds.enabled", "value": "false"}]}}
    assert unmodelled_helm_keys(source) == {"parameters"}


def test_every_unmodelled_argo_helm_key_is_caught() -> None:
    """Every real Argo CD `source.helm` field this gate does not model, read off the pinned argo-cd CRD."""
    for key in ("fileParameters", "kubeVersion", "apiVersions", "namespace", "passCredentials", "skipSchemaValidation", "skipTests"):
        source = {"helm": {"valuesObject": {}, key: True}}
        assert unmodelled_helm_keys(source) == {key}, key


def test_skip_crds_is_modelled_not_unmodelled() -> None:
    source = {"helm": {"valuesObject": {}, "skipCrds": True}}
    assert unmodelled_helm_keys(source) == set()


def test_skip_crds_renders_as_zero_crds_without_even_pulling(monkeypatch: pytest.MonkeyPatch) -> None:
    """GREEN CASE: `skipCrds: true` short-circuits before any network call."""

    def boom(*_args, **_kwargs):
        raise AssertionError("skipCrds must short-circuit before any helm pull")

    monkeypatch.setattr(THIS_MODULE, "pull_chart", boom)
    source = fake_application(helm_block={"skipCrds": True})["spec"]["source"]
    assert crds_rendered("release", source, "ns") == set()


def test_an_in_scope_source_with_an_unmodelled_key_fails_the_gate_red(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Findings 2+3's exact example, carried through `crds_at`: an unmodelled key fails loud, not open."""
    apps_dir = tmp_path / "applications"
    apps_dir.mkdir()
    document = fake_application(helm_block={"valuesObject": {}, "skipCrds": False, "parameters": [{"name": "x", "value": "y"}]})
    (apps_dir / "fake.yaml").write_text(yaml.safe_dump(document))
    monkeypatch.setattr(THIS_MODULE, "REPOSITORY", tmp_path)
    monkeypatch.setattr(THIS_MODULE, "base_application_filenames", lambda ref: set())
    with pytest.raises(AssertionError, match="parameters"):
        crds_at("head", "irrelevant-ref")


# ── TDD: release name and the non-OCI pull (findings 5 + 6) ────────────────


def test_release_name_is_honoured_when_set() -> None:
    document = fake_application(name="app-name", helm_block={"releaseName": "custom-release"})
    source = document["spec"]["source"]
    assert release_name_of(document, source, "app.yaml") == "custom-release"


def test_release_name_falls_back_to_the_applications_own_name() -> None:
    document = fake_application(name="app-name", helm_block={})
    source = document["spec"]["source"]
    assert release_name_of(document, source, "app.yaml") == "app-name"


def test_pull_chart_uses_the_classic_repo_form_for_an_http_repo_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RED under the pre-review code: it always built `oci://...`, which crashes against a classic repo."""
    captured: dict[str, tuple] = {}

    def fake_helm(*args: str) -> str:
        captured["args"] = args
        return ""

    monkeypatch.setattr(THIS_MODULE, "helm", fake_helm)
    source = {"repoURL": "https://argoproj.github.io/argo-helm", "chart": "argo-cd", "targetRevision": "8.6.1"}
    pull_chart(source, str(tmp_path))
    assert captured["args"][:2] == ("pull", "argo-cd")
    assert "--repo" in captured["args"]
    assert "https://argoproj.github.io/argo-helm" in captured["args"]
    assert not any(a.startswith("oci://") for a in captured["args"])


def test_pull_chart_uses_the_oci_form_for_a_schemeless_repo_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, tuple] = {}

    def fake_helm(*args: str) -> str:
        captured["args"] = args
        return ""

    monkeypatch.setattr(THIS_MODULE, "helm", fake_helm)
    source = {"repoURL": "ghcr.io/yadgarhq/charts", "chart": "platform", "targetRevision": "0.1.21"}
    pull_chart(source, str(tmp_path))
    assert captured["args"][0] == "pull"
    assert captured["args"][1] == "oci://ghcr.io/yadgarhq/charts/platform"


def test_argocd_yaml_is_now_covered() -> None:
    """Finding 6: argocd.yaml's classic-repo + `$self` valueFiles source is modelled, not excluded."""
    document = yaml.safe_load((REPOSITORY / APPLICATIONS / "argocd.yaml").read_text())
    source = chart_source(document)
    assert source is not None
    assert unmodelled_helm_keys(source) == set()
    assert self_value_file_path(source) == "install/values.yaml"
    assert source["repoURL"].startswith("https://")


def test_self_value_file_content_reads_the_working_tree_at_head() -> None:
    content = self_value_file_content("install/values.yaml", "head", base_ref="irrelevant")
    assert "configs" in content or len(content) > 0


# ── TDD: more than one chart source fails red (finding 7) ──────────────────


def test_a_second_chart_source_fails_red() -> None:
    document = {
        "spec": {
            "sources": [
                {"repoURL": "a", "chart": "x", "targetRevision": "1"},
                {"repoURL": "b", "chart": "y", "targetRevision": "1"},
            ]
        }
    }
    with pytest.raises(AssertionError, match="chart source"):
        chart_source(document)


def test_a_path_only_application_has_no_chart_source() -> None:
    document = yaml.safe_load((REPOSITORY / APPLICATIONS / "tls.yaml").read_text())
    assert chart_source(document) is None


# ── TDD: the production path's own wiring, mutants named in the review ─────


def test_the_production_path_reddens_when_an_application_drops_a_crd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kills the `head_crds = crds_at("base", ...)` mutant AND a disabled `assert not missing`.

    A real toggle-off pair carried through `crds_at` + `gate`, with only the
    network-touching leaf (`crds_rendered`) faked.
    """
    apps_dir = tmp_path / "applications"
    apps_dir.mkdir()
    head_document = fake_application(helm_block={"valuesObject": {"enabled": False}})
    base_document = fake_application(helm_block={"valuesObject": {"enabled": True}})
    (apps_dir / "fake.yaml").write_text(yaml.safe_dump(head_document))

    def fake_crds_rendered(_release: str, source: dict, _namespace: str, _extra: str | None = None) -> set[str]:
        enabled = values_of(source).get("enabled")
        return {"widget.fake.io", "gadget.fake.io"} if enabled else {"gadget.fake.io"}

    monkeypatch.setattr(THIS_MODULE, "REPOSITORY", tmp_path)
    monkeypatch.setattr(THIS_MODULE, "base_application_filenames", lambda ref: {"fake.yaml"})
    monkeypatch.setattr(THIS_MODULE, "base_application", lambda filename, ref: base_document)
    monkeypatch.setattr(THIS_MODULE, "crds_rendered", fake_crds_rendered)

    with pytest.raises(AssertionError, match="widget.fake.io"):
        gate("fake-ref")


def test_the_production_path_reddens_when_the_base_side_renders_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kills the "floor removed" mutant: a base side that measures zero CRDs must not report green."""
    apps_dir = tmp_path / "applications"
    apps_dir.mkdir()
    document = fake_application()
    (apps_dir / "fake.yaml").write_text(yaml.safe_dump(document))

    monkeypatch.setattr(THIS_MODULE, "REPOSITORY", tmp_path)
    monkeypatch.setattr(THIS_MODULE, "base_application_filenames", lambda ref: {"fake.yaml"})
    monkeypatch.setattr(THIS_MODULE, "base_application", lambda filename, ref: document)
    monkeypatch.setattr(THIS_MODULE, "crds_rendered", lambda release, source, namespace, extra=None: set())

    with pytest.raises(AssertionError, match="zero CRDs"):
        gate("fake-ref")


def test_the_production_path_sees_a_deleted_applications_crds_as_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An Application deleted at head is a drop too, not a silent shrink of the comparison."""
    apps_dir = tmp_path / "applications"
    apps_dir.mkdir()  # EMPTY at head — "fake.yaml" existed at base and was deleted by this pull request
    base_document = fake_application()

    monkeypatch.setattr(THIS_MODULE, "REPOSITORY", tmp_path)
    monkeypatch.setattr(THIS_MODULE, "base_application_filenames", lambda ref: {"fake.yaml"})
    monkeypatch.setattr(THIS_MODULE, "base_application", lambda filename, ref: base_document)
    monkeypatch.setattr(THIS_MODULE, "crds_rendered", lambda release, source, namespace, extra=None: {"widget.fake.io"})

    with pytest.raises(AssertionError, match="widget.fake.io"):
        gate("fake-ref")


def test_the_production_path_reddens_on_a_stale_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kills a disabled `assert not stale` in isolation: `missing` stays empty throughout this scenario."""
    apps_dir = tmp_path / "applications"
    apps_dir.mkdir()
    document = fake_application()
    (apps_dir / "fake.yaml").write_text(yaml.safe_dump(document))
    ack_file = tmp_path / "ack.json"
    ack_file.write_text(json.dumps([{"name": "widget.fake.io", "reason": "r", "cleanup": "kubectl ... delete crd ..."}]))

    monkeypatch.setattr(THIS_MODULE, "REPOSITORY", tmp_path)
    monkeypatch.setattr(THIS_MODULE, "ACK_FILE", ack_file)
    monkeypatch.setattr(THIS_MODULE, "base_application_filenames", lambda ref: {"fake.yaml"})
    monkeypatch.setattr(THIS_MODULE, "base_application", lambda filename, ref: document)
    # Present on BOTH sides — never dropped, so `missing` is empty and only the stale check can catch this.
    monkeypatch.setattr(THIS_MODULE, "crds_rendered", lambda release, source, namespace, extra=None: {"widget.fake.io"})

    with pytest.raises(AssertionError, match="widget.fake.io"):
        gate("fake-ref")


# ── the live gate: this organisation's real Applications, base vs head ─────


def test_a_chart_bump_or_values_change_does_not_silently_drop_a_crd() -> None:
    """LEDGER 1217, THE PRODUCTION GATE. See the module docstring for the shape."""
    gate(os.environ["PR_BASE_SHA"])
