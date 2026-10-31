"""`scripts/pin_dryrun.py`'s read table covers what main renders (ledger 1632 C4).

`pin_dryrun.py` refuses a pin whose render holds a kind outside `READS`, so a
parent release that adds a kind would turn every automatic pin red on the
private runner. This gate renders main here, in CI, and fails first, naming
the kind. It also proves the render holds no Secret: the runner's
ServiceAccount cannot read one, and `pin_dryrun.py` refuses to try.

The same for the five operator Applications (car C10 part 2): main's files
rendered at main's `platform_version`, CRDs included, hold exactly the steady
kinds of `OPERATOR_READS` and no Secret, so a platform release that adds a kind
fails here before it turns a platform pin red on the runner.

A GATE, NOT A HOOK: it needs helm 3.18.4 and the registry (`helm pull` of the
parent at the pin), as the other files in `scripts/gates/` do.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from test_no_two_owners import REPOSITORY, parent_render

sys.path.insert(0, str(REPOSITORY / "scripts"))

import pin_dryrun  # noqa: E402


@pytest.fixture(scope="module")
def steady() -> list:
    """Main's render, `helm.sh/hook` objects left out, as `pin_dryrun.py` compares it."""
    documents = [d for d in parent_render(REPOSITORY) if isinstance(d, dict) and d.get("kind")]
    return [d for d in documents if not pin_dryrun.is_hook(d)]


def test_every_kind_main_renders_is_in_the_read_table(steady: list) -> None:
    missing = sorted({"/".join(pin_dryrun.group_kind(d)) for d in steady} - {"/".join(k) for k in pin_dryrun.READS})
    assert missing == [], f"add these to pin_dryrun.READS (and check the runner's ClusterRole reads them): {missing}"


def test_main_renders_no_secret(steady: list) -> None:
    assert [pin_dryrun.object_key(d) for d in steady if d.get("kind") == "Secret"] == []


def test_every_object_lands_in_the_yadgar_namespace(steady: list) -> None:
    elsewhere = [
        pin_dryrun.object_key(d)
        for d in steady
        if (d.get("metadata") or {}).get("namespace", pin_dryrun.TARGET_NAMESPACE) != pin_dryrun.TARGET_NAMESPACE
    ]
    assert elsewhere == []


def test_the_read_table_names_only_kinds_main_renders(steady: list) -> None:
    # A stale entry widens nothing (the ServiceAccount decides what is readable),
    # but it hides a kind the chart dropped. Kept exact.
    unused = sorted({"/".join(k) for k in pin_dryrun.READS} - {"/".join(pin_dryrun.group_kind(d)) for d in steady})
    assert unused == [], f"pin_dryrun.READS names kinds main no longer renders: {unused}"


@pytest.fixture(scope="module")
def operator_steady() -> list:
    """Main's five operator Applications at main's `platform_version`, hooks left out, as step 9 reads them."""
    pin_bump = pin_dryrun._load("pin_bump")
    platform = json.loads((REPOSITORY / pin_dryrun.CHART_PIN).read_text())["platform_version"]
    helm = shutil.which("helm")
    assert helm, "helm 3.18.4 on PATH"
    documents = [
        d
        for path in pin_dryrun.OPERATOR_FILES
        for d in pin_bump.render_operator((REPOSITORY / path).read_text(), platform, helm)
        if d.get("kind")
    ]
    return [d for d in documents if not pin_dryrun.is_hook(d)]


def test_every_kind_the_operators_render_is_in_the_operator_read_table(operator_steady: list) -> None:
    kinds = {"/".join(pin_dryrun.group_kind(d)) for d in operator_steady}
    table = {"/".join(k) for k in pin_dryrun.OPERATOR_READS}
    assert sorted(kinds - table) == [], "add these to pin_dryrun.OPERATOR_READS (and the runner's ClusterRole)"
    assert sorted(table - kinds) == [], "pin_dryrun.OPERATOR_READS names kinds the operators no longer render"


def test_the_operators_render_no_secret(operator_steady: list) -> None:
    assert [pin_dryrun.object_key(d) for d in operator_steady if d.get("kind") == "Secret"] == []
