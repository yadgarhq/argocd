#!/usr/bin/env python3
"""The settled-state gate's derivation: which render epoch argocd `main` names (ledger 675, stage 2a).

    settled_gate.py derive --repo DIR --sha SHA --out FILE
    settled_gate.py stale

THE PLAN IS `yadgarhq/docs` `plans/settled-state-smoke-gate.md`, "The
settled-state definition" and "How the digests are derived". This file is
stage 2a: `derive`. Stage 2b adds `judge` (one set of cluster reads),
`find-verdict` and `reachable`. The private argocd-verify workflow runs this
file only at argocd `main`, against data at a full commit sha (ADR-0844 (1)).

WHAT `derive` COMPUTES, all from the data files at one commit `S`:

  P  `spec.source.targetRevision` of `applications/yadgar.yaml`.
  V  `spec.source.helm.valuesObject` of the same file.
  T  the bytes of `scripts/gates/yadgar_render.sha256`.
  K  sha256(T + canonical JSON of `spec.source`), the JSON being
     `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
     over ONE `yaml.safe_load` (PyYAML 6.0.3, so YAML 1.1: `on` is true and
     `0755` is 493). Estate's verdict job computes the same K from the same
     bytes; the shared vectors are in `scripts/tests/test_settled_gate.py`.
  A  the anchor: walking the history of the two files K reads newest-first,
     the last commit whose K still equals the current K. When history runs
     out, the oldest listed commit. A commit where K cannot be computed (a
     file absent, or the Application not a mapping that parses) differs from
     every K, so it ends the walk. A merge commit in the walk is red: argocd's
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

`stale` FAILS when estate's newest `smoke.yaml` run is older than 45 days, or
when there is none, or when the API cannot answer. GitHub disables a public
repository's scheduled workflows after 60 days without activity, and nothing
else would notice smoke going silent.

Exit codes: 0 derived (or fresh), 1 red (or stale), 2 usage, 3 infrastructure.
Standard library plus PyYAML; `derive`'s default render also needs pytest
importable, because it imports the gate modules rather than copying them.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

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


# ── K ────────────────────────────────────────────────────────────────────────


def canonical_source(spec_source) -> bytes:
    """The one encoding of `spec.source` both sides hash. Changing any argument splits K from estate's."""
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

    `spec.source` absent hashes as JSON `null`: the key still exists, and step 0
    refuses the source.
    """
    document = parse_application(application)
    if table is None or document is None:
        return None
    return hashlib.sha256(table + canonical_source(document["spec"].get("source"))).hexdigest()


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
        if render_key(read_at(repo, commit, TABLE, runner), read_at(repo, commit, APPLICATION, runner)) != key:
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
    key = render_key(table, application)
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


# ── CLI ──────────────────────────────────────────────────────────────────────


def _full_sha(value: str) -> str:
    if not FULL_SHA.fullmatch(value):
        raise argparse.ArgumentTypeError(f"{value!r} is not a full 40-hex commit sha")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    derived = sub.add_parser("derive", help="P, V, T, K, A, E, deadline and R at one commit; exit 1 on a red")
    derived.add_argument("--repo", type=Path, default=REPOSITORY, help="the argocd checkout holding the sha")
    derived.add_argument("--sha", type=_full_sha, required=True, help="full 40-hex commit sha of the data")
    derived.add_argument("--out", type=Path, required=True, help="where the derivation (or the red) is written")
    sub.add_parser("stale", help=f"fail when estate's newest smoke.yaml run is older than {STALE_DAYS} days")
    return parser


def main(
    argv: list[str] | None = None,
    *,
    render: Callable[[bytes], list] | None = None,
    runner: Callable = subprocess.run,
    fetch: Callable[[], dict] | None = None,
    now: dt.datetime | None = None,
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


if __name__ == "__main__":
    sys.exit(main())
