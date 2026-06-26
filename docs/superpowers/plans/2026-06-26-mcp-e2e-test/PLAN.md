# MCP E2E Test Suite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create an end-to-end test suite for the MCP server that verifies JSON-RPC protocol compliance, tool registration, resource endpoints, and transport safety over stdio.

**Architecture:**
- Test the actual MCP server process via subprocess + stdio communication
- Use FastMCP's test client utilities for in-process testing where appropriate
- Verify rate limiter behavior under controlled timing conditions
- Test stdout isolation to ensure JSON-RPC channel integrity

**Tech Stack:** pytest, asyncio, FastMCP test client, subprocess management

---

## File structure

### New files
```
tests/mcp/test_e2e.py
  End-to-end tests for MCP server: JSON-RPC protocol, tools, resources, transport safety.
tests/mcp/conftest.py
  Shared fixtures for MCP e2e tests (server process, test client, isolated environment).
```

### Modified files
```
tests/mcp/__init__.py
  No changes needed — already a package marker.
pyproject.toml
  Add mcp[e2e] extra dependency if needed for test utilities.
```

---

## Task 1 — Create MCP E2E Test Fixtures

**Files:**
- Create: `tests/mcp/conftest.py`

- [ ] **Step 1: Write the conftest.py with server fixtures**

```python
# tests/mcp/conftest.py
"""Shared fixtures for MCP end-to-end tests."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture()
def mcp_server():
    """Return the FastMCP server instance with all tools/resources registered."""
    import gflow_cli.mcp.resources  # noqa: F401
    import gflow_cli.mcp.tools  # noqa: F401
    from gflow_cli.mcp.server import server

    return server


@pytest.fixture()
def isolated_stdout():
    """Isolate stdout for MCP transport safety testing."""
    original_stdout = sys.stdout
    try:
        yield sys.stdout
    finally:
        sys.stdout = original_stdout


@pytest.fixture()
def mcp_env(tmp_path: Path) -> dict[str, str]:
    """Build an isolated environment for MCP server testing."""
    env = {}
    env["PYTHONUTF8"] = "1"
    env["GFLOW_CLI_DB_PATH"] = str(tmp_path / "gflow.db")
    env["GFLOW_CLI_OUTPUT_DIR"] = str(tmp_path / "out")
    return env
```

- [ ] **Step 2: Verify fixture imports work**

Run: `uv run python -c "from tests.mcp.conftest import *; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add tests/mcp/conftest.py
git commit -m "test(mcp): add e2e test fixtures for server and environment isolation"
```

---

## Task 2 — Test MCP Server Tool Registration (E2E)

**Files:**
- Create: `tests/mcp/test_e2e.py`

- [ ] **Step 1: Write failing test for tool discovery**

```python
# tests/mcp/test_e2e.py
"""End-to-end tests for MCP server — JSON-RPC protocol, tools, resources, transport."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest


class TestMcpToolDiscovery:
    """Verify MCP server exposes expected tools via JSON-RPC."""

    def test_server_exposes_all_core_tools(self, mcp_server: Any) -> None:
        """All four core tools must be registered and discoverable."""
        tools = mcp_server._tool_manager._tools
        expected_tools = {
            "gflow_generate_image",
            "gflow_generate_video",
            "gflow_list_projects",
            "gflow_list_characters",
        }
        actual_tools = set(tools.keys())
        assert expected_tools.issubset(actual_tools), (
            f"Missing tools: {expected_tools - actual_tools}"
        )

    def test_generate_image_tool_has_correct_schema(self, mcp_server: Any) -> None:
        """gflow_generate_image must accept prompt, model, aspect, count, seed, profile."""
        tool = mcp_server._tool_manager._tools["gflow_generate_image"]
        schema = tool.parameters
        properties = set(schema.get("properties", {}).keys())
        required = set(schema.get("required", []))
        
        assert "prompt" in required, "prompt must be required"
        assert {"prompt", "model", "aspect", "count", "seed", "profile"}.issubset(properties)

    def test_generate_video_tool_has_correct_schema(self, mcp_server: Any) -> None:
        """gflow_generate_video must accept prompt, mode, aspect, image_path, profile."""
        tool = mcp_server._tool_manager._tools["gflow_generate_video"]
        schema = tool.parameters
        properties = set(schema.get("properties", {}).keys())
        required = set(schema.get("required", []))
        
        assert "prompt" in required, "prompt must be required"
        assert {"prompt", "mode", "aspect", "image_path", "profile"}.issubset(properties)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/mcp/test_e2e.py::TestMcpToolDiscovery -v`
Expected: FAIL (test file doesn't exist yet)

- [ ] **Step 3: Run test to verify it passes (fixtures already provide server)**

Run: `uv run pytest tests/mcp/test_e2e.py::TestMcpToolDiscovery -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/mcp/test_e2e.py
git commit -m "test(mcp): add tool discovery e2e tests"
```

---

## Task 3 — Test MCP Tool Execution (E2E)

**Files:**
- Modify: `tests/mcp/test_e2e.py`

- [ ] **Step 1: Write failing tests for tool execution**

Append to `tests/mcp/test_e2e.py`:

```python
class TestMcpToolExecution:
    """Verify MCP tools return structured responses with correct schemas."""

    @pytest.mark.asyncio
    async def test_generate_image_returns_valid_response(self) -> None:
        """gflow_generate_image must return status and params."""
        from gflow_cli.mcp.tools import gflow_generate_image

        result = await gflow_generate_image(
            prompt="test sunset over mountains",
            model="nano2",
            aspect="16:9",
            count=1,
        )
        
        assert isinstance(result, dict)
        assert "status" in result
        assert "params" in result
        assert result["params"]["prompt"] == "test sunset over mountains"
        assert result["params"]["model"] == "nano2"

    @pytest.mark.asyncio
    async def test_generate_video_returns_valid_response(self) -> None:
        """gflow_generate_video must return status and params."""
        from gflow_cli.mcp.tools import gflow_generate_video

        result = await gflow_generate_video(
            prompt="cinematic drone shot of city",
            mode="t2v",
            aspect="9:16",
        )
        
        assert isinstance(result, dict)
        assert "status" in result
        assert "params" in result
        assert result["params"]["mode"] == "t2v"

    @pytest.mark.asyncio
    async def test_list_projects_returns_structured_response(self) -> None:
        """gflow_list_projects must return status and projects list."""
        from gflow_cli.mcp.tools import gflow_list_projects

        result = await gflow_list_projects()
        
        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert "projects" in result
        assert isinstance(result["projects"], list)

    @pytest.mark.asyncio
    async def test_list_characters_returns_structured_response(self) -> None:
        """gflow_list_characters must return status and characters list."""
        from gflow_cli.mcp.tools import gflow_list_characters

        result = await gflow_list_characters()
        
        assert isinstance(result, dict)
        assert result["status"] == "ok"
        assert "characters" in result
        assert isinstance(result["characters"], list)
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/mcp/test_e2e.py::TestMcpToolExecution -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/mcp/test_e2e.py
git commit -m "test(mcp): add tool execution e2e tests"
```

---

## Task 4 — Test MCP Resource Endpoints (E2E)

**Files:**
- Modify: `tests/mcp/test_e2e.py`

- [ ] **Step 1: Write failing tests for resources**

Append to `tests/mcp/test_e2e.py`:

```python
class TestMcpResources:
    """Verify MCP resources return expected content."""

    @pytest.mark.asyncio
    async def test_mcp_guide_resource_returns_content(self) -> None:
        """gflow://docs/mcp-guide must return agent instructions."""
        from gflow_cli.mcp.resources import mcp_guide

        content = await mcp_guide()
        
        assert isinstance(content, str)
        assert len(content) > 0
        assert "gflow_generate_image" in content
        assert "gflow_generate_video" in content
        assert "Use tools, not shell commands" in content

    @pytest.mark.asyncio
    async def test_known_issues_resource_returns_content(self) -> None:
        """gflow://docs/known-issues must return KNOWN_ISSUES.md content."""
        from gflow_cli.mcp.resources import known_issues

        content = await known_issues()
        
        assert isinstance(content, str)
        assert len(content) > 0

    @pytest.mark.asyncio
    async def test_db_schema_resource_returns_content(self) -> None:
        """gflow://db/schema must return SQL schema."""
        from gflow_cli.mcp.resources import db_schema

        content = await db_schema()
        
        assert isinstance(content, str)
        assert len(content) > 0
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/mcp/test_e2e.py::TestMcpResources -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/mcp/test_e2e.py
git commit -m "test(mcp): add resource endpoint e2e tests"
```

---

## Task 5 — Test Rate Limiter Behavior (E2E)

**Files:**
- Modify: `tests/mcp/test_e2e.py`

- [ ] **Step 1: Write failing tests for rate limiter**

Append to `tests/mcp/test_e2e.py`:

```python
class TestMcpRateLimiter:
    """Verify token-bucket rate limiter behavior under controlled conditions."""

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_burst_within_capacity(self) -> None:
        """Should allow up to 8 consecutive calls (bucket capacity)."""
        from gflow_cli.mcp.tools import _TokenBucket

        bucket = _TokenBucket(capacity=8, refill_rate=0.0)
        
        for _ in range(8):
            assert await bucket.acquire() is True

    @pytest.mark.asyncio
    async def test_rate_limiter_blocks_when_exhausted(self) -> None:
        """Should block after capacity is exhausted."""
        from gflow_cli.mcp.tools import _TokenBucket

        bucket = _TokenBucket(capacity=2, refill_rate=0.0)
        
        assert await bucket.acquire() is True
        assert await bucket.acquire() is True
        assert await bucket.acquire() is False

    @pytest.mark.asyncio
    async def test_rate_limiter_refills_over_time(self) -> None:
        """Tokens should refill at the configured rate."""
        from gflow_cli.mcp.tools import _TokenBucket

        bucket = _TokenBucket(capacity=1, refill_rate=100.0)
        
        assert await bucket.acquire() is True
        assert await bucket.acquire() is False
        await asyncio.sleep(0.02)
        assert await bucket.acquire() is True

    @pytest.mark.asyncio
    async def test_rate_limiter_concurrent_access(self) -> None:
        """Rate limiter should handle concurrent acquisitions safely."""
        from gflow_cli.mcp.tools import _TokenBucket

        bucket = _TokenBucket(capacity=4, refill_rate=0.0)
        
        async def acquire_token() -> bool:
            return await bucket.acquire()
        
        results = await asyncio.gather(*[acquire_token() for _ in range(6)])
        assert sum(results) == 4
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/mcp/test_e2e.py::TestMcpRateLimiter -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/mcp/test_e2e.py
git commit -m "test(mcp): add rate limiter e2e tests"
```

---

## Task 6 — Test Stdout Isolation (E2E)

**Files:**
- Modify: `tests/mcp/test_e2e.py`

- [ ] **Step 1: Write failing tests for stdout isolation**

Append to `tests/mcp/test_e2e.py`:

```python
class TestMcpStdoutIsolation:
    """Verify stdout is redirected to stderr for JSON-RPC transport safety."""

    def test_redirect_stdout_to_stderr(self) -> None:
        """After redirection, sys.stdout should write to stderr's buffer."""
        import io
        import sys
        from unittest.mock import patch

        from gflow_cli.mcp.server import _redirect_stdout_to_stderr

        mock_stdout = object()

        class MockStderr:
            buffer = io.BytesIO()

        with (
            patch("sys.stdout", mock_stdout),
            patch("sys.stderr", MockStderr()),
            patch("sys.modules", {}),
            patch("gflow_cli.mcp.server.io.TextIOWrapper") as mock_wrapper,
        ):
            _redirect_stdout_to_stderr()
            mock_wrapper.assert_called_once()

    def test_utf8_pipes_configured(self) -> None:
        """UTF-8 encoding should be configured for stdin/stdout on Windows."""
        import sys
        from unittest.mock import MagicMock, patch

        from gflow_cli.mcp.server import _configure_utf8_pipes

        mock_stream = MagicMock()
        mock_stream.reconfigure = MagicMock()

        with patch.object(sys, "platform", "win32"), \
             patch.object(sys, "stdin", mock_stream), \
             patch.object(sys, "stdout", mock_stream), \
             patch.object(sys, "stderr", mock_stream):
            _configure_utf8_pipes()
            assert mock_stream.reconfigure.call_count == 3
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/mcp/test_e2e.py::TestMcpStdoutIsolation -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/mcp/test_e2e.py
git commit -m "test(mcp): add stdout isolation e2e tests"
```

---

## Task 7 — Test CLI/MCP Parameter Symmetry (E2E)

**Files:**
- Modify: `tests/mcp/test_e2e.py`

- [ ] **Step 1: Write failing tests for parameter symmetry**

Append to `tests/mcp/test_e2e.py`:

```python
class TestCliMcpParameterSymmetry:
    """Verify CLI command parameters match MCP tool signatures.

    This is a CI gate — any new CLI option must have a corresponding
    MCP tool parameter. See AGENTS.md: 'MCP & CLI Schema Symmetry'.
    """

    def test_image_t2i_params_mirrored(self, mcp_server: Any) -> None:
        """Key parameters of `gflow image t2i` must appear in gflow_generate_image."""
        tool = mcp_server._tool_manager._tools["gflow_generate_image"]
        schema_props = set(tool.parameters.get("properties", {}).keys())
        required_in_both = {"prompt", "model", "aspect", "count", "seed", "profile"}
        assert required_in_both.issubset(schema_props), (
            f"MCP tool missing CLI params: {required_in_both - schema_props}"
        )

    def test_video_t2v_params_mirrored(self, mcp_server: Any) -> None:
        """Key parameters of `gflow video t2v` must appear in gflow_generate_video."""
        tool = mcp_server._tool_manager._tools["gflow_generate_video"]
        schema_props = set(tool.parameters.get("properties", {}).keys())
        required_in_both = {"prompt", "mode", "aspect", "profile"}
        assert required_in_both.issubset(schema_props), (
            f"MCP tool missing CLI params: {required_in_both - schema_props}"
        )
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/mcp/test_e2e.py::TestCliMcpParameterSymmetry -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/mcp/test_e2e.py
git commit -m "test(mcp): add CLI/MCP parameter symmetry e2e tests"
```

---

## Task 8 — Run Full Test Suite and Validate

**Files:**
- None (verification only)

- [ ] **Step 1: Run all MCP tests**

Run: `uv run pytest tests/mcp/ -v`
Expected: All tests PASS

- [ ] **Step 2: Run ruff linter**

Run: `uv run ruff check tests/mcp/`
Expected: No errors

- [ ] **Step 3: Run ruff formatter check**

Run: `uv run ruff format --check tests/mcp/`
Expected: No formatting issues

- [ ] **Step 4: Run pyright type checker**

Run: `uv run pyright tests/mcp/`
Expected: No type errors

- [ ] **Step 5: Run full test suite with coverage**

Run: `uv run pytest -m "not e2e and not live and not smoke" -q --cov=gflow_cli --cov-report=term-missing`
Expected: All tests PASS, coverage ≥ 80%

- [ ] **Step 6: Commit final state**

```bash
git add -A
git commit -m "test(mcp): complete e2e test suite for MCP server

- Tool discovery and schema validation
- Tool execution with structured responses
- Resource endpoint content verification
- Rate limiter behavior under controlled conditions
- Stdout isolation for JSON-RPC transport safety
- CLI/MCP parameter symmetry enforcement"
```

---

## Definition of Done

- [ ] All task steps checked off.
- [ ] `uv run pytest tests/mcp/ -v` — all tests green.
- [ ] `uv run ruff check tests/mcp/` — no lint errors.
- [ ] `uv run ruff format --check tests/mcp/` — no formatting issues.
- [ ] `uv run pyright tests/mcp/` — no type errors.
- [ ] Coverage for `gflow_cli.mcp` module ≥ 80%.
- [ ] E2E test suite covers: tool registration, tool execution, resources, rate limiter, stdout isolation, parameter symmetry.
