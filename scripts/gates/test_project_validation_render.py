"""The gateway renders `YADGAR_PROJECT_VALIDATION_MODE=enforcing` (ADR-0967).

K3 (`yadgar_render.sha256`) alone cannot hold this: `--write` blesses whatever
renders, and the gateway chart's default is `counting`, so a dropped or
misplaced `projectValidation` value renders cleanly and only counts refusals
again. This pins what the value reaches: the env var the gateway binary reads.
The offline twin is `scripts/tests/test_project_validation_mode.py`.

A FILE OF ITS OWN, not a section of `test_yadgar_application.py`: that file is
`scripts/pin_bump.py`'s writer, and `test_pin_bump_gate_logs.py` holds it to
writing the table and nothing else. The real-render red case below writes a
copy of `applications/yadgar.yaml`, so it lives here.

Needs helm 3.18.4 and the registry, like every file in `scripts/gates/`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_no_two_owners import REPOSITORY, a_copy_of_the_tree, parent_render
from test_yadgar_application import env_of, env_value, mutated, set_env

PROJECT_VALIDATION_ENV = "YADGAR_PROJECT_VALIDATION_MODE"
ORG_GRANTS_ENV = "YADGAR_PROJECT_VALIDATION_ORG_GRANTS"
PROJECT_VALIDATION_LINE = "projectValidation: { mode: enforcing, orgGrants: enforcing }"


def project_validation_failures(documents: list) -> dict[str, object]:
    """`{object: rendered value}` when the gateway Deployment does not render `enforcing`."""
    if not any(d["kind"] == "Deployment" and d["metadata"]["name"] == "gateway" for d in documents):
        return {"Deployment/gateway": "not rendered"}
    value = env_value(documents, "Deployment", "gateway", PROJECT_VALIDATION_ENV)
    return {} if value == "enforcing" else {"Deployment/gateway": value}


def org_grants_failures(documents: list) -> dict[str, object]:
    """`{object: rendered value}` when the gateway Deployment does not render `orgGrants: enforcing`."""
    if not any(d["kind"] == "Deployment" and d["metadata"]["name"] == "gateway" for d in documents):
        return {"Deployment/gateway": "not rendered"}
    value = env_value(documents, "Deployment", "gateway", ORG_GRANTS_ENV)
    return {} if value == "enforcing" else {"Deployment/gateway": value}


def documents_of(tree: Path) -> list:
    return [d for d in parent_render(tree) if isinstance(d, dict) and d.get("kind")]


@pytest.fixture(scope="module")
def rendered() -> list:
    return documents_of(REPOSITORY)


def test_the_gateway_renders_enforcing(rendered: list) -> None:
    assert project_validation_failures(rendered) == {}


def test_the_gateway_renders_org_grants_enforcing(rendered: list) -> None:
    assert org_grants_failures(rendered) == {}


def test_a_gateway_rendering_org_grants_counting_names_it(rendered: list) -> None:
    flipped = mutated(rendered, "Deployment", "gateway", set_env(ORG_GRANTS_ENV, "counting"))
    assert org_grants_failures(flipped) == {"Deployment/gateway": "counting"}


def test_a_gateway_rendering_counting_names_it(rendered: list) -> None:
    flipped = mutated(rendered, "Deployment", "gateway", set_env(PROJECT_VALIDATION_ENV, "counting"))
    assert project_validation_failures(flipped) == {"Deployment/gateway": "counting"}


def test_a_gateway_without_the_mode_env_names_it(rendered: list) -> None:
    def drop(deployment: dict) -> None:
        env_of(deployment)[:] = [e for e in env_of(deployment) if e["name"] != PROJECT_VALIDATION_ENV]

    assert project_validation_failures(mutated(rendered, "Deployment", "gateway", drop)) == {
        "Deployment/gateway": None
    }


def test_a_missing_gateway_names_it(rendered: list) -> None:
    others = [d for d in rendered if not (d["kind"] == "Deployment" and d["metadata"]["name"] == "gateway")]
    assert project_validation_failures(others) == {"Deployment/gateway": "not rendered"}


def test_the_mode_line_removed_renders_the_chart_default(tmp_path: Path) -> None:
    """The red case on a REAL render: without the line, the chart default `counting` reaches the pod."""
    tree = a_copy_of_the_tree(tmp_path)
    application = tree / "applications" / "yadgar.yaml"
    text = application.read_text()
    assert text.count(PROJECT_VALIDATION_LINE) == 1
    kept = [line for line in text.splitlines() if PROJECT_VALIDATION_LINE not in line]
    application.write_text("\n".join(kept) + "\n")
    assert project_validation_failures(documents_of(tree)) == {"Deployment/gateway": "counting"}
