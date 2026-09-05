#!/usr/bin/env python3
"""A module that has released stays pinned, and losing its pin is a red build.

LEDGER 608. `applicationsets/modules.yaml` reads `versions/<module>.yaml` as a
second Helm source with `ignoreMissingValueFiles: true`. Delete one of those
files and helm is handed no `-f`, `image.digest` renders empty, the module
chart's deployment template takes its `else` arm, and the pod is back on
`image.tag: latest` — the moving tag D81 was written about. Argo CD reports
Synced and Healthy throughout and raises no condition, so a one-file deletion
silently returns a module to a moving tag. That silence is the defect; this gate
is the signal.

THE ASYMMETRY THIS GATE EXISTS FOR. `ignoreMissingValueFiles` cannot be removed
as the fix: a module that has never released has no file, and without the flag
its Application fails to render rather than falling back. So "never released"
and "released, then the pin was deleted" have to be told apart, and no single
file can do it — the file whose absence is the question is the one that was
deleted.

THIS REPOSITORY'S OWN HISTORY IS THE RECORD, and that is the whole trick. A
module that has released has a commit here that wrote its version file, because
`ci-release` writes one on every release. A module that has never released has
no such commit. History answers the question the working tree cannot, and it
answers it without anybody maintaining a list — which is what D54 refuses. A new
module needs no entry here, and this file names no module.

SO A SHALLOW CHECKOUT IS A FAILURE AND NOT A DEGRADED PASS, which is why it is
the first thing checked. A shallow root commit reports its ENTIRE tree under
`git log --name-only`, so on `--depth 1` the derived "ever written" set collapses
to "whatever is present right now". A pin deleted before the clone was made is
then absent from BOTH halves of the comparison, the two sets agree, and this
script prints its success sentence and exits 0 — the exact silence it exists to
break, turned on itself. `git rev-parse --is-shallow-repository` is asked first
and anything but a confident `false` is refused, so the gate no longer rests on a
`fetch-depth: 0` written in another repository for unrelated reasons.

WHY A GATE AND NOT A RUNTIME ALARM. Argo CD v3.1.8 has no health check for a
kind it does not know and `argocd-cm` carries no `resource.customizations.health.*`
entry, so "the Application goes Degraded" is not a mechanism that exists to be
relied on. The paths that reach `main` here are two: a pull request, gated by the
required status check `ci / passed`, which this hook runs inside; and the release
App's ruleset bypass, which writes a version file rather than deleting one. What
closing the pull request path buys is precise, and worth stating exactly: a pull
request that deletes or empties a released module's pin, does not itself modify
this gate, and runs on a full-history checkout is refused. All three qualifiers
are load-bearing — one pull request can delete this file and a pin together, and
`main` requires no approving review, so this is a signal rather than a wall.

IT REVALIDATES THE WHOLE TREE, and that is its best property rather than an
inefficiency. `always_run: true` with `pass_filenames: false` means every pull
request re-asks the question of every released module, not only of the files that
pull request touched — so a bad state that arrived by some other path, the
release App's bypass included, is caught by the next pull request to run at all.

RETIRING A MODULE NEEDS NO EXCEPTION HERE, and that is a property of the design
rather than an omission. A module leaves the cluster by losing the
`yadgar-deployable` topic or its `chart/` directory: the scmProvider generator in
`applicationsets/modules.yaml` then stops generating its Application, nothing
reads its version file, and `root.yaml`'s include glob never applied `versions/`
as a manifest anyway. So a retired module's file simply STAYS, inert and costing
nothing. Deleting it buys no behaviour and stays refused, because a deliberate
deletion and an accidental one are the same diff.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import yaml

VERSIONS = Path("versions")

# A GHCR tag is mutable and a digest is not, which is why the chart prefers the
# digest — so a value that is not one is a pin that does not pin. Lowercase hex
# only: that is what the registry emits and what the kubelet accepts.
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def one_line(text: object) -> str:
    """`::error::` annotates the FIRST line of its payload and drops the rest."""
    return " ".join(str(text).split())


def shallow_refusal() -> str | None:
    """The reason to refuse this checkout, or `None` when the history is whole.

    The test is `!= "false"` rather than `== "true"` DELIBERATELY. Written the
    other way, a probe that fails for any reason — git absent, not a work tree,
    a future git wording the answer differently — yields an empty string, reads
    as "not shallow", and passes. That is the same silent pass this gate exists
    to close, reintroduced one line below the check that closes it.
    """
    probe = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        capture_output=True,
        text=True,
        check=False,
    )
    answer = probe.stdout.strip()
    if probe.returncode == 0 and answer == "false":
        return None
    seen = answer or one_line(probe.stderr) or f"exit {probe.returncode}"
    return (
        f"This checkout is not known to carry the full history "
        f"(`git rev-parse --is-shallow-repository` said `{seen}`), so this gate "
        f"cannot run. A shallow root commit reports its whole tree under "
        f"`git log --name-only`, so the set of released modules collapses to the "
        f"set of files present: a deleted pin is missing from both sides, the "
        f"comparison agrees with itself, and the check passes while blind. Run "
        f"`git fetch --unshallow` locally, or set `fetch-depth: 0` on the "
        f"checkout in CI."
    )


def ever_written() -> set[str]:
    """Every `versions/*.yaml` this repository has ever carried.

    `--name-only` over the whole history rather than `--diff-filter=A`: a rename
    is recorded as neither an addition nor a deletion under the default rename
    detection, and the union of every path ever mentioned needs no such subtlety.
    Whether the history IS whole is `shallow_refusal`'s question, asked first.
    """
    out = subprocess.run(
        ["git", "log", "--name-only", "--format=", "--", str(VERSIONS)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return {
        Path(line).name
        for line in out.splitlines()
        if line.startswith(f"{VERSIONS}/") and line.endswith(".yaml")
    }


def pin_of(path: Path) -> str:
    """The digest a version file carries, or an empty string.

    `yaml.YAMLError` PROPAGATES rather than being reported here. A file that does
    not parse and a file that parses but carries no pin are different faults that
    deserve different sentences, and folding the first into an empty string made
    the parse error a footnote under the wrong headline.
    """
    doc = yaml.safe_load(path.read_text()) or {}
    image = doc.get("image") if isinstance(doc, dict) else None
    digest = image.get("digest") if isinstance(image, dict) else None
    return digest.strip() if isinstance(digest, str) else ""


def main() -> int:
    refusal = shallow_refusal()
    if refusal:
        print(f"::error::{refusal}")
        return 1

    released = ever_written()
    if not released:
        # Neither the shallow case above nor anything pre-commit can produce,
        # which runs hooks from the repository root. It is the cheap third way
        # for the derived set to be empty rather than wrong: `VERSIONS` is a
        # RELATIVE path, so a run from elsewhere sees no history and no files.
        print(
            "::error::No `versions/*.yaml` path appears anywhere in this "
            "repository's history, which cannot be true of a repository that has "
            "recorded a release. Run this from the repository root."
        )
        return 1

    problems: list[str] = []

    for name in sorted(released):
        module = name[: -len(".yaml")]
        path = VERSIONS / name
        if not path.exists():
            problems.append(
                f"`{path}` is gone. `{module}` has released, so deleting its pin "
                f"returns it to `ghcr.io/yadgarhq/{module}:latest` with no signal "
                f"anywhere. Restore the file with the digest and tag of the "
                f"release you want running — rolling back is an EDIT of both "
                f"keys, never a delete, because `:latest` already points at the "
                f"release being rolled back from. Retiring `{module}` is not a "
                f"reason to delete it either: a module leaves the cluster by "
                f"losing its `yadgar-deployable` topic or its `chart/`, after "
                f"which nothing reads this file and leaving it costs nothing."
            )
            continue
        try:
            digest = pin_of(path)
        except yaml.YAMLError as exc:
            problems.append(
                f"`{path}` is not valid YAML, so `{module}` has no readable pin "
                f"and helm would be handed a file it cannot load: {one_line(exc)}"
            )
            continue
        if not digest:
            problems.append(
                f"`{path}` carries no `image.digest`. The chart reads the digest "
                f"in preference to the tag, and with the key empty it falls back "
                f"to `image.tag: latest`, which is the same silent revert as "
                f"deleting the file."
            )
        elif not DIGEST.fullmatch(digest):
            problems.append(
                f"`{path}` carries `image.digest: {digest}`, which is not a "
                f"sha256 digest. The chart renders "
                f"`ghcr.io/yadgarhq/{module}@{digest}`, which the kubelet refuses "
                f"to pull — so `{module}` stops at the pod rather than here. "
                f"Expected `sha256:` and 64 lowercase hex characters."
            )

    if not problems:
        print("Every released module is still pinned to a digest.")
        return 0

    # ONE LINE PER PROBLEM, ON STDOUT ONLY. An earlier revision printed each
    # problem twice, once as an annotation and once to stderr, so a reader
    # counted every fault twice over.
    for problem in problems:
        print(f"::error::{problem}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
