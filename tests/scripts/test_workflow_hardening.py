"""Workflow-hardening gates (#565).

Offline regression locks for the CI/supply-chain hardening pass:

* every ``actions/checkout`` sets ``persist-credentials: false`` — the default
  leaves the job token in ``.git/config`` for the rest of the job (and inside
  any artifact built from the workspace);
* no ``run:`` block interpolates a ``${{ github.* }}`` context directly — that
  is template injection; the value must arrive through ``env:`` instead;
* the ``zizmor`` static analyser actually runs in CI, pinned;
* the release job does not restore a shared uv cache (cache-poisoning surface
  on the one workflow that publishes artefacts).

These duplicate the ``workflow-audit`` CI job on purpose: the job is the
authority, this is the copy that fails on a developer's machine — and in every
matrix leg — before a push burns a CI cycle.

**Why the duplication is kept** (#568, decided). A council reviewer proposed
cutting the three property tests as redundant with the zizmor rules running on
the same commit — and in CI they largely are: ``workflow-audit`` carries no
``if:`` guard, so it runs on every PR. Two things survive that objection:

* they need **no network**. ``uvx zizmor`` downloads before it audits; these run
  offline in well under a second, so they bite pre-push and in every matrix leg,
  which is where a hardening slip is cheapest to fix.
* every assertion has been **watched failing** against a deliberate revert —
  including the ``.yaml`` enumeration gap below. A guard never seen red is not a
  guard.

``test_ci_runs_the_zizmor_workflow_audit`` and
``test_docs_quote_the_zizmor_pin_that_ci_actually_runs`` were never in scope for
cutting: they check properties zizmor cannot check about itself — that the gate
exists, is pinned, and matches its own documentation.

One correction worth leaving here, because the opposite is easy to assume:
``develop`` has **no required status checks at all** (verified 2026-08-25), so on
the default branch neither this test job nor the zizmor job blocks a merge.
Both are required on ``main``. See #567.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

# uvx has no Dependabot ecosystem, so the zizmor bump is manual. ci.yml is the
# authority — it is where the gate actually runs — so the pin is DERIVED from it
# rather than restated here. A constant would be one more copy to keep in step,
# which is the failure this guards against (#568).
_ZIZMOR_PIN_RE = re.compile(r"\bzizmor==\d+\.\d+\.\d+\b")


def _workflow_paths(directory: Path = WORKFLOWS) -> list[Path]:
    """Every file GitHub would treat as a workflow — ``.yml`` **and** ``.yaml``.

    ``*.y*ml`` rather than one glob per extension: a workflow this enumeration
    misses is invisible to every assertion below, so it would escape the
    persist-credentials and template-injection guards entirely. Over-inclusion
    (a contrived ``x.yZZml``) is the safe direction; under-inclusion is not.
    """
    paths = sorted(directory.glob("*.y*ml"))
    assert paths, f"no workflows found under {directory}"
    return paths


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _steps(doc: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        step
        for job in (doc.get("jobs") or {}).values()
        for step in (job.get("steps") or [])
        if isinstance(step, dict)
    ]


def _workflow_audit_commands() -> str:
    """The shell commands ci.yml's ``workflow-audit`` job runs, joined."""
    ci = _load(WORKFLOWS / "ci.yml")
    audit = (ci.get("jobs") or {}).get("workflow-audit")
    assert audit is not None, "ci.yml: no 'workflow-audit' job"
    return " ".join(
        str(step.get("run", "")) for step in audit.get("steps") or [] if isinstance(step, dict)
    )


def _zizmor_pin_from_ci() -> str:
    """The exact ``zizmor==X.Y.Z`` spec ci.yml runs — the single source of truth."""
    match = _ZIZMOR_PIN_RE.search(_workflow_audit_commands())
    assert match is not None, (
        "ci.yml: workflow-audit must run zizmor pinned to an exact version "
        "(no 'zizmor==X.Y.Z' found) — an unpinned analyser silently changes "
        "what the gate enforces between runs"
    )
    return match.group(0)


@pytest.mark.parametrize("path", _workflow_paths(), ids=lambda p: p.name)
def test_every_checkout_disables_credential_persistence(path: Path) -> None:
    for step in _steps(_load(path)):
        if not str(step.get("uses", "")).startswith("actions/checkout@"):
            continue
        with_block = step.get("with") or {}
        assert with_block.get("persist-credentials") is False, (
            f"{path.name}: actions/checkout must set 'persist-credentials: false' "
            "(the default persists the job token in .git/config)"
        )


@pytest.mark.parametrize("path", _workflow_paths(), ids=lambda p: p.name)
def test_no_run_block_interpolates_a_github_context(path: Path) -> None:
    """Ban EVERY ``github.*`` context in a ``run:`` block — deliberately blanket.

    This is broader than zizmor's ``template-injection``, which is
    context-aware: a benign ``${{ github.run_id }}`` passes zizmor and fails
    here. That is the intended trade (#568). Narrowing to the
    attacker-controllable set was considered and rejected — that set is long and
    keeps moving (``event.*``, ``head_ref``, ``base_ref``, ``actor``,
    ``triggering_actor``, ``event.issue.title``, ...), and an allowlist that is
    wrong about one entry is a hole, whereas a blanket ban is at worst
    inconvenient. "Always go through ``env:``" is one rule a contributor can
    hold in their head, every workflow already complies, and the escape hatch is
    a single ``env:`` line.
    """
    pattern = re.compile(r"\$\{\{\s*github\.")
    for step in _steps(_load(path)):
        run = step.get("run")
        if not isinstance(run, str):
            continue
        assert not pattern.search(run), (
            f"{path.name}: run block interpolates a ${{{{ github.* }}}} context — "
            "pass it through 'env:' and reference the shell variable instead "
            "(template injection)"
        )


def test_workflow_enumeration_covers_the_yaml_extension(tmp_path: Path) -> None:
    """GitHub honours ``.yaml`` too, so the guard must (#568).

    Not hypothetical bookkeeping: a ``.yaml`` workflow the enumeration skips is
    checked by nothing in this module — it could persist credentials or
    interpolate an attacker-controlled context and every test here would still
    be green. Every workflow is ``.yml`` today, which is exactly why this needs
    a test rather than a habit.
    """
    (tmp_path / "a.yml").write_text("on: push\n", encoding="utf-8")
    (tmp_path / "b.yaml").write_text("on: push\n", encoding="utf-8")
    (tmp_path / "notes.md").write_text("not a workflow\n", encoding="utf-8")

    assert [p.name for p in _workflow_paths(tmp_path)] == ["a.yml", "b.yaml"]


def test_an_unpinned_zizmor_command_is_not_mistaken_for_a_pin() -> None:
    """The pin detector must reject a bare ``uvx zizmor``.

    A regex matching anything containing "zizmor" would let
    ``test_ci_runs_the_zizmor_workflow_audit`` pass on an unpinned gate — a
    guard that cannot fail.
    """
    assert _ZIZMOR_PIN_RE.search("uvx zizmor --offline .github/workflows/") is None
    assert _ZIZMOR_PIN_RE.search("uvx zizmor==1.29.0 --offline .github/workflows/") is not None


def test_ci_runs_the_zizmor_workflow_audit() -> None:
    commands = _workflow_audit_commands()
    assert _ZIZMOR_PIN_RE.search(commands), (
        "ci.yml: workflow-audit must run zizmor pinned to an exact version — "
        "an unpinned analyser silently changes what the gate enforces"
    )
    assert ".github/workflows" in commands, "ci.yml: workflow-audit must audit .github/workflows"


def test_docs_quote_the_zizmor_pin_that_ci_actually_runs() -> None:
    """``docs/GITHUB.md`` tells contributors to reproduce the gate locally, and
    claims the gate and the test "cannot drift apart silently" (#568).

    Nothing enforced that for the doc's own copy of the command. A bump to
    ci.yml would leave the doc quoting the previous analyser, so anyone
    following the documented command reproduces a *different* result from CI —
    while reading a sentence promising they cannot.
    """
    pin = _zizmor_pin_from_ci()
    doc = (ROOT / "docs" / "GITHUB.md").read_text(encoding="utf-8")
    assert pin in doc, f"docs/GITHUB.md must quote the pin ci.yml runs ({pin}) — bump both together"


def test_release_does_not_restore_a_shared_uv_cache() -> None:
    for step in _steps(_load(WORKFLOWS / "release.yml")):
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@"):
            with_block = step.get("with") or {}
            assert with_block.get("enable-cache") is False, (
                "release.yml: setup-uv must set 'enable-cache: false' — the publishing "
                "workflow must not restore a cache another workflow can write"
            )


# --- The MCP Registry publish wiring (#841) -------------------------------------
#
# These lock the shape of a bug that ran for every release that shipped with the
# automation and was caught by nobody: mcp-registry.yml triggered on
# `release: published`, release.yml created the Release with the default
# GITHUB_TOKEN, and GitHub starts no workflow runs from GITHUB_TOKEN-created
# events. Zero runs, ever, every check green throughout.
#
# The fix is to CALL the workflow from release.yml rather than trigger it. Each
# assertion below was verified to fail against the pre-fix file — the module's
# charter is that a guard never seen red is not a guard.

_REGISTRY_WORKFLOW = "mcp-registry.yml"


def _registry_doc() -> dict[str, Any]:
    return _load(WORKFLOWS / _REGISTRY_WORKFLOW)


def _triggers(doc: dict[str, Any]) -> dict[str, Any]:
    """A workflow's ``on:`` block. PyYAML parses bare ``on:`` as the boolean True."""
    return doc.get(True) if True in doc else doc.get("on")


def test_the_registry_publish_is_called_by_the_release_workflow() -> None:
    """release.yml must CALL the registry publish, after the PyPI upload.

    Triggering it by event is what failed for a year. ``needs:`` is also the only
    thing enforcing the ordering mcp-publisher requires: it reads the ``mcp-name:``
    token from the PUBLISHED PyPI README, so the wheel has to be up first.
    """
    jobs = _load(WORKFLOWS / "release.yml").get("jobs") or {}
    callers = {
        name: job
        for name, job in jobs.items()
        if str(job.get("uses", "")).endswith(_REGISTRY_WORKFLOW)
    }
    assert callers, (
        "release.yml does not call mcp-registry.yml. An event trigger cannot replace "
        "this: a Release created with the default GITHUB_TOKEN starts no workflow runs, "
        "which is why the registry publish never fired once (#841)"
    )
    for name, job in callers.items():
        needs = job.get("needs")
        needs = [needs] if isinstance(needs, str) else list(needs or [])
        assert "build-and-publish" in needs, (
            f"release.yml: job {name!r} calls the registry publish without "
            "`needs: build-and-publish`, so it can run before the wheel is on PyPI — "
            "mcp-publisher reads its ownership token from the published PyPI README"
        )


def test_the_registry_publish_is_not_wired_to_the_dead_release_trigger() -> None:
    """``on: release:`` here is the bug, not a belt-and-braces extra.

    Restoring it would double-publish on every release (once via the call, once via
    the event) the moment anyone made the Release with a user token.
    """
    triggers = _triggers(_registry_doc()) or {}
    assert "release" not in triggers, (
        "mcp-registry.yml declares an `on: release:` trigger. It never fired under the "
        "default GITHUB_TOKEN (#841), and if it ever did it would double-publish "
        "alongside the call from release.yml"
    )


def test_the_registry_publish_takes_a_required_version_on_every_trigger() -> None:
    """A run publishes whatever ``server.json`` the REF carries.

    Dispatching ``develop`` before the release back-merge landed republished the
    PREVIOUS version to the registry, green, on v0.76.0.
    """
    triggers = _triggers(_registry_doc()) or {}
    assert set(triggers) == {"workflow_call", "workflow_dispatch"}, (
        f"mcp-registry.yml triggers are {sorted(triggers)} — expected exactly "
        "workflow_call (the release path) and workflow_dispatch (manual re-run)"
    )
    for trigger, spec in triggers.items():
        version = ((spec or {}).get("inputs") or {}).get("version")
        assert version is not None, (
            f"mcp-registry.yml: {trigger} declares no `version` input — the run would "
            "publish whatever server.json the ref happens to carry (#841)"
        )
        assert version.get("required") is True, (
            f"mcp-registry.yml: {trigger}'s `version` input must be required; an "
            "optional one is an empty string on every hand-run dispatch (#841)"
        )


def test_the_release_skips_the_registry_for_a_prerelease() -> None:
    """An ``rc`` must never become the registry's ACTIVE listing.

    That listing is what PulseMCP and GitHub's MCP gallery ingest. The repo has
    shipped ten prerelease tags (v0.2.0a1 ... v0.6.0a6), and release.yml fires on
    every one of them.
    """
    jobs = _load(WORKFLOWS / "release.yml").get("jobs") or {}
    callers = [
        job for job in jobs.values() if str(job.get("uses", "")).endswith(_REGISTRY_WORKFLOW)
    ]
    assert callers, "release.yml does not call mcp-registry.yml"
    for job in callers:
        assert "prerelease" in str(job.get("if", "")), (
            "release.yml calls the registry publish with no prerelease guard, so a "
            "v1.2.3rc1 would be published as the registry's active listing"
        )


def test_the_prerelease_classification_has_exactly_one_definition() -> None:
    """The GitHub Release flag and the registry gate must not drift apart.

    As two copies of the same four-clause expression they eventually would, and the
    two consumers disagreeing is silent: a prerelease marked correctly on the Release
    but published to the registry anyway.
    """
    doc = _load(WORKFLOWS / "release.yml")
    build = (doc.get("jobs") or {})["build-and-publish"]
    assert "prerelease" in (build.get("outputs") or {}), (
        "release.yml: build-and-publish exposes no `prerelease` output, so the registry "
        "gate cannot share the classification the Release flag uses"
    )
    inline = [
        step
        for step in build.get("steps") or []
        if "contains(github.ref_name" in str(step.get("with", {}).get("prerelease", ""))
    ]
    assert not inline, (
        "release.yml: the prerelease expression is inlined on a step as well as being a "
        "job output — one definition, or the two copies will drift"
    )
