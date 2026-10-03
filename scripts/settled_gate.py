#!/usr/bin/env python3
"""The settled-state gate: derive the render epoch argocd `main` names, and judge it once (ledger 675, stage 2).

    settled_gate.py derive       --repo DIR --sha SHA --out FILE
    settled_gate.py judge        --repo DIR --sha S [--trial-sha SHA] --context CTX --out-dir DIR
    settled_gate.py find-verdict --key K --anchor A --out FILE [--exclude-run-id ID]
    settled_gate.py reachable    --sha SHA
    settled_gate.py stale

THE PLAN IS `yadgarhq/docs` `plans/settled-state-smoke-gate.md`, "The
settled-state definition", "How the digests are derived" and "The gate's
shape". The private argocd-verify workflow runs this file only at argocd
`main`, against data at a full commit sha (ADR-0844 (1)). `find-verdict` and
`reachable` live here, not in the workflow's script, so the ADR-0829 and
ADR-0844 controls they carry have tests.

WHAT `derive` COMPUTES, all from the data files at one commit `S`:

  P  `spec.source.targetRevision` of `applications/yadgar.yaml`.
  V  `spec.source.helm.valuesObject` of the same file.
  T  the bytes of `scripts/gates/yadgar_render.sha256`.
  K  sha256(T + canonical JSON of `spec.source`), the JSON being
     `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
     over ONE `yaml.safe_load` (PyYAML 6.0.3, so YAML 1.1: `on` is true and
     `0755` is 493). Estate's verdict job computes the same K from the same
     bytes; the shared vectors are in `scripts/tests/test_settled_gate.py`.
     A `spec.source` that is not a mapping, or that holds a non-string key or
     a value JSON cannot carry (an unquoted date), is REFUSED, as estate
     refuses it: at S that is a red with no K.
  A  the anchor: walking the history of the two files K reads newest-first,
     the last commit whose K still equals the current K. When history runs
     out, the oldest listed commit. A commit where K cannot be computed (a
     file absent, the Application not a mapping that parses, or a refused
     source) differs from every K, so it ends the walk. A merge commit in the walk is red: argocd's
     rulesets are squash-only, so one was never reviewed as a linear change.
  E  (K, A), the epoch. Deadline = A's committer date + 3900s.
  R  every `apps/Deployment` the render holds, each container and init
     container with its image and, when the image is `<repo>@sha256:<64
     hex>`, its digest. Any other image is tag-pinned and recorded by name.

THE STEPS, in the plan's order, and what each costs when it fails:

  0  allow-list `spec.source`: `repoURL` exactly `ghcr.io/yadgarhq/charts`,
     `chart` exactly `yadgar`, no `path`, `kustomize` or `plugin`, and a helm
     block of `valuesObject` alone. RED, before any helm call.
  1  T's first data line is `targetRevision  P`, and `chart_pin.json`'s
     `chart_tag` is `vP`. RED, before any helm call. Every disagreement is
     named, not only the first.
  2  render: `parent_render` from `scripts/gates/test_no_two_owners.py`, the
     function T was written with, fed the data at S.
  3  `digests` of the render (from `test_yadgar_application.py`) equal T
     object for object. RED, naming each object, before any cluster read.
  4  R is non-empty and holds at least one digest-pinned Deployment. RED.

AN INFRASTRUCTURE FAILURE IS NOT A VERDICT. The Application missing or not
parsing at S, an unknown sha, git or helm failing: `derive` raises
`InfrastructureError`, the CLI exits 3 and writes NO output file, and the next
poll retries. A red is a finding about the estate; a registry blip is not.

`judge` DERIVES FIRST, THEN MAKES ONE SET OF READS through
`verify_handover.Cluster` (get only, explicit context): `root` and `yadgar`
Applications, Deployments and pods in `yadgar`. A red derivation is red with
no cluster read. Then every clause is evaluated and every problem reported:

  Clause A  the Deployments `yadgar` tracks equal R by name; each live pod
            template's images equal R's; every pod the selector matches is
            counted except a `Failed`/`Succeeded` one (listed, not counted); a
            counted pod is Running, not terminating, and every container and
            init container status carries R's digest. A tag-pinned container's
            pod image equals the rendered string. Zero counted pods fail.
  Clause B  root Synced at S or a descendant with no operation running
            (`verify_handover.revision_state`); `yadgar` has P in
            `spec.source.targetRevision`, `status.sync.revision` and
            `status.sync.comparedTo.source.targetRevision`, V as parsed objects
            in `comparedTo`, `Synced`, `Healthy` and `operationState.phase`
            `Succeeded`; every rollout complete. An OutOfSync Application
            names each object marked `requiresPruning` with status OutOfSync
            (ADR-0851: FLAGGED, not tolerated). A Synced Application with
            hook objects marked `requiresPruning` (measured live) is not failed.

  green    every clause held: the verdict goes to `<out-dir>/settled-verdict/`.
  pending  a clause is false and the deadline has not passed: NOTHING written.
  red      a clause is false after the deadline, or the derivation is red:
           the verdict goes to `<out-dir>/settled-verdict/`.
  infra    a refused or failed read: NOTHING written, exit 3.

`--trial-sha` reads the DATA at that sha with this (main's) code, judges root
against `--sha`, treats the deadline as passed, and writes `settled-trial/`,
never `settled-verdict/`, so a trial can never become the verdict a poll reads.

`find-verdict` returns the newest `settled-verdict` whose recorded epoch is
(K, A): this repository and head repository, the default branch, the gate
workflow's path, a `schedule` or `workflow_dispatch` run, not expired; never
filtered on the run's conclusion; `settled-trial` never read.

`reachable` accepts a full sha only when `compare/{branch}...{sha}` reads
`behind` or `identical` for a branch of the repository itself. Never
`commits/{sha}`: GitHub serves the whole fork network there.

`stale` FAILS when estate's newest `smoke.yaml` run is older than 45 days, or
when there is none, or when the API cannot answer. GitHub disables a public
repository's scheduled workflows after 60 days without activity, and nothing
else would notice smoke going silent.

Exit codes: 0 derived, green, pending, found-or-not, reachable or fresh; 1 red,
unreachable or stale; 2 usage; 3 infrastructure. Standard library plus PyYAML;
`derive`'s default render also needs pytest importable, because it imports the
gate modules rather than copying them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

import yaml

REPOSITORY = Path(__file__).resolve().parents[1]
GATES = REPOSITORY / "scripts" / "gates"

APPLICATION = "applications/yadgar.yaml"
TABLE = "scripts/gates/yadgar_render.sha256"
CHART_PIN = "scripts/chart_pin.json"
KEY_PATHS = (APPLICATION, TABLE)

ALLOWED_REPO_URL = "ghcr.io/yadgarhq/charts"
ALLOWED_CHART = "yadgar"
FORBIDDEN_SOURCE_KEYS = ("path", "kustomize", "plugin")

# 437s (both hops, worst measured) + 3315s (one bounded sync attempt, ADR-0835),
# rounded up. The plan's "The budget, re-argued from the measurements".
BUDGET_SECONDS = 3900

STALE_DAYS = 45
ESTATE_SMOKE_RUNS = "https://api.github.com/repos/yadgarhq/estate/actions/workflows/smoke.yaml/runs?per_page=1"

FULL_SHA = re.compile(r"[0-9a-f]{40}")
DIGEST_IMAGE = re.compile(r"(?P<repository>[^@\s]+)@(?P<digest>sha256:[0-9a-f]{64})")


class InfrastructureError(Exception):
    """A failure of the run, not of the estate. No verdict is written."""


class KeyRefusal(ValueError):
    """`spec.source` holds something canonical JSON cannot carry faithfully. A red, naming the field."""


# ── K ────────────────────────────────────────────────────────────────────────


def _refuse_unencodable(node, where: str) -> None:
    """Refuse what YAML 1.1 can produce and canonical JSON cannot carry, exactly as estate's verdict job does.

    PyYAML reads an `on:`/`yes:` key as a bool and `1:` as an int: mixed key
    types make `sort_keys` raise, and a lone bool key would encode silently as
    "true". An unquoted date is a `datetime.date`, which json cannot encode.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                raise KeyRefusal(
                    f"spec.source{where} has a key {key!r} that YAML read as {type(key).__name__}, not a string; quote it"
                )
            _refuse_unencodable(value, f"{where}.{key}")
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _refuse_unencodable(value, f"{where}[{index}]")
    elif not (node is None or isinstance(node, (str, int, float, bool))):
        raise KeyRefusal(f"spec.source{where} is a {type(node).__name__} ({node!r}), which canonical JSON cannot carry; quote it")


def canonical_source(spec_source) -> bytes:
    """The one encoding of `spec.source` both sides hash. Changing any argument splits K from estate's."""
    _refuse_unencodable(spec_source, "")
    return json.dumps(spec_source, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def parse_application(application: bytes | None) -> dict | None:
    """The Application as a mapping with a `spec` mapping, or None (⊥) when it is absent or does not parse."""
    if application is None:
        return None
    try:
        document = yaml.safe_load(application)
    except yaml.YAMLError:
        return None
    if not isinstance(document, dict) or not isinstance(document.get("spec"), dict):
        return None
    return document


def render_key(table: bytes | None, application: bytes | None) -> str | None:
    """K, or None (⊥) when either file is absent or the Application does not parse.

    Raises `KeyRefusal` when `spec.source` is not a mapping or holds a non-string
    key or a value JSON cannot carry: estate refuses the same inputs, so the
    two sides never hash a silently coerced source.
    """
    document = parse_application(application)
    if table is None or document is None:
        return None
    source = document["spec"].get("source")
    if not isinstance(source, dict):
        raise KeyRefusal(f"{APPLICATION} has no mapping at spec.source")
    return hashlib.sha256(table + canonical_source(source)).hexdigest()


# ── git, read-only ───────────────────────────────────────────────────────────


def _git(repo: Path, args: list[str], runner: Callable) -> subprocess.CompletedProcess:
    # GIT_DIR and friends override `-C`; a pre-commit hook exports them.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    try:
        return runner(["git", "-C", str(repo), *args], capture_output=True, check=False, env=env)
    except FileNotFoundError as error:
        raise InfrastructureError("no git binary on PATH") from error


def read_at(repo: Path, sha: str, path: str, runner: Callable) -> bytes | None:
    """The bytes of `path` at `sha`, None when the commit does not hold it. Any other git failure raises."""
    exists = _git(repo, ["cat-file", "-e", f"{sha}^{{commit}}"], runner)
    if exists.returncode != 0:
        raise InfrastructureError(f"commit {sha} is not in {repo}")
    listed = _git(repo, ["ls-tree", "--name-only", sha, "--", path], runner)
    if listed.returncode != 0:
        raise InfrastructureError(f"`git ls-tree {sha} -- {path}` exited {listed.returncode}")
    if not _decode(listed.stdout).strip():
        return None
    shown = _git(repo, ["show", f"{sha}:{path}"], runner)
    if shown.returncode != 0:
        raise InfrastructureError(f"`git show {sha}:{path}` exited {shown.returncode}")
    return shown.stdout if isinstance(shown.stdout, bytes) else shown.stdout.encode()


def _decode(output) -> str:
    return output.decode() if isinstance(output, bytes) else output


def render_history(repo: Path, sha: str, runner: Callable) -> list[tuple[str, list[str], str]]:
    """`(commit, parents, committer date)` for each commit at or before `sha` touching a K path, newest first.

    `--full-history` without parent rewriting, so a merge that brought a K path
    in is listed with its real parents rather than simplified away.
    """
    listed = _git(
        repo,
        ["log", "--full-history", "--topo-order", "--format=%H %P%x09%cI", sha, "--", *KEY_PATHS],
        runner,
    )
    if listed.returncode != 0:
        raise InfrastructureError(f"`git log {sha} -- {' '.join(KEY_PATHS)}` exited {listed.returncode}")
    history = []
    for line in _decode(listed.stdout).splitlines():
        ids, _, committed = line.partition("\t")
        commit, *parents = ids.split()
        history.append((commit, parents, committed))
    return history


def utc(stamp: str) -> dt.datetime:
    return dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


def zulu(moment: dt.datetime) -> str:
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def find_anchor(repo: Path, sha: str, key: str, runner: Callable) -> tuple[str, str, list[str]]:
    """`(A, A's committer date, problems)`. A merge commit met on the walk is a problem, and red."""
    history = render_history(repo, sha, runner)
    if not history:
        raise InfrastructureError(f"no commit at or before {sha} touches {' or '.join(KEY_PATHS)}")
    problems: list[str] = []
    anchor, committed = history[0][0], history[0][2]
    for commit, parents, stamp in history:
        if len(parents) > 1:
            problems.append(
                f"merge commit {commit} in the history of {' and '.join(KEY_PATHS)}:"
                " argocd's main is squash-only, so this render change was never reviewed as one commit"
            )
            break
        try:
            older = render_key(read_at(repo, commit, TABLE, runner), read_at(repo, commit, APPLICATION, runner))
        except KeyRefusal:
            older = None
        if older != key:
            break
        anchor, committed = commit, stamp
    return anchor, committed, problems


# ── steps 0, 1, 3, 4 ─────────────────────────────────────────────────────────


def allow_list_problems(spec: dict) -> list[str]:
    """Step 0. `parent_render` pulls `oci://{repoURL}/{chart}`, so an unreviewed source would be pulled and templated."""
    source = spec.get("source")
    if not isinstance(source, dict):
        listed = " (it has `spec.sources`, which this gate never renders)" if "sources" in spec else ""
        return [f"allow-list: `spec.source` is not a mapping{listed}"]
    problems = []
    if source.get("repoURL") != ALLOWED_REPO_URL:
        problems.append(f"allow-list: spec.source.repoURL is {source.get('repoURL')!r}, not {ALLOWED_REPO_URL!r}")
    if source.get("chart") != ALLOWED_CHART:
        problems.append(f"allow-list: spec.source.chart is {source.get('chart')!r}, not {ALLOWED_CHART!r}")
    for key in FORBIDDEN_SOURCE_KEYS:
        if key in source:
            problems.append(f"allow-list: spec.source.{key} is present; this gate renders a published chart only")
    if "sources" in spec:
        problems.append("allow-list: `spec.sources` is present beside `spec.source`")
    helm = source.get("helm")
    if not isinstance(helm, dict) or set(helm) != {"valuesObject"}:
        held = sorted(helm) if isinstance(helm, dict) else helm
        problems.append(f"allow-list: spec.source.helm holds {held}; the gate renders `valuesObject` alone")
    return problems


def parse_table(table: bytes) -> tuple[str | None, dict[str, str], list[str]]:
    """`(first data line's targetRevision, {object: digest}, problems)`, the layout `test_yadgar_application.py` writes."""
    revision, objects, problems = None, {}, []
    first = True
    for line in table.decode("utf-8", errors="replace").splitlines():
        if not line or line.startswith("#"):
            continue
        key, _, rest = line.partition("  ")
        if first:
            first = False
            if key == "targetRevision":
                revision = rest
                continue
            problems.append(f"{TABLE}: the first data line is {line!r}, not `targetRevision  <pin>`")
        if key == "targetRevision":
            problems.append(f"{TABLE}: a second `targetRevision` line, {line!r}")
        elif rest in objects:
            problems.append(f"{TABLE}: {rest} is listed twice")
        else:
            objects[rest] = key
    return revision, objects, problems


def consistency_problems(pin, table_revision: str | None, chart_pin: bytes | None) -> list[str]:
    """Step 1: the three things a pin bump moves together, all at P."""
    if not isinstance(pin, str) or not pin:
        return [f"{APPLICATION} spec.source.targetRevision is {pin!r}, not a version string; quote it"]
    problems = []
    if table_revision is not None and table_revision != pin:
        problems.append(f"{TABLE} says targetRevision {table_revision}, but {APPLICATION} pins {pin}")
    if chart_pin is None:
        problems.append(f"{CHART_PIN} is absent")
    else:
        try:
            tag = json.loads(chart_pin).get("chart_tag")
        except (ValueError, AttributeError):
            tag = None
            problems.append(f"{CHART_PIN} does not parse as a JSON object")
        if tag is not None and tag != f"v{pin}":
            problems.append(f"{CHART_PIN} chart_tag is {tag}, but {APPLICATION} pins {pin} (wanted v{pin})")
    return problems


def image_digest(image: str) -> str | None:
    """`sha256:<64 hex>` for `<repository>@sha256:<64 hex>`, else None: the image is tag-pinned."""
    match = DIGEST_IMAGE.fullmatch(image or "")
    return match.group("digest") if match else None


def rendered_deployments(documents: list) -> dict[str, dict]:
    """R: every `apps/Deployment`, each container and init container by name, with its image and digest."""
    found: dict[str, dict] = {}
    for document in documents:
        if not isinstance(document, dict) or document.get("kind") != "Deployment":
            continue
        if not str(document.get("apiVersion", "")).startswith("apps/"):
            continue
        pod = ((document.get("spec") or {}).get("template") or {}).get("spec") or {}
        found[document["metadata"]["name"]] = {
            field: {
                container["name"]: {"image": container.get("image"), "digest": image_digest(container.get("image"))}
                for container in pod.get(field) or []
            }
            for field in ("containers", "initContainers")
        }
    return found


def tag_pinned(deployments: dict[str, dict]) -> list[str]:
    """Every tag-pinned image's repository, so the run can say whose digest is not asserted."""
    return sorted(
        {
            entry["image"].rsplit(":", 1)[0] if entry["image"] else "<no image>"
            for containers in deployments.values()
            for field in containers.values()
            for entry in field.values()
            if entry["digest"] is None
        }
    )


def is_digest_pinned(containers: dict) -> bool:
    entries = [entry for field in containers.values() for entry in field.values()]
    return bool(entries) and all(entry["digest"] for entry in entries)


def set_problems(deployments: dict[str, dict]) -> list[str]:
    """Step 4."""
    if not deployments:
        return ["R is empty: the render holds no Deployment"]
    if not any(is_digest_pinned(containers) for containers in deployments.values()):
        return [f"no Deployment in R is digest-pinned: {sorted(deployments)}"]
    return []


def table_problems(table: dict[str, str], rendered: dict[str, str]) -> list[str]:
    """Step 3, every object named."""
    problems = [f"render vs {TABLE}: {key} removed (in the table, not rendered)" for key in sorted(table.keys() - rendered.keys())]
    problems += [f"render vs {TABLE}: {key} added (rendered, not in the table)" for key in sorted(rendered.keys() - table.keys())]
    problems += [
        f"render vs {TABLE}: {key} changed (table {table[key][:12]}…, render {rendered[key][:12]}…)"
        for key in sorted(table.keys() & rendered.keys())
        if table[key] != rendered[key]
    ]
    return problems


# ── the render, imported from the gates, never copied ────────────────────────


def _gate_module(name: str):
    if str(GATES) not in sys.path:
        sys.path.insert(0, str(GATES))
    return __import__(name)


def gate_digests() -> Callable[[list], dict[str, str]]:
    return _gate_module("test_yadgar_application").digests


def gate_parent_render() -> Callable:
    return _gate_module("test_no_two_owners").parent_render


def default_render(application: bytes) -> list:
    """`parent_render` over a one-file tree holding the data at S. A helm failure is infrastructure."""
    parent_render = gate_parent_render()
    with tempfile.TemporaryDirectory() as scratch:
        tree = Path(scratch)
        (tree / "applications").mkdir()
        (tree / APPLICATION).write_bytes(application)
        try:
            return parent_render(tree)
        except AssertionError as error:
            raise InfrastructureError(f"the render failed: {error}") from error


# ── derive ───────────────────────────────────────────────────────────────────


def derive(
    repo: Path,
    sha: str,
    *,
    render: Callable[[bytes], list] | None = None,
    runner: Callable = subprocess.run,
) -> dict:
    """The derivation at `sha`, or a red. Raises `InfrastructureError` for a failure of the run."""
    render = render or default_render
    application = read_at(repo, sha, APPLICATION, runner)
    if application is None:
        raise InfrastructureError(f"{APPLICATION} is absent at {sha}")
    document = parse_application(application)
    if document is None:
        raise InfrastructureError(f"{APPLICATION} at {sha} does not parse as an Application with a `spec`")
    table = read_at(repo, sha, TABLE, runner)
    if table is None:
        raise InfrastructureError(f"{TABLE} is absent at {sha}")
    chart_pin = read_at(repo, sha, CHART_PIN, runner)

    spec = document["spec"]
    source = spec.get("source") if isinstance(spec.get("source"), dict) else {}
    try:
        key = render_key(table, application)
    except KeyRefusal as refusal:
        return {
            "outcome": "red",
            "problems": [f"render key refused: {refusal}", *allow_list_problems(spec)],
            "sha": sha,
            "pin": source.get("targetRevision"),
            "values": None,
            "key": None,
            "anchor": None,
            "anchor_committed_at": None,
            "deadline": None,
            "epoch": {"key": None, "anchor": None},
            "deployments": {},
            "tag_pinned": [],
        }
    anchor, committed, problems = find_anchor(repo, sha, key, runner)
    committed_at = utc(committed)
    result = {
        "sha": sha,
        "pin": source.get("targetRevision"),
        "values": (source.get("helm") or {}).get("valuesObject") if isinstance(source.get("helm"), dict) else None,
        "key": key,
        "anchor": anchor,
        "anchor_committed_at": zulu(committed_at),
        "deadline": zulu(committed_at + dt.timedelta(seconds=BUDGET_SECONDS)),
        "epoch": {"key": key, "anchor": anchor},
        "deployments": {},
        "tag_pinned": [],
    }

    table_revision, table_objects, table_shape = parse_table(table)
    problems += allow_list_problems(spec)
    problems += table_shape
    problems += consistency_problems(result["pin"], table_revision, chart_pin)
    if problems:
        return {**result, "outcome": "red", "problems": problems}

    documents = render(application)
    problems = table_problems(table_objects, gate_digests()(documents))
    if problems:
        return {**result, "outcome": "red", "problems": problems}

    deployments = rendered_deployments(documents)
    result.update(deployments=deployments, tag_pinned=tag_pinned(deployments))
    problems = set_problems(deployments)
    return {**result, "outcome": "red" if problems else "derived", "problems": problems}


# ── stale ────────────────────────────────────────────────────────────────────


def fetch_estate_runs() -> dict:
    """Estate's newest `smoke.yaml` run. Estate is public; a token, when present, only lifts the rate limit."""
    request = urllib.request.Request(ESTATE_SMOKE_RUNS, headers={"Accept": "application/vnd.github+json"})
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # a fixed https URL
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise InfrastructureError(f"GET {ESTATE_SMOKE_RUNS}: HTTP {error.code}") from error
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        raise InfrastructureError(f"GET {ESTATE_SMOKE_RUNS}: {error}") from error


def stale_problem(fetch: Callable[[], dict], now: dt.datetime, max_days: int = STALE_DAYS) -> str | None:
    """Why estate smoke counts as stale, or None. No runs and an API error are both stale: neither proves life."""
    try:
        listed = fetch()
    except InfrastructureError as error:
        return f"cannot read estate's smoke runs, so their age is unknown: {error}"
    runs = listed.get("workflow_runs") if isinstance(listed, dict) else None
    if not runs:
        return "estate has no smoke.yaml run at all"
    newest = runs[0]
    age = now - utc(newest["created_at"])
    if age > dt.timedelta(days=max_days):
        return (
            f"estate's newest smoke.yaml run ({newest.get('html_url')}, {newest['created_at']}) is {age.days} days old,"
            f" past {max_days}: GitHub disables a public repository's schedules after 60 idle days"
        )
    print(f"estate's newest smoke.yaml run is {age.days} days old ({newest['created_at']}); the limit is {max_days}")
    return None


# ── judge: one set of cluster reads ──────────────────────────────────────────

TRACKING_ID = "argocd.argoproj.io/tracking-id"
APPLICATION_NAME = "yadgar"
TARGET_NAMESPACE = "yadgar"
TERMINAL_POD_PHASES = frozenset({"Failed", "Succeeded"})
VERDICT_ARTIFACT = "settled-verdict"
TRIAL_ARTIFACT = "settled-trial"
VERDICT_FILE = "verdict.json"
IMAGE_ID_DIGEST = re.compile(r"(sha256:[0-9a-f]{64})$")


def handover():
    """`verify_handover`, loaded once: its `Cluster` (the read-only kubectl gate) and `revision_state` are reused."""
    if "verify_handover" not in sys.modules:
        scripts = str(REPOSITORY / "scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
    return __import__("verify_handover")


def _labels_match(selector: dict, labels: dict) -> bool:
    """A Kubernetes label selector, `matchLabels` and `matchExpressions`. An empty selector matches nothing here."""
    match_labels = selector.get("matchLabels") or {}
    expressions = selector.get("matchExpressions") or []
    if not match_labels and not expressions:
        return False
    if any(labels.get(key) != value for key, value in match_labels.items()):
        return False
    for expression in expressions:
        key, operator, values = expression.get("key"), expression.get("operator"), expression.get("values") or []
        if operator == "In" and labels.get(key) not in values:
            return False
        if operator == "NotIn" and key in labels and labels[key] in values:
            return False
        if operator == "Exists" and key not in labels:
            return False
        if operator == "DoesNotExist" and key in labels:
            return False
        if operator not in {"In", "NotIn", "Exists", "DoesNotExist"}:
            return False
    return True


def _tracked_by_application(deployment: dict) -> bool:
    annotation = ((deployment.get("metadata") or {}).get("annotations") or {}).get(TRACKING_ID, "")
    return annotation.split(":", 1)[0] == APPLICATION_NAME


def _image_id_digest(image_id: str | None) -> str | None:
    match = IMAGE_ID_DIGEST.search(image_id or "")
    return match.group(1) if match else None


def clause_a(derived: dict, deployments: list[dict], pods: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """`(problems, notes, pods not counted)` for Clause A: every Deployment runs what the pin renders."""
    problems: list[str] = []
    notes: list[str] = []
    not_counted: list[str] = []
    wanted = derived["deployments"]
    tracked = {d["metadata"]["name"]: d for d in deployments if _tracked_by_application(d)}
    for name in sorted(tracked.keys() - wanted.keys()):
        problems.append(f"Clause A: Deployment {name} is tracked by `{APPLICATION_NAME}` but not in R")
    for name in sorted(wanted.keys() - tracked.keys()):
        problems.append(f"Clause A: Deployment {name} is in R but missing from namespace {TARGET_NAMESPACE}")

    for name in sorted(wanted.keys() & tracked.keys()):
        rendered = wanted[name]
        live = tracked[name]
        template = ((live.get("spec") or {}).get("template") or {}).get("spec") or {}
        for field in ("containers", "initContainers"):
            live_images = {c["name"]: c.get("image") for c in template.get(field) or []}
            rendered_images = {c: entry["image"] for c, entry in rendered[field].items()}
            if live_images != rendered_images:
                problems.append(
                    f"Clause A: Deployment {name} pod template {field}: wanted {rendered_images}, found {live_images}"
                )
            for container, entry in rendered[field].items():
                if entry["digest"] is None:
                    notes.append(f"Deployment {name} container {container} is tag-pinned ({entry['image']}); its digest is not asserted")

        selector = (live.get("spec") or {}).get("selector") or {}
        matched = [p for p in pods if _labels_match(selector, (p.get("metadata") or {}).get("labels") or {})]
        counted = []
        for pod in matched:
            pod_name = pod["metadata"]["name"]
            phase = (pod.get("status") or {}).get("phase")
            if phase in TERMINAL_POD_PHASES:
                not_counted.append(pod_name)
                notes.append(f"pod {pod_name} of Deployment {name} is {phase}; not counted")
            else:
                counted.append(pod)
        if not counted:
            problems.append(f"Clause A: Deployment {name} has no pod to count")
        for pod in counted:
            problems += _pod_problems(name, rendered, pod)
    return problems, notes, not_counted


def _pod_problems(name: str, rendered: dict, pod: dict) -> list[str]:
    pod_name = pod["metadata"]["name"]
    status = pod.get("status") or {}
    problems = []
    if pod["metadata"].get("deletionTimestamp"):
        problems.append(f"Clause A: Deployment {name} pod {pod_name} is terminating (deletionTimestamp set) and may still serve")
    if status.get("phase") != "Running":
        problems.append(f"Clause A: Deployment {name} pod {pod_name} is {status.get('phase')}, not Running")
    spec_images = {
        c["name"]: c.get("image")
        for field in ("containers", "initContainers")
        for c in (pod.get("spec") or {}).get(field) or []
    }
    for field, status_field in (("containers", "containerStatuses"), ("initContainers", "initContainerStatuses")):
        statuses = {c.get("name"): c for c in status.get(status_field) or []}
        for container, entry in rendered[field].items():
            if entry["digest"] is None:
                if spec_images.get(container) != entry["image"]:
                    problems.append(
                        f"Clause A: Deployment {name} pod {pod_name} container {container} (tag-pinned):"
                        f" wanted image {entry['image']}, found {spec_images.get(container)}"
                    )
                continue
            if container not in statuses:
                problems.append(f"Clause A: Deployment {name} pod {pod_name} container {container}: no status in {status_field}")
                continue
            found = _image_id_digest(statuses[container].get("imageID"))
            if found != entry["digest"]:
                problems.append(
                    f"Clause A: Deployment {name} pod {pod_name} container {container}: wanted {entry['digest']}, found {found}"
                )
    return problems


def _parsed(value):
    """Values as parsed objects: a YAML string is parsed, anything else round-trips through JSON."""
    if isinstance(value, str):
        try:
            value = yaml.safe_load(value)
        except yaml.YAMLError:
            return value
    return json.loads(json.dumps(value))


def clause_b(derived: dict, root: dict, application: dict, deployments: list[dict], root_sha: str, is_ancestor) -> list[str]:
    """Clause B: root at S, the Application Synced at P with V and a Succeeded operation, every rollout complete."""
    problems: list[str] = []
    state, message = handover().revision_state(root, root_sha, is_ancestor)
    if state != "done":
        problems.append(f"Clause B: root is not Synced at {root_sha} or a descendant: {message}")

    pin = derived["pin"]
    spec_source = (application.get("spec") or {}).get("source") or {}
    status = application.get("status") or {}
    sync = status.get("sync") or {}
    compared = (sync.get("comparedTo") or {}).get("source") or {}
    if spec_source.get("targetRevision") != pin:
        problems.append(f"Clause B: {APPLICATION_NAME} spec.source.targetRevision is {spec_source.get('targetRevision')}, wanted {pin}")
    if sync.get("revision") != pin:
        found = sync.get("revision") if "revision" in sync else f"absent (revisions: {sync.get('revisions')})"
        problems.append(f"Clause B: {APPLICATION_NAME} status.sync.revision is {found}, wanted {pin}")
    if compared.get("targetRevision") != pin:
        problems.append(
            f"Clause B: {APPLICATION_NAME} status.sync.comparedTo.source.targetRevision is {compared.get('targetRevision')}, wanted {pin}"
        )
    if _parsed((compared.get("helm") or {}).get("valuesObject")) != _parsed(derived["values"]):
        problems.append(f"Clause B: {APPLICATION_NAME} status.sync.comparedTo.source.helm.valuesObject differs from V")
    if sync.get("status") != "Synced":
        pruning = [
            f"{r.get('group', '')}/{r.get('kind')}/{r.get('namespace', '')}/{r.get('name')}"
            for r in status.get("resources") or []
            if r.get("requiresPruning") and r.get("status") == "OutOfSync"
        ]
        why = f"; each of these requires pruning (ADR-0851: delete it by hand): {pruning}" if pruning else ""
        problems.append(f"Clause B: {APPLICATION_NAME} status.sync.status is {sync.get('status')}, wanted Synced{why}")
    health = (status.get("health") or {}).get("status")
    if health != "Healthy":
        problems.append(f"Clause B: {APPLICATION_NAME} status.health.status is {health}, wanted Healthy")
    phase = (status.get("operationState") or {}).get("phase")
    if phase != "Succeeded":
        problems.append(
            f"Clause B: {APPLICATION_NAME} operationState.phase is {phase}, wanted Succeeded (a failed PostSync hook reads Synced and Healthy)"
        )

    by_name = {d["metadata"]["name"]: d for d in deployments}
    for name in sorted(derived["deployments"]):
        if name in by_name:
            problems += _rollout_problems(name, by_name[name])
    return problems


def _rollout_problems(name: str, deployment: dict) -> list[str]:
    status = deployment.get("status") or {}
    generation = (deployment.get("metadata") or {}).get("generation")
    problems = []
    if status.get("observedGeneration") != generation:
        problems.append(f"Clause B: Deployment {name} observedGeneration {status.get('observedGeneration')} != generation {generation}")
    counts = {field: status.get(field, 0) for field in ("replicas", "updatedReplicas", "readyReplicas", "availableReplicas")}
    if len(set(counts.values())) != 1:
        problems.append(f"Clause B: Deployment {name} replica counts differ: {counts}")
    if status.get("unavailableReplicas"):
        problems.append(f"Clause B: Deployment {name} unavailableReplicas is {status['unavailableReplicas']}")
    progressing = next((c for c in status.get("conditions") or [] if c.get("type") == "Progressing"), {})
    if progressing.get("reason") != "NewReplicaSetAvailable":
        problems.append(f"Clause B: Deployment {name} Progressing reason is {progressing.get('reason')}, wanted NewReplicaSetAvailable")
    return problems


def judge(derived: dict, cluster, *, root_sha: str, now: dt.datetime, trial: bool = False, is_ancestor=None) -> dict:
    """One set of reads, judged once. `result` is green, pending or red. A refused or failed read raises.

    A red derivation is red with NO cluster read. Every clause is evaluated and
    every problem reported, never only the first. Trial mode treats the
    deadline as passed, so a not-settled read is red, not pending.
    """
    verdict = {
        "sha": root_sha,
        "data_sha": derived["sha"],
        "trial": trial,
        "pin": derived["pin"],
        "key": derived["key"],
        "anchor": derived["anchor"],
        "epoch": derived["epoch"],
        "anchor_committed_at": derived["anchor_committed_at"],
        "deadline": derived["deadline"],
        "digests": {
            name: {c: e["digest"] for field in containers.values() for c, e in field.items() if e["digest"]}
            for name, containers in derived["deployments"].items()
        },
        "tag_pinned": derived["tag_pinned"],
        "read_at": zulu(now),
        "notes": [],
        "not_counted": [],
    }
    if derived["outcome"] == "red":
        return {**verdict, "result": "red", "problems": list(derived["problems"])}
    verify_handover = handover()
    try:
        root = cluster.json("get", "application", "root", "-n", "argocd")
        application = cluster.json("get", "application", APPLICATION_NAME, "-n", "argocd")
        deployments = cluster.json("get", "deployments", "-n", TARGET_NAMESPACE).get("items") or []
        pods = cluster.json("get", "pods", "-n", TARGET_NAMESPACE).get("items") or []
    except (verify_handover.KubectlError, verify_handover.UsageError, ValueError) as error:
        raise InfrastructureError(f"a cluster read failed: {error}") from error
    problems, notes, not_counted = clause_a(derived, deployments, pods)
    problems += clause_b(derived, root, application, deployments, root_sha, is_ancestor)
    verdict.update(notes=notes, not_counted=not_counted, problems=problems)
    if not problems:
        return {**verdict, "result": "green"}
    expired = trial or now > utc(derived["deadline"])
    return {**verdict, "result": "red" if expired else "pending"}


def run_judge(
    repo: Path,
    sha: str,
    *,
    context: str,
    out_dir: Path,
    trial_sha: str | None = None,
    now: dt.datetime | None = None,
    derive_at: Callable | None = None,
    kubectl_runner: Callable = subprocess.run,
    is_ancestor=None,
    render: Callable[[bytes], list] | None = None,
) -> int:
    """Derive at the data sha, judge once, write the verdict. 0 green or pending, 1 red, 3 infrastructure.

    A green or red verdict is written to `<out_dir>/settled-verdict/verdict.json`,
    or to `settled-trial/` in trial mode, never both. Pending and an
    infrastructure failure write nothing.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    derive_at = derive_at or (lambda r, s, **_k: derive(r, s, render=render))
    try:
        derived = derive_at(repo, trial_sha or sha)
        cluster = handover().Cluster(context, runner=kubectl_runner)
        verdict = judge(derived, cluster, root_sha=sha, now=now, trial=trial_sha is not None, is_ancestor=is_ancestor)
    except InfrastructureError as error:
        print(f"INFRASTRUCTURE FAILURE, no verdict: {error}", file=sys.stderr)
        return 3
    print(f"S {verdict['sha']}  data {verdict['data_sha']}  P {verdict['pin']}  K {verdict['key']}  A {verdict['anchor']}")
    print(f"deadline {verdict['deadline']}  read at {verdict['read_at']}  result {verdict['result']}")
    for name, containers in sorted(verdict["digests"].items()):
        print(f"  {name}: {containers}")
    for note in verdict["notes"]:
        print(f"note: {note}")
    for problem in verdict["problems"]:
        print(f"{verdict['result'].upper()}: {problem}")
    if verdict["result"] == "pending":
        return 0
    artifact = TRIAL_ARTIFACT if trial_sha is not None else VERDICT_ARTIFACT
    target = out_dir / artifact
    target.mkdir(parents=True, exist_ok=True)
    (target / VERDICT_FILE).write_text(json.dumps({**verdict, "artifact": artifact}, indent=2, sort_keys=True) + "\n")
    return 1 if verdict["result"] == "red" else 0


# ── GitHub, read-only: find-verdict and reachable ────────────────────────────

GITHUB_API = "https://api.github.com"
VERDICT_EVENTS = frozenset({"schedule", "workflow_dispatch"})


class GitHubError(InfrastructureError):
    """A GitHub API call that did not answer 2xx."""

    def __init__(self, status: int, path: str) -> None:
        super().__init__(f"GET {path}: HTTP {status}")
        self.status = status


class GitHub:
    """GET-only GitHub REST client. The token (from GITHUB_TOKEN) is never printed and never follows a redirect."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")

    def _request(self, url: str):
        request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json"})
        if self.token:
            # Unredirected: an artifact download redirects to blob storage, which must not see the token.
            request.add_unredirected_header("Authorization", f"Bearer {self.token}")
        try:
            return urllib.request.urlopen(request, timeout=30)  # only GITHUB_API and its artifact redirects
        except urllib.error.HTTPError as error:
            raise GitHubError(error.code, url.removeprefix(GITHUB_API)) from error
        except (urllib.error.URLError, TimeoutError) as error:
            raise InfrastructureError(f"GET {url.removeprefix(GITHUB_API)}: {error}") from error

    def get(self, path: str):
        with self._request(GITHUB_API + path) as response:
            return json.loads(response.read())

    def paginate(self, path: str, key: str | None = None) -> list:
        items: list = []
        separator = "&" if "?" in path else "?"
        for page in range(1, 101):
            body = self.get(f"{path}{separator}per_page=100&page={page}")
            batch = body[key] if key else body
            items += batch
            if len(batch) < 100:
                return items
        raise InfrastructureError(f"GET {path}: more than 100 pages")

    def download(self, url: str) -> bytes:
        with self._request(url) as response:
            return response.read()


def _read_verdict(archive: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as opened:
            return json.loads(opened.read(VERDICT_FILE))
    except (zipfile.BadZipFile, KeyError, ValueError) as error:
        raise InfrastructureError(f"a {VERDICT_ARTIFACT} artifact holds no readable {VERDICT_FILE}: {error}") from error


def find_verdict(github, repository: str, workflow_path: str, epoch: dict, *, exclude_run_id: int | None = None) -> dict | None:
    """The newest `settled-verdict` whose recorded epoch is `epoch`, or None.

    The same filters `verify.yaml` applies to its baseline: this repository and
    head repository, the default branch, this workflow's path, a `schedule` or
    `workflow_dispatch` run, not expired. NOT the run's conclusion: a red
    verdict's run may conclude failure, and a red verdict is still the
    verdict. `settled-trial` is never read.
    """
    own = github.get(f"/repos/{repository}")
    artifacts = github.paginate(f"/repos/{repository}/actions/artifacts?name={VERDICT_ARTIFACT}", "artifacts")
    candidates = sorted(
        (
            a
            for a in artifacts
            if a.get("name") == VERDICT_ARTIFACT
            and not a.get("expired")
            and (run := a.get("workflow_run") or {})
            and run.get("id") != exclude_run_id
            and run.get("repository_id") == own["id"]
            and run.get("head_repository_id") == own["id"]
            and run.get("head_branch") == own["default_branch"]
        ),
        key=lambda a: utc(a["created_at"]),
        reverse=True,
    )
    for candidate in candidates:
        run = github.get(f"/repos/{repository}/actions/runs/{candidate['workflow_run']['id']}")
        if (
            run.get("path") != workflow_path
            or run.get("event") not in VERDICT_EVENTS
            or (run.get("head_repository") or {}).get("id") != own["id"]
        ):
            continue
        verdict = _read_verdict(github.download(candidate["archive_download_url"]))
        if verdict.get("epoch") == epoch:
            return {"found": True, "artifact_id": candidate["id"], "run_id": run["id"], "created_at": candidate["created_at"], "verdict": verdict}
    return None


def reachable(github, repository: str, sha: str) -> str | None:
    """The first branch of `repository` that contains `sha`, or None.

    `compare/{branch}...{sha}` reads `behind` or `identical` exactly when `sha`
    is an ancestor of (or is) the branch head. Never `commits/{sha}`: GitHub
    serves commits from the whole fork network through the parent's API, so a
    fork-only sha resolves there. A 404 compare means "not on this branch".
    """
    if not FULL_SHA.fullmatch(sha):
        raise InfrastructureError(f"{sha!r} is not a full 40-hex commit sha")
    for branch in github.paginate(f"/repos/{repository}/branches"):
        try:
            status = github.get(f"/repos/{repository}/compare/{quote(branch['name'], safe='/')}...{sha}").get("status")
        except GitHubError as error:
            if error.status == 404:
                continue
            raise
        if status in {"behind", "identical"}:
            return branch["name"]
    return None


# ── CLI ──────────────────────────────────────────────────────────────────────


def _full_sha(value: str) -> str:
    if not FULL_SHA.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a full 40-hex commit sha")
    return value


def _hex64(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a 64-hex render key")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    derived = sub.add_parser("derive", help="P, V, T, K, A, E, deadline and R at one commit; exit 1 on a red")
    derived.add_argument("--repo", type=Path, default=REPOSITORY, help="the argocd checkout holding the sha")
    derived.add_argument("--sha", type=_full_sha, required=True, help="full 40-hex commit sha of the data")
    derived.add_argument("--out", type=Path, required=True, help="where the derivation (or the red) is written")
    judged = sub.add_parser("judge", help="derive, then one set of cluster reads; exit 0 green/pending, 1 red, 3 infrastructure")
    judged.add_argument("--repo", type=Path, default=REPOSITORY, help="the argocd checkout holding the sha(s)")
    judged.add_argument("--sha", type=_full_sha, required=True, help="argocd main's full sha S (root must be at it)")
    judged.add_argument("--trial-sha", type=_full_sha, help="read the DATA at this sha instead; writes settled-trial")
    judged.add_argument("--context", required=True, help="kubectl context; there is no default")
    judged.add_argument("--out-dir", type=Path, required=True, help="the verdict lands in <dir>/settled-verdict/ or settled-trial/")
    found = sub.add_parser("find-verdict", help="the newest settled-verdict for an epoch; exit 3 when the API fails")
    found.add_argument("--repository", default="yadgarhq/argocd-verify")
    found.add_argument("--workflow-path", default=".github/workflows/settled.yaml")
    found.add_argument("--key", type=_hex64, required=True)
    found.add_argument("--anchor", type=_full_sha, required=True)
    found.add_argument("--exclude-run-id", type=int, help="this run's own id")
    found.add_argument("--out", type=Path, required=True, help='the verdict found, or {"found": false}')
    reach = sub.add_parser("reachable", help="exit 0 when a full sha is on a branch of the repository itself, 1 when not")
    reach.add_argument("--repository", default="yadgarhq/argocd")
    reach.add_argument("--sha", type=_full_sha, required=True)
    sub.add_parser("stale", help=f"fail when estate's newest smoke.yaml run is older than {STALE_DAYS} days")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    render: Callable[[bytes], list] | None = None,
    runner: Callable = subprocess.run,
    fetch: Callable[[], dict] | None = None,
    now: dt.datetime | None = None,
    github=None,
) -> int:
    try:
        args = _parser().parse_args(argv)
    except SystemExit as exit_:
        return 2 if exit_.code else 0
    if args.command == "stale":
        problem = stale_problem(fetch or fetch_estate_runs, now or dt.datetime.now(dt.timezone.utc))
        if problem:
            print(f"FAIL: {problem}", file=sys.stderr)
            return 1
        return 0
    if args.command == "judge":
        verify_handover = handover()
        return run_judge(
            args.repo,
            args.sha,
            context=args.context,
            out_dir=args.out_dir,
            trial_sha=args.trial_sha,
            now=now,
            render=render,
            is_ancestor=verify_handover.git_is_ancestor(args.repo),
        )
    if args.command in {"find-verdict", "reachable"}:
        return _github_command(args, github or GitHub())
    try:
        result = derive(args.repo, args.sha, render=render, runner=runner)
    except InfrastructureError as error:
        print(f"INFRASTRUCTURE FAILURE, no verdict: {error}", file=sys.stderr)
        return 3
    args.out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"S {result['sha']}  P {result['pin']}  K {result['key']}  A {result['anchor']}  deadline {result['deadline']}")
    for problem in result["problems"]:
        print(f"RED: {problem}")
    if result["tag_pinned"]:
        print(f"tag-pinned, digest not asserted: {', '.join(result['tag_pinned'])}")
    return 1 if result["outcome"] == "red" else 0


def _github_command(args: argparse.Namespace, github) -> int:
    try:
        if args.command == "reachable":
            branch = reachable(github, args.repository, args.sha)
        else:
            epoch = {"key": args.key, "anchor": args.anchor}
            found = find_verdict(github, args.repository, args.workflow_path, epoch, exclude_run_id=args.exclude_run_id)
    except InfrastructureError as error:
        print(f"INFRASTRUCTURE FAILURE: {error}", file=sys.stderr)
        return 3
    if args.command == "reachable":
        if branch is None:
            print(f"REFUSED: {args.sha} is on no branch of {args.repository} (compare reads neither behind nor identical)")
            return 1
        print(f"{args.sha} is on {args.repository} branch {branch}")
        return 0
    args.out.write_text(json.dumps(found or {"found": False}, indent=2, sort_keys=True) + "\n")
    print(f"verdict for epoch {epoch}: " + (f"artifact {found['artifact_id']}, run {found['run_id']}, {found['verdict'].get('result')}" if found else "none"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
