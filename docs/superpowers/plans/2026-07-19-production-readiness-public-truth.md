# Production Readiness: Public Truth and Data Correctness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans (recommended). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the nonfunctional video-batch surface while preserving image batch, and make mention/index and recorder ownership failures explicit and safe.

**Architecture:** Keep working image-batch execution unchanged. Remove only the video-batch parser/click surface and synchronize current operator documentation. Make mention lookup distinguish empty from unavailable sources, and make recorder ownership explicit with one private owned-store flag.

**Tech Stack:** Click, Python dataclasses, structlog, SQLite repositories, pytest, Ruff, markdown link checks.

---

### Task 1: Pin and remove the video-batch command

**Files:** `src/gflow_cli/cli_video.py`, `src/gflow_cli/manifest.py`, `tests/test_cli_video.py`, `tests/mcp/test_cli_parity.py`, existing manifest tests.

- [ ] **Step 1: Write failing tests**

```python
def test_video_help_does_not_advertise_batch(runner):
    result = runner.invoke(cli, ["video", "--help"])
    assert result.exit_code == 0
    assert " batch" not in result.output

def test_video_batch_is_rejected_before_profile_resolution(runner, monkeypatch):
    monkeypatch.setattr("gflow_cli.config.get_settings", lambda: (_ for _ in ()).throw(AssertionError()))
    result = runner.invoke(cli, ["video", "batch", "manifest.tsv"])
    assert result.exit_code == 2

def test_image_batch_remains_a_leaf_command(runner):
    assert runner.invoke(cli, ["image", "batch", "--help"]).exit_code == 0
```

Remove the explicit video-batch parity exemption in the same failing-test change.

- [ ] **Step 2: Run focused tests to verify failure**

Run: `uv run pytest tests/test_cli_video.py tests/mcp/test_cli_parity.py -q`

Expected: FAIL because the stub is still registered.

- [ ] **Step 3: Remove only video batch**

Delete the `batch` Click command/callback from `cli_video.py`. Run `rg -n "gflow_cli\.manifest|parse_manifest|Manifest" src tests`; delete `manifest.py` and parser-only tests only if no production import remains. Keep `src/gflow_cli/image_batch.py` unchanged.

- [ ] **Step 4: Verify command and image-batch contracts**

Run: `uv run pytest tests/test_cli_video.py tests/mcp/test_cli_parity.py tests/test_cli_image.py -q`

Expected: PASS; video batch is absent and image batch help remains available.

- [ ] **Step 5: Commit**

```text
git add src/gflow_cli/cli_video.py src/gflow_cli/manifest.py tests
git commit -m "fix: remove nonfunctional video batch command"
```

### Task 2: Correct current documentation and operator guidance

**Files:** `README.md`, `AGENTS.md`, `docs/USAGE.md`, `docs/USER_GUIDE.md`, `docs/CONFIGURATION.md`, `.env.template`, `skills/gflow-cli/SKILL.md`, current-facing references found with `rg -l "gflow video batch|video batch"`, `CHANGELOG.md`.

- [ ] **Step 1: Write the failing documentation assertion**

```python
def test_current_docs_do_not_instruct_video_batch():
    for path in CURRENT_OPERATOR_DOCS:
        assert "gflow video batch" not in path.read_text(encoding="utf-8")
```

Exclude explicitly historical release-note sections and require an Unreleased removal note.

- [ ] **Step 2: Run the assertion**

Run: `uv run pytest tests/test_documentation_gate.py -q`

Expected: FAIL on current README/AGENTS/template/skill claims.

- [ ] **Step 3: Update docs**

Replace current claims with the truthful command surface. Add Bash and PowerShell sequential-loop alternatives, state that image batch remains supported, and correct the skill’s stale “bypass web UI” and video-batch claims. Preserve historical changelog text and add an Unreleased `Removed` correction.

- [ ] **Step 4: Verify docs**

Run: `uv run python scripts/ci/check_doc_links.py; uv run pytest tests/test_documentation_gate.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add README.md AGENTS.md docs .env.template skills/gflow-cli/SKILL.md CHANGELOG.md tests
git commit -m "docs: correct video batch command surface"
```

### Task 3: Make mention-index outages explicit

**Files:** `src/gflow_cli/services/mentions.py`, `tests/services/test_mentions_resolve.py`, existing mention-source tests.

- [ ] **Step 1: Add failing tests**

```python
async def test_mention_lookup_raises_when_character_source_unavailable(monkeypatch):
    monkeypatch.setattr("gflow_cli.services.mentions._load_characters", failing_source)
    with pytest.raises(MentionIndexUnavailableError, match="character"):
        await resolve_mentions("@Ada", client=client, project_id="project")

async def test_mention_free_prompt_does_not_load_indexes(monkeypatch):
    monkeypatch.setattr("gflow_cli.services.mentions._load_characters", failing_source)
    assert await resolve_mentions("plain prompt", client=client, project_id="project") == []
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/services/test_mentions_resolve.py -q`

Expected: FAIL because broad exception handling currently produces an empty index.

- [ ] **Step 3: Implement narrow fail-closed behavior**

Call source loaders only after parsing a mention. Return empty for a successful empty source. Catch expected source failures, preserve `__cause__`, and raise the project’s stable typed error with redacted detail.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/services/test_mentions_resolve.py tests/services/test_resolve_and_apply.py -q`

Expected: PASS, including ambiguity, shadowing, and deduplication tests.

- [ ] **Step 5: Commit**

```text
git add src/gflow_cli/services/mentions.py tests/services
git commit -m "fix: distinguish unavailable mention indexes"
```

### Task 4: Fix injected recorder ownership

**Files:** `src/gflow_cli/data/recorder.py`, `src/gflow_cli/data/chain_repo.py`, `tests/data/test_recorder.py`, `tests/data/test_chain_repo.py`.

- [ ] **Step 1: Write failing ownership tests**

```python
def test_injected_repository_is_not_closed(tmp_path):
    store = DataStore.open(tmp_path / "db.sqlite")
    recorder = OperationRecorder(DataRepository(store))
    recorder.close()
    store.conn.execute("SELECT 1")

def test_factory_owned_store_is_closed(tmp_path):
    recorder = OperationRecorder.open(tmp_path / "db.sqlite")
    owned = recorder.store
    recorder.close()
    with pytest.raises(sqlite3.ProgrammingError):
        owned.conn.execute("SELECT 1")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/data/test_recorder.py tests/data/test_chain_repo.py -q`

Expected: FAIL for injected ownership.

- [ ] **Step 3: Track ownership with one private field**

Set `_owns_store=True` only in factory constructors and `False` for injected repositories. Guard `close()` with that field while preserving context-manager behavior.

- [ ] **Step 4: Verify data paths**

Run: `uv run pytest tests/data/test_recorder.py tests/data/test_chain_repo.py tests/data/test_failure_recording.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```text
git add src/gflow_cli/data/recorder.py src/gflow_cli/data/chain_repo.py tests/data
git commit -m "fix: preserve injected datastore ownership"
```
