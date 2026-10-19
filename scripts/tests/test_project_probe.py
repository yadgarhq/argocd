"""`scripts/project_probe.py`: the 785 refusal-counter probe (ADR-0841).

Offline: every network hop goes through a `Transport`, and these tests hand the
probe a scripted one. The red-first list is the A-U12 card's: per-pod compare
(a reset is no false rise, a new pod counts from 0, a decrease is flagged), only
`PROJECT_UNRESOLVABLE` passes, "no series" is 0 only beside
`registry_loaded == 1` for the same pod in the same read, an overlapping estate
`smoke` job is inconclusive, a 401 / non-2xx / Prometheus down fails, and the
trial claim passes only when `PROJECT_UNREGISTERED_PERSONAL` rose and
`PROJECT_UNRESOLVABLE` did not.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]

# `scripts/` is not a package, so the module is loaded by path.
_spec = importlib.util.spec_from_file_location("project_probe", REPOSITORY / "scripts" / "project_probe.py")
pp = importlib.util.module_from_spec(_spec)
sys.modules["project_probe"] = pp
_spec.loader.exec_module(pp)

U = pp.UNRESOLVABLE
P = pp.PERSONAL
N = pp.NOT_MEMBER

# The probe account's own segment, as `GET /auth/whoami` answers it (gateway#124,
# ADR-0959): a canonical v7 uuid, `account_segment(user_id)`.
OWN = "0192f0a0-0000-7000-8000-00000000a11c"
WHOAMI = (200, {"account_segment": OWN, "user_id": f"yadgar:user:{OWN}"})
OWN_CLAIM = f"local/{OWN}/probe-never-registered"


def vector(loaded: dict[str, float], refusals: dict[tuple[str, str], float]) -> dict:
    """A Prometheus instant-query body, shaped like the live one (2026-10-03)."""
    result = [
        {"metric": {"__name__": pp.LOADED, "app": "gateway", "namespace": "yadgar", "pod": pod}, "value": [1.0, str(v)]}
        for pod, v in loaded.items()
    ]
    result += [
        {
            "metric": {"__name__": pp.REFUSALS, "app": "gateway", "namespace": "yadgar", "pod": pod, "reason": reason},
            "value": [1.0, str(v)],
        }
        for (pod, reason), v in refusals.items()
    ]
    return {"status": "success", "data": {"resultType": "vector", "result": result}}


def reading(loaded, refusals) -> pp.Reading:
    return pp.parse_reading(vector(loaded, refusals))


# --- the per-pod compare ------------------------------------------------------


def test_todays_case_a_lazily_registered_counter_appearing_at_one_is_a_rise_of_one() -> None:
    # Measured 2026-10-03: both pods report registry_loaded = 1 and no refusal
    # series at all. The probe's call then registers the series at 1.
    before = reading({"a": 1, "b": 1}, {})
    after = reading({"a": 1, "b": 1}, {("a", U): 1})
    assert pp.compare(before, after, U).rise == 1


def test_no_series_is_not_zero_without_registry_loaded_for_the_same_pod() -> None:
    # Pod b has no gauge in the BEFORE read, so its absent counter is unknown,
    # not 0, and the 3 it shows later cannot be counted as a rise of 3.
    # Pod b reports registry_loaded = 0 in the BEFORE read: present, not live.
    before = reading({"a": 1, "b": 0}, {})
    after = reading({"a": 1, "b": 1}, {("b", U): 3})
    delta = pp.compare(before, after, U)
    assert delta.rise == 0
    assert delta.unknown == ["b"]
    # And the same in the AFTER read: no series and no live gauge is unknown.
    delta = pp.compare(reading({"a": 1, "b": 1}, {}), reading({"a": 1, "b": 0}, {}), U)
    assert delta.unknown == ["b"]


def test_a_new_pod_counts_from_zero() -> None:
    before = reading({"a": 1}, {("a", U): 26})
    after = reading({"a": 1, "c": 1}, {("a", U): 26, ("c", U): 1})
    delta = pp.compare(before, after, U)
    assert delta.rise == 1
    assert delta.resets == []


def test_a_reset_is_no_false_rise_and_the_decrease_is_flagged() -> None:
    before = reading({"a": 1, "b": 1}, {("a", U): 26, ("b", U): 24})
    after = reading({"a": 1, "b": 1}, {("a", U): 2, ("b", U): 24})
    delta = pp.compare(before, after, U)
    assert delta.rise == 0
    assert delta.resets == ["a"]


def test_a_reset_on_one_pod_does_not_hide_a_rise_on_another() -> None:
    before = reading({"a": 1, "b": 1}, {("a", U): 26, ("b", U): 24})
    after = reading({"a": 1, "b": 1}, {("a", U): 2, ("b", U): 25})
    assert pp.compare(before, after, U).rise == 1


def test_a_pod_gone_by_the_after_read_is_reported_missing() -> None:
    before = reading({"a": 1, "b": 1}, {("a", U): 26, ("b", U): 24})
    after = reading({"a": 1}, {("a", U): 26})
    assert pp.compare(before, after, U).missing == ["b"]


def test_a_read_with_no_live_gateway_pod_is_refused() -> None:
    with pytest.raises(pp.ProbeError, match="registry_loaded"):
        pp.parse_reading(vector({"a": 0}, {("a", U): 5}))
    with pytest.raises(pp.ProbeError, match="registry_loaded"):
        pp.parse_reading(vector({}, {}))


def test_a_prometheus_error_body_is_refused() -> None:
    with pytest.raises(pp.ProbeError):
        pp.parse_reading({"status": "error", "error": "bad query"})


def test_one_query_reads_both_series_so_liveness_is_the_same_read() -> None:
    assert pp.REFUSALS in pp.QUERY and pp.LOADED in pp.QUERY
    assert 'namespace="yadgar"' in pp.QUERY and 'app="gateway"' in pp.QUERY


# --- the verdict --------------------------------------------------------------


def deltas(u_rise=0, p_rise=0, n_rise=0, **kw) -> dict[str, pp.Delta]:
    return {U: pp.Delta(rise=u_rise, **kw), P: pp.Delta(rise=p_rise), N: pp.Delta(rise=n_rise)}


def test_only_project_unresolvable_passes() -> None:
    assert pp.evaluate(U, deltas(u_rise=1)).kind == pp.PASS
    verdict = pp.evaluate(U, deltas(p_rise=1))
    assert verdict.kind == pp.FAIL
    assert U in verdict.message and P in verdict.message


def test_no_series_moved_fails_and_says_so() -> None:
    verdict = pp.evaluate(U, deltas())
    assert verdict.kind == pp.FAIL
    assert "no series moved" in verdict.message


def test_a_reset_without_a_rise_is_inconclusive_not_a_pass() -> None:
    assert pp.evaluate(U, deltas(resets=["a"])).kind == pp.INCONCLUSIVE
    assert pp.evaluate(U, deltas(missing=["a"])).kind == pp.INCONCLUSIVE


def test_an_unknown_pod_without_a_rise_fails() -> None:
    assert pp.evaluate(U, deltas(unknown=["b"])).kind == pp.FAIL


def test_the_trial_passes_only_when_personal_rose_and_unresolvable_did_not() -> None:
    assert pp.trial_valid(deltas(p_rise=1)) is True
    assert pp.trial_valid(deltas(p_rise=1, u_rise=1)) is False
    assert pp.trial_valid(deltas()) is False
    assert pp.trial_valid(deltas(u_rise=1)) is False
    assert pp.trial_valid(deltas(p_rise=1, resets=["a"])) is False
    # Run 39267418592's failure: the claim counted NOT_MEMBER. A trial that saw
    # NOT_MEMBER move sent a claim outside its own segment and proved nothing.
    assert pp.trial_valid(deltas(p_rise=1, n_rise=1)) is False


# --- attribution under estate's own traffic -----------------------------------

T0 = "2026-10-03T10:00:00Z"
T1 = "2026-10-03T10:02:00Z"


def job(name="smoke", status="completed", conclusion="success", started=None, completed=None) -> dict:
    return {"name": name, "status": status, "conclusion": conclusion, "started_at": started, "completed_at": completed}


@pytest.mark.parametrize(
    ("j", "overlaps"),
    [
        (job(status="in_progress", started="2026-10-03T09:55:00Z"), True),
        (job(started="2026-10-03T09:58:00Z", completed="2026-10-03T10:01:00Z"), True),
        (job(started="2026-10-03T10:01:00Z", completed="2026-10-03T10:05:00Z"), True),
        (job(started="2026-10-03T09:50:00Z", completed="2026-10-03T09:59:00Z"), False),
        (job(started="2026-10-03T10:03:00Z", completed="2026-10-03T10:05:00Z"), False),
        (job(conclusion="skipped", started="2026-10-03T10:01:00Z", completed="2026-10-03T10:01:00Z"), False),
        (job(name="verdict", status="in_progress", started="2026-10-03T10:01:00Z"), False),
        (job(status="queued", started=None), False),
    ],
)
def test_only_a_smoke_job_running_inside_the_window_overlaps(j, overlaps) -> None:
    assert pp.job_overlaps(j, pp.parse_time(T0), pp.parse_time(T1)) is overlaps


# --- end to end, over a scripted transport ------------------------------------


class Fake:
    """Scripted network. `readings` is consumed one per Prometheus query."""

    def __init__(self, readings, *, login=(200, {"token": "tok-SECRET"}), whoami=WHOAMI, call=None, runs=None, jobs=None, prom_down=False):
        self.readings = list(readings)
        self.login = login
        self.whoami = whoami
        self.gets: list[tuple[str, dict]] = []
        self.call = call or (200, {"jsonrpc": "2.0", "id": 1, "result": {"resultType": "complete", "structuredContent": {}}})
        self.runs = runs if runs is not None else {"workflow_runs": []}
        self.jobs = jobs or {}
        self.prom_down = prom_down
        self.posts: list[tuple[str, dict, dict]] = []
        self.queries: list[str] = []
        self.github: list[str] = []

    def prom_query(self, query):
        if self.prom_down:
            raise pp.ProbeError("prometheus unreachable: connection refused")
        self.queries.append(query)
        return self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]

    def github_get(self, path):
        self.github.append(path)
        if "/jobs" in path:
            return self.jobs.get(path, {"jobs": []})
        return self.runs

    def edge_post(self, path, headers, body):
        self.posts.append((path, headers, json.loads(body)))
        status, payload = self.login if path == "/auth/login" else self.call
        return status, json.dumps(payload).encode()

    def edge_get(self, path, headers):
        self.gets.append((path, headers))
        status, payload = self.whoami
        return status, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


class Clock:
    def __init__(self):
        self.t = pp.parse_time(T0)

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def run(fake, *argv, env=None):
    clock = Clock()
    return pp.run(
        pp.parse_args(["--edge-ca", "/dev/null", *argv]),
        fake,
        env={"ESTATE_PROBE_PASSWORD": "pw-SECRET"} if env is None else env,
        now=clock.now,
        sleep=clock.sleep,
    )


BEFORE = vector({"a": 1, "b": 1}, {})
ROSE_U = vector({"a": 1, "b": 1}, {("a", U): 1})
ROSE_P = vector({"a": 1, "b": 1}, {("a", P): 1})


def test_a_counted_call_passes_and_the_call_is_read_only_and_shaped(capsys) -> None:
    fake = Fake([BEFORE, BEFORE, ROSE_U])
    assert run(fake) == 0
    out = capsys.readouterr().out
    assert "PASS" in out
    login, call = fake.posts
    assert login[0] == "/auth/login" and login[2] == {"username": "estate-probe", "password": "pw-SECRET"}
    path, headers, body = call
    assert path == "/"
    assert body["method"] == "tools/call"
    assert body["params"]["name"] == "find_tasks"  # read-only: creates nothing
    assert body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
    assert "io.modelcontextprotocol/clientCapabilities" in body["params"]["_meta"]
    assert headers["x-yadgar-project"] == pp.CLAIM
    assert headers["authorization"] == "Bearer tok-SECRET"
    assert headers["mcp-method"] == "tools/call" and headers["mcp-name"] == "find_tasks"
    assert headers["mcp-protocol-version"] == "2026-07-28"
    # Every Prometheus read was the one combined query.
    assert set(fake.queries) == {pp.QUERY}
    # Both estate run-list reads happened.
    assert sum(1 for p in fake.github if p.endswith("/runs?per_page=20")) == 2


def test_neither_the_password_nor_the_token_is_ever_printed(capsys) -> None:
    run(Fake([BEFORE, BEFORE, ROSE_U]))
    run(Fake([BEFORE], call=(401, {"error": "x"})))
    captured = capsys.readouterr()
    assert "SECRET" not in captured.out + captured.err


def test_no_rise_within_the_window_fails() -> None:
    fake = Fake([BEFORE])
    assert run(fake) == 1
    # 120 s at 15 s: eight scrapes after the call, plus the before read.
    assert len(fake.queries) == 9


def test_a_rise_of_another_reason_only_fails() -> None:
    assert run(Fake([BEFORE, ROSE_P])) == 1


def test_another_reason_moving_first_does_not_end_the_poll_early() -> None:
    # Someone else's `local/...` claim is scraped one poll before the probe's
    # own increment. The poll waits for PROJECT_UNRESOLVABLE, not for any series.
    both = vector({"a": 1, "b": 1}, {("a", U): 1, ("b", P): 1})
    assert run(Fake([BEFORE, ROSE_P, both])) == 0


def test_a_401_at_login_fails() -> None:
    fake = Fake([BEFORE], login=(401, {"error": "unauthenticated"}))
    assert run(fake) == 1


@pytest.mark.parametrize("status", [401, 403, 429, 500, 503])
def test_a_non_2xx_call_fails(status) -> None:
    assert run(Fake([BEFORE, ROSE_U], call=(status, {"error": "x"}))) == 1


def test_a_json_rpc_error_on_a_200_fails() -> None:
    assert run(Fake([BEFORE, ROSE_U], call=(200, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "x"}}))) == 1


def test_prometheus_down_fails() -> None:
    assert run(Fake([BEFORE], prom_down=True)) == 1


def test_a_missing_password_fails_before_any_call() -> None:
    fake = Fake([BEFORE])
    assert run(fake, env={}) == 1
    assert fake.posts == []


def test_a_smoke_job_already_running_is_inconclusive_and_sends_nothing(capsys) -> None:
    runs = {"workflow_runs": [{"id": 77, "status": "in_progress", "updated_at": T0}]}
    jobs = {"/repos/yadgarhq/estate/actions/runs/77/jobs?per_page=100": {"jobs": [job(status="in_progress", started="2026-10-03T09:59:00Z")]}}
    fake = Fake([BEFORE, ROSE_U], runs=runs, jobs=jobs)
    assert run(fake) == 0
    assert "inconclusive: estate smoke run 77 overlapped" in capsys.readouterr().out
    assert fake.posts == []


def test_a_smoke_job_starting_inside_the_window_is_inconclusive(capsys) -> None:
    class Late(Fake):
        def github_get(self, path):
            if self.posts and "/jobs" in path:
                return {"jobs": [job(status="in_progress", started="2026-10-03T10:00:10Z")]}
            if self.posts:
                return {"workflow_runs": [{"id": 78, "status": "in_progress", "updated_at": "2026-10-03T10:00:10Z"}]}
            return super().github_get(path)

    assert run(Late([BEFORE, ROSE_U])) == 0
    assert "inconclusive: estate smoke run 78 overlapped" in capsys.readouterr().out


def test_an_old_finished_run_is_not_read_for_jobs() -> None:
    runs = {"workflow_runs": [{"id": 5, "status": "completed", "updated_at": "2026-10-03T08:00:00Z"}]}
    fake = Fake([BEFORE, ROSE_U], runs=runs)
    assert run(fake) == 0
    assert not any("/jobs" in p for p in fake.github)


def test_the_github_api_failing_fails() -> None:
    class Down(Fake):
        def github_get(self, path):
            raise pp.ProbeError("github: 503")

    assert run(Down([BEFORE, ROSE_U])) == 1


def test_the_trial_sends_the_personal_claim_and_reddens_as_designed(capsys) -> None:
    fake = Fake([BEFORE, ROSE_P])
    assert run(fake, "--trial", "unregistered-personal") == pp.EXIT_TRIAL_RED
    out = capsys.readouterr().out
    # ADR-0959: the claim is under the caller's OWN segment, read from whoami.
    assert fake.gets == [("/auth/whoami", {"authorization": "Bearer tok-SECRET"})]
    assert fake.posts[1][1]["x-yadgar-project"] == OWN_CLAIM
    assert "trial: VALID" in out


def test_the_trial_claim_follows_whatever_segment_whoami_answers() -> None:
    # A mutant that hardcodes a segment (or the old `local/probe-never-registered`)
    # sends the same claim for every account; this pins derivation, not a value.
    other = "0192f0a0-ffff-7abc-b000-0000000000b0"
    fake = Fake([BEFORE, ROSE_P], whoami=(200, {"account_segment": other, "user_id": f"yadgar:user:{other}"}))
    assert run(fake, "--trial", "unregistered-personal") == pp.EXIT_TRIAL_RED
    assert fake.posts[1][1]["x-yadgar-project"] == f"local/{other}/probe-never-registered"


def test_the_old_shared_personal_claim_is_gone() -> None:
    # gateway v0.12.16 `member.rs`: `local/probe-never-registered` names the
    # segment `probe-never-registered`, which is nobody's own, so it counts
    # PROJECT_NOT_MEMBER. That made leg (c) run 39267418592 red.
    assert not hasattr(pp, "TRIAL_CLAIM")
    assert pp.personal_claim(OWN) == OWN_CLAIM


# --- whoami fails closed (ADR-0959) -------------------------------------------
#
# A run that cannot learn its own segment must never guess one: every refusal
# below ends the run before ANY MCP call is sent, so no claim is built.

BAD_WHOAMI = {
    "401": (401, {"error": "unauthenticated"}),
    "500": (500, {"error": "x"}),
    "not json": (200, b"<html>"),
    "missing key": (200, {"user_id": f"yadgar:user:{OWN}"}),
    "null segment": (200, {"account_segment": None, "user_id": "dev-user"}),
    "empty segment": (200, {"account_segment": "", "user_id": "yadgar:user:"}),
    "not a uuid": (200, {"account_segment": "probe", "user_id": "yadgar:user:probe"}),
    "upper case": (200, {"account_segment": OWN.upper(), "user_id": f"yadgar:user:{OWN.upper()}"}),
    "not v7": (200, {"account_segment": OWN.replace("-7000-", "-4000-"), "user_id": "yadgar:user:" + OWN.replace("-7000-", "-4000-")}),
    "bad variant": (200, {"account_segment": OWN.replace("-8000-", "-c000-"), "user_id": "yadgar:user:" + OWN.replace("-8000-", "-c000-")}),
    "segment with a slash": (200, {"account_segment": OWN + "/x", "user_id": f"yadgar:user:{OWN}/x"}),
    "user_id disagrees": (200, {"account_segment": OWN, "user_id": "yadgar:user:0192f0a0-0000-7000-8000-0000000000b0"}),
    "not an object": (200, [OWN]),
}


@pytest.mark.parametrize("mode", [("--trial", "unregistered-personal"), ("--leg-c",)])
@pytest.mark.parametrize("case", sorted(BAD_WHOAMI))
def test_whoami_refused_or_malformed_fails_closed_and_sends_no_call(case, mode, capsys) -> None:
    fake = Arms([BEFORE, rose(*ALL)], whoami=BAD_WHOAMI[case])
    assert run(fake, *mode) == pp.EXIT_FAIL
    assert [p[0] for p in fake.posts] == ["/auth/login"], "no MCP call may follow a failed whoami"
    out = capsys.readouterr().out
    assert "whoami" in out and "FAIL" in out
    assert "SECRET" not in out


def test_leg_c_reads_whoami_once_with_the_login_token() -> None:
    # The segment comes from the credential the run just obtained, never from config.
    fake = Arms([BEFORE, rose(*ALL)])
    assert run(fake, "--leg-c") == pp.EXIT_PASS
    assert fake.gets == [("/auth/whoami", {"authorization": "Bearer tok-SECRET"})]


def test_the_routine_probe_and_the_no_token_trial_never_call_whoami() -> None:
    # The routine probe runs on a cron; it must not depend on gateway#124.
    fake = Fake([BEFORE, ROSE_U], whoami=(404, {"error": "x"}))
    assert run(fake) == 0
    assert fake.gets == []
    fake = Fake([BEFORE], call=UNAUTHENTICATED, whoami=(404, {"error": "x"}))
    assert run(fake, "--trial", "no-token") == pp.EXIT_TRIAL_RED
    assert fake.gets == []


def test_a_trial_where_nothing_moved_is_itself_a_failure(capsys) -> None:
    assert run(Fake([BEFORE]), "--trial", "unregistered-personal") == pp.EXIT_TRIAL_INVALID
    assert "trial: INVALID" in capsys.readouterr().out


def test_a_trial_where_unresolvable_rose_is_invalid() -> None:
    both = vector({"a": 1, "b": 1}, {("a", U): 1, ("b", P): 1})
    assert run(Fake([BEFORE, both]), "--trial", "unregistered-personal") == pp.EXIT_TRIAL_INVALID


def test_a_trial_where_not_member_rose_is_invalid() -> None:
    both = vector({"a": 1, "b": 1}, {("a", N): 1, ("b", P): 1})
    assert run(Fake([BEFORE, both]), "--trial", "unregistered-personal") == pp.EXIT_TRIAL_INVALID


UNAUTHENTICATED = (401, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32001, "message": "x"}})


def test_the_no_token_trial_is_valid_on_a_401_with_no_counter_moved(capsys) -> None:
    fake = Fake([BEFORE], call=UNAUTHENTICATED)
    assert run(fake, "--trial", "no-token") == pp.EXIT_TRIAL_RED
    assert [p[0] for p in fake.posts] == ["/"]
    assert "authorization" not in fake.posts[0][1]
    assert "trial: VALID" in capsys.readouterr().out


def test_the_no_token_trial_on_a_500_is_invalid() -> None:
    fake = Fake([BEFORE], call=(500, {"error": "x"}))
    assert run(fake, "--trial", "no-token") == pp.EXIT_TRIAL_INVALID


def test_the_no_token_trial_with_a_counter_rise_is_invalid() -> None:
    assert run(Fake([BEFORE, ROSE_U], call=UNAUTHENTICATED), "--trial", "no-token") == pp.EXIT_TRIAL_INVALID
    assert run(Fake([BEFORE, ROSE_P], call=UNAUTHENTICATED), "--trial", "no-token") == pp.EXIT_TRIAL_INVALID


def test_the_no_token_trial_with_a_reset_or_vanished_pod_is_invalid() -> None:
    before = vector({"a": 1, "b": 1}, {("a", U): 5})
    reset = vector({"a": 1, "b": 1}, {("a", U): 1})
    gone = vector({"a": 1}, {("a", U): 5})
    assert run(Fake([before, reset], call=UNAUTHENTICATED), "--trial", "no-token") == pp.EXIT_TRIAL_INVALID
    assert run(Fake([before, gone], call=UNAUTHENTICATED), "--trial", "no-token") == pp.EXIT_TRIAL_INVALID


def test_the_no_token_trial_with_prometheus_down_is_never_valid() -> None:
    assert run(Fake([BEFORE], call=UNAUTHENTICATED, prom_down=True), "--trial", "no-token") in (
        pp.EXIT_FAIL,
        pp.EXIT_TRIAL_INVALID,
    )


def test_an_inconclusive_reset_is_a_workflow_warning(capsys) -> None:
    before = vector({"a": 1, "b": 1}, {("a", U): 5})
    reset = vector({"a": 1, "b": 1}, {("a", U): 1})
    assert run(Fake([before, reset])) == 0
    assert "::warning::project-probe inconclusive:" in capsys.readouterr().out


# --- the runner's egress policy -----------------------------------------------
#
# `verifier/manifests/networkpolicy.yaml`. The CNI enforces it (ADR-0688), so a
# missing destination is a verifier outage that presents as a timeout. Each
# ClusterIP destination the probe and `verify.yaml` reach is allowed both as the
# Service address (pre-DNAT) and as the pods or endpoint behind it (post-DNAT).

import yaml  # noqa: E402  (PyYAML: the hook installs it)

POLICY = REPOSITORY / "verifier" / "manifests" / "networkpolicy.yaml"


def egress_rules() -> list[dict]:
    doc = yaml.safe_load(POLICY.read_text())
    assert doc["kind"] == "NetworkPolicy"
    assert doc["metadata"]["namespace"] == "post-merge-verifier"
    assert doc["spec"]["podSelector"] == {}
    assert doc["spec"]["policyTypes"] == ["Egress"]
    return doc["spec"]["egress"]


def allowed_blocks() -> set[tuple[str, int]]:
    return {
        (peer["ipBlock"]["cidr"], port["port"])
        for rule in egress_rules()
        for peer in rule.get("to", [])
        if "ipBlock" in peer and not peer["ipBlock"].get("except")
        for port in rule.get("ports", [])
    }


def allowed_selectors() -> set[tuple[str, int]]:
    return {
        (peer["namespaceSelector"]["matchLabels"]["kubernetes.io/metadata.name"], port["port"])
        for rule in egress_rules()
        for peer in rule.get("to", [])
        if "namespaceSelector" in peer
        for port in rule.get("ports", [])
    }


def test_every_egress_rule_names_a_destination_and_a_port() -> None:
    # A `to`-less or port-less rule admits every destination or every port.
    for rule in egress_rules():
        assert rule.get("to"), rule
        assert rule.get("ports"), rule


# THE SHARED PEERS ARE DERIVED, NOT RETYPED. kube-dns, the edge's envoy pods
# and the cluster ranges are what `estate-front-egress` already selects and
# excepts, and `yadgar-edge`'s address is pinned in its Service. A second copy
# typed here would drift from the first unnoticed.
ESTATE_POLICY = REPOSITORY / "manifests" / "estate-front" / "networkpolicy.yaml"
EDGE_SERVICE = REPOSITORY / "manifests" / "estate-front" / "edge-service.yaml"

# MEASURED on kind-yadgar, 2026-10-03: the labels on pod
# `observability/prometheus-server-*`, the same three the Service selects
# less `instance`. Pinned exactly: one label too many matches no pod, and the
# probe then times out against Prometheus.
PROMETHEUS_PODS = {"app.kubernetes.io/name": "prometheus", "app.kubernetes.io/component": "server"}


def peers(doc_rules: list[dict], namespace: str) -> list[tuple[dict, list[int]]]:
    return [
        (peer, sorted(port["port"] for port in rule.get("ports", [])))
        for rule in doc_rules
        for peer in rule.get("to", [])
        if peer.get("namespaceSelector", {}).get("matchLabels", {}).get("kubernetes.io/metadata.name") == namespace
    ]


def estate_rules() -> list[dict]:
    return yaml.safe_load(ESTATE_POLICY.read_text())["spec"]["egress"]


def edge_address() -> str:
    for doc in yaml.safe_load_all(EDGE_SERVICE.read_text()):
        if doc and doc.get("kind") == "Service" and doc["metadata"]["name"] == "yadgar-edge":
            return doc["spec"]["clusterIP"]
    raise AssertionError("no Service yadgar-edge")


def test_the_api_server_prometheus_and_the_edge_are_allowed_before_and_after_dnat() -> None:
    # 6443 on every node: only the control plane listens there, so a rebuild
    # that renumbers nodes inside the /24 does not cut the verifier.
    expected = {("10.96.0.1/32", 443), ("10.89.4.0/24", 6443), ("10.96.63.35/32", 80), (f"{edge_address()}/32", 443)}
    assert expected <= allowed_blocks()
    assert {("kube-system", 53), ("observability", 9090), ("envoy-gateway-system", 10443)} <= allowed_selectors()


@pytest.mark.parametrize("namespace", ["kube-system", "envoy-gateway-system"])
def test_kube_dns_and_the_edge_are_the_peers_estate_front_egress_selects(namespace) -> None:
    ours, theirs = peers(egress_rules(), namespace), peers(estate_rules(), namespace)
    assert len(ours) == 1 and len(theirs) == 1
    assert ours[0] == theirs[0]


def test_prometheus_is_selected_by_exactly_the_measured_labels() -> None:
    (peer, ports), = peers(egress_rules(), "observability")
    assert peer["podSelector"] == {"matchLabels": PROMETHEUS_PODS}
    assert ports == [9090]


def test_nothing_in_namespace_yadgar_is_reachable() -> None:
    # The probe reaches the gateway only through the edge, like any client.
    assert not any(ns == "yadgar" for ns, _ in allowed_selectors())


def test_the_internet_rule_is_443_only_and_excepts_every_cluster_range() -> None:
    wide = [
        (peer["ipBlock"], [p["port"] for p in rule["ports"]])
        for rule in egress_rules()
        for peer in rule["to"]
        if "ipBlock" in peer and peer["ipBlock"]["cidr"] == "0.0.0.0/0"
    ]
    assert len(wide) == 1
    block, ports = wide[0]
    assert ports == [443]
    (theirs,) = [peer["ipBlock"] for rule in estate_rules() for peer in rule["to"] if peer.get("ipBlock", {}).get("cidr") == "0.0.0.0/0"]
    assert sorted(block["except"]) == sorted(theirs["except"])


# --- leg (c): one deliberate call per reason class (ledger 1624) --------------
#
# `--leg-c` is dispatch-only. It sends one call per probeable reason class and
# passes only when every class's counter rose. The ARCHIVED arm is a write
# (`create_task`) that must never write: it omits `title`, so the gateway counts
# the claim during attestation and the tool then refuses before `task` is
# dialled. A served success there means a task WAS written, and that fails loud.

O = pp.UNREGISTERED_ORG
I = pp.INVALID
A = pp.ARCHIVED
ALL = (U, P, O, I, A, N)


class Arms(Fake):
    """A scripted network that answers each call by the claim it carries."""

    def __init__(self, readings, answers=None, **kw):
        super().__init__(readings, **kw)
        self.answers = answers or {}

    def edge_post(self, path, headers, body):
        if path == "/auth/login":
            return super().edge_post(path, headers, body)
        self.posts.append((path, headers, json.loads(body)))
        claim = headers["x-yadgar-project"]
        status, payload = self.answers.get(claim, (200, served(claim)))
        return status, json.dumps(payload).encode()


TITLE_REQUIRED = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "`title` is required"}], "isError": True}}
TASK_WRITTEN = {"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {"id": "yadgar:task:01", "number": 7, "version": 1}}}


def served(claim: str) -> dict:
    if claim == pp.ARCHIVED_CLAIM:
        return TITLE_REQUIRED
    return {"jsonrpc": "2.0", "id": 1, "result": {"structuredContent": {"tasks": []}}}


def rose(*reasons: str) -> dict:
    return vector({"a": 1, "b": 1}, {("a", r): 1 for r in reasons})


def test_leg_c_sends_one_call_per_reason_class_with_matching_mirror_headers(capsys) -> None:
    fake = Arms([BEFORE, rose(*ALL)])
    assert run(fake, "--leg-c") == pp.EXIT_PASS, capsys.readouterr().out
    calls = fake.posts[1:]
    by_claim = {h["x-yadgar-project"]: (h, b) for _, h, b in calls}
    assert set(by_claim) == {pp.CLAIM, OWN_CLAIM, pp.ORG_CLAIM, pp.INVALID_CLAIM, pp.ARCHIVED_CLAIM, pp.NOT_MEMBER_CLAIM}
    assert len(calls) == 6
    for headers, body in by_claim.values():
        # The gateway cross-checks Mcp-Name against the body's tool name.
        assert headers["mcp-name"] == body["params"]["name"]
    assert by_claim[pp.ARCHIVED_CLAIM][1]["params"]["name"] == "create_task"
    # No title: the tool refuses before `task` is dialled, so nothing is written.
    assert "title" not in by_claim[pp.ARCHIVED_CLAIM][1]["params"]["arguments"]
    for claim in (pp.CLAIM, OWN_CLAIM, pp.ORG_CLAIM, pp.INVALID_CLAIM, pp.NOT_MEMBER_CLAIM):
        assert by_claim[claim][1]["params"]["name"] == "find_tasks"
    # Reads only, bar the ARCHIVED arm's title-less create_task.
    assert sorted(b["params"]["name"] for _, _, b in calls).count("create_task") == 1


def test_leg_c_has_one_arm_per_probed_reason_and_every_reason_is_watched() -> None:
    arms = pp.leg_c_arms(OWN)
    assert sorted(a.reason for a in arms) == sorted(pp.REASONS)
    assert pp.NOT_MEMBER == "PROJECT_NOT_MEMBER"  # gateway `src/project.rs` Reason::NotMember
    (personal,) = [a for a in arms if a.reason == P]
    assert personal.claim == OWN_CLAIM


def test_the_not_member_claim_is_a_well_formed_foreign_private_claim() -> None:
    # gateway `member::is_foreign_private`: well-formed, under `local`, and a
    # segment that is not the caller's. The fixed segment is canonical-shaped so
    # a future gateway that also checks the segment's shape still counts it.
    root, segment, rest = pp.NOT_MEMBER_CLAIM.split("/")
    assert root == "local" and rest == "probe"
    assert pp.is_canonical_segment(segment)
    assert segment != OWN


@pytest.mark.parametrize("own", [pp.FOREIGN_SEGMENT, pp.FOREIGN_SEGMENT.upper()])
def test_a_not_member_claim_equal_to_the_own_segment_is_refused(own) -> None:
    # The gateway compares segments ASCII-case-insensitively, so must the probe.
    with pytest.raises(pp.ProbeError, match="NOT_MEMBER"):
        pp.leg_c_arms(own)


def test_leg_c_refuses_to_run_when_its_own_segment_is_the_foreign_one(capsys) -> None:
    whoami = (200, {"account_segment": pp.FOREIGN_SEGMENT, "user_id": f"yadgar:user:{pp.FOREIGN_SEGMENT}"})
    fake = Arms([BEFORE, rose(*ALL)], whoami=whoami)
    assert run(fake, "--leg-c") == pp.EXIT_FAIL
    assert [p[0] for p in fake.posts] == ["/auth/login"]


def test_leg_c_names_a_registered_private_ancestor_when_personal_did_not_rise(capsys) -> None:
    # The probe cannot read the private registry (no egress into `yadgar`). If a
    # row at `local/<own>` or the claim itself existed, the claim would resolve
    # and PERSONAL would not count: leg (c) is red, and says why it might be.
    assert run(Arms([BEFORE, rose(U, O, I, A, N)]), "--leg-c") == pp.EXIT_FAIL
    out = capsys.readouterr().out
    assert f"local/{OWN}" in out and "registered" in out


def test_leg_c_claims_reach_the_classes_the_gateway_assigns() -> None:
    # gateway `src/project/classify.rs`: a trailing `/` fails the grammar; a
    # claim under the registered root `yadgarhq` lands on the anchor (ORG); the
    # archived claim is its own declared row; the other two have no ancestor.
    assert pp.INVALID_CLAIM.endswith("/")
    assert pp.ORG_CLAIM.split("/")[0] == "yadgarhq" and pp.ORG_CLAIM != pp.ARCHIVED_CLAIM
    assert pp.ARCHIVED_CLAIM == "yadgarhq/probe-archived"


def test_leg_c_passes_only_when_every_reason_rose(capsys) -> None:
    assert run(Arms([BEFORE, rose(U, P, O, I)]), "--leg-c") == pp.EXIT_FAIL
    out = capsys.readouterr().out
    assert A in out and "did not rise" in out


def test_leg_c_waits_for_every_reason_before_it_stops_polling() -> None:
    assert run(Arms([BEFORE, rose(U, P), rose(*ALL)]), "--leg-c") == pp.EXIT_PASS


def test_leg_c_fails_loud_when_the_archived_write_was_served(capsys) -> None:
    fake = Arms([BEFORE, rose(*ALL)], answers={pp.ARCHIVED_CLAIM: (200, TASK_WRITTEN)})
    assert run(fake, "--leg-c") == pp.EXIT_FAIL
    out = capsys.readouterr().out
    assert "a task was WRITTEN" in out and "yadgar:task:01" in out


def test_leg_c_fails_when_the_archived_write_failed_for_another_reason() -> None:
    other = (200, {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "unavailable"}], "isError": True}})
    assert run(Arms([BEFORE, rose(*ALL)], answers={pp.ARCHIVED_CLAIM: other}), "--leg-c") == pp.EXIT_FAIL


def test_leg_c_accepts_an_enforcing_refusal_with_the_expected_reason() -> None:
    refused = (400, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "x", "data": {"reason": A}}})
    assert run(Arms([BEFORE, rose(*ALL)], answers={pp.ARCHIVED_CLAIM: refused}), "--leg-c") == pp.EXIT_PASS
    not_member = (403, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "x", "data": {"reason": N}}})
    assert run(Arms([BEFORE, rose(*ALL)], answers={pp.NOT_MEMBER_CLAIM: not_member}), "--leg-c") == pp.EXIT_PASS


def test_leg_c_fails_on_a_refusal_with_another_reason() -> None:
    refused = (400, {"jsonrpc": "2.0", "id": 1, "error": {"code": -32602, "message": "x", "data": {"reason": O}}})
    assert run(Arms([BEFORE, rose(*ALL)], answers={pp.ARCHIVED_CLAIM: refused}), "--leg-c") == pp.EXIT_FAIL


@pytest.mark.parametrize("status", [401, 500, 503])
def test_leg_c_fails_on_a_non_2xx_read(status) -> None:
    assert run(Arms([BEFORE, rose(*ALL)], answers={pp.ORG_CLAIM: (status, {"error": "x"})}), "--leg-c") == pp.EXIT_FAIL


def test_leg_c_is_never_green_when_inconclusive(capsys) -> None:
    # A reset that could hide a rise is green for the routine probe, but leg (c)
    # needs proof that every class counted, so it is red and says dispatch again.
    before = vector({"a": 1, "b": 1}, {("a", U): 5})
    after = vector({"a": 1, "b": 1}, {("a", U): 1, ("b", P): 1, ("b", O): 1, ("b", I): 1, ("b", A): 1})
    assert run(Arms([before, after]), "--leg-c") == pp.EXIT_FAIL
    assert "dispatch again" in capsys.readouterr().out


def test_leg_c_with_a_smoke_job_running_sends_nothing_and_is_red() -> None:
    runs = {"workflow_runs": [{"id": 77, "status": "in_progress", "updated_at": T0}]}
    jobs = {"/repos/yadgarhq/estate/actions/runs/77/jobs?per_page=100": {"jobs": [job(status="in_progress", started="2026-10-03T09:59:00Z")]}}
    fake = Arms([BEFORE, rose(*ALL)], runs=runs, jobs=jobs)
    assert run(fake, "--leg-c") == pp.EXIT_FAIL
    assert fake.posts == []


def test_leg_c_and_a_trial_cannot_both_be_given() -> None:
    with pytest.raises(SystemExit):
        pp.parse_args(["--edge-ca", "/dev/null", "--leg-c", "--trial", "no-token"])


def test_leg_c_never_prints_the_password_or_the_token(capsys) -> None:
    run(Arms([BEFORE, rose(*ALL)]), "--leg-c")
    captured = capsys.readouterr()
    assert "SECRET" not in captured.out + captured.err


def test_the_routine_probe_still_sends_exactly_one_call() -> None:
    fake = Arms([BEFORE, ROSE_U])
    assert run(fake) == 0
    assert [p[0] for p in fake.posts] == ["/auth/login", "/"]



def test_leg_c_archived_arm_needs_the_gateways_exact_title_refusal() -> None:
    # gateway tools.rs:327: ToolError::Invalid("`title` is required"). Another
    # tool failure that merely mentions a title is not that refusal.
    near = (200, {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "title service unavailable"}], "isError": True}})
    assert run(Arms([BEFORE, rose(*ALL)], answers={pp.ARCHIVED_CLAIM: near}), "--leg-c") == pp.EXIT_FAIL
    assert pp.TITLE_REQUIRED == "`title` is required"
