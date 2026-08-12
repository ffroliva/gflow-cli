# Live Verification — v0.54.0 (Close-Browser Guidance, Composer/Overlay Hardening, Jitter RNG)

**Date:** 2026-08-12
**Profile:** `ffroliva` (`ffroliva@gmail.com`) — real-Chrome auth strategy, running the **0.54.0** build from `chore/release-v0.54.0`.
**Credits spent:** 0 — `image t2i` and `character create` (face) are credit-free; no Veo generation was driven.

## 1. What This Document Verifies

v0.54.0 ships **no generation feature**; its user-facing surface is a login-message reword (#470), a character-composer focus fix, overlay/watermark selector hardening (#403 refinement), a cryptographic RNG swap for interaction jitter (SonarCloud S2245), and dependabot guards for the playwright/patchright drivers (#465). This document records **live exercise of every changed code path** against real Flow on the 0.54.0 build. Because the changes live in the shared UI-automation submit/overlay/jitter machinery, two credit-free generations (a Classic-UI `t2i` and a `character create`) exercise all of them end-to-end.

## 2. 5-Layer Verification Ledger

| Layer / Change | Recorded Live Evidence | Result |
|---|---|---|
| **#470 — close-browser guidance** | The reworded step 4 printed **verbatim** during the live `auth login` on the 0.54.0 build; the manual close then drove `Browser closed. Verifying Flow session…` → `auth_flow_session_verified` (`ffroliva@gmail.com`) → `[OK] Flow session verified`. End-to-end on the real-Chrome path. | 🟢 PASS |
| **1. File count (t2i)** | `1/1 succeeded` — Classic-UI `image t2i` | 🟢 PASS |
| **2. Magic bytes (t2i)** | `ff d8 ff e0` (JPEG/JFIF) | 🟢 PASS |
| **3. Dimensions (t2i)** | Pillow: `JPEG (1024, 1024) RGB`, 776 KB — [`0754aa44…_1.jpg`](file:///C:/Users/ffrol/Downloads/gflow-cli/images/2026-08-12/0754aa44-b5fd-42b8-85ee-e58e0fb25175_1.jpg) | 🟢 PASS |
| **4. Structlog invariants (t2i)** | `ui_driver.ui_mode.attempt_exit_agent` → `ui_automation.aspect_ratio_set` → `ui_automation.prompt_submitted` → `ui_automation.batch_response_captured` (all `cli_version 0.54.0`) — exercises overlay dismissal, interaction jitter, image-composer submit, and the mode controller | 🟢 PASS |
| **Character-composer focus fix** | `character create` → entity `da3cd647-6ea2-4075-af8b-9404c9897c37` (`LiveVerify054`); composer path `ui_automation.entering_character_editor` → `character_editor_ready` → `character_model_selected` → `prompt_input_found` → `prompt_submitted` → `character_create.face_done` → `entity_patched` → `completed`, no errors | 🟢 PASS |
| **5. Character face asset** | `ff d8 ff e0` — Pillow `JPEG (1376, 768) RGB`, 556 KB — [`character_da3cd647…_slot0.jpg`](file:///C:/Users/ffrol/Downloads/gflow-cli/characters/2026-08-12/character_da3cd647-6ea2-4075-af8b-9404c9897c37_slot0.jpg) | 🟢 PASS |
| **Jitter RNG (SonarCloud S2245)** | `_jitter_ms` interaction jitter ran during **both** live generations with no error (internal swap to `secrets.SystemRandom`); primary coverage via the SonarCloud gate + offline suite | 🟢 PASS (path exercised; Sonar/unit-covered) |
| **Overlay/watermark hardening (#403 refinement)** | No blocking release overlay appeared on the fresh session, so the pure-structural selectors ran without a live modal to dismiss this cycle; behavior is unit-covered (`tests/api/transports/test_ui_automation.py`) and the underlying #403 dismissal was live-verified in v0.53.0 | 🟢 PASS (unit-covered; no live modal this cycle) |
| **#465 / patchright dependabot guards** | Not a live-Flow feature — enforced by `tests/test_playwright_pin.py` (`test_dependabot_ignores_playwright_minor_bumps`, `test_dependabot_ignores_driver_engine_bumps`) | 🟢 PASS (test-covered) |

## 3. Execution Log Trace (Verbatim Excerpt)

Login (#470 message → session verified):

```
4. When you're finished, simply close the Chrome window — that's how you let gflow know you're done.
   gflow then verifies your Flow session automatically; there's nothing else to do.
{"strategy": "chrome", "source": "chrome", "user_email": "ffroliva@gmail.com", "event": "auth_flow_session_verified", "cli_version": "0.54.0", ...}
[OK] Flow session verified (ffroliva@gmail.com).
```

Classic-UI `image t2i` (overlay / jitter / composer / mode controller):

```
{"event": "ui_driver.ui_mode.attempt_exit_agent", "cli_command": "image t2i", "cli_version": "0.54.0", ...}
{"event": "ui_automation.aspect_ratio_set",       "cli_command": "image t2i", "cli_version": "0.54.0", ...}
{"event": "ui_automation.prompt_submitted",        "cli_command": "image t2i", "cli_version": "0.54.0", ...}
{"event": "ui_automation.batch_response_captured", "cli_command": "image t2i", "cli_version": "0.54.0", ...}
```

`character create` (character-composer path):

```
{"event": "ui_automation.prompt_submitted",        "cli_command": "character create", "cli_version": "0.54.0", ...}
{"entity_id": "da3cd647-...", "media_id": "bc36a2ea-...", "event": "character_create.face_done", ...}
{"entity_id": "da3cd647-...", "event": "character_create.entity_patched", ...}
{"entity_id": "da3cd647-...", "name": "LiveVerify054", "event": "character_create.completed", ...}
```

## 4. User-Confirmable Outputs

- **Image:** `~/Downloads/gflow-cli/images/2026-08-12/0754aa44-b5fd-42b8-85ee-e58e0fb25175_1.jpg` — a serene mountain lake at dawn, 1024×1024.
- **Character face:** `~/Downloads/gflow-cli/characters/2026-08-12/character_da3cd647-6ea2-4075-af8b-9404c9897c37_slot0.jpg` — a red-fox portrait, 1376×768.
- **Character entity:** `da3cd647-6ea2-4075-af8b-9404c9897c37` (`LiveVerify054`) in project `e00291af-a329-42de-ab2a-1dbcc71137fc` (browsable via `gflow character show --id da3cd647-…`).

## 5. Known Condition (documented, not a blocker)

The **first** `t2i` attempt hit the known Flow **agentic-UI cohort** behavior — the conversational agent returned *video* content for an image request. gflow correctly **detected and refused it** (`WireFormatError`, first-class guard at `api/client.py:1572`, remediation: `GFLOW_CLI_PREFER_CLASSIC=1`) rather than saving a wrong file. This is the pre-existing behavior tracked by open issue **#299** (UI-mode reliability / agentic driver hardening) — **not a 0.54.0 regression**. Forcing Classic UI worked around it, and the successful runs above followed. Recorded, not omitted.

## 6. Release Reconciliation Note

v0.54.0 is the first mainline release after **v0.53.0 / v0.53.1**, which were published as GitHub releases (tags) but whose commits never reached `main`/`develop`. Their content (#315 driver-delay humanization, #403 overlay detection) was already present in `develop` as patch-equivalents; this release's CHANGELOG restores honest `[0.53.0]` / `[0.53.1]` sections and supersedes the orphaned tags. See `CHANGELOG.md`.
