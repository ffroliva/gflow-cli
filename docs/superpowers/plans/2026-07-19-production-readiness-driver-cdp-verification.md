# Production Readiness: Driver, CDP, and Verification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans (recommended). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove dishonest driver plumbing, decide the research-only CDP lifecycle from evidence, correct runtime documentation, and finish with complete offline and minimal live evidence.

**Architecture:** Preserve classic listener-before-click and agentic attribution defenses as separate mechanisms. Replace private transport mutation with typed setup and explicit request arguments. CDP remains packaged only if safe ownership and a real production consumer are demonstrated.

**Tech Stack:** Playwright, typed dataclasses/protocols, Click/MCP schemas, pytest, package build, live Flow E2E.

---

### Task 1: Make image-driver contracts honest

**Files:** `src/gflow_cli/api/transports/drivers/base.py`, `classic.py`, `agentic.py`, `factory.py`, `ui_automation.py`, `client.py`, and existing transport/client tests.

- [ ] **Step 1: Add failing contract tests**

```python
def test_classic_driver_does_not_advertise_unimplemented_await_images(classic_driver):
    assert not hasattr(classic_driver, "await_images")

async def test_agentic_image_request_is_passed_without_pending_driver_state(agentic_driver, request):
    await agentic_driver.submit_images(request, expected_count=1)
    assert agentic_driver.pending_request is None

async def test_submit_listener_is_registered_before_click(fake_page, transport):
    await transport.submit_images(fake_page, request, expected_count=1)
    assert fake_page.events.index("listener_registered") < fake_page.events.index("submit_clicked")
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/transports/test_ui_automation.py tests/api/transports/test_ui_automation_video.py tests/api/test_client.py -q`

Expected: FAIL on the dishonest classic method and private `_transport`/pending-state assertions.

- [ ] **Step 3: Implement the smallest typed seam**

Remove classic `await_images()` that raises `NotImplementedError`. Add a typed submit callable or setup object passed by the transport. Pass request and expected count directly into agentic submit/await. Preserve listener registration before click and attribution guards.

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/api/transports/test_ui_automation.py tests/api/transports/test_ui_automation_video.py tests/api/test_client.py -q`

Expected: PASS with no late private transport mutation.

- [ ] **Step 5: Commit**

```text
git add src/gflow_cli/api tests/api
git commit -m "refactor: make image driver boundaries explicit"
```

### Task 2: Replace private client-to-transport writes

**Files:** `src/gflow_cli/api/client.py`, `src/gflow_cli/api/transports/base.py`, concrete transport setup classes, `tests/api/test_client.py`.

- [ ] **Step 1: Write failing setup tests**

```python
async def test_client_passes_typed_output_configuration_to_transport(client, transport, tmp_path):
    await client.setup_transport(transport, output_dir=tmp_path)
    assert transport.setup_config.output_dir == tmp_path

async def test_client_does_not_write_transport_private_attributes(client, transport):
    await client.setup_transport(transport, output_dir=None)
    assert "_out_dir" not in transport.__dict__
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/api/test_client.py -q`

Expected: FAIL because client setup currently writes `_out_dir`/storage fields after `hasattr` checks.

- [ ] **Step 3: Implement typed setup**

Define the smallest immutable setup object needed by concrete transports. Make setup accept it publicly and remove private-field writes. Preserve preinitialized transport lifecycle ownership.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/api/test_client.py tests/api/transports -q`

```text
git add src/gflow_cli/api tests/api
git commit -m "refactor: type transport setup configuration"
```

### Task 3: Resolve the research CDP lifecycle

**Files:** `src/gflow_cli/browser_manager.py`, Chrome-discovery imports, `tests/test_browser_manager.py`, `scripts/smoke_real_chrome_image.py`, `PLAN.md`, `docs/ARCHITECTURE.md`.

- [ ] **Step 1: Add a zero-credit ownership probe**

```python
def test_cdp_probe_requires_loopback_and_owned_process(probe_result):
    assert probe_result.address.startswith("127.0.0.1:")
    assert probe_result.owner_pid == os.getpid()
```

Run only with a dedicated expendable Chrome profile and dynamic port. Record status, identity, authenticated DOM reachability, teardown, and no-secret artifact metadata.

- [ ] **Step 2: Verify current call graph and failure**

Run: `uv run pytest tests/test_browser_manager.py -q`; inspect callers with `rg -n "get_or_launch_browser|close_browser|_connect_cdp" src scripts tests`.

Expected: no production CDP consumer and unmet ownership criteria unless a safe live probe proves otherwise.

- [ ] **Step 3: Keep or remove based on evidence**

If all identity, ownership, cleanup, authenticated-DOM, and consumer gates pass, isolate CDP behind a typed owned-browser handle and add a real consumer test. Otherwise remove packaged CDP lifecycle and production-only tests, retaining Chrome discovery/channel helpers and a safe development note.

- [ ] **Step 4: Verify and commit**

Run: `uv run pytest tests/test_browser_manager.py tests/data/test_packaging.py -q`

```text
git add src/gflow_cli/browser_manager.py tests/test_browser_manager.py tests/data/test_packaging.py docs scripts
git commit -m "chore: resolve research CDP lifecycle"
```

### Task 4: Correct runtime documentation and produce live evidence

**Files:** `docs/CONFIGURATION.md`, `.env.template`, `docs/AUTHENTICATION.md`, `KNOWN_ISSUES.md`, `docs/E2E_TESTING.md`, new `docs/LIVE_VERIFICATION_v0.40.0-production-readiness.md`, documentation tests.

- [ ] **Step 1: Add failing stale-headless assertion**

```python
def test_current_docs_describe_headed_default_and_waf_safe_mode():
    text = Path("docs/CONFIGURATION.md").read_text(encoding="utf-8")
    assert "GFLOW_CLI_HEADLESS=false" in text
    assert "headed" in text.lower()
```

- [ ] **Step 2: Run documentation tests**

Run: `uv run pytest tests/test_documentation_gate.py tests/test_marker_registry.py -q`

Expected: FAIL on stale headless defaults and missing verification boundary.

- [ ] **Step 3: Update docs**

Document headed real Chrome as production default, `GFLOW_CLI_HEADLESS=false` for WAF-sensitive runs, fail-fast contention, queue V0/V1/indeterminate semantics, and the CDP decision. Keep Flow-side limitations explicit.

- [ ] **Step 4: Execute approved live matrix serially**

Run zero-credit auth/health/schema and daemon/MCP tests first. Then one queue-boundary image generation and one cheapest stable T2V generation without explicit duration. Stop on WAF 403, auth expiry, quota, or profile contention. Verify artifacts, provenance, handles, lease release, cookie DB health, and no leftover Chrome process.

- [ ] **Step 5: Record evidence and run final gates**

Record exact commands, commit, environment, timings, artifacts, skips, and external limitations. Run the full Impeccable Routine, default coverage suite, focused subprocess tests, `uv build`, wheel/sdist content checks, and `git status --short`.

- [ ] **Step 6: Commit**

```text
git add docs .env.template tests
git commit -m "docs: record production readiness verification"
```
