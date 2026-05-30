# L0 Pivot — Bearer token for aisandbox-pa (replaces SAPISIDHASH)

> **Why:** The live smoke (2026-05-31) proved `aisandbox-pa` authenticates with
> `Authorization: Bearer ya29.<oauth>`, **not** `SAPISIDHASH`. A read-only probe
> of the live session confirmed it, and the HAR shows the token source.
> This plan pivots the committed L0 work; the infrastructure is reused.

**Goal:** aisandbox `_post_json`/`_patch_json` calls attach `Authorization: Bearer <access_token>` fetched from `GET /fx/api/auth/session`, cached against `expires`, re-fetched on 401.

**Evidence (verified):**
- Real header on every aisandbox request: `Authorization: Bearer ya29.…` (Playwright probe).
- Token source: `GET https://labs.google/fx/api/auth/session` (BFF, cookie-auth — the surface `create_project` already uses) → `{"user":…, "expires":"2026-05-31T13:14:44Z", "access_token":"ya29.…"}` (HAR `labs.google15.har`).
- `SESSION_API_URL` already exists at `src/gflow_cli/auth/verification.py:38`.

---

## What's reused (no change)
- The header-injection point in `_post_json`/`_patch_json` (`is_aisandbox` branch + `headers.update(await self._aisandbox_auth_headers())`).
- The 401 refresh-retry block and `AisandboxAuthError` (Task 2 / Task 4 commits).
- The BrowserContext-read pattern (the deadlock fix) — the token fetch uses `self._context.request`, **not** a checked-out Page, for the same reason.
- The credit-free live smoke (`tests/e2e/test_aisandbox_auth_live.py`) — unchanged; it re-verifies the pivot.

## What changes

### Task 1 — Token fetch + cache on the client
**Files:** `src/gflow_cli/api/client.py`, `src/gflow_cli/api/routes.py` (add `SESSION_API_URL` or import from verification), test `tests/api/test_aisandbox_bearer.py`.

- Replace the `self._sapisid` field with token cache:
  ```python
  self._access_token: str | None = None
  self._access_token_exp: float = 0.0   # epoch seconds; 0 = unknown/expired
  ```
- New method (uses `self._context.request` — **no page checkout, no deadlock**):
  ```python
  async def _fetch_access_token(self) -> tuple[str, float]:
      ctx = self._context
      if ctx is None:
          raise AuthMissingError("access-token fetch needs an active browser context")
      resp = await ctx.request.get(routes.SESSION_API_URL)
      data = json.loads(await resp.text())
      token = data.get("access_token")
      if not token:
          raise AisandboxAuthError(
              detail="no access_token in /fx/api/auth/session (session expired?)",
              status=resp.status,
              instance=_make_instance(),
              route="auth/session",
          )
      exp = _parse_iso_to_epoch(data.get("expires"))  # far-future on parse miss
      return str(token), exp
  ```
- `_ensure_access_token`: fetch+cache when missing or within a safety margin of `exp`:
  ```python
  async def _ensure_access_token(self) -> str:
      now = time.time()
      if self._access_token is None or now >= self._access_token_exp - 60:
          self._access_token, self._access_token_exp = await self._fetch_access_token()
      return self._access_token
  ```
- `_parse_iso_to_epoch(s)`: parse the ISO-8601 `expires` (`datetime` already imported) → epoch; return `now + 3300` (≈55 min) if absent/unparseable, so caching still works.

### Task 2 — Swap the header builder
- Rewrite `_aisandbox_auth_headers` to:
  ```python
  async def _aisandbox_auth_headers(self) -> dict[str, str]:
      token = await self._ensure_access_token()
      return {"authorization": f"Bearer {token}", "origin": _LABS_ORIGIN}
  ```
  (`origin: https://labs.google` retained for browser parity — aisandbox requests carry it.)
- Update the 401 refresh in `_post_json`/`_patch_json`: `self._access_token = None` (force re-fetch) instead of `self._sapisid = None`, then `await self._ensure_access_token()`.

### Task 3 — Remove the SAPISIDHASH bits from the client
- Drop `from gflow_cli.api._sapisidhash import compute_sapisidhash`, `_SAPISID_ORIGIN`, `_read_sapisid_from_context`, `_ensure_sapisid`, `self._sapisid`.
- **Keep** `src/gflow_cli/api/_sapisidhash.py` and the experimental `sapisidhash` transport (separate concern; out of scope). Keep `AisandboxAuthError` (now means "Bearer rejected/unobtainable").

### Task 4 — Tests (replace the SAPISIDHASH unit/integration tests)
- `test_aisandbox_bearer.py` (rename/replace `test_aisandbox_auth_headers.py` + `test_post_json_aisandbox_auth.py`):
  - `_aisandbox_auth_headers` returns `Authorization: Bearer <token>` from a stubbed `_fetch_access_token`.
  - `_post_json` attaches Bearer for aisandbox, **not** for BFF.
  - 401 → clears cache, re-fetches token once, raises `AisandboxAuthError` on a second 401 (assert exactly one re-fetch).
  - **Deadlock regression:** `_fetch_access_token` reads via `self._context.request` and never calls `_checkout_page` (stub `_checkout_page` to raise).
  - Token-expiry: a cached, non-expired token is reused (no re-fetch); an expired one triggers re-fetch.
- `test_sapisidhash_redaction.py` → `test_bearer_redaction.py`: assert the `ya29` token never appears in logs (the existing `_redact_headers_for_log` already masks `authorization` → confirm).

### Task 5 — Verify
- Scoped: `pytest tests/api` green; ruff/pyright clean.
- **Live (you):** re-run the credit-free smoke — expect `create_project` 200 → `upload_image` returns an asset id. That's L0 truly green.
- Update KNOWN_ISSUES note (SAPISIDHASH → Bearer).

---

## Open question for review
- **`origin` header:** I'll send `origin: https://labs.google` on aisandbox calls for parity. If the live smoke 200s without it, drop it (minimalism). If it 401s *with* the Bearer, the next suspects are other browser headers (`x-client-data`) — but a valid Bearer alone is normally sufficient for googleapis.
- **Token TTL:** I treat `expires` from `/auth/session` as the cache horizon (minus 60 s); the 401-refresh is the safety net if the real token expires sooner.

## Estimated change
~3 commits (token fetch+cache, header swap + cleanup, tests). No new dependencies. Reuses the deadlock-safe BrowserContext pattern and the existing 401/error/redaction infra.
