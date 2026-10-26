"""The gateway's project validation is `enforcing` (ADR-0967, superseding ADR-0841's `counting` hold).

The gateway chart ships `projectValidation.mode: counting`, so removing the one
line in `applications/yadgar.yaml` silently turns enforcement off: the render
still succeeds, Argo still syncs, and every refusal is only counted again. K3
(`scripts/gates/yadgar_render.sha256`) does not hold this, because its
`--write` blesses whatever renders. This file pins the value offline, so the
pre-commit hook reddens on it;
`scripts/gates/test_project_validation_render.py` pins the env var it renders
to.

The rollback is a revert of the flip's merge, which deletes this file with the
line it pins; removing only the line leaves this test red, on purpose.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
APPLICATION = REPOSITORY / "applications" / "yadgar.yaml"
EXPECTED_MODE = "enforcing"


def mode_errors(document: dict) -> list[str]:
    """Why `document`'s gateway project validation is not `enforcing`; empty when it is."""
    values = document.get("spec", {}).get("source", {}).get("helm", {}).get("valuesObject", {})
    gateway = values.get("gateway")
    if not isinstance(gateway, dict):
        return ["valuesObject.gateway is absent"]
    validation = gateway.get("projectValidation")
    if not isinstance(validation, dict) or "mode" not in validation:
        return ["valuesObject.gateway.projectValidation.mode is absent: the chart default is counting"]
    if validation["mode"] != EXPECTED_MODE:
        return [f"valuesObject.gateway.projectValidation.mode is {validation['mode']!r}, not {EXPECTED_MODE!r}"]
    return []


def committed() -> dict:
    return yaml.safe_load(APPLICATION.read_text())


def test_the_gateway_enforces_project_validation() -> None:
    assert mode_errors(committed()) == []


def test_a_removed_mode_reddens() -> None:
    document = copy.deepcopy(committed())
    del document["spec"]["source"]["helm"]["valuesObject"]["gateway"]["projectValidation"]
    assert mode_errors(document) == [
        "valuesObject.gateway.projectValidation.mode is absent: the chart default is counting"
    ]


def test_mode_back_on_counting_reddens() -> None:
    document = copy.deepcopy(committed())
    document["spec"]["source"]["helm"]["valuesObject"]["gateway"]["projectValidation"]["mode"] = "counting"
    assert mode_errors(document) == ["valuesObject.gateway.projectValidation.mode is 'counting', not 'enforcing'"]


def test_org_grants_is_enforcing() -> None:
    """Team access stage 12: the grant refusal answers. Rollback is `counting`; this pin goes with it."""
    validation = committed()["spec"]["source"]["helm"]["valuesObject"]["gateway"]["projectValidation"]
    assert validation.get("orgGrants") == "enforcing"


def test_a_removed_gateway_block_reddens() -> None:
    document = copy.deepcopy(committed())
    del document["spec"]["source"]["helm"]["valuesObject"]["gateway"]
    assert mode_errors(document) == ["valuesObject.gateway is absent"]
