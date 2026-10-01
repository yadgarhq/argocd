#!/usr/bin/env python3
"""`scripts/chart_pin.json` must still describe a running chart (ledger 1206).

TWO INDEPENDENT CHECKS, because one tag alone cannot prove either half:

  platform-version  `platform_version` equals `platform`'s pin among
                     `yadgarhq/chart@<chart_tag>`'s `chart/Chart.yaml`
                     `dependencies`. A git tag never moves, so this check alone
                     cannot detect staleness: `chart_tag` could be any tag that
                     ever existed and this check would still pass against IT.
  chart-tag          `chart_tag` equals `"v" + targetRevision`, where
                     `targetRevision` is read from the Application THIS
                     ORGANISATION actually runs — this repository's own
                     `applications/yadgar.yaml`, `spec.source.targetRevision`
                     (or, for a `sources` list, the entry with a `chart:` key).
                     This is the half that can detect staleness: if the
                     running pin moves and nobody updates `chart_pin.json`,
                     THIS check reddens even though `platform-version` above
                     still passes against the (now wrong) `chart_tag`.

OPTION A LANDED, AND THIS READ FOLLOWED IT. The `yadgar` Application moved out
of `yadgarhq/deploy` (`infra/yadgar-app.yaml`) and into this repository as
`applications/yadgar.yaml` when `infra` retired (ADR-0824), in the same pull
request that repointed this read. So the chart-tag half reads a LOCAL file and
makes no request; `scripts/tests/test_check_chart_pin.py` covers it offline.

WHY THIS IS A SEPARATE CI JOB AND NOT A PRE-COMMIT HOOK. `operator-applications`
(scripts/tests/test_operator_applications.py) asserts every platform-sourced
Application's `targetRevision` equals `PLATFORM_VERSION`, which this repository
now reads from the committed `scripts/chart_pin.json` rather than a literal. That
gate needs no network and runs on every commit through pre-commit. The
platform-version half above cannot run without a real request, so this script
runs here instead, on pull requests only, and the hook stays offline.

THE ONE READ USES A DEFAULT `GITHUB_TOKEN`, NO WIDER PERMISSION.
`yadgarhq/chart` is a public repository (checked by hand,
`gh api repos/yadgarhq/chart --jq .private` → `false`), so a workflow's default
`GITHUB_TOKEN` — scoped read-only to the calling repository's own `contents` —
reads it exactly as an unauthenticated request would.

NO TOKEN REACHES THE LOG. `GITHUB_TOKEN` travels through the environment, is
read once, and is never interpolated into a printed string, an error message, or
a URL. The token is passed only to raise the rate limit — it is not what makes
either read possible.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parent.parent
CHART_PIN = REPOSITORY / "scripts" / "chart_pin.json"

CHART_REPO = "yadgarhq/chart"
CHART_PATH = "chart/Chart.yaml"

# SEE "OPTION A" ABOVE. The file this organisation's running `yadgar`
# Application lives in.
APPLICATION = REPOSITORY / "applications" / "yadgar.yaml"

API = "https://api.github.com/repos/{repo}/contents/{path}"


def committed_pin() -> dict:
    return json.loads(CHART_PIN.read_text())


def fetch_contents(repo: str, path: str, ref: str | None = None) -> str:
    """The text of `path` in `repo`, via the contents API, optionally at `ref`.

    Raises `urllib.error.HTTPError` / `URLError` on failure, which `main`
    reports without the request's own text — that text could carry the token
    were it ever echoed back, and GitHub does not do that, but the caller does
    not need to trust that to stay safe.
    """
    url = API.format(repo=repo, path=path)
    if ref:
        url = f"{url}?ref={ref}"
    request = urllib.request.Request(url)
    request.add_header("Accept", "application/vnd.github+json")
    token = os.environ.get("GITHUB_TOKEN", "")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.loads(response.read())
    return base64.b64decode(payload["content"]).decode()


def platform_version_at(chart_tag: str) -> str | None:
    """`platform`'s pinned `version` among `chart/Chart.yaml`'s `dependencies`, or `None`."""
    document = yaml.safe_load(fetch_contents(CHART_REPO, CHART_PATH, ref=chart_tag)) or {}
    for dependency in document.get("dependencies") or []:
        if isinstance(dependency, dict) and dependency.get("name") == "platform":
            version = dependency.get("version")
            return version if isinstance(version, str) else None
    return None


def running_target_revision(application: Path = APPLICATION) -> str | None:
    """The chart pull's `targetRevision` from the running `yadgar` Application, or `None`.

    `spec` carries either a single `source` or a list `sources`. This
    Application uses the single form since option A. In deploy it used the list
    form, with a SECOND entry (`ref: self`, no `chart` key) feeding the first a
    values file. The entry THIS FUNCTION WANTS is whichever one carries a
    `chart` key, in either form; reading `sources[0]` unconditionally would
    silently read the wrong source the day a list is reordered.
    """
    document = yaml.safe_load(application.read_text()) or {}
    spec = document.get("spec") or {}
    sources = spec.get("sources")
    if not isinstance(sources, list):
        single = spec.get("source")
        sources = [single] if isinstance(single, dict) else []
    for source in sources:
        if isinstance(source, dict) and source.get("chart"):
            revision = source.get("targetRevision")
            return revision if isinstance(revision, str) else None
    return None


def check_platform_version(chart_tag: str, committed_version: str) -> str | None:
    """`None` on a match, else the error line."""
    try:
        live_version = platform_version_at(chart_tag)
    except (urllib.error.URLError, json.JSONDecodeError, yaml.YAMLError, KeyError) as exc:
        return (
            f"could not read `platform`'s version from `{CHART_REPO}@{chart_tag}`'s "
            f"`{CHART_PATH}`: {type(exc).__name__}."
        )
    if live_version is None:
        return (
            f"`{CHART_REPO}@{chart_tag}`'s `{CHART_PATH}` carries no `platform` dependency "
            f"any more. `{CHART_PIN.name}` needs a new `chart_tag` this organisation actually "
            f"runs."
        )
    if live_version != committed_version:
        return (
            f"`{CHART_PIN.name}` says `platform_version` is `{committed_version}`, but "
            f"`{CHART_REPO}@{chart_tag}`'s `{CHART_PATH}` pins `platform` at `{live_version}`. "
            f"Update `{CHART_PIN.name}` (and the `targetRevision` of every platform-sourced "
            f"Application under `applications/` to match) to re-close ledger 1206."
        )
    return None


def check_chart_tag(chart_tag: str, application: Path = APPLICATION) -> str | None:
    """`None` on a match, else the error line. This is the staleness half (ledger 1206)."""
    where = application.relative_to(REPOSITORY) if application.is_relative_to(REPOSITORY) else application
    try:
        revision = running_target_revision(application)
    except (OSError, yaml.YAMLError, AttributeError) as exc:
        return (
            f"could not read the running `targetRevision` from `{where}`: "
            f"{type(exc).__name__}. If the `yadgar` Application has moved, repoint "
            f"`APPLICATION` in this script at its new location."
        )
    if revision is None:
        return f"`{where}` carries no chart source with a `targetRevision` any more."
    running_tag = f"v{revision}"
    if chart_tag != running_tag:
        return (
            f"`{CHART_PIN.name}` says `chart_tag` is `{chart_tag}`, but the running `yadgar` "
            f"Application (`{where}`) pins `targetRevision: "
            f"{revision}` — `{running_tag}`. A tag never moves, so this is the check that "
            f"catches a `chart_pin.json` that went stale after the organisation moved to a "
            f"newer chart release. Update `{CHART_PIN.name}`'s `chart_tag` (and re-verify "
            f"`platform_version` against it) to re-close ledger 1206."
        )
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

    errors = [
        error
        for error in (check_platform_version(chart_tag, committed_version), check_chart_tag(chart_tag))
        if error
    ]
    for error in errors:
        print(f"::error file={CHART_PIN}::{error}")
    if errors:
        return 1

    print(
        f"{CHART_PIN.name}: platform_version {committed_version} matches {CHART_REPO}@{chart_tag}, "
        f"and chart_tag matches the running yadgar Application's targetRevision "
        f"({APPLICATION.relative_to(REPOSITORY)})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
