"""`install/values.yaml` carries the chart's two Argo health overrides (ledger 1221).

`yadgarhq/chart`'s kind bootstrap (`bootstrap/argocd-values.yaml`) overrides two
of Argo CD v3.1.8's built-in health scripts. Each one stops a sync from failing
on a state that is not a failure:

- `apiextensions.k8s.io_CustomResourceDefinition` (chart#21, ledger 1197): a CRD
  that is still Installing, or whose conditions hold no `Established` entry yet,
  reads Progressing, not Degraded.
- `k8s.mariadb.com_MariaDB` (chart#22, ledger 1205): the one mariadb-operator
  cache race on its own `mariadb.sys` Grant reads Progressing, not Degraded.

This organisation's Argo runs the same Applications, so it needs the same two
scripts. A copy that drifts from the chart's is a second, untested health rule.

THE PIN. `scripts/tests/fixtures/chart_health_overrides.json` records where the
scripts were copied from (`yadgarhq/chart` tag and commit) and the sha256 of
each script. The digest is the one the chart's own
`scripts/tests/test_argocd_values.py` pins (`OVERRIDE_SHA256`,
`MARIADB_OVERRIDE_SHA256`), computed the same way: the parsed YAML string,
trailing newlines stripped. Parsed, not raw bytes, so a YAML re-layout by
prettier (`key: |` against `key:\\n  |`) does not move it.

THE SOURCE IS NOT `chart_pin.json`'s `chart_tag`. That tag follows the `yadgar`
Application's `targetRevision` (v0.3.13 today), and v0.3.13's
`bootstrap/argocd-values.yaml` holds neither override. Both overrides first
appear together at v0.3.18.

TO MOVE THE PIN: copy the new scripts from the chart at a newer tag, then
update the tag, the commit and both digests in the fixture together.

Run: python3 -m pytest scripts/tests/test_install_health_overrides.py -q
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
VALUES = REPOSITORY / "install" / "values.yaml"
PIN = REPOSITORY / "scripts" / "tests" / "fixtures" / "chart_health_overrides.json"

CRD_KEY = "resource.customizations.health.apiextensions.k8s.io_CustomResourceDefinition"
MARIADB_KEY = "resource.customizations.health.k8s.mariadb.com_MariaDB"


def _pin() -> dict:
    return json.loads(PIN.read_text())


def _sha256(script: str) -> str:
    return hashlib.sha256(script.rstrip("\n").encode()).hexdigest()


def _cm() -> dict:
    values = yaml.safe_load(VALUES.read_text())
    return (values.get("configs") or {}).get("cm") or {}


def test_the_pin_names_exactly_the_two_overrides() -> None:
    assert set(_pin()["sha256"]) == {CRD_KEY, MARIADB_KEY}


@pytest.mark.parametrize("key", [CRD_KEY, MARIADB_KEY])
def test_the_override_is_present(key: str) -> None:
    cm = _cm()
    assert key in cm, (
        f"install/values.yaml configs.cm has no {key!r}. Copy it from "
        f"{_pin()['repository']}@{_pin()['tag']} {_pin()['path']}."
    )
    assert isinstance(cm[key], str), f"{key} must be a Lua string, got {type(cm[key])}"


@pytest.mark.parametrize("key", [CRD_KEY, MARIADB_KEY])
def test_the_override_equals_the_chart_copy(key: str) -> None:
    pin = _pin()
    script = _cm().get(key)
    assert script is not None, f"install/values.yaml configs.cm has no {key!r}"
    assert _sha256(script) == pin["sha256"][key], (
        f"{key} differs from {pin['repository']}@{pin['tag']} "
        f"({pin['commit'][:12]}) {pin['path']}. Copy the script from there "
        "again; do not edit it here. If the chart moved, update the fixture's "
        "tag, commit and digest together."
    )
