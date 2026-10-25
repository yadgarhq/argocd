"""iam derives `is_admin` from the `administrators` team (team access stage 10; ADR-0962, ADR-0963).

The iam chart ships `adminFromTeam: false`, so removing the one line in
`applications/yadgar.yaml` silently turns derivation off: the render still
succeeds, Argo still syncs, and the `iam_user.is_admin` column decides `/admin`
again. K3 (`scripts/gates/yadgar_render.sha256`) does not hold this, because
its `--write` blesses whatever renders. This file pins the value offline, so
the pre-commit hook reddens on it;
`scripts/gates/test_iam_admin_from_team_render.py` pins the env var it renders
to.

The rollback is a revert of the switch's merge. The revert deletes this file
with the line it pins, and the chart default `false` returns: that is the
plan's rollback, "set IAM_ADMIN_FROM_TEAM=false". Setting only the line to
`false` leaves this test red, on purpose.
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
APPLICATION = REPOSITORY / "applications" / "yadgar.yaml"


def admin_from_team_errors(document: dict) -> list[str]:
    """Why `document`'s iam does not derive admin from the team; empty when it does."""
    values = document.get("spec", {}).get("source", {}).get("helm", {}).get("valuesObject", {})
    iam = values.get("iam")
    if not isinstance(iam, dict):
        return ["valuesObject.iam is absent"]
    if "adminFromTeam" not in iam:
        return ["valuesObject.iam.adminFromTeam is absent: the chart default is false"]
    if iam["adminFromTeam"] is not True:
        return [f"valuesObject.iam.adminFromTeam is {iam['adminFromTeam']!r}, not True"]
    return []


def committed() -> dict:
    return yaml.safe_load(APPLICATION.read_text())


def test_iam_derives_admin_from_the_team() -> None:
    assert admin_from_team_errors(committed()) == []


def test_a_removed_value_reddens() -> None:
    document = copy.deepcopy(committed())
    document["spec"]["source"]["helm"]["valuesObject"]["iam"].pop("adminFromTeam", None)
    assert admin_from_team_errors(document) == [
        "valuesObject.iam.adminFromTeam is absent: the chart default is false"
    ]


def test_the_value_back_on_false_reddens() -> None:
    document = copy.deepcopy(committed())
    document["spec"]["source"]["helm"]["valuesObject"]["iam"]["adminFromTeam"] = False
    assert admin_from_team_errors(document) == ["valuesObject.iam.adminFromTeam is False, not True"]


def test_a_string_true_reddens() -> None:
    document = copy.deepcopy(committed())
    document["spec"]["source"]["helm"]["valuesObject"]["iam"]["adminFromTeam"] = "true"
    assert admin_from_team_errors(document) == ["valuesObject.iam.adminFromTeam is 'true', not True"]


def test_a_removed_iam_block_reddens() -> None:
    document = copy.deepcopy(committed())
    del document["spec"]["source"]["helm"]["valuesObject"]["iam"]
    assert admin_from_team_errors(document) == ["valuesObject.iam is absent"]
