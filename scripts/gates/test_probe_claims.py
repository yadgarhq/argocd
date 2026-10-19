"""The probe's claims against the declared registry (ledger 1624).

`scripts/project_probe.py` reaches each refusal class through a fixed claim.
The class a claim reaches depends on what `seeder.registry` declares (gateway
`src/project/classify.rs`): a claim under a declared single-segment root lands
on that anchor and counts `PROJECT_UNREGISTERED_ORG`; a claim with no declared
ancestor counts `PROJECT_UNRESOLVABLE`. A registry change that moved either
claim into another class would turn the probe's arm into a measurement of
nothing, so these tests bind the claims to the declaration.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location("project_probe", REPOSITORY / "scripts" / "project_probe.py")
pp = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("project_probe", pp)
_spec.loader.exec_module(pp)


def registry() -> dict:
    doc = yaml.safe_load((REPOSITORY / "applications" / "yadgar.yaml").read_text())
    return doc["spec"]["source"]["helm"]["valuesObject"]["seeder"]["registry"]


def declared() -> set[str]:
    reg = registry()
    return {r["path"].lower() for r in reg["roots"]} | {p["path"].lower() for p in reg["projects"]}


def ancestors(claim: str) -> list[str]:
    parts = claim.lower().split("/")
    return ["/".join(parts[:i]) for i in range(len(parts), 0, -1)]


def nearest(claim: str) -> str | None:
    return next((a for a in ancestors(claim) if a in declared()), None)


def test_the_org_claims_nearest_declared_ancestor_is_a_single_segment_root() -> None:
    hit = nearest(pp.ORG_CLAIM)
    assert hit is not None and "/" not in hit, hit
    assert hit in {r["path"].lower() for r in registry()["roots"]}


def test_the_unresolvable_claim_has_no_declared_ancestor() -> None:
    assert nearest(pp.CLAIM) is None


# A canonical v7 segment standing in for whatever `GET /auth/whoami` answers
# at run time (ADR-0959). The personal claim is built from it, so the check
# below holds for every account the probe could log in as.
SOME_SEGMENT = "0192f0a0-0000-7000-8000-00000000a11c"


def test_the_personal_claim_is_under_the_callers_own_segment_and_undeclared() -> None:
    claim = pp.personal_claim(SOME_SEGMENT)
    assert claim.split("/")[:2] == ["local", SOME_SEGMENT]
    assert nearest(claim) is None


def test_the_seeder_declares_nothing_under_the_reserved_local_root() -> None:
    # The personal arm counts PROJECT_UNREGISTERED_PERSONAL only while no
    # ancestor of `local/<own>/probe-never-registered` is registered. The
    # declared registry is checked here; private rows made at run time cannot be
    # read by the probe, whose leg (c) goes red and names the cause instead.
    assert not [p for p in declared() if p == "local" or p.startswith("local/")]


def test_the_not_member_claim_is_undeclared() -> None:
    assert nearest(pp.NOT_MEMBER_CLAIM) is None
