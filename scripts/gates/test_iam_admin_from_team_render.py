"""iam renders `IAM_ADMIN_FROM_TEAM=true` (team access stage 10; ADR-0962, ADR-0963).

K3 (`yadgar_render.sha256`) alone cannot hold this: `--write` blesses whatever
renders, and the iam chart's default is `false`, so a dropped or misplaced
`adminFromTeam` value renders cleanly and the `is_admin` column decides `/admin`
again. This pins what the value reaches: the env var the iam binary reads. The
offline twin is `scripts/tests/test_iam_admin_from_team.py`.

A FILE OF ITS OWN, not a section of `test_yadgar_application.py`, for the
reason `test_project_validation_render.py` gives: that file is
`scripts/pin_bump.py`'s writer, and the real-render red case below writes a
copy of `applications/yadgar.yaml`.

Needs helm and the registry, like every file in `scripts/gates/`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_no_two_owners import REPOSITORY, a_copy_of_the_tree, parent_render
from test_yadgar_application import env_of, env_value, mutated, set_env

ADMIN_FROM_TEAM_ENV = "IAM_ADMIN_FROM_TEAM"
ADMIN_FROM_TEAM_LINE = "adminFromTeam: true"


def admin_from_team_failures(documents: list) -> dict[str, object]:
    """`{object: rendered value}` when the iam Deployment does not render `"true"`."""
    if not any(d["kind"] == "Deployment" and d["metadata"]["name"] == "iam" for d in documents):
        return {"Deployment/iam": "not rendered"}
    value = env_value(documents, "Deployment", "iam", ADMIN_FROM_TEAM_ENV)
    return {} if value == "true" else {"Deployment/iam": value}


def documents_of(tree: Path) -> list:
    return [d for d in parent_render(tree) if isinstance(d, dict) and d.get("kind")]


@pytest.fixture(scope="module")
def rendered() -> list:
    return documents_of(REPOSITORY)


def test_iam_renders_admin_from_team_true(rendered: list) -> None:
    assert admin_from_team_failures(rendered) == {}


def test_an_iam_rendering_false_names_it(rendered: list) -> None:
    flipped = mutated(rendered, "Deployment", "iam", set_env(ADMIN_FROM_TEAM_ENV, "false"))
    assert admin_from_team_failures(flipped) == {"Deployment/iam": "false"}


def test_an_iam_without_the_env_names_it(rendered: list) -> None:
    def drop(deployment: dict) -> None:
        env_of(deployment)[:] = [e for e in env_of(deployment) if e["name"] != ADMIN_FROM_TEAM_ENV]

    assert admin_from_team_failures(mutated(rendered, "Deployment", "iam", drop)) == {"Deployment/iam": None}


def test_a_missing_iam_names_it(rendered: list) -> None:
    others = [d for d in rendered if not (d["kind"] == "Deployment" and d["metadata"]["name"] == "iam")]
    assert admin_from_team_failures(others) == {"Deployment/iam": "not rendered"}


def test_the_line_removed_renders_the_chart_default(tmp_path: Path) -> None:
    """The red case on a REAL render: without the line, the chart default `false` reaches the pod."""
    tree = a_copy_of_the_tree(tmp_path)
    application = tree / "applications" / "yadgar.yaml"
    text = application.read_text()
    assert text.count(ADMIN_FROM_TEAM_LINE) == 1
    kept = [line for line in text.splitlines() if ADMIN_FROM_TEAM_LINE not in line]
    application.write_text("\n".join(kept) + "\n")
    assert admin_from_team_failures(documents_of(tree)) == {"Deployment/iam": "false"}
