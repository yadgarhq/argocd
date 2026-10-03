"""`scripts/settled_gate.py judge`, `find-verdict` and `reachable` (ledger 675, stage 2b).

No cluster and no network. `judge` reads the cluster through
`verify_handover.Cluster`, answered by a fake kubectl runner from objects built
here; an argument list the fake does not hold answers as a kubectl failure, as
in `test_verify_handover.py`. `find-verdict` and `reachable` read GitHub
through a fake API object; a path it does not hold answers HTTP 404.

WHAT IS ASSERTED, and each has a red case below:

  1. `judge` makes ONE set of reads and ends in one of four outcomes: green
     (verdict written), pending (nothing written, before the deadline), red
     (verdict written), or an infrastructure failure (nothing written, exit 3).
     A derivation that is red is red with no cluster read at all.
  2. Clause A: the tracked Deployments equal R by name; the live pod template
     images equal R's; every counted pod runs R's digest for each container
     and init container by name; terminating pods are counted and fail;
     `Failed`/`Succeeded` pods are listed and not counted; a missing status
     fails; zero pods fail; a tag-pinned container's pod image must equal the
     rendered string.
  3. Clause B: root Synced at S or a descendant, no operation running; the
     `yadgar` Application has P in all three fields, V in `comparedTo` as
     parsed objects, `Synced`, `Healthy`, and `operationState.phase`
     `Succeeded` (a failed PostSync hook is not settled); each Deployment's
     rollout is complete. An OutOfSync Application names the objects that
     require pruning (ADR-0851: flagged, not tolerated).
  4. Every clause is reported in one result, never only the first.
  5. Trial mode reads data at another sha, treats the deadline as passed, and
     writes `settled-trial`, never `settled-verdict`.
  6. `find-verdict` takes the newest `settled-verdict` for the epoch from
     this repository's default branch and this workflow's path, whatever the
     run's conclusion; it ignores other branches, fork head repositories,
     `settled-trial`, other workflows, other events and expired artifacts.
  7. `reachable` accepts a sha only when `compare/{branch}...{sha}` says
     `behind` or `identical` for some branch, and never asks `commits/{sha}`,
     which GitHub answers from the whole fork network.
"""

from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("settled_gate", REPOSITORY / "scripts" / "settled_gate.py")
sg = importlib.util.module_from_spec(_spec)
sys.modules["settled_gate"] = sg
_spec.loader.exec_module(sg)

CONTEXT = "kind-test"
S = "1" * 40
LATER = "2" * 40
TRIAL = "3" * 40
P = "0.3.38"
OLD_P = "0.3.13"
V = {"gateway": {"image": "x", "replicas": 2}, "tls": {"enabled": True}}
MODULES = ("gateway", "iam", "iam-db", "project", "project-db", "task")
DEADLINE = "2026-10-03T13:05:00Z"
BEFORE_DEADLINE = dt.datetime(2026, 10, 3, 12, 30, tzinfo=dt.timezone.utc)
AFTER_DEADLINE = dt.datetime(2026, 10, 3, 14, 0, tzinfo=dt.timezone.utc)


def digest(character: str) -> str:
    return "sha256:" + character * 64


DIGESTS = {name: digest(c) for name, c in zip(MODULES, "abcdef")}
MIGRATE = digest("9")
VALKEY = "valkey/valkey:9.1.1"


def derivation() -> dict:
    """What `derive` returns for a settled-looking render: six modules, one init container, valkey tag-pinned."""
    deployments = {
        name: {
            "containers": {name: {"image": f"ghcr.io/yadgarhq/{name}@{DIGESTS[name]}", "digest": DIGESTS[name]}},
            "initContainers": {},
        }
        for name in MODULES
    }
    deployments["iam"]["initContainers"] = {"migrate": {"image": f"ghcr.io/yadgarhq/iam@{MIGRATE}", "digest": MIGRATE}}
    deployments["valkey"] = {"containers": {"valkey": {"image": VALKEY, "digest": None}}, "initContainers": {}}
    return {
        "outcome": "derived",
        "problems": [],
        "sha": S,
        "pin": P,
        "values": copy.deepcopy(V),
        "key": "e" * 64,
        "anchor": S,
        "anchor_committed_at": "2026-10-03T12:00:00Z",
        "deadline": DEADLINE,
        "epoch": {"key": "e" * 64, "anchor": S},
        "deployments": deployments,
        "tag_pinned": ["valkey/valkey"],
    }


# ── the live objects of a settled estate ─────────────────────────────────────


def live_deployment(name: str, spec: dict) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "namespace": "yadgar",
            "generation": 7,
            "annotations": {"argocd.argoproj.io/tracking-id": f"yadgar:apps/Deployment:yadgar/{name}"},
        },
        "spec": {
            "replicas": 2,
            "selector": {"matchLabels": {"app.kubernetes.io/name": name}},
            "template": {
                "metadata": {"labels": {"app.kubernetes.io/name": name}},
                "spec": {
                    field: [{"name": c, "image": entry["image"]} for c, entry in spec[field].items()]
                    for field in ("containers", "initContainers")
                    if spec[field]
                },
            },
        },
        "status": {
            "observedGeneration": 7,
            "replicas": 2,
            "updatedReplicas": 2,
            "readyReplicas": 2,
            "availableReplicas": 2,
            "conditions": [
                {"type": "Available", "status": "True", "reason": "MinimumReplicasAvailable"},
                {"type": "Progressing", "status": "True", "reason": "NewReplicaSetAvailable"},
            ],
        },
    }


def live_pod(name: str, index: int, spec: dict) -> dict:
    def statuses(field: str) -> list:
        return [
            {"name": c, "image": entry["image"], "imageID": f"{entry['image'].split('@')[0]}@{entry['digest'] or digest('0')}"}
            for c, entry in spec[field].items()
        ]

    return {
        "metadata": {"name": f"{name}-{index}", "namespace": "yadgar", "labels": {"app.kubernetes.io/name": name}},
        "spec": {
            field: [{"name": c, "image": entry["image"]} for c, entry in spec[field].items()]
            for field in ("containers", "initContainers")
            if spec[field]
        },
        "status": {
            "phase": "Running",
            "containerStatuses": statuses("containers"),
            "initContainerStatuses": statuses("initContainers"),
        },
    }


def root_app(revision: str = S) -> dict:
    return {
        "metadata": {"name": "root"},
        "status": {
            "sync": {"status": "Synced", "revision": revision},
            "health": {"status": "Healthy"},
            "operationState": {"phase": "Succeeded", "syncResult": {"revision": revision}},
        },
    }


def yadgar_app() -> dict:
    return {
        "metadata": {"name": "yadgar"},
        "spec": {"source": {"repoURL": "ghcr.io/yadgarhq/charts", "chart": "yadgar", "targetRevision": P, "helm": {"valuesObject": copy.deepcopy(V)}}},
        "status": {
            "sync": {
                "status": "Synced",
                "revision": P,
                "comparedTo": {"source": {"repoURL": "ghcr.io/yadgarhq/charts", "chart": "yadgar", "targetRevision": P, "helm": {"valuesObject": copy.deepcopy(V)}}},
            },
            "health": {"status": "Healthy"},
            "operationState": {"phase": "Succeeded", "syncResult": {"revision": P}},
            "resources": [],
        },
    }


class Estate:
    """The four reads `judge` makes, as mutable objects a test can break."""

    def __init__(self) -> None:
        self.derived = derivation()
        self.root = root_app()
        self.yadgar = yadgar_app()
        self.deployments = [live_deployment(n, s) for n, s in self.derived["deployments"].items()]
        self.pods = [live_pod(n, i, s) for n, s in self.derived["deployments"].items() for i in (0, 1)]

    def deployment(self, name: str) -> dict:
        return next(d for d in self.deployments if d["metadata"]["name"] == name)

    def pods_of(self, name: str) -> list[dict]:
        return [p for p in self.pods if p["metadata"]["labels"].get("app.kubernetes.io/name") == name]

    def responses(self) -> dict:
        return {
            "get application root -n argocd -o json": self.root,
            "get application yadgar -n argocd -o json": self.yadgar,
            "get deployments -n yadgar -o json": {"items": self.deployments},
            "get pods -n yadgar -o json": {"items": self.pods},
        }


class FakeRunner:
    """Answers kubectl from a dict; records every command; a missing key is a kubectl failure."""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **_kwargs):
        self.calls.append(list(cmd))
        assert cmd[:3] == ["kubectl", "--context", CONTEXT], cmd
        key = " ".join(cmd[3:])
        if key not in self.responses:
            return subprocess.CompletedProcess(cmd, 1, "", f'Error from server (Forbidden): no fixture for "{key}"')
        return subprocess.CompletedProcess(cmd, 0, json.dumps(self.responses[key]), "")


@pytest.fixture
def estate() -> Estate:
    return Estate()


def judge(estate: Estate, *, now=BEFORE_DEADLINE, trial: bool = False, is_ancestor=None) -> tuple[dict, FakeRunner]:
    runner = FakeRunner(estate.responses())
    cluster = sg.handover().Cluster(CONTEXT, runner=runner)
    verdict = sg.judge(estate.derived, cluster, root_sha=S, now=now, trial=trial, is_ancestor=is_ancestor)
    return verdict, runner


def problems_of(verdict: dict) -> str:
    return "\n".join(verdict["problems"])


# ── 1. the four outcomes ─────────────────────────────────────────────────────


def test_a_settled_estate_is_green(estate: Estate) -> None:
    verdict, runner = judge(estate)
    assert verdict["result"] == "green", verdict["problems"]
    assert verdict["problems"] == []
    assert verdict["digests"]["gateway"] == {"gateway": DIGESTS["gateway"]}
    assert verdict["digests"]["iam"] == {"iam": DIGESTS["iam"], "migrate": MIGRATE}
    assert verdict["tag_pinned"] == ["valkey/valkey"]
    assert verdict["epoch"] == estate.derived["epoch"]
    assert verdict["read_at"] == "2026-10-03T12:30:00Z"
    assert len(runner.calls) == 4


def test_the_reads_are_get_only(estate: Estate) -> None:
    _, runner = judge(estate)
    assert all(call[3] == "get" and call[-2:] == ["-o", "json"] for call in runner.calls), runner.calls


def test_a_red_derivation_is_red_with_no_cluster_read(estate: Estate) -> None:
    estate.derived.update(outcome="red", problems=["render vs table: apps/Deployment//gateway changed"])
    verdict, runner = judge(estate)
    assert verdict["result"] == "red"
    assert "apps/Deployment//gateway" in problems_of(verdict)
    assert runner.calls == []


def test_a_not_settled_read_before_the_deadline_is_pending(estate: Estate) -> None:
    estate.yadgar["status"]["operationState"]["phase"] = "Running"
    verdict, _ = judge(estate, now=BEFORE_DEADLINE)
    assert verdict["result"] == "pending"
    assert "Running" in problems_of(verdict)


def test_the_same_read_after_the_deadline_is_red(estate: Estate) -> None:
    estate.yadgar["status"]["operationState"]["phase"] = "Running"
    verdict, _ = judge(estate, now=AFTER_DEADLINE)
    assert verdict["result"] == "red"


def test_a_refused_read_is_an_infrastructure_failure(estate: Estate) -> None:
    responses = estate.responses()
    del responses["get pods -n yadgar -o json"]
    cluster = sg.handover().Cluster(CONTEXT, runner=FakeRunner(responses))
    with pytest.raises(sg.InfrastructureError, match="Forbidden"):
        sg.judge(estate.derived, cluster, root_sha=S, now=BEFORE_DEADLINE)


# ── 2. Clause A ──────────────────────────────────────────────────────────────


def test_a_pod_on_another_digest_fails_clause_a(estate: Estate) -> None:
    estate.pods_of("task")[1]["status"]["containerStatuses"][0]["imageID"] = f"ghcr.io/yadgarhq/task@{digest('0')}"
    text = problems_of(judge(estate)[0])
    assert "Clause A" in text and "task-1" in text and DIGESTS["task"] in text and digest("0") in text


def test_a_terminating_pod_on_the_old_digest_fails_clause_a(estate: Estate) -> None:
    old = live_pod("gateway", 9, {"containers": {"gateway": {"image": "ghcr.io/yadgarhq/gateway@" + digest("0"), "digest": digest("0")}}, "initContainers": {}})
    old["metadata"]["deletionTimestamp"] = "2026-10-03T12:29:00Z"
    estate.pods.append(old)
    text = problems_of(judge(estate)[0])
    assert "gateway-9" in text and "terminating" in text


def test_a_terminating_pod_on_the_right_digest_still_fails(estate: Estate) -> None:
    estate.pods_of("gateway")[0]["metadata"]["deletionTimestamp"] = "2026-10-03T12:29:00Z"
    verdict, _ = judge(estate)
    assert verdict["result"] != "green"
    assert "gateway-0" in problems_of(verdict)


@pytest.mark.parametrize("phase", ["Failed", "Succeeded"])
def test_a_terminal_pod_on_the_old_digest_is_listed_and_not_counted(estate: Estate, phase: str) -> None:
    evicted = live_pod("gateway", 9, {"containers": {"gateway": {"image": "ghcr.io/yadgarhq/gateway@" + digest("0"), "digest": digest("0")}}, "initContainers": {}})
    evicted["status"]["phase"] = phase
    evicted["status"]["reason"] = "Evicted"
    estate.pods.append(evicted)
    verdict, _ = judge(estate)
    assert verdict["result"] == "green", verdict["problems"]
    assert "gateway-9" in verdict["not_counted"]


def test_a_missing_init_container_status_fails_clause_a(estate: Estate) -> None:
    estate.pods_of("iam")[0]["status"]["initContainerStatuses"] = []
    text = problems_of(judge(estate)[0])
    assert "iam-0" in text and "migrate" in text and "no status" in text


def test_a_pod_that_is_not_running_fails_clause_a(estate: Estate) -> None:
    estate.pods_of("project")[0]["status"]["phase"] = "Pending"
    assert "project-0" in problems_of(judge(estate)[0])


def test_a_deployment_with_zero_pods_fails_clause_a(estate: Estate) -> None:
    estate.pods = [p for p in estate.pods if p["metadata"]["labels"]["app.kubernetes.io/name"] != "task-db"]
    estate.deployments.append(live_deployment("task-db", {"containers": {"task-db": {"image": "i@" + digest("7"), "digest": digest("7")}}, "initContainers": {}}))
    estate.derived["deployments"]["task-db"] = {"containers": {"task-db": {"image": "i@" + digest("7"), "digest": digest("7")}}, "initContainers": {}}
    text = problems_of(judge(estate)[0])
    assert "task-db" in text and "no pod" in text


def test_an_extra_tracked_deployment_fails_clause_a(estate: Estate) -> None:
    estate.deployments.append(live_deployment("ghost", {"containers": {"ghost": {"image": "i@" + digest("7"), "digest": digest("7")}}, "initContainers": {}}))
    text = problems_of(judge(estate)[0])
    assert "ghost" in text and "not in R" in text


def test_a_missing_tracked_deployment_fails_clause_a(estate: Estate) -> None:
    estate.deployments = [d for d in estate.deployments if d["metadata"]["name"] != "project-db"]
    text = problems_of(judge(estate)[0])
    assert "project-db" in text and "missing" in text


def test_a_deployment_another_application_tracks_is_not_counted(estate: Estate) -> None:
    other = live_deployment("runner", {"containers": {"runner": {"image": "r:1", "digest": None}}, "initContainers": {}})
    other["metadata"]["annotations"]["argocd.argoproj.io/tracking-id"] = "other-app:apps/Deployment:yadgar/runner"
    untracked = live_deployment("manual", {"containers": {"manual": {"image": "m:1", "digest": None}}, "initContainers": {}})
    del untracked["metadata"]["annotations"]
    estate.deployments += [other, untracked]
    assert judge(estate)[0]["result"] == "green"


def test_a_live_template_image_other_than_the_render_fails_clause_a(estate: Estate) -> None:
    estate.deployment("iam-db")["spec"]["template"]["spec"]["containers"][0]["image"] = "ghcr.io/yadgarhq/iam-db@" + digest("0")
    text = problems_of(judge(estate)[0])
    assert "iam-db" in text and "template" in text


def test_valkey_is_reported_tag_pinned_and_a_changed_tag_fails(estate: Estate) -> None:
    green, _ = judge(estate)
    assert green["result"] == "green"
    assert any("valkey" in note and "tag-pinned" in note for note in green["notes"]), green["notes"]
    estate.pods_of("valkey")[0]["spec"]["containers"][0]["image"] = "valkey/valkey:9.2.0"
    text = problems_of(judge(estate)[0])
    assert "valkey-0" in text and "valkey/valkey:9.2.0" in text


# ── 3. Clause B ──────────────────────────────────────────────────────────────


def test_a_failed_post_sync_hook_is_not_settled(estate: Estate) -> None:
    """Synced, Healthy, P in all three fields, V in comparedTo, and `operationState.phase: Failed`."""
    estate.yadgar["status"]["operationState"]["phase"] = "Failed"
    verdict, _ = judge(estate, now=AFTER_DEADLINE)
    assert verdict["result"] == "red"
    assert "operationState.phase is Failed" in problems_of(verdict)


def test_compared_values_that_differ_from_v_are_not_settled(estate: Estate) -> None:
    estate.yadgar["status"]["sync"]["comparedTo"]["source"]["helm"]["valuesObject"]["tls"]["enabled"] = False
    assert "valuesObject" in problems_of(judge(estate)[0])


def test_compared_values_differing_only_in_order_or_quoting_are_equal(estate: Estate) -> None:
    estate.yadgar["status"]["sync"]["comparedTo"]["source"]["helm"]["valuesObject"] = "tls: {enabled: true}\ngateway: {replicas: 2, image: 'x'}\n"
    assert judge(estate)[0]["result"] == "green"


def test_compared_target_revision_at_the_old_pin_fails_clause_b(estate: Estate) -> None:
    estate.yadgar["status"]["sync"]["comparedTo"]["source"]["targetRevision"] = OLD_P
    text = problems_of(judge(estate)[0])
    assert "comparedTo.source.targetRevision" in text and OLD_P in text


def test_a_spec_pin_other_than_p_fails_clause_b(estate: Estate) -> None:
    estate.yadgar["spec"]["source"]["targetRevision"] = OLD_P
    assert "spec.source.targetRevision" in problems_of(judge(estate)[0])


def test_a_multi_source_revisions_array_fails_clause_b_naming_it(estate: Estate) -> None:
    sync = estate.yadgar["status"]["sync"]
    del sync["revision"]
    sync["revisions"] = [P, "187f1593" + "0" * 32]
    text = problems_of(judge(estate)[0])
    assert "status.sync.revision" in text and "187f1593" in text


@pytest.mark.parametrize(("path", "value"), [(("sync", "status"), "OutOfSync"), (("health", "status"), "Progressing")])
def test_not_synced_or_not_healthy_fails_clause_b(estate: Estate, path, value) -> None:
    estate.yadgar["status"][path[0]][path[1]] = value
    assert value in problems_of(judge(estate)[0])


def test_out_of_sync_names_the_objects_that_require_pruning(estate: Estate) -> None:
    """ADR-0851: a `Prune=false` object left behind is flagged by name, and the read is not settled."""
    estate.yadgar["status"]["sync"]["status"] = "OutOfSync"
    estate.yadgar["status"]["resources"] = [
        {"group": "gateway.networking.k8s.io", "kind": "Gateway", "namespace": "yadgar", "name": "edge", "status": "OutOfSync", "requiresPruning": True},
        {"group": "batch", "kind": "Job", "namespace": "yadgar", "name": "preflight", "requiresPruning": True},
    ]
    text = problems_of(judge(estate)[0])
    assert "requires pruning" in text and "gateway.networking.k8s.io/Gateway/yadgar/edge" in text
    assert "preflight" not in text


def test_hook_leftovers_that_require_pruning_do_not_fail_a_synced_application(estate: Estate) -> None:
    """Measured live 2026-10-03: `yadgar` reads Synced with 13 hook objects marked `requiresPruning` and no status."""
    estate.yadgar["status"]["resources"] = [{"group": "batch", "kind": "Job", "namespace": "yadgar", "name": "preflight", "requiresPruning": True}]
    assert judge(estate)[0]["result"] == "green"


def test_root_not_yet_at_s_fails_clause_b(estate: Estate) -> None:
    estate.root = root_app("9" * 40)
    text = problems_of(judge(estate)[0])
    assert "root" in text and S in text


def test_root_at_a_descendant_of_s_passes(estate: Estate) -> None:
    estate.root = root_app(LATER)
    verdict, _ = judge(estate, is_ancestor=lambda old, new: (old, new) == (S, LATER))
    assert verdict["result"] == "green", verdict["problems"]


def test_root_running_an_operation_fails_clause_b(estate: Estate) -> None:
    estate.root["status"]["operationState"]["phase"] = "Running"
    assert "root" in problems_of(judge(estate)[0])


@pytest.mark.parametrize(
    ("mutate", "named"),
    [
        (lambda d: d["status"].update(observedGeneration=6), "observedGeneration"),
        (lambda d: d["status"].update(updatedReplicas=1), "updatedReplicas"),
        (lambda d: d["status"].update(unavailableReplicas=1), "unavailableReplicas"),
        (lambda d: d["status"].pop("availableReplicas"), "availableReplicas"),
        (lambda d: d["status"]["conditions"][1].update(reason="ReplicaSetUpdated"), "ReplicaSetUpdated"),
    ],
)
def test_an_incomplete_rollout_fails_clause_b(estate: Estate, mutate, named: str) -> None:
    mutate(estate.deployment("gateway"))
    text = problems_of(judge(estate)[0])
    assert "gateway" in text and named in text


# ── 4. every clause, in one result ───────────────────────────────────────────


def test_a_read_failing_both_clauses_names_all_six_deployments_and_the_b_arm(estate: Estate) -> None:
    """The 0.3.13 -> 0.3.38 shape: six modules on old digests, and the Application at the old pin."""
    for name in MODULES:
        for pod in estate.pods_of(name):
            pod["status"]["containerStatuses"][0]["imageID"] = f"ghcr.io/yadgarhq/{name}@{digest('0')}"
    estate.yadgar["status"]["sync"]["revision"] = OLD_P
    verdict, _ = judge(estate, now=AFTER_DEADLINE)
    clause_a = [p for p in verdict["problems"] if p.startswith("Clause A")]
    assert {name for name in MODULES if any(f"Deployment {name} " in p for p in clause_a)} == set(MODULES)
    assert any(p.startswith("Clause B") and "status.sync.revision" in p for p in verdict["problems"])


# ── 5. run_judge: what is written, and trial mode ────────────────────────────


def run(estate: Estate, out: Path, *, trial_sha=None, now=BEFORE_DEADLINE, data_derivation=None) -> tuple[int, FakeRunner]:
    runner = FakeRunner(estate.responses())
    calls = []

    def fake_derive(_repo, sha, **_kwargs):
        calls.append(sha)
        derived = copy.deepcopy(data_derivation or estate.derived)
        derived["sha"] = sha
        return derived

    code = sg.run_judge(
        Path("."), S, context=CONTEXT, out_dir=out, trial_sha=trial_sha, now=now,
        derive_at=fake_derive, kubectl_runner=runner, is_ancestor=None,
    )
    run.derived_at = calls
    return code, runner


def test_green_writes_settled_verdict(estate: Estate, tmp_path: Path) -> None:
    code, _ = run(estate, tmp_path)
    assert code == 0
    written = json.loads((tmp_path / "settled-verdict" / "verdict.json").read_text())
    assert written["result"] == "green"
    assert written["sha"] == S and written["trial"] is False
    assert not (tmp_path / "settled-trial").exists()


def test_pending_writes_nothing(estate: Estate, tmp_path: Path) -> None:
    estate.yadgar["status"]["health"]["status"] = "Progressing"
    code, _ = run(estate, tmp_path)
    assert code == 0
    assert list(tmp_path.iterdir()) == []


def test_red_writes_settled_verdict_and_exits_1(estate: Estate, tmp_path: Path) -> None:
    estate.yadgar["status"]["health"]["status"] = "Degraded"
    code, _ = run(estate, tmp_path, now=AFTER_DEADLINE)
    assert code == 1
    assert json.loads((tmp_path / "settled-verdict" / "verdict.json").read_text())["result"] == "red"


def test_an_infrastructure_failure_writes_nothing_and_exits_3(estate: Estate, tmp_path: Path) -> None:
    def broken(_repo, _sha, **_kwargs):
        raise sg.InfrastructureError("helm pull exited 1")

    code = sg.run_judge(Path("."), S, context=CONTEXT, out_dir=tmp_path, now=BEFORE_DEADLINE, derive_at=broken, kubectl_runner=FakeRunner({}))
    assert code == 3
    assert list(tmp_path.iterdir()) == []


def test_a_forbidden_read_writes_nothing_and_exits_3(estate: Estate, tmp_path: Path) -> None:
    responses = estate.responses()
    del responses["get deployments -n yadgar -o json"]

    code = sg.run_judge(
        Path("."), S, context=CONTEXT, out_dir=tmp_path, now=BEFORE_DEADLINE,
        derive_at=lambda *_a, **_k: copy.deepcopy(estate.derived), kubectl_runner=FakeRunner(responses),
    )
    assert code == 3
    assert list(tmp_path.iterdir()) == []


def test_trial_writes_settled_trial_never_settled_verdict(estate: Estate, tmp_path: Path) -> None:
    code, _ = run(estate, tmp_path, trial_sha=TRIAL)
    assert code == 0
    assert run.derived_at == [TRIAL]
    written = json.loads((tmp_path / "settled-trial" / "verdict.json").read_text())
    assert written["trial"] is True and written["data_sha"] == TRIAL and written["sha"] == S
    assert not (tmp_path / "settled-verdict").exists()


def test_trial_treats_the_deadline_as_passed(estate: Estate, tmp_path: Path) -> None:
    estate.pods_of("gateway")[0]["status"]["containerStatuses"][0]["imageID"] = "ghcr.io/yadgarhq/gateway@" + digest("0")
    code, _ = run(estate, tmp_path, trial_sha=TRIAL, now=BEFORE_DEADLINE)
    assert code == 1
    written = json.loads((tmp_path / "settled-trial" / "verdict.json").read_text())
    assert written["result"] == "red"
    assert not (tmp_path / "settled-verdict").exists()


def test_a_red_trial_derivation_never_reads_the_cluster(estate: Estate, tmp_path: Path) -> None:
    red = derivation()
    red.update(outcome="red", problems=["allow-list: spec.source.repoURL is 'ghcr.io/attacker/charts'"])
    code, runner = run(estate, tmp_path, trial_sha=TRIAL, data_derivation=red)
    assert code == 1
    assert runner.calls == []
    assert (tmp_path / "settled-trial" / "verdict.json").exists()


def test_the_judge_cli_refuses_a_short_sha(tmp_path: Path) -> None:
    for bad in ("main", "refs/pull/1/head", "abc"):
        assert sg.main(["judge", "--sha", bad, "--context", CONTEXT, "--out-dir", str(tmp_path)]) == 2
        assert sg.main(["judge", "--sha", S, "--trial-sha", bad, "--context", CONTEXT, "--out-dir", str(tmp_path)]) == 2


# ── 6. find-verdict ──────────────────────────────────────────────────────────

SELF_ID = 1001
FORK_ID = 2002
WORKFLOW = ".github/workflows/settled.yaml"
EPOCH = {"key": "e" * 64, "anchor": S}


class FakeGitHub:
    """Paths to JSON, and artifact download URLs to zip bytes. Every request is recorded."""

    def __init__(self, responses: dict, downloads: dict | None = None) -> None:
        self.responses = responses
        self.downloads = downloads or {}
        self.requested: list[str] = []

    def get(self, path: str):
        self.requested.append(path)
        if path not in self.responses:
            raise sg.GitHubError(404, path)
        value = self.responses[path]
        if isinstance(value, Exception):
            raise value
        return value

    def paginate(self, path: str, key: str | None = None) -> list:
        page = self.get(path)
        return page[key] if key else page

    def download(self, url: str) -> bytes:
        self.requested.append(url)
        return self.downloads[url]


def verdict_zip(epoch: dict, result: str = "green") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("verdict.json", json.dumps({"epoch": epoch, "result": result}))
    return buffer.getvalue()


def artifact(artifact_id: int, created: str, *, name="settled-verdict", branch="main", head_repo=SELF_ID, expired=False) -> dict:
    return {
        "id": artifact_id,
        "name": name,
        "expired": expired,
        "created_at": created,
        "archive_download_url": f"https://api/zip/{artifact_id}",
        "workflow_run": {"id": artifact_id * 10, "repository_id": SELF_ID, "head_repository_id": head_repo, "head_branch": branch},
    }


def workflow_run(run_id: int, *, path=WORKFLOW, event="schedule", head_repo=SELF_ID, conclusion="success") -> dict:
    return {"id": run_id, "path": path, "event": event, "conclusion": conclusion, "head_repository": {"id": head_repo}}


def github_with(artifacts: list[dict], runs: dict[int, dict], zips: dict[int, bytes]) -> FakeGitHub:
    responses = {
        "/repos/yadgarhq/argocd-verify": {"id": SELF_ID, "default_branch": "main"},
        "/repos/yadgarhq/argocd-verify/actions/artifacts?name=settled-verdict": {"artifacts": artifacts},
    }
    responses.update({f"/repos/yadgarhq/argocd-verify/actions/runs/{rid}": run_ for rid, run_ in runs.items()})
    return FakeGitHub(responses, {f"https://api/zip/{aid}": data for aid, data in zips.items()})


def find(github: FakeGitHub, epoch=EPOCH) -> dict | None:
    return sg.find_verdict(github, "yadgarhq/argocd-verify", WORKFLOW, epoch)


def test_find_verdict_takes_the_newest_verdict_for_the_epoch() -> None:
    github = github_with(
        [artifact(1, "2026-10-03T10:00:00Z"), artifact(2, "2026-10-03T11:00:00Z")],
        {10: workflow_run(10), 20: workflow_run(20, event="workflow_dispatch")},
        {1: verdict_zip(EPOCH, "red"), 2: verdict_zip(EPOCH, "green")},
    )
    found = find(github)
    assert found["verdict"]["result"] == "green"
    assert found["artifact_id"] == 2 and found["run_id"] == 20


def test_find_verdict_ignores_the_run_conclusion() -> None:
    github = github_with([artifact(1, "2026-10-03T10:00:00Z")], {10: workflow_run(10, conclusion="failure")}, {1: verdict_zip(EPOCH, "red")})
    assert find(github)["verdict"]["result"] == "red"


def test_find_verdict_skips_a_newer_verdict_for_another_epoch() -> None:
    other = {"key": "f" * 64, "anchor": S}
    github = github_with(
        [artifact(1, "2026-10-03T10:00:00Z"), artifact(2, "2026-10-03T11:00:00Z")],
        {10: workflow_run(10), 20: workflow_run(20)},
        {1: verdict_zip(EPOCH), 2: verdict_zip(other)},
    )
    assert find(github)["artifact_id"] == 1


def test_find_verdict_answers_none_when_no_verdict_names_the_epoch() -> None:
    github = github_with([artifact(1, "2026-10-03T10:00:00Z")], {10: workflow_run(10)}, {1: verdict_zip({"key": "f" * 64, "anchor": S})})
    assert find(github) is None


@pytest.mark.parametrize(
    ("bad_artifact", "bad_run"),
    [
        (artifact(9, "2026-10-03T12:00:00Z", branch="feature"), workflow_run(90)),
        (artifact(9, "2026-10-03T12:00:00Z", head_repo=FORK_ID), workflow_run(90)),
        (artifact(9, "2026-10-03T12:00:00Z"), workflow_run(90, head_repo=FORK_ID)),
        (artifact(9, "2026-10-03T12:00:00Z", name="settled-trial"), workflow_run(90)),
        (artifact(9, "2026-10-03T12:00:00Z"), workflow_run(90, path=".github/workflows/verify.yaml")),
        (artifact(9, "2026-10-03T12:00:00Z"), workflow_run(90, event="pull_request")),
        (artifact(9, "2026-10-03T12:00:00Z", expired=True), workflow_run(90)),
    ],
    ids=["other-branch", "fork-artifact", "fork-run", "settled-trial", "other-workflow", "other-event", "expired"],
)
def test_find_verdict_ignores_an_ineligible_artifact(bad_artifact: dict, bad_run: dict) -> None:
    github = github_with(
        [artifact(1, "2026-10-03T10:00:00Z"), bad_artifact],
        {10: workflow_run(10), 90: bad_run},
        {1: verdict_zip(EPOCH, "green"), 9: verdict_zip(EPOCH, "red")},
    )
    found = find(github)
    assert found["artifact_id"] == 1
    assert "https://api/zip/9" not in github.requested


def test_find_verdict_never_trusts_a_verdict_without_its_epoch() -> None:
    github = github_with([artifact(1, "2026-10-03T10:00:00Z")], {10: workflow_run(10)}, {1: b"not a zip"})
    with pytest.raises(sg.InfrastructureError):
        find(github)


def test_find_verdict_cli_writes_the_verdict_or_nothing(tmp_path: Path) -> None:
    github = github_with([artifact(1, "2026-10-03T10:00:00Z")], {10: workflow_run(10)}, {1: verdict_zip(EPOCH)})
    out = tmp_path / "found.json"
    argv = ["find-verdict", "--repository", "yadgarhq/argocd-verify", "--key", EPOCH["key"], "--anchor", S, "--out", str(out)]
    assert sg.main(argv, github=github) == 0
    assert json.loads(out.read_text())["artifact_id"] == 1
    out.unlink()
    assert sg.main([*argv[:4], "f" * 64, *argv[5:]], github=github) == 0
    assert json.loads(out.read_text()) == {"found": False}


def test_find_verdict_cli_api_error_exits_3(tmp_path: Path) -> None:
    github = FakeGitHub({})
    out = tmp_path / "found.json"
    argv = ["find-verdict", "--repository", "yadgarhq/argocd-verify", "--key", EPOCH["key"], "--anchor", S, "--out", str(out)]
    assert sg.main(argv, github=github) == 3
    assert not out.exists()


# ── 7. reachable ─────────────────────────────────────────────────────────────


def reach(branches: list[str], compare: dict[str, object]) -> FakeGitHub:
    responses = {"/repos/yadgarhq/argocd/branches": [{"name": b} for b in branches]}
    for branch, status in compare.items():
        path = f"/repos/yadgarhq/argocd/compare/{branch}...{TRIAL}"
        responses[path] = status if isinstance(status, Exception) else {"status": status}
    responses[f"/repos/yadgarhq/argocd/commits/{TRIAL}"] = {"sha": TRIAL}
    return FakeGitHub(responses)


@pytest.mark.parametrize("status", ["behind", "identical"])
def test_a_sha_on_a_branch_is_reachable(status: str) -> None:
    github = reach(["main", "trial"], {"main": "diverged", "trial": status})
    assert sg.reachable(github, "yadgarhq/argocd", TRIAL) == "trial"


def test_a_fork_only_sha_is_refused_and_commits_is_never_asked() -> None:
    github = reach(["main"], {"main": "diverged"})
    assert sg.reachable(github, "yadgarhq/argocd", TRIAL) is None
    assert not any("/commits/" in path for path in github.requested), github.requested


def test_a_sha_ahead_of_every_branch_is_refused() -> None:
    github = reach(["main"], {"main": "ahead"})
    assert sg.reachable(github, "yadgarhq/argocd", TRIAL) is None


def test_a_compare_404_means_not_on_that_branch() -> None:
    github = reach(["gone", "main"], {"gone": sg.GitHubError(404, "compare"), "main": "behind"})
    assert sg.reachable(github, "yadgarhq/argocd", TRIAL) == "main"


def test_a_compare_server_error_is_an_infrastructure_failure() -> None:
    github = reach(["main"], {"main": sg.GitHubError(502, "compare")})
    with pytest.raises(sg.GitHubError):
        sg.reachable(github, "yadgarhq/argocd", TRIAL)


def test_reachable_cli(tmp_path: Path) -> None:
    assert sg.main(["reachable", "--sha", TRIAL], github=reach(["main"], {"main": "behind"})) == 0
    assert sg.main(["reachable", "--sha", TRIAL], github=reach(["main"], {"main": "diverged"})) == 1
    assert sg.main(["reachable", "--sha", TRIAL], github=reach(["main"], {"main": sg.GitHubError(500, "x")})) == 3
    nothing = FakeGitHub({})
    for bad in ("main", "refs/pull/7/head", TRIAL[:12]):
        assert sg.main(["reachable", "--sha", bad], github=nothing) == 2
    assert nothing.requested == []
