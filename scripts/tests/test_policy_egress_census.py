"""`scripts/policy_sources_named.py`'s egress half (yaadgaar 156, ledger 1064).

`EXPECTED_ALLOW_ALL_PORTS` compared `set()` to `set()` for `estate-front-egress`:
the policy has no `ingress` key, so nothing about it was examined. These tests
load the real tree through the script's own `policies()` and mutate the policy
in memory; each mutant must be refused with a message naming the policy.

Run: python3 -m pytest scripts/tests/test_policy_egress_census.py -q
"""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("policy_sources_named", REPOSITORY / "scripts" / "policy_sources_named.py")
psn = importlib.util.module_from_spec(_spec)
sys.modules["policy_sources_named"] = psn
_spec.loader.exec_module(psn)

POLICY = "estate-front-egress"


def tree() -> dict[str, tuple[Path, dict]]:
    return {policy["metadata"]["name"]: (path, policy) for path, policy in psn.policies()}


def with_rules(mutate) -> dict[str, tuple[Path, dict]]:
    found = copy.deepcopy(tree())
    mutate(found[POLICY][1]["spec"]["egress"])
    return found


def test_the_real_tree_passes_the_egress_census() -> None:
    assert psn.check_egress(tree()) == []


def test_the_census_is_not_vacuous() -> None:
    """Five destinations written down, and the real policy yields exactly those five (ADR-0645)."""
    assert len(psn.EXPECTED_EGRESS[POLICY]) == 5
    path, policy = tree()[POLICY]
    assert psn.egress_tuples(psn.egress_rules(policy)) == psn.EXPECTED_EGRESS[POLICY]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rules: rules[0].pop("to"),
        lambda rules: rules[0].__setitem__("to", []),
        lambda rules: rules[1].pop("ports"),
        lambda rules: rules[1].__setitem__("ports", []),
        lambda rules: rules.append({"to": [{"ipBlock": {"cidr": "10.96.0.1/32"}}], "ports": [{"protocol": "TCP", "port": 443}]}),
        lambda rules: rules[3]["to"][0]["ipBlock"]["except"].remove("10.96.0.0/16"),
        lambda rules: rules[1]["to"][0]["podSelector"]["matchLabels"].pop("gateway.envoyproxy.io/owning-gateway-name"),
        lambda rules: rules[2]["ports"][0].__setitem__("port", 8443),
        lambda rules: rules[0]["ports"][0].pop("protocol"),
        lambda rules: rules[1]["to"][0]["podSelector"].__setitem__("matchExpressions", [{"key": "x", "operator": "Exists"}]),
        lambda rules: rules[3]["ports"][0].__setitem__("endPort", 65535),
        lambda rules: rules[2]["ports"][0].__setitem__("endPort", 443),
        lambda rules: rules[2]["ports"][0].__setitem__("unmodelled", True),
        lambda rules: rules[3]["ports"][0].pop("port"),
        lambda rules: rules[3]["ports"][0].__setitem__("port", None),
    ],
    ids=[
        "to-deleted", "to-empty", "ports-deleted", "ports-empty", "extra-peer", "except-dropped",
        "selector-label-dropped", "port-changed", "protocol-defaulted", "match-expressions-added",
        "end-port-widens-443", "end-port-equal-to-port", "unmodelled-port-key", "port-key-deleted", "port-null",
    ],
)  # fmt: skip
def test_every_egress_mutant_is_refused_naming_the_policy(mutate) -> None:
    problems = psn.check_egress(with_rules(mutate))
    assert problems, "the mutant passed the egress census"
    assert all(POLICY in problem for problem in problems)


def test_the_extra_peer_is_named() -> None:
    found = with_rules(
        lambda rules: rules.append({"to": [{"ipBlock": {"cidr": "10.96.0.1/32"}}], "ports": [{"protocol": "TCP", "port": 443}]})
    )
    assert any("cidr=10.96.0.1/32" in problem for problem in psn.check_egress(found))


@pytest.mark.parametrize(
    "shape",
    [
        {"ports": [{"protocol": "TCP", "port": 443}]},
        {"to": [{"ipBlock": {"cidr": "1.2.3.4/32"}}]},
        {"to": [{"ipBlock": {"cidr": "1.2.3.4/32"}}], "ports": [{"protocol": "TCP"}]},
        {"to": [{"ipBlock": {"cidr": "1.2.3.4/32"}}], "ports": [{"protocol": "TCP", "port": 443}, {"protocol": "UDP"}]},
    ],
    ids=["to-less", "ports-less", "port-entry-without-port", "second-port-entry-without-port"],
)
def test_a_to_less_or_port_less_rule_is_refused_in_any_policy(shape: dict) -> None:
    """The census covers the policies it names; the open-rule refusal covers every policy the tree declares."""
    found = copy.deepcopy(tree())
    other = next(name for name in found if name != POLICY)
    found[other][1]["spec"].setdefault("egress", []).append(shape)
    problems = psn.check_egress(found)
    assert any(other in problem for problem in problems)


@pytest.mark.parametrize(
    "port",
    [
        {"protocol": "TCP", "port": 1, "endPort": 65535},
        {"protocol": "TCP", "port": 443, "endPort": 444},
        {"protocol": "UDP", "port": 53, "endPort": 53},
        {"port": 1, "endPort": None},
    ],
    ids=["every-port", "narrow-range", "range-of-one", "null-end-port"],
)
def test_an_end_port_is_refused_in_a_policy_outside_the_census(port: dict) -> None:
    """yaadgaar 533: outside the census only `to` and `port` were checked, so `{port: 1, endPort: 65535}` passed."""
    found = copy.deepcopy(tree())
    other = next(name for name in found if name not in psn.EXPECTED_EGRESS)
    found[other][1]["spec"].setdefault("egress", []).append({"to": [{"ipBlock": {"cidr": "1.2.3.4/32"}}], "ports": [port]})
    problems = psn.check_egress(found)
    assert any(other in problem and "endPort" in problem for problem in problems), problems


def test_an_end_port_in_an_existing_rule_outside_the_census_is_refused() -> None:
    found = copy.deepcopy(tree())
    other = next(name for name in found if name not in psn.EXPECTED_EGRESS)
    rules = found[other][1]["spec"]["egress"]
    assert rules and rules[0].get("ports"), "the fixture needs a written-down port to widen"
    rules[0]["ports"][0]["endPort"] = 65535
    assert any(other in problem and "endPort" in problem for problem in psn.check_egress(found))


def test_a_census_policy_end_port_is_still_caught_by_the_census_not_doubled() -> None:
    """Inside the census an `endPort` changes the tuple (yaadgaar 328); the refusal here is for the rest."""
    found = with_rules(lambda rules: rules[3]["ports"][0].__setitem__("endPort", 65535))
    problems = psn.check_egress(found)
    assert any("'443-65535'" in problem for problem in problems)
    assert not any("outside the census" in problem for problem in problems)


def test_main_runs_the_egress_census(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Kills a `check_egress` that is defined but never called from `main`."""
    declared = copy.deepcopy(psn.policies())
    for _path, policy in declared:
        if policy["metadata"]["name"] == POLICY:
            policy["spec"]["egress"][0].pop("to")
    monkeypatch.setattr(psn, "policies", lambda: declared)
    assert psn.main() == 1
    assert POLICY in capsys.readouterr().err


def duplicated(name: str, mutate, namespace: str | None, first: bool) -> list:
    """The real tree plus a copy of policy `name` (mutated, in `namespace`), before or after the original."""
    declared = copy.deepcopy(psn.policies())
    path, original = next((path, policy) for path, policy in declared if policy["metadata"]["name"] == name)
    twin = copy.deepcopy(original)
    if namespace is not None:
        twin["metadata"]["namespace"] = namespace
    mutate(twin["spec"].setdefault("egress", []))
    entry = (path.parent / "aaa" / "networkpolicy.yaml", twin)
    return [entry, *declared] if first else [*declared, entry]


OPEN_EVERY_PORT = lambda rules: rules.append({"to": [{"ipBlock": {"cidr": "0.0.0.0/0"}}], "ports": [{"port": 1, "endPort": 65535}]})  # noqa: E731
OPEN_EVERY_DESTINATION = lambda rules: rules.append({"ports": [{"protocol": "TCP", "port": 443}]})  # noqa: E731


@pytest.mark.parametrize("first", [True, False], ids=["twin-first", "twin-last"])
@pytest.mark.parametrize("namespace", ["aaa", None], ids=["other-namespace", "same-namespace"])
@pytest.mark.parametrize(
    ("name", "mutate"),
    [("post-merge-verifier-egress", OPEN_EVERY_PORT), ("estate-front-egress", OPEN_EVERY_DESTINATION), ("estate-front-egress", lambda rules: None)],
    ids=["non-census-every-port", "census-every-destination", "census-verbatim"],
)
def test_a_duplicate_policy_name_is_refused_and_shadows_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str, mutate, namespace, first: bool
) -> None:
    """#238 review: `found` was keyed by `metadata.name`, so a same-named policy that sorted earlier was overwritten
    by the real one and never examined (rc 0 for a twin opening every port). Any repeated name is refused."""
    declared = duplicated(name, mutate, namespace, first)
    monkeypatch.setattr(psn, "policies", lambda: declared)
    assert psn.main() == 1
    err = capsys.readouterr().err
    assert "declared more than once" in err and name in err, err


def test_duplicate_names_names_each_copy_with_its_namespace() -> None:
    declared = duplicated("post-merge-verifier-egress", lambda rules: None, "aaa", True)
    problems = psn.duplicate_names(declared)
    assert len(problems) == 1
    assert "aaa/post-merge-verifier-egress" in problems[0] and "post-merge-verifier/post-merge-verifier-egress" in problems[0]


def test_the_real_tree_has_no_duplicate_name() -> None:
    assert psn.duplicate_names(psn.policies()) == []


def test_an_end_port_shows_in_the_tuple() -> None:
    """yaadgaar 328: `{port: 443, endPort: 65535}` admitted every port above 443 and the census read `443`."""
    found = with_rules(lambda rules: rules[3]["ports"][0].__setitem__("endPort", 65535))
    assert any("'443-65535'" in problem for problem in psn.check_egress(found))


def test_an_unmodelled_port_key_is_shown_verbatim() -> None:
    found = with_rules(lambda rules: rules[2]["ports"][0].__setitem__("unmodelled", True))
    assert any("'unmodelled': True" in problem for problem in psn.check_egress(found))


def test_a_port_entry_without_a_port_is_named_as_such() -> None:
    found = copy.deepcopy(tree())
    other = next(name for name in found if name != POLICY)
    found[other][1]["spec"].setdefault("egress", []).append({"to": [{"ipBlock": {"cidr": "1.2.3.4/32"}}], "ports": [{"protocol": "TCP"}]})
    assert any(other in problem and "has no `port`" in problem for problem in psn.check_egress(found))


def test_the_policy_gate_reads_yaml_strictly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """yaadgaar 219: a duplicate key in any document the gate walks is reported, not read as its last value."""
    (tmp_path / "p.yaml").write_text("kind: NetworkPolicy\nmetadata:\n  name: a\n  name: b\nspec: {}\n")
    monkeypatch.setattr(psn, "REPOSITORY", tmp_path)
    with pytest.raises(SystemExit, match="duplicate key"):
        psn.policies()
