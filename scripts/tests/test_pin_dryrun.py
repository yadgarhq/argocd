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
     values), a `chart_pin.json` that differs in anything but `chart_tag`, a
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
import io
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

CHART_PIN = '{{\n  "chart_tag": "v{pin}",\n  "platform_version": "0.2.6"\n}}\n'


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
    write(root, "scripts/chart_pin.json", CHART_PIN.format(pin=PIN))
    write(root, "scripts/gates/yadgar_render.sha256", table_for(base_documents(), PIN))
    write(root, ".github/workflows/ci.yaml", "name: ci\n")
    write(root, "README.md", "argocd\n")
    commit(root, "main")
    return root


def main_sha(repo: Path) -> str:
    return git(repo, "rev-parse", "main")


def pin_branch(repo: Path, name: str = "auto/pin-0.25.28", *, target: str = TARGET, table_docs: list | None = None, extra=None) -> str:
    git(repo, "checkout", "-q", "-b", name, "main")
    write(repo, "applications/yadgar.yaml", APPLICATION.format(pin=target))
    write(repo, "scripts/chart_pin.json", CHART_PIN.format(pin=target))
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


def run(repo: Path, sha: str, *, kubectl=None, render: Render | None = None, base: str | None = None) -> dict:
    cluster = verify_handover.Cluster(CONTEXT, runner=kubectl if kubectl is not None else Kubectl())
    return pin_dryrun.dry_run(repo, base or main_sha(repo), sha, cluster, render=render or Render())


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
        write(r, "scripts/chart_pin.json", CHART_PIN.format(pin=TARGET).replace("0.2.6", "0.2.7"))

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


# ── 6. find: the reader, unforgeable ────────────────────────────────────────

REPO = "yadgarhq/argocd-verify"
OWN_ID = 4242
PATH = ".github/workflows/pin-dryrun.yaml"
WANTED = "f" * 40
HEAD = "9" * 40


def archive(result: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as opened:
        opened.writestr("result.json", json.dumps(result))
    return buffer.getvalue()


class FakeGitHub:
    def __init__(self) -> None:
        self.paths: dict[str, object] = {f"/repos/{REPO}": {"id": OWN_ID, "default_branch": "main"}}
        self.downloads: dict[str, bytes] = {}
        self.artifacts: list[dict] = []
        self.reachable: set[str] = {HEAD}

    def add(self, run_id: int, *, created: str, result: str = "green", sha: str = WANTED, **run_overrides) -> None:
        run = {
            "id": run_id,
            "status": "completed",
            "event": "workflow_dispatch",
            "head_branch": "main",
            "head_sha": HEAD,
            "path": PATH,
            "repository": {"id": OWN_ID},
            "head_repository": {"id": OWN_ID},
        }
        run.update(run_overrides)
        self.paths[f"/repos/{REPO}/actions/runs/{run_id}"] = run
        url = f"https://api.github.com/artifacts/{run_id}/zip"
        self.downloads[url] = archive({"sha": sha, "result": result, "objects": []})
        self.artifacts.append(
            {
                "id": run_id * 10,
                "name": "pin-dryrun",
                "expired": False,
                "created_at": created,
                "archive_download_url": url,
                "workflow_run": {"id": run_id, "repository_id": OWN_ID, "head_repository_id": OWN_ID, "head_branch": "main", "head_sha": HEAD},
            }
        )

    def get(self, path: str):
        if path.startswith(f"/repos/{REPO}/actions/artifacts?"):
            assert "name=pin-dryrun" in path
            return {"artifacts": self.artifacts if "page=1" in path else []}
        if path.startswith(f"/repos/{REPO}/compare/main..."):
            sha = path.rsplit("...", 1)[1]
            if sha in self.reachable:
                return {"status": "behind"}
            raise pin_dryrun.NotFound(path)
        if path in self.paths:
            return self.paths[path]
        raise pin_dryrun.NotFound(path)

    def download(self, url: str) -> bytes:
        return self.downloads[url]


def find(github: FakeGitHub) -> dict | None:
    return pin_dryrun.find(github, REPO, PATH, WANTED)


def test_find_takes_the_newest_result_for_the_sha() -> None:
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z", result="red")
    github.add(2, created="2026-10-19T09:00:00Z", result="green")
    found = find(github)
    assert found is not None and found["run_id"] == 2 and found["result"]["result"] == "green"


def test_find_lets_a_newer_red_win_over_an_older_green() -> None:
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z", result="green")
    github.add(2, created="2026-10-19T09:00:00Z", result="red")
    assert find(github)["result"]["result"] == "red"


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
        {"status": "in_progress"},
        # A TAG NAMED `main` (argocd#111 S1): head_branch reads "main", the
        # tag's copy of the workflow ran, and its head_sha is on no branch.
        {"head_sha": "7" * 40},
    ],
)
def test_find_ignores_a_forgeable_run(overrides: dict) -> None:
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z", result="green", **overrides)
    assert find(github) is None


def test_find_ignores_an_expired_artifact_and_another_shas_result() -> None:
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z", sha="0" * 40)
    github.add(2, created="2026-10-19T09:00:00Z")
    github.artifacts[1]["expired"] = True
    assert find(github) is None


def test_find_never_reads_commits_of_a_sha() -> None:
    github = FakeGitHub()
    seen: list[str] = []
    original = github.get

    def get(path: str):
        seen.append(path)
        return original(path)

    github.get = get
    github.add(1, created="2026-10-19T08:00:00Z")
    find(github)
    assert not any("/commits/" in p for p in seen)


def test_the_find_cli_exits_0_only_on_green(tmp_path: Path) -> None:
    github = FakeGitHub()
    github.add(1, created="2026-10-19T08:00:00Z", result="red")
    out = tmp_path / "found.json"
    assert pin_dryrun.main(["find", "--sha", WANTED, "--out", str(out)], github=github) == 1
    assert json.loads(out.read_text())["result"]["result"] == "red"
    github.add(2, created="2026-10-19T09:00:00Z", result="green")
    assert pin_dryrun.main(["find", "--sha", WANTED, "--out", str(out)], github=github) == 0
    assert pin_dryrun.main(["find", "--sha", "1" * 40, "--out", str(out)], github=github) == 1
    assert json.loads(out.read_text()) == {"found": False}


# ── 7. the reader imports without PyYAML ────────────────────────────────────


def test_the_module_imports_without_pyyaml() -> None:
    code = "import sys; sys.modules['yaml'] = None; sys.path.insert(0, sys.argv[1]); import pin_dryrun; print('ok')"
    finished = subprocess.run([sys.executable, "-c", code, str(SCRIPTS)], capture_output=True, text=True, check=False)
    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.strip() == "ok"
