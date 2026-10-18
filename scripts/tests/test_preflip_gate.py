"""`scripts/preflip_gate.py`: the pre-flip refusal gate (ledgers 1608, 1618).

Offline: every network hop goes through a `Transport`, and these tests hand the
gate a scripted one. One constructed case per arm the ledger names, plus the
fail-closed reads around them:

- probe-only refusals pass: the probe is serialized OUT of the window, so its
  increments land at or before the baseline and count 0;
- one extra refusal fails, in any reason, including a series born mid-window
  at 1 (the lazily registered counter a bare `increase()` reads as 0);
- a pod restart mid-window is still counted, both as a new pod name and as a
  reset of the same pod's series;
- Prometheus unreachable fails closed, and so do a window older than the
  retention, a scrape gap, a probe run inside the window, the probe switch
  left on, and a window with no resolutions at all (zero traffic is no proof).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]

# `scripts/` is not a package, so the module is loaded by path.
_spec = importlib.util.spec_from_file_location("preflip_gate", REPOSITORY / "scripts" / "preflip_gate.py")
pg = importlib.util.module_from_spec(_spec)
sys.modules["preflip_gate"] = pg
_spec.loader.exec_module(pg)

U = "PROJECT_UNRESOLVABLE"
UNAVAILABLE = "PROJECT_REGISTRY_UNAVAILABLE"

# The window: 2026-10-18 00:00:00Z .. 06:00:00Z.
START = 1792281600.0
END = START + 6 * 3600
NOW = END + 60
STEP = 15.0
LOOKBACK = 300.0


def iso(t: float) -> str:
    return pg.iso(t)


def ticks(first: float, last: float) -> list[float]:
    out, t = [], first
    while t <= last:
        out.append(t)
        t += STEP
    return out


def gauge(pod: str, first: float = START - LOOKBACK, last: float = END) -> dict:
    return {
        "metric": {"__name__": pg.LOADED, "app": "gateway", "namespace": "yadgar", "pod": pod},
        "values": [[t, "1"] for t in ticks(first, last)],
    }


def counter(name: str, pod: str, label: tuple[str, str], steps: list[tuple[float, float]], last: float = END) -> dict:
    """A counter series: `steps` is [(time, value)], each value held until the next."""
    values = []
    for i, (t0, v) in enumerate(steps):
        t1 = steps[i + 1][0] - STEP if i + 1 < len(steps) else last
        values += [[t, str(v)] for t in ticks(t0, t1)]
    metric = {"__name__": name, "app": "gateway", "namespace": "yadgar", "pod": pod, label[0]: label[1]}
    return {"metric": metric, "values": values}


def refusal(pod: str, steps, reason: str = U, last: float = END) -> dict:
    return counter(pg.REFUSALS, pod, ("reason", reason), steps, last)


def resolution(pod: str, steps, last: float = END) -> dict:
    return counter(pg.RESOLUTIONS, pod, ("outcome", "exact"), steps, last)


def matrix(*series: dict) -> dict:
    return {"status": "success", "data": {"resultType": "matrix", "result": list(series)}}


TRAFFIC = resolution("a", [(START - LOOKBACK, 3), (START + 3600, 9)])


def run_record(run_id: int, started: float, completed: float | None, conclusion: str = "success") -> tuple[dict, dict]:
    run = {
        "id": run_id,
        "status": "completed" if completed is not None else "in_progress",
        "created_at": iso(started - 5),
        "updated_at": iso(completed if completed is not None else started),
    }
    job = {
        "name": "785 refusal-counter probe",
        "status": "completed" if completed is not None else "in_progress",
        "conclusion": conclusion if completed is not None else None,
        "started_at": iso(started),
        "completed_at": iso(completed) if completed is not None else None,
    }
    return run, {"jobs": [job]}


class FakeTransport:
    def __init__(self, body: dict | None, lowest: float = START - 86000, runs: list[tuple[dict, dict]] = ()):
        self.body = body
        self.lowest = lowest
        self.runs = list(runs)
        self.queries: list[dict] = []
        self.prom_down = False
        self.github_down = False

    def prom_query(self, params: dict) -> dict:
        self.queries.append(params)
        if self.prom_down:
            raise pg.GateError("prometheus: connection refused")
        if params["query"] == pg.LOWEST_QUERY:
            return {
                "status": "success",
                "data": {"resultType": "vector", "result": [{"metric": {}, "value": [NOW, str(self.lowest)]}]},
            }
        return self.body

    def github_get(self, path: str) -> dict:
        if self.github_down:
            raise pg.GateError("github: 502")
        if "/jobs" in path:
            run_id = int(path.split("/runs/")[1].split("/")[0])
            return next(jobs for run, jobs in self.runs if run["id"] == run_id)
        return {"total_count": len(self.runs), "workflow_runs": [run for run, _ in self.runs]}


ENV = {"PROJECT_PROBE_ENABLED": "false", "GITHUB_TOKEN": "x"}


def gate(transport: FakeTransport, env: dict | None = None, argv: list[str] | None = None) -> tuple[int, str]:
    lines: list[str] = []
    args = pg.parse_args(argv if argv is not None else ["--start", iso(START), "--end", iso(END)])
    code = pg.run(args, ENV if env is None else env, transport, now=lambda: NOW, out=lines.append)
    return code, "\n".join(lines)


# --- arm 1: probe-only refusals pass ------------------------------------------


def test_probe_increments_before_the_window_are_in_the_baseline_and_pass() -> None:
    # The probe ran at 23:20 the evening before and was then switched off: its
    # increment (4 -> 5) is in the baseline, and the window itself is flat.
    body = matrix(
        gauge("a"),
        refusal("a", [(START - LOOKBACK, 4), (START - 200, 5)]),
        TRAFFIC,
    )
    runs = [run_record(1, START - 2400, START - 2350)]
    code, out = gate(FakeTransport(body, runs=runs))
    assert code == pg.EXIT_PASS, out
    assert "PASS" in out


def test_a_probe_run_ending_inside_the_slack_before_the_start_is_an_overlap() -> None:
    # The other direction: a probe run that ended inside the slack before the
    # start is an overlap, so its late-scraped increment is not waved through.
    body = matrix(gauge("a"), refusal("a", [(START - LOOKBACK, 4), (START + 15, 5)]), TRAFFIC)
    runs = [run_record(1, START - 60, START - 10)]
    code, out = gate(FakeTransport(body, runs=runs))
    assert code == pg.EXIT_FAIL, out
    assert "overlaps the window" in out


# --- arm 2: one extra refusal fails --------------------------------------------


def test_one_refusal_inside_the_window_fails() -> None:
    body = matrix(gauge("a"), refusal("a", [(START - LOOKBACK, 4), (START + 1800, 5)]), TRAFFIC)
    code, out = gate(FakeTransport(body))
    assert code == pg.EXIT_FAIL, out
    assert "refusals in window: 1" in out


def test_a_series_born_mid_window_at_one_is_one_refusal_not_zero() -> None:
    # The gateway registers the counter lazily: the first refusal makes the
    # series appear at 1. `increase()` reads that as 0; the gate must not.
    body = matrix(gauge("a"), refusal("a", [(START + 1800, 1)]), TRAFFIC)
    code, out = gate(FakeTransport(body))
    assert code == pg.EXIT_FAIL, out
    assert "refusals in window: 1" in out


def test_every_reason_counts_including_registry_unavailable() -> None:
    body = matrix(gauge("a"), refusal("a", [(START + 60, 1)], reason=UNAVAILABLE), TRAFFIC)
    code, out = gate(FakeTransport(body))
    assert code == pg.EXIT_FAIL, out
    assert UNAVAILABLE in out


# --- arm 3: a pod restart mid-window is still counted ----------------------------


def test_a_refusal_on_a_replacement_pod_is_counted() -> None:
    # Pod a rolls out at 02:00; pod b replaces it and refuses once.
    body = matrix(
        gauge("a", last=START + 7200),
        gauge("b", first=START + 7200 - 30),
        refusal("a", [(START - LOOKBACK, 6)], last=START + 7200),
        refusal("b", [(START + 7300, 1)]),
        TRAFFIC,
    )
    code, out = gate(FakeTransport(body))
    assert code == pg.EXIT_FAIL, out
    assert "refusals in window: 1" in out


def test_a_same_pod_reset_counts_the_value_after_the_reset() -> None:
    # Measured shape, kind-yadgar 2026-10-17T19:40:22Z: pod 84z54's series went
    # 5 -> 1 (a container restart, same pod name). The 1 is a refusal.
    body = matrix(gauge("a"), refusal("a", [(START - LOOKBACK, 5), (START + 3600, 1)]), TRAFFIC)
    code, out = gate(FakeTransport(body))
    assert code == pg.EXIT_FAIL, out
    assert "refusals in window: 1" in out
    assert "reset" in out


def test_a_restart_with_no_refusal_passes() -> None:
    body = matrix(
        gauge("a", last=START + 7200),
        gauge("b", first=START + 7200 - 30),
        refusal("a", [(START - LOOKBACK, 6), (START + 3600, 0)], last=START + 7200),
        TRAFFIC,
    )
    code, out = gate(FakeTransport(body))
    assert code == pg.EXIT_PASS, out


# --- arm 4: Prometheus unreachable fails closed ------------------------------------


def test_prometheus_unreachable_fails_closed() -> None:
    t = FakeTransport(matrix())
    t.prom_down = True
    code, out = gate(t)
    assert code == pg.EXIT_FAIL, out
    assert "connection refused" in out


def test_prometheus_error_body_fails_closed() -> None:
    code, out = gate(FakeTransport({"status": "error", "error": "query timed out"}))
    assert code == pg.EXIT_FAIL, out
    assert "query timed out" in out


def test_no_gateway_series_at_all_fails_closed() -> None:
    # An empty answer is a dead scrape until proven otherwise, not zero refusals.
    code, out = gate(FakeTransport(matrix()))
    assert code == pg.EXIT_FAIL, out
    assert pg.LOADED in out


def test_a_window_older_than_the_retention_fails_closed() -> None:
    body = matrix(gauge("a"), TRAFFIC)
    code, out = gate(FakeTransport(body, lowest=START))
    assert code == pg.EXIT_FAIL, out
    assert "retention" in out


def test_a_scrape_gap_inside_the_window_fails_closed() -> None:
    # No gateway pod scraped between 02:00 and 03:00: a pod could have lived
    # and refused in that hour with nothing recorded.
    body = matrix(gauge("a", last=START + 7200), gauge("b", first=START + 10800), TRAFFIC)
    code, out = gate(FakeTransport(body))
    assert code == pg.EXIT_FAIL, out
    assert "scrape gap" in out


# --- the serialization guard and the traffic leg ----------------------------------


def test_a_probe_run_inside_the_window_fails_even_when_the_counter_is_flat() -> None:
    body = matrix(gauge("a"), TRAFFIC)
    code, out = gate(FakeTransport(body, runs=[run_record(7, START + 3600, START + 3650, "success")]))
    assert code == pg.EXIT_FAIL, out
    assert "overlaps the window" in out


def test_a_probe_run_that_was_skipped_by_its_switch_is_not_an_overlap() -> None:
    body = matrix(gauge("a"), TRAFFIC)
    code, out = gate(FakeTransport(body, runs=[run_record(7, START + 3600, START + 3601, "skipped")]))
    assert code == pg.EXIT_PASS, out


def test_a_probe_run_after_the_window_ends_is_not_an_overlap() -> None:
    # The window closed and the gate is read later: a run after `end` cannot
    # have touched the window's samples.
    body = matrix(gauge("a"), TRAFFIC)
    code, out = gate(FakeTransport(body, runs=[run_record(8, END + 30, END + 50, "success")]))
    assert code == pg.EXIT_PASS, out


def test_the_probe_switch_left_on_fails() -> None:
    body = matrix(gauge("a"), TRAFFIC)
    code, out = gate(FakeTransport(body), env={"PROJECT_PROBE_ENABLED": "true", "GITHUB_TOKEN": "x"})
    assert code == pg.EXIT_FAIL, out
    assert "PROJECT_PROBE_ENABLED" in out


def test_github_unreachable_fails_closed() -> None:
    t = FakeTransport(matrix(gauge("a"), TRAFFIC))
    t.github_down = True
    code, out = gate(t)
    assert code == pg.EXIT_FAIL, out
    assert "502" in out


def test_zero_refusals_with_zero_resolutions_fails_as_vacuous() -> None:
    body = matrix(gauge("a"), resolution("a", [(START - LOOKBACK, 3)]))
    code, out = gate(FakeTransport(body))
    assert code == pg.EXIT_FAIL, out
    assert "resolutions in window: 0" in out


def test_a_resolution_series_born_without_a_gauge_before_it_counts_nothing() -> None:
    # For the traffic leg an unknown baseline must under-count: the series'
    # first value could predate the window.
    body = matrix(gauge("a", first=START + 600), resolution("a", [(START + 600, 40)]))
    code, out = gate(FakeTransport(body))
    assert code == pg.EXIT_FAIL, out
    assert "resolutions in window: 0" in out


def test_a_resolution_series_born_after_its_pods_gauge_counts_in_full() -> None:
    body = matrix(gauge("a"), resolution("a", [(START + 600, 4)]))
    code, out = gate(FakeTransport(body))
    assert code == pg.EXIT_PASS, out
    assert "resolutions in window: 4" in out


def test_min_resolutions_is_enforced() -> None:
    body = matrix(gauge("a"), TRAFFIC)
    argv = ["--start", iso(START), "--end", iso(END), "--min-resolutions", "7"]
    code, out = gate(FakeTransport(body), argv=argv)
    assert code == pg.EXIT_FAIL, out
    assert "resolutions in window: 6" in out


# --- the window itself ----------------------------------------------------------


def test_an_end_in_the_future_or_before_the_start_is_refused() -> None:
    body = matrix(gauge("a"), TRAFFIC)
    code, _ = gate(FakeTransport(body), argv=["--start", iso(START), "--end", iso(NOW + 600)])
    assert code == pg.EXIT_FAIL
    code, _ = gate(FakeTransport(body), argv=["--start", iso(END), "--end", iso(START)])
    assert code == pg.EXIT_FAIL


def test_the_matrix_read_spans_the_lookback_and_is_evaluated_at_the_end() -> None:
    t = FakeTransport(matrix(gauge("a"), TRAFFIC))
    gate(t)
    q = next(p for p in t.queries if p["query"] != pg.LOWEST_QUERY)
    assert q["time"] == f"{END:.3f}"
    assert q["query"].endswith(f"[{int(END - START + LOOKBACK)}s]")
    assert 'namespace="yadgar"' in q["query"] and 'app="gateway"' in q["query"]


def test_the_report_never_prints_the_token() -> None:
    _, out = gate(FakeTransport(matrix(gauge("a"), TRAFFIC)), env={"PROJECT_PROBE_ENABLED": "false", "GITHUB_TOKEN": "s3cr3t"})
    assert "s3cr3t" not in out
