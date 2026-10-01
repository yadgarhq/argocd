#!/usr/bin/env python3
"""`scripts/chart_pin.json`'s `platform_version` must equal `platform`'s pin in
`yadgarhq/chart`'s `chart/Chart.yaml`, at that same file's `chart_tag` (ledger
1206).

WHY THIS IS A SEPARATE CI JOB AND NOT A PRE-COMMIT HOOK. `operator-applications`
(scripts/tests/test_operator_applications.py) asserts every platform-sourced
Application's `targetRevision` equals `PLATFORM_VERSION`, which this repository
now reads from the committed `scripts/chart_pin.json` rather than a literal. That
gate needs no network and runs on every commit through pre-commit. What it
CANNOT check, without a real request, is whether the committed file itself still
describes `yadgarhq/chart`'s actual state — so this script makes that one
request, on pull requests only, and leaves the hook offline.

NO TOKEN REACHES THE LOG. `GITHUB_TOKEN` travels through the environment, is
read once, and is never interpolated into a printed string, an error message, or
a URL. `yadgarhq/chart` is public, so the only thing the token buys here is a
higher, authenticated rate limit — it is not what makes the read possible.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parent.parent
CHART_PIN = REPOSITORY / "scripts" / "chart_pin.json"
API = "https://api.github.com/repos/yadgarhq/chart/contents/chart/Chart.yaml"


def committed_pin() -> dict:
    return json.loads(CHART_PIN.read_text())


def fetch_chart_yaml(chart_tag: str) -> str:
    """The text of `chart/Chart.yaml` at `chart_tag`, via the contents API.

    Raises `urllib.error.HTTPError` / `URLError` on failure, which `main`
    reports without the request's own text — that text could carry the token
    were it ever echoed back, and GitHub does not do that, but the caller does
    not need to trust that to stay safe.
    """
    request = urllib.request.Request(f"{API}?ref={chart_tag}")
    request.add_header("Accept", "application/vnd.github+json")
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.loads(response.read())
    return base64.b64decode(payload["content"]).decode()


def platform_version_at(chart_tag: str) -> str | None:
    """`platform`'s pinned `version` among `chart/Chart.yaml`'s `dependencies`, or `None`."""
    document = yaml.safe_load(fetch_chart_yaml(chart_tag)) or {}
    for dependency in document.get("dependencies") or []:
        if isinstance(dependency, dict) and dependency.get("name") == "platform":
            version = dependency.get("version")
            return version if isinstance(version, str) else None
    return None


def main() -> int:
    pin = committed_pin()
    chart_tag, committed_version = pin.get("chart_tag"), pin.get("platform_version")
    if not chart_tag or not committed_version:
        print(
            f"::error file={CHART_PIN}::`chart_tag` and `platform_version` must both be "
            f"non-empty strings; got {pin!r}."
        )
        return 1

    try:
        live_version = platform_version_at(chart_tag)
    except (urllib.error.URLError, json.JSONDecodeError, yaml.YAMLError, KeyError) as exc:
        print(
            f"::error file={CHART_PIN}::could not read `platform`'s version from "
            f"`yadgarhq/chart@{chart_tag}`'s `chart/Chart.yaml`: {type(exc).__name__}."
        )
        return 1

    if live_version is None:
        print(
            f"::error file={CHART_PIN}::`yadgarhq/chart@{chart_tag}`'s `chart/Chart.yaml` "
            f"carries no `platform` dependency any more. `{CHART_PIN.name}` needs a new "
            f"`chart_tag` this organisation actually runs."
        )
        return 1

    if live_version != committed_version:
        print(
            f"::error file={CHART_PIN}::`{CHART_PIN.name}` says `platform_version` is "
            f"`{committed_version}`, but `yadgarhq/chart@{chart_tag}`'s `chart/Chart.yaml` "
            f"pins `platform` at `{live_version}`. Update `{CHART_PIN.name}` (and the "
            f"`targetRevision` of every platform-sourced Application under `applications/` "
            f"to match) to re-close ledger 1206."
        )
        return 1

    print(f"{CHART_PIN.name}: platform_version {committed_version} matches yadgarhq/chart@{chart_tag}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
