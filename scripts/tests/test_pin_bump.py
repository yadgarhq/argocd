"""`scripts/pin_bump.py`: the offline engine of the automatic pin (ADR-0946, ledger 1632 C1).

OFFLINE. Every render, registry digest and module source is handed to the
engine as a fixture here, so this file runs in the `operator-applications` hook
with pytest and pyyaml only. The live half (the #106, #107 and #109 replays
against the registry and helm) is `scripts/gates/test_pin_bump_replay.py`.

One constructed case per arm:

- version: target == pin is a no-op with its own exit code; a lower target and
  a prerelease target are refused; the highest release ignores prereleases and
  is the numeric maximum, never the lexical one;
- the `targetRevision` rewrite refuses a line matched twice or not at all, and
  never touches the comments that name the field;
- the classifier (E1) accepts only the image-only shape and names every other
  shape: an env entry added, an object added, a digest mismatch, a repository
  changed under a matching digest, a helm disagreement, a single helm, a
  platform move, and a module image changed with no module version moved;
- E3: a new `env_required` key the render lacks is NEEDS-HUMAN, and the same
  key rendered is green;
- E4: an added migration version is NEEDS-HUMAN; an unchanged schema is green;
- the body carries the template's five headings and one Conventional Commits
  bullet, a rollback with `argocd app terminate-op`, and no MIGRATION_NOTES.
"""

from __future__ import annotations

import copy
import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pin_bump"

# `scripts/` is not a package, so the module is loaded by path.
_spec = importlib.util.spec_from_file_location("pin_bump", REPOSITORY / "scripts" / "pin_bump.py")
pb = importlib.util.module_from_spec(_spec)
sys.modules["pin_bump"] = pb
_spec.loader.exec_module(pb)


# ── versions ────────────────────────────────────────────────────────────────


def test_a_target_equal_to_the_pin_is_a_no_op() -> None:
    with pytest.raises(pb.NoOp):
        pb.decide("0.25.27", "0.25.27")


def test_a_lower_target_is_refused() -> None:
    with pytest.raises(pb.Refused, match="lower"):
        pb.decide("0.25.27", "0.25.14")


def test_a_prerelease_target_is_refused() -> None:
    with pytest.raises(pb.Refused, match="release"):
        pb.decide("0.25.27", "0.25.28-rc.1")


def test_a_higher_target_is_a_bump() -> None:
    assert pb.decide("0.25.14", "0.25.27") is None


def test_the_highest_release_ignores_prereleases() -> None:
    assert pb.highest_release(["v0.25.14", "v0.25.27", "v0.26.0-rc.1", "v0.25.28-beta"]) == "0.25.27"


def test_the_highest_release_is_numeric_not_lexical() -> None:
    """ADR-0690: a lexical maximum of these answers 0.25.9, a downgrade."""
    assert pb.highest_release(["v0.25.9", "v0.25.27", "v0.25.10", "not-a-version"]) == "0.25.27"


def test_the_no_op_exit_code_is_distinct() -> None:
    codes = {pb.EXIT_ACCEPT, pb.EXIT_ERROR, pb.EXIT_NEEDS_HUMAN, pb.EXIT_NO_OP}
    assert len(codes) == 4 and pb.EXIT_ACCEPT == 0


# ── the three mechanical files ──────────────────────────────────────────────

APPLICATION_TEXT = (REPOSITORY / "applications" / "yadgar.yaml").read_text()
PIN = re.search(r"^    targetRevision: (\S+)$", APPLICATION_TEXT, re.M).group(1)


def test_the_target_revision_line_alone_moves() -> None:
    rewritten, old = pb.rewrite_target_revision(APPLICATION_TEXT, "9.9.9")
    assert old == PIN
    before, after = APPLICATION_TEXT.splitlines(), rewritten.splitlines()
    assert len(before) == len(after) and rewritten.endswith("\n")
    changed = [(a, b) for a, b in zip(before, after, strict=True) if a != b]
    assert changed == [(f"    targetRevision: {PIN}", "    targetRevision: 9.9.9")]


def test_a_target_revision_matched_twice_is_refused() -> None:
    doubled = APPLICATION_TEXT.replace(
        f"    targetRevision: {PIN}\n", f"    targetRevision: {PIN}\n    targetRevision: {PIN}\n"
    )
    with pytest.raises(pb.Refused, match="2"):
        pb.rewrite_target_revision(doubled, "9.9.9")


def test_a_target_revision_matched_nowhere_is_refused() -> None:
    with pytest.raises(pb.Refused, match="0"):
        pb.rewrite_target_revision(APPLICATION_TEXT.replace("    targetRevision:", "    revision:"), "9.9.9")


def test_the_chart_pin_moves_its_tag_only() -> None:
    text = '{\n  "chart_tag": "v0.25.14",\n  "platform_version": "0.2.6"\n}\n'
    assert pb.rewrite_chart_pin(text, "0.25.27") == '{\n  "chart_tag": "v0.25.27",\n  "platform_version": "0.2.6"\n}\n'


# ── E1: the render classifier ───────────────────────────────────────────────

DIGEST = {
    ("gateway", "0.12.10"): "sha256:" + "1" * 64,
    ("gateway", "0.12.11"): "sha256:" + "2" * 64,
    ("iam", "0.11.8"): "sha256:" + "3" * 64,
}
OLD_MODULES = {"config": "0.2.1", "gateway": "0.12.10", "iam": "0.11.8", "platform": "0.2.6"}
NEW_MODULES = {**OLD_MODULES, "gateway": "0.12.11"}


def deployment(name: str, image: str, env: list | None = None) -> dict:
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name},
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"name": name, "image": image, "env": env or [{"name": "LISTEN", "value": ":8080"}]}]
                }
            }
        },
    }


def config_map(name: str) -> dict:
    return {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": name}, "data": {"k": "v"}}


def render_at(gateway_version: str) -> list:
    return [
        deployment("gateway", f"ghcr.io/yadgarhq/gateway@{DIGEST[('gateway', gateway_version)]}"),
        deployment("iam", f"ghcr.io/yadgarhq/iam@{DIGEST[('iam', '0.11.8')]}"),
        config_map("audit"),
    ]


def two_helms(base: list, head: list) -> dict:
    return {"v3.18.4": (base, head), "v4.3.0": (copy.deepcopy(base), copy.deepcopy(head))}


def digest_of(module: str, version: str) -> str:
    return DIGEST[(module, version)]


def classify(renders: dict, new_modules: dict | None = None, old_modules: dict | None = None):
    return pb.classify(renders, old_modules or OLD_MODULES, new_modules or NEW_MODULES, digest_of)


def test_green_the_107_shape_gateway_image_only_with_its_tag_digest() -> None:
    verdict = classify(two_helms(render_at("0.12.10"), render_at("0.12.11")))
    assert verdict.reasons == []
    assert verdict.accept
    assert [c["object"] for c in verdict.changed] == ["apps/Deployment//gateway"]


def test_an_env_entry_added_names_the_object_and_field() -> None:
    head = render_at("0.12.11")
    head[0]["spec"]["template"]["spec"]["containers"][0]["env"].append({"name": "NEW_KEY", "value": "x"})
    verdict = classify(two_helms(render_at("0.12.10"), head))
    assert not verdict.accept
    assert any("apps/Deployment//gateway" in r and "env" in r for r in verdict.reasons), verdict.reasons


def test_an_object_added_is_needs_human() -> None:
    """The #104 seeder shape: a new object in the render."""
    head = render_at("0.12.11") + [config_map("seeder-registry")]
    verdict = classify(two_helms(render_at("0.12.10"), head))
    assert not verdict.accept
    assert any("added" in r and "/ConfigMap//seeder-registry" in r for r in verdict.reasons), verdict.reasons


def test_an_object_removed_is_needs_human() -> None:
    verdict = classify(two_helms(render_at("0.12.10"), render_at("0.12.11")[:2]))
    assert not verdict.accept
    assert any("removed" in r and "/ConfigMap//audit" in r for r in verdict.reasons), verdict.reasons


def test_a_digest_that_is_not_the_tag_digest_is_needs_human() -> None:
    head = render_at("0.12.11")
    head[0]["spec"]["template"]["spec"]["containers"][0]["image"] = "ghcr.io/yadgarhq/gateway@sha256:" + "9" * 64
    verdict = classify(two_helms(render_at("0.12.10"), head))
    assert not verdict.accept
    assert any("gateway:0.12.11" in r and "digest" in r for r in verdict.reasons), verdict.reasons


def test_a_repository_changed_under_a_matching_digest_is_needs_human() -> None:
    head = render_at("0.12.11")
    head[0]["spec"]["template"]["spec"]["containers"][0]["image"] = f"ghcr.io/elsewhere/gateway@{DIGEST[('gateway', '0.12.11')]}"
    verdict = classify(two_helms(render_at("0.12.10"), head))
    assert not verdict.accept
    assert any("repository" in r for r in verdict.reasons), verdict.reasons


def test_the_two_helms_disagreeing_by_one_byte_is_needs_human() -> None:
    renders = two_helms(render_at("0.12.10"), render_at("0.12.11"))
    renders["v4.3.0"][1][2]["data"]["k"] = "w"
    verdict = classify(renders)
    assert not verdict.accept
    assert any("disagree" in r for r in verdict.reasons), verdict.reasons


def test_a_single_helm_is_needs_human() -> None:
    verdict = classify({"v3.18.4": (render_at("0.12.10"), render_at("0.12.11"))})
    assert not verdict.accept
    assert any("one helm" in r for r in verdict.reasons), verdict.reasons


def test_a_platform_move_is_needs_human() -> None:
    """The #103 shape: platform 0.2.4 -> 0.2.5 moves the operator Applications."""
    old = {**OLD_MODULES, "platform": "0.2.4"}
    new = {**NEW_MODULES, "platform": "0.2.5"}
    verdict = classify(two_helms(render_at("0.12.10"), render_at("0.12.11")), new_modules=new, old_modules=old)
    assert not verdict.accept
    assert any("platform moved" in r and "0.2.4" in r and "0.2.5" in r for r in verdict.reasons), verdict.reasons


def test_a_module_image_changed_without_its_version_moving_is_needs_human() -> None:
    verdict = classify(two_helms(render_at("0.12.10"), render_at("0.12.11")), new_modules=OLD_MODULES)
    assert not verdict.accept
    assert any("gateway" in r and "did not move" in r for r in verdict.reasons), verdict.reasons


def test_a_changed_config_map_is_needs_human() -> None:
    head = render_at("0.12.11")
    head[2]["data"]["k"] = "changed"
    renders = two_helms(render_at("0.12.10"), head)
    verdict = classify(renders)
    assert not verdict.accept
    assert any("/ConfigMap//audit" in r for r in verdict.reasons), verdict.reasons


# ── E3: the boot audit ──────────────────────────────────────────────────────


def tree(name: str) -> dict[str, str]:
    root = FIXTURES / name
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*.rs"))}


def test_the_boot_audit_reads_required_keys_in_every_call_shape_and_skips_tests() -> None:
    required, literals = pb.env_keys(tree("e3-new"))
    assert required == {"LISTEN", "DB_HOST", "NEW_KEY"}
    assert "NATS_TLS_DOMAIN" in literals and "YADGAR_TEST_ONLY_KEY" not in literals


def test_a_new_required_key_the_render_lacks_is_needs_human() -> None:
    reasons = pb.boot_audit("gateway", tree("e3-old"), tree("e3-new"), {"LISTEN", "DB_HOST"})
    assert reasons and any("NEW_KEY" in r and "gateway" in r for r in reasons), reasons


def test_a_new_required_key_the_render_carries_is_green() -> None:
    assert pb.boot_audit("gateway", tree("e3-old"), tree("e3-new"), {"LISTEN", "DB_HOST", "NEW_KEY"}) == []


def test_an_unchanged_source_is_green() -> None:
    assert pb.boot_audit("gateway", tree("e3-old"), tree("e3-old"), {"LISTEN", "DB_HOST"}) == []


# ── E4: the migration diff ──────────────────────────────────────────────────


def test_an_added_migration_version_is_needs_human() -> None:
    reasons = pb.migration_audit("iam-db", tree("e4-old"), tree("e4-new"))
    assert reasons and any("iam-db" in r and "7" in r for r in reasons), reasons


def test_an_unchanged_schema_is_green() -> None:
    assert pb.migration_audit("iam-db", tree("e4-old"), tree("e4-old")) == []


def test_a_changed_migration_is_needs_human() -> None:
    new = tree("e4-old")
    new["src/schema/policy.rs"] = new["src/schema/policy.rs"].replace("VARCHAR(64)", "VARCHAR(128)")
    reasons = pb.migration_audit("iam-db", tree("e4-old"), new)
    assert reasons and any("2" in r and "changed" in r for r in reasons), reasons


def test_the_migration_versions_read_both_files_and_skip_tests() -> None:
    assert set(pb.migration_blocks(tree("e4-new"))) == {1, 2, 7}


# ── the body ────────────────────────────────────────────────────────────────

BULLET = re.compile(r"^- (build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)(\([^)]+\))?!?: \S")


def evidence(accept: bool = True, reasons: list | None = None) -> dict:
    return {
        "pin": "0.25.12",
        "target": "0.25.14",
        "modules": {"old": OLD_MODULES, "new": NEW_MODULES},
        "commits": [{"sha": "abc1234", "subject": "ci: pin gateway 0.12.11 in the parent chart"}],
        "helms": ["v3.18.4", "v4.3.0"],
        "objects": 94,
        "changed": [
            {
                "object": "apps/Deployment//gateway",
                "field": "spec.template.spec.containers[0].image",
                "module": "gateway",
                "version": "0.12.11",
                "digest": DIGEST[("gateway", "0.12.11")],
            }
        ],
        "boot_audit": {"gateway": {"status": "green", "reasons": []}},
        "migrations": {},
        "accept": accept,
        "reasons": reasons or [],
    }


def headings(body: str) -> list[str]:
    return [line[3:] for line in body.splitlines() if line.startswith("## ")]


def test_the_body_carries_the_template_headings_and_one_bullet() -> None:
    body = pb.pr_body(evidence())
    assert headings(body) == ["What", "Why", "Changelog", "Verification", "Risk"]
    changelog = body.split("## Changelog\n", 1)[1].split("\n## ", 1)[0]
    lines = [line for line in changelog.splitlines() if line.strip()]
    assert len(lines) == 1 and BULLET.match(lines[0]), lines
    assert "0.25.14" in lines[0] and "gateway 0.12.11" in lines[0]


def test_the_body_carries_the_commit_table_the_digest_and_the_rollback() -> None:
    body = pb.pr_body(evidence())
    assert "abc1234" in body and DIGEST[("gateway", "0.12.11")] in body
    assert "argocd app terminate-op yadgar" in body
    assert "MIGRATION_NOTES" not in body


def test_a_needs_human_body_names_every_reason() -> None:
    body = pb.pr_body(evidence(False, ["platform moved 0.2.4 -> 0.2.5"]))
    assert "NEEDS-HUMAN" in body and "platform moved 0.2.4 -> 0.2.5" in body
