"""`scripts/pin_dryrun.py`'s read table covers what main renders (ledger 1632 C4).

`pin_dryrun.py` refuses a pin whose render holds a kind outside `READS`, so a
parent release that adds a kind would turn every automatic pin red on the
private runner. This gate renders main here, in CI, and fails first, naming
the kind. It also proves the render holds no Secret: the runner's
ServiceAccount cannot read one, and `pin_dryrun.py` refuses to try.

A GATE, NOT A HOOK: it needs helm 3.18.4 and the registry (`helm pull` of the
parent at the pin), as the other files in `scripts/gates/` do.
"""

from __future__ import annotations

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
