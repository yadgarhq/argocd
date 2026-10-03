"""`scripts/settled_gate.py derive`: the render epoch the settled-state gate judges (ledger 675, stage 2a).

No cluster, no network, no helm. The render is injected: every test that
reaches step 2 hands `derive` a fake renderer, and the tests that must stop
before step 2 hand it one that fails the test if it is called. Git history is
real: each anchor test builds a small repository in `tmp_path` with the three
data files at the paths argocd uses.

WHAT IS ASSERTED, and each has a red case below:

  1. K. `K = sha256(T bytes + canonical JSON of spec.source)`, the JSON
     `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=True)`
     over one `yaml.safe_load` (PyYAML 6.0.3, YAML 1.1). The two shared
     vectors are argocd's own bytes at `13a2db6` (pin 0.3.13, table blob
     2911480; `eb43ea9` carries the same two blobs) and `d41c07f` (pin
     0.3.38, table blob 1e13998). Estate's verdict job copies these vectors
     byte for byte (ruling 4). A YAML 1.1 trap vector pins what `on`, `yes`,
     `0755`, `1e3`, a float and non-ASCII text become.
  2. A, the anchor: the last commit, walking newest-first, whose K still
     equals the current K. History running out gives the oldest listed; a
     commit where K cannot be computed ends the walk; a merge commit is red;
     A -> B -> A gives three epochs; a comment-only edit moves neither A nor
     the deadline.
  3. The allow-list (step 0) and the consistency check (step 1) are red
     before any render. A render that differs from T (step 3) is red and
     names the objects. R (step 4) empty, or with no digest-pinned
     Deployment, is red; valkey is recorded as tag-pinned.
  4. An infrastructure failure (data missing or unparseable at S, helm
     failing) writes no output at all: it is a failed run, not a verdict.
  5. `stale`: estate smoke's newest run 44 days old passes; 46 days fails;
     no runs fails; an API error fails.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "settled_gate"

_spec = importlib.util.spec_from_file_location("settled_gate", REPOSITORY / "scripts" / "settled_gate.py")
sg = importlib.util.module_from_spec(_spec)
sys.modules["settled_gate"] = sg
_spec.loader.exec_module(sg)

APPLICATION = "applications/yadgar.yaml"
TABLE = "scripts/gates/yadgar_render.sha256"
CHART_PIN = "scripts/chart_pin.json"

# ── 1. K ─────────────────────────────────────────────────────────────────────

# THE SHARED VECTORS. Estate's decider (A-U11) asserts the same two values from
# the same bytes. Changing one here without changing it there splits the key.
K_AT_13A2DB6 = "2e275ae13256aae5d731c11a1e44a17e9a9bd6e815789c77cd86d20a5b950ee3"
K_AT_D41C07F = "dc901cd764b3e8fdebba5d7a086544d89741c68d82c8876c8677cff947bfd942"


def fixture(sha: str, name: str) -> bytes:
    return (FIXTURES / sha / name).read_bytes()


def test_the_fixtures_are_argocds_own_blobs() -> None:
    """The vectors are worth sharing only if they are the real bytes: git blob ids, measured on argocd."""

    def blob(data: bytes) -> str:
        return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # git's own object id

    assert blob(fixture("13a2db6", "yadgar.yaml.raw")).startswith("87618dd")
    assert blob(fixture("13a2db6", "yadgar_render.sha256")).startswith("2911480")
    assert blob(fixture("d41c07f", "yadgar.yaml.raw")).startswith("5b3c43b")
    assert blob(fixture("d41c07f", "yadgar_render.sha256")).startswith("1e13998")


@pytest.mark.parametrize(("sha", "expected"), [("13a2db6", K_AT_13A2DB6), ("d41c07f", K_AT_D41C07F)])
def test_the_shared_key_vectors(sha: str, expected: str) -> None:
    assert sg.render_key(fixture(sha, "yadgar_render.sha256"), fixture(sha, "yadgar.yaml.raw")) == expected


def test_the_two_pins_are_two_keys() -> None:
    assert K_AT_13A2DB6 != K_AT_D41C07F


YAML_TRAPS = """\
spec:
  source:
    repoURL: ghcr.io/yadgarhq/charts
    chart: yadgar
    targetRevision: 0.3.38
    helm:
      valuesObject:
        enabled: on
        confirm: yes
        mode: 0755
        big: 1e3
        ratio: 0.5
        name: "Göteborg ✓"
"""
YAML_TRAPS_JSON = (
    '{"chart":"yadgar","helm":{"valuesObject":{"big":"1e3","confirm":true,"enabled":true,'
    '"mode":493,"name":"G\\u00f6teborg \\u2713","ratio":0.5}},'
    '"repoURL":"ghcr.io/yadgarhq/charts","targetRevision":"0.3.38"}'
)
YAML_TRAPS_TABLE = b"targetRevision  0.3.38\n"
YAML_TRAPS_K = "3a6bcb437e3ce19a7db3f616adfd22fc4e15d60cf152ee71eb98761d9b05a818"


def test_the_yaml_1_1_trap_vector_canonicalises_exactly() -> None:
    """YAML 1.1 reads `on`/`yes` as true and `0755` as octal 493; PyYAML keeps `1e3` a string. Estate must agree."""
    source = yaml.safe_load(YAML_TRAPS.encode())["spec"]["source"]
    assert sg.canonical_source(source) == YAML_TRAPS_JSON.encode("ascii")


def test_the_yaml_1_1_trap_vector_key() -> None:
    expected = hashlib.sha256(YAML_TRAPS_TABLE + YAML_TRAPS_JSON.encode("ascii")).hexdigest()
    assert sg.render_key(YAML_TRAPS_TABLE, YAML_TRAPS.encode()) == expected == YAML_TRAPS_K


def test_key_order_and_quoting_do_not_move_the_key() -> None:
    reordered = YAML_TRAPS.replace("    repoURL: ghcr.io/yadgarhq/charts\n    chart: yadgar\n", "    chart: 'yadgar'\n    repoURL: \"ghcr.io/yadgarhq/charts\"\n")
    assert reordered != YAML_TRAPS
    assert sg.render_key(YAML_TRAPS_TABLE, reordered.encode()) == YAML_TRAPS_K


def test_a_key_outside_spec_source_does_not_move_the_key() -> None:
    moved = YAML_TRAPS + "  syncPolicy:\n    automated:\n      selfHeal: true\n"
    assert sg.render_key(YAML_TRAPS_TABLE, moved.encode()) == YAML_TRAPS_K


def test_unparseable_or_absent_data_has_no_key() -> None:
    assert sg.render_key(YAML_TRAPS_TABLE, b"spec: [unclosed\n") is None
    assert sg.render_key(YAML_TRAPS_TABLE, None) is None
    assert sg.render_key(None, YAML_TRAPS.encode()) is None
    assert sg.render_key(YAML_TRAPS_TABLE, b"- a list\n") is None


# ── a small argocd: three data files, real history ───────────────────────────

GATEWAY = "ghcr.io/yadgarhq/gateway@sha256:" + "a" * 64
IAM = "ghcr.io/yadgarhq/iam@sha256:" + "b" * 64
VALKEY = "valkey/valkey:9.1.1"


def application(pin: str = "0.3.38", gateway: str = GATEWAY, comment: str = "", **source_extra) -> str:
    source = {
        "repoURL": "ghcr.io/yadgarhq/charts",
        "chart": "yadgar",
        "targetRevision": pin,
        "helm": {"valuesObject": {"gateway": {"image": gateway}}},
        **source_extra,
    }
    document = {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {"name": "yadgar", "namespace": "argocd"},
        "spec": {"project": "default", "source": source, "syncPolicy": {"automated": {"selfHeal": True}}},
    }
    return (f"# {comment}\n" if comment else "") + yaml.safe_dump(document, sort_keys=False)


def deployment(name: str, containers: list[tuple[str, str]], init: list[tuple[str, str]] = ()) -> dict:
    spec = {"containers": [{"name": n, "image": i} for n, i in containers]}
    if init:
        spec["initContainers"] = [{"name": n, "image": i} for n, i in init]
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name},
        "spec": {"template": {"spec": spec}},
    }


def fake_render(application_bytes: bytes) -> list:
    """A stand-in for `parent_render`: the gateway image comes from V, so a values edit moves the render."""
    values = yaml.safe_load(application_bytes)["spec"]["source"]["helm"]["valuesObject"]
    return [
        deployment("gateway", [("gateway", values["gateway"]["image"])]),
        deployment("iam", [("iam", IAM)], init=[("migrate", IAM)]),
        deployment("valkey", [("valkey", VALKEY)]),
        {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "audit"}, "data": {"a": "b"}},
        None,
    ]


def table_for(application_text: str, pin: str | None = None) -> str:
    """T as `test_yadgar_application.py --write` writes it, from the fake render."""
    revision = pin if pin is not None else yaml.safe_load(application_text)["spec"]["source"]["targetRevision"]
    rendered = sg.gate_digests()(fake_render(application_text.encode()))
    lines = ["# K3: header", "# Regenerate: ...", f"targetRevision  {revision}"]
    lines += [f"{digest}  {key}" for key, digest in sorted(rendered.items())]
    return "\n".join(lines) + "\n"


def chart_pin(pin: str = "0.3.38") -> str:
    return json.dumps({"chart_tag": f"v{pin}", "platform_version": "0.1.26"}, indent=2) + "\n"


class Repo:
    """A git repository with argocd's three data paths. Every commit gets a distinct committer date."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)
        self.clock = dt.datetime(2026, 10, 1, 12, 0, 0, tzinfo=dt.timezone.utc)
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str, date: str | None = None) -> str:
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        if date:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        command = ["git", "-C", str(self.path), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *args]
        return subprocess.run(command, capture_output=True, text=True, check=True, env=env).stdout.strip()

    def commit(self, message: str, files: dict[str, str | None]) -> tuple[str, str]:
        """Write (or, with None, delete) files and commit. Returns (sha, committer date in UTC, `Z` form)."""
        for name, content in files.items():
            target = self.path / name
            if content is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        self.git("add", "-A")
        self.clock += dt.timedelta(hours=1)
        stamp = self.clock.strftime("%Y-%m-%dT%H:%M:%SZ")
        self.git("commit", "-q", "--allow-empty", "-m", message, date=stamp)
        return self.git("rev-parse", "HEAD"), stamp

    def adopt(self, text: str | None = None, pin: str = "0.3.38") -> tuple[str, str]:
        text = text if text is not None else application(pin)
        return self.commit("adopt", {APPLICATION: text, TABLE: table_for(text), CHART_PIN: chart_pin(pin)})

    def bump(self, text: str, pin: str = "0.3.38", message: str = "bump") -> tuple[str, str]:
        return self.commit(message, {APPLICATION: text, TABLE: table_for(text), CHART_PIN: chart_pin(pin)})


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "argocd")


def never_render(_application_bytes: bytes) -> list:
    raise AssertionError("the render was reached; this case must stop before any helm call")


def plus(stamp: str, seconds: int) -> str:
    moment = dt.datetime.fromisoformat(stamp.replace("Z", "+00:00")) + dt.timedelta(seconds=seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── 2. the anchor ────────────────────────────────────────────────────────────


def test_a_single_commit_is_its_own_anchor_when_history_runs_out(repo: Repo) -> None:
    sha, stamp = repo.adopt()
    result = sg.derive(repo.path, sha, render=fake_render)
    assert result["outcome"] == "derived", result["problems"]
    assert result["anchor"] == sha
    assert result["anchor_committed_at"] == stamp
    assert result["deadline"] == plus(stamp, 3900)
    assert result["epoch"] == {"key": result["key"], "anchor": sha}


def test_an_unrelated_commit_after_the_anchor_keeps_it(repo: Repo) -> None:
    anchor, _ = repo.adopt()
    later, _ = repo.commit("readme", {"README.md": "hello\n"})
    result = sg.derive(repo.path, later, render=fake_render)
    assert result["sha"] == later
    assert result["anchor"] == anchor


def test_a_comment_only_edit_moves_neither_the_anchor_nor_the_deadline(repo: Repo) -> None:
    anchor, stamp = repo.adopt()
    edited, _ = repo.commit("comment", {APPLICATION: application(comment="a reviewer's note")})
    result = sg.derive(repo.path, edited, render=fake_render)
    assert result["anchor"] == anchor
    assert result["deadline"] == plus(stamp, 3900)


def test_a_sync_policy_edit_does_not_move_the_anchor(repo: Repo) -> None:
    anchor, _ = repo.adopt()
    text = application().replace("selfHeal: true", "selfHeal: false")
    assert text != application()
    edited, _ = repo.commit("syncPolicy", {APPLICATION: text})
    assert sg.derive(repo.path, edited, render=fake_render)["anchor"] == anchor


def test_a_values_only_change_is_a_new_epoch(repo: Repo) -> None:
    first, _ = repo.adopt()
    moved = application(gateway="ghcr.io/yadgarhq/gateway@sha256:" + "c" * 64)
    second, stamp = repo.bump(moved)
    before = sg.derive(repo.path, first, render=fake_render)
    after = sg.derive(repo.path, second, render=fake_render)
    assert after["outcome"] == "derived", after["problems"]
    assert after["pin"] == before["pin"] == "0.3.38"
    assert after["key"] != before["key"]
    assert after["anchor"] == second
    assert after["deadline"] == plus(stamp, 3900)


def test_a_revert_is_three_epochs(repo: Repo) -> None:
    """A -> B -> A. The second A shares the first A's K but not its anchor, so it is judged afresh."""
    a1, _ = repo.adopt(pin="0.3.38")
    b, _ = repo.bump(application("0.3.40"), pin="0.3.40")
    a2, _ = repo.bump(application("0.3.38"), pin="0.3.38", message="revert")
    epochs = [sg.derive(repo.path, sha, render=fake_render) for sha in (a1, b, a2)]
    assert [e["anchor"] for e in epochs] == [a1, b, a2]
    assert epochs[0]["key"] == epochs[2]["key"] != epochs[1]["key"]
    assert len({(e["key"], e["anchor"]) for e in epochs}) == 3


def test_a_commit_with_no_key_ends_the_walk(repo: Repo) -> None:
    """⊥: an older commit where the application does not parse cannot share the current K."""
    repo.adopt()
    repo.commit("broken", {APPLICATION: "spec: [unclosed\n"})
    fixed, _ = repo.commit("fixed", {APPLICATION: application()})
    assert sg.derive(repo.path, fixed, render=fake_render)["anchor"] == fixed


def test_a_commit_where_the_table_is_absent_ends_the_walk(repo: Repo) -> None:
    text = application()
    repo.adopt(text)
    repo.commit("no table", {TABLE: None})
    restored, _ = repo.commit("table back", {TABLE: table_for(text)})
    assert sg.derive(repo.path, restored, render=fake_render)["anchor"] == restored


def test_a_merge_commit_in_the_render_history_is_red(repo: Repo) -> None:
    repo.adopt()
    repo.git("checkout", "-q", "-b", "side")
    repo.bump(application(gateway="ghcr.io/yadgarhq/gateway@sha256:" + "d" * 64), message="side")
    repo.git("checkout", "-q", "main")
    repo.commit("main moves", {"README.md": "x\n"})
    repo.git("merge", "-q", "--no-ff", "side", "-m", "merge side", date="2026-10-02T00:00:00Z")
    merge = repo.git("rev-parse", "HEAD")
    result = sg.derive(repo.path, merge, render=never_render)
    assert result["outcome"] == "red"
    assert any("merge commit" in p and merge in p for p in result["problems"]), result["problems"]


# ── 3. steps 0, 1, 3 and 4 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("extra", "named"),
    [
        ({"repoURL": "ghcr.io/attacker/charts"}, "repoURL"),
        ({"repoURL": "ghcr.io/yadgarhq/charts/"}, "repoURL"),
        ({"chart": "yadgar-evil"}, "chart"),
        ({"path": "chart"}, "path"),
        ({"kustomize": {}}, "kustomize"),
        ({"plugin": {"name": "x"}}, "plugin"),
    ],
)
def test_the_allow_list_is_red_before_any_helm_call(repo: Repo, extra: dict, named: str) -> None:
    text = application()
    document = yaml.safe_load(text)
    document["spec"]["source"].update(extra)
    sha, _ = repo.adopt(yaml.safe_dump(document, sort_keys=False))
    result = sg.derive(repo.path, sha, render=never_render)
    assert result["outcome"] == "red"
    assert any("allow-list" in p and named in p for p in result["problems"]), result["problems"]


def test_a_second_helm_input_is_red_before_any_helm_call(repo: Repo) -> None:
    document = yaml.safe_load(application())
    document["spec"]["source"]["helm"]["valueFiles"] = ["values.yaml"]
    sha, _ = repo.adopt(yaml.safe_dump(document, sort_keys=False))
    result = sg.derive(repo.path, sha, render=never_render)
    assert result["outcome"] == "red"
    assert any("valueFiles" in p for p in result["problems"]), result["problems"]


def test_a_missing_spec_source_is_red(repo: Repo) -> None:
    document = yaml.safe_load(application())
    document["spec"]["sources"] = [document["spec"].pop("source")]
    text = yaml.safe_dump(document, sort_keys=False)
    sha, _ = repo.commit("adopt", {APPLICATION: text, TABLE: table_for(application()), CHART_PIN: chart_pin()})
    result = sg.derive(repo.path, sha, render=never_render)
    assert result["outcome"] == "red"
    assert any("spec.source" in p for p in result["problems"]), result["problems"]


def test_the_bot_bypass_pin_move_without_the_table_is_red_at_step_1(repo: Repo) -> None:
    """A pin moved in the Application with T unchanged: a new K, so it is judged, and red, never "already judged"."""
    first, _ = repo.adopt()
    moved, _ = repo.commit("ci: bot moves the pin", {APPLICATION: application("0.3.40")})
    before = sg.derive(repo.path, first, render=fake_render)
    result = sg.derive(repo.path, moved, render=never_render)
    assert result["key"] != before["key"]
    assert result["anchor"] == moved
    assert result["outcome"] == "red"
    assert any("targetRevision" in p and "0.3.40" in p for p in result["problems"]), result["problems"]


def test_a_chart_pin_that_disagrees_is_red_at_step_1(repo: Repo) -> None:
    repo.adopt()
    sha, _ = repo.commit("pin", {CHART_PIN: chart_pin("0.3.37")})
    result = sg.derive(repo.path, sha, render=never_render)
    assert result["outcome"] == "red"
    assert any("chart_pin.json" in p and "v0.3.37" in p for p in result["problems"]), result["problems"]


def test_every_step_1_disagreement_is_named_at_once(repo: Repo) -> None:
    repo.adopt()
    sha, _ = repo.commit("both", {APPLICATION: application("0.3.40"), CHART_PIN: chart_pin("0.3.37")})
    problems = sg.derive(repo.path, sha, render=never_render)["problems"]
    assert any("chart_pin.json" in p for p in problems), problems
    assert any("yadgar_render.sha256" in p for p in problems), problems


def test_a_table_whose_first_data_line_is_not_the_pin_is_red(repo: Repo) -> None:
    text = application()
    lines = table_for(text).splitlines()
    swapped = "\n".join([lines[0], lines[1], lines[3], lines[2], *lines[4:]]) + "\n"
    sha, _ = repo.commit("adopt", {APPLICATION: text, TABLE: swapped, CHART_PIN: chart_pin()})
    result = sg.derive(repo.path, sha, render=never_render)
    assert result["outcome"] == "red"
    assert any("first data line" in p for p in result["problems"]), result["problems"]


def test_the_bot_bypass_values_change_without_the_table_is_red_at_step_3(repo: Repo) -> None:
    repo.adopt()
    moved, _ = repo.commit("ci: bot moves a value", {APPLICATION: application(gateway="ghcr.io/yadgarhq/gateway@sha256:" + "e" * 64)})
    result = sg.derive(repo.path, moved, render=fake_render)
    assert result["outcome"] == "red"
    assert any("apps/Deployment//gateway" in p for p in result["problems"]), result["problems"]


def test_a_render_that_differs_from_the_table_names_each_object(repo: Repo) -> None:
    text = application()
    lines = [line for line in table_for(text).splitlines() if not line.endswith("/ConfigMap//audit")]
    lines.append("f" * 64 + "  apps/Deployment//ghost")
    sha, _ = repo.commit("adopt", {APPLICATION: text, TABLE: "\n".join(lines) + "\n", CHART_PIN: chart_pin()})
    problems = sg.derive(repo.path, sha, render=fake_render)["problems"]
    assert any("/ConfigMap//audit" in p and "added" in p for p in problems), problems
    assert any("apps/Deployment//ghost" in p and "removed" in p for p in problems), problems


def test_the_derived_set_records_digests_and_the_tag_pinned_container(repo: Repo) -> None:
    sha, _ = repo.adopt()
    result = sg.derive(repo.path, sha, render=fake_render)
    assert result["outcome"] == "derived", result["problems"]
    assert result["pin"] == "0.3.38"
    assert result["values"] == {"gateway": {"image": GATEWAY}}
    assert result["deployments"]["gateway"] == {
        "containers": {"gateway": {"image": GATEWAY, "digest": "sha256:" + "a" * 64}},
        "initContainers": {},
    }
    assert result["deployments"]["iam"]["initContainers"] == {"migrate": {"image": IAM, "digest": "sha256:" + "b" * 64}}
    assert result["deployments"]["valkey"]["containers"] == {"valkey": {"image": VALKEY, "digest": None}}
    assert result["tag_pinned"] == ["valkey/valkey"]
    assert sorted(result["deployments"]) == ["gateway", "iam", "valkey"]


def render_without_deployments(application_bytes: bytes) -> list:
    return [d for d in fake_render(application_bytes) if d and d["kind"] != "Deployment"]


def render_tag_pinned_only(application_bytes: bytes) -> list:
    return [deployment("valkey", [("valkey", VALKEY)])]


@pytest.mark.parametrize(
    ("render", "named"),
    [(render_without_deployments, "no Deployment"), (render_tag_pinned_only, "digest-pinned")],
)
def test_an_empty_or_undigested_set_is_red(repo: Repo, render, named: str) -> None:
    text = application()
    rendered = sg.gate_digests()(render(text.encode()))
    table = "targetRevision  0.3.38\n" + "".join(f"{d}  {k}\n" for k, d in sorted(rendered.items()))
    sha, _ = repo.commit("adopt", {APPLICATION: text, TABLE: table, CHART_PIN: chart_pin()})
    result = sg.derive(repo.path, sha, render=render)
    assert result["outcome"] == "red"
    assert any(named in p for p in result["problems"]), result["problems"]


def test_a_digest_that_is_not_64_hex_is_tag_pinned_not_digest_pinned() -> None:
    short = "ghcr.io/yadgarhq/gateway@sha256:" + "a" * 63
    assert sg.image_digest(short) is None
    assert sg.image_digest(GATEWAY) == "sha256:" + "a" * 64
    assert sg.image_digest("ghcr.io/yadgarhq/gateway:v1@sha256:" + "A" * 64) is None


# ── 4. infrastructure failures write nothing ─────────────────────────────────


def test_an_application_missing_at_s_is_an_infrastructure_failure(repo: Repo) -> None:
    repo.adopt()
    sha, _ = repo.commit("gone", {APPLICATION: None})
    with pytest.raises(sg.InfrastructureError, match="applications/yadgar.yaml"):
        sg.derive(repo.path, sha, render=never_render)


def test_an_application_that_does_not_parse_at_s_is_an_infrastructure_failure(repo: Repo) -> None:
    repo.adopt()
    sha, _ = repo.commit("broken", {APPLICATION: "spec: [unclosed\n"})
    with pytest.raises(sg.InfrastructureError, match="parse"):
        sg.derive(repo.path, sha, render=never_render)


def test_an_unknown_sha_is_an_infrastructure_failure(repo: Repo) -> None:
    repo.adopt()
    with pytest.raises(sg.InfrastructureError):
        sg.derive(repo.path, "0" * 40, render=never_render)


def failing_helm(_application_bytes: bytes) -> list:
    raise sg.InfrastructureError("helm pull exited 1: registry unavailable")


def test_the_cli_writes_the_derivation_and_exits_0(repo: Repo, tmp_path: Path) -> None:
    sha, _ = repo.adopt()
    out = tmp_path / "derived.json"
    assert sg.main(["derive", "--repo", str(repo.path), "--sha", sha, "--out", str(out)], render=fake_render) == 0
    written = json.loads(out.read_text())
    assert written["outcome"] == "derived"
    assert written["sha"] == sha


def test_the_cli_writes_a_red_and_exits_1(repo: Repo, tmp_path: Path) -> None:
    repo.adopt()
    sha, _ = repo.commit("pin", {CHART_PIN: chart_pin("0.3.37")})
    out = tmp_path / "derived.json"
    assert sg.main(["derive", "--repo", str(repo.path), "--sha", sha, "--out", str(out)], render=never_render) == 1
    assert json.loads(out.read_text())["outcome"] == "red"


def test_a_helm_failure_writes_nothing_and_exits_3(repo: Repo, tmp_path: Path) -> None:
    sha, _ = repo.adopt()
    out = tmp_path / "derived.json"
    assert sg.main(["derive", "--repo", str(repo.path), "--sha", sha, "--out", str(out)], render=failing_helm) == 3
    assert not out.exists()


def test_a_missing_application_writes_nothing_and_exits_3(repo: Repo, tmp_path: Path) -> None:
    repo.adopt()
    sha, _ = repo.commit("gone", {APPLICATION: None})
    out = tmp_path / "derived.json"
    assert sg.main(["derive", "--repo", str(repo.path), "--sha", sha, "--out", str(out)], render=never_render) == 3
    assert not out.exists()


def test_the_sha_must_be_full_hex(repo: Repo, tmp_path: Path) -> None:
    repo.adopt()
    out = tmp_path / "derived.json"
    for bad in ("main", "HEAD", "refs/pull/1/head", "abc123"):
        assert sg.main(["derive", "--repo", str(repo.path), "--sha", bad, "--out", str(out)], render=never_render) == 2
    assert not out.exists()


def test_git_environment_variables_do_not_redirect_the_reads(repo: Repo, monkeypatch) -> None:
    """A pre-commit hook exports GIT_DIR; `git -C` would then answer for the wrong repository."""
    sha, _ = repo.adopt()
    monkeypatch.setenv("GIT_DIR", str(REPOSITORY / ".git"))
    assert sg.derive(repo.path, sha, render=fake_render)["anchor"] == sha


# ── the real render wiring ───────────────────────────────────────────────────


def test_the_default_render_is_the_gates_own_functions() -> None:
    """Not re-implemented: `digests` and `parent_render` are imported from `scripts/gates` at S, so T's flags hold."""
    gates = REPOSITORY / "scripts" / "gates"
    sys.path.insert(0, str(gates))
    try:
        import test_no_two_owners
        import test_yadgar_application
    finally:
        sys.path.remove(str(gates))
    assert sg.gate_digests() is test_yadgar_application.digests
    assert sg.gate_parent_render() is test_no_two_owners.parent_render


# ── 5. stale ─────────────────────────────────────────────────────────────────

NOW = dt.datetime(2026, 10, 3, 12, 0, 0, tzinfo=dt.timezone.utc)


def runs(days_ago: float | None) -> dict:
    if days_ago is None:
        return {"total_count": 0, "workflow_runs": []}
    created = (NOW - dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"total_count": 1, "workflow_runs": [{"id": 1, "created_at": created, "html_url": "https://example/1"}]}


def test_stale_passes_at_44_days() -> None:
    assert sg.stale_problem(lambda: runs(44), NOW) is None


def test_stale_fails_at_46_days() -> None:
    problem = sg.stale_problem(lambda: runs(46), NOW)
    assert problem is not None and "46" in problem


def test_stale_fails_with_no_runs() -> None:
    problem = sg.stale_problem(lambda: runs(None), NOW)
    assert problem is not None and "no" in problem


def test_stale_fails_on_an_api_error() -> None:
    def broken():
        raise sg.InfrastructureError("GET .../runs: HTTP 403")

    problem = sg.stale_problem(broken, NOW)
    assert problem is not None and "403" in problem


def test_stale_cli_exit_codes() -> None:
    assert sg.main(["stale"], fetch=lambda: runs(44), now=NOW) == 0
    assert sg.main(["stale"], fetch=lambda: runs(46), now=NOW) == 1
    assert sg.main(["stale"], fetch=lambda: runs(None), now=NOW) == 1
