# Live Verification — v0.52.0 (Overlay Dismissal, Delay Jittering & Intra-Batch References)

**Date:** 2026-08-06  
**Profile:** `promo-denon82`  
**Mode:** Headed visible Chrome (`GFLOW_CLI_HEADLESS=false`)  
**Cost:** 2 Imagen credits (1 T2I + 1 I2I with intra-batch reference `ref: "batch:0"`)  

---

## 1. What This Document Verifies

This live verification run exercises recent core infrastructure, transport, and batch improvements against a live Google Flow session in visible headed Chrome mode:

1. **Language-Agnostic Overlay Dismissal ([#403](https://github.com/ffroliva/gflow-cli/issues/403)):** Pure DOM structural selectors (`a[href*='changelog']`, `[role='dialog']:has(a[href*='changelog']) button`) detecting and dismissing release-note modals across profiles.
2. **Driver Interaction Delay Jittering ([#315](https://github.com/ffroliva/gflow-cli/issues/315)):** Randomized timing entropy (`_jitter_ms`) replacing static wait durations (`wait_for_timeout`) to break automated Playwright WAF signatures.
3. **Intra-Batch Reference Staging ([#317](https://github.com/ffroliva/gflow-cli/issues/317)):** DAG dependency resolution where Prompt 0's generated image is automatically bound as `ref: "batch:0"` for Prompt 1 down the line.

---

## 2. 5-Layer Verification Ledger

| Layer | Recorded Live Evidence | Result |
|---|---|---|
| **1. File Count & Summary** | `2/2 succeeded · 0 failure(s) · 0 skipped` | 🟢 PASS |
| **2. Artifact 0 (Prompt 0)** | [`prompt_0_0.jpg`](file:///C:/Users/ffrol/Downloads/gflow-cli/images/2026-08-06/prompt_0_0.jpg) (862,548 bytes, `1:1` aspect, Nano Banana 2) | 🟢 PASS |
| **3. Artifact 1 (Prompt 1)** | [`prompt_1_0.jpg`](file:///C:/Users/ffrol/Downloads/gflow-cli/images/2026-08-06/prompt_1_0.jpg) (1,093,468 bytes, `16:9` aspect, Nano Banana 2) | 🟢 PASS |
| **4. Structlog Invariants** | `ui_automation.image_mode_entered` → `ui_automation.image_model_selected` → `ui_automation.aspect_ratio_set` → `ui_automation.prompt_submitted` → `batch_jitter_sleep` (0.99s) | 🟢 PASS |
| **5. DAG Intra-Batch Binding** | Prompt 1 (`16:9`) bound Prompt 0's generated asset as `ref: "batch:0"` via `batchGenerateImages` payload | 🟢 PASS |

---

## 3. Execution Log Trace (Verbatim Excerpt)

```json
{"project_id": "a1462f9a-504e-4503-9475-1c59e6de4c3d", "event": "ui_automation.entering_existing_project", "level": "info"}
{"mode": "agentic", "ui_mode": "auto", "event": "ui_driver.bound", "level": "info"}
{"seconds": 0.99, "index": 1, "event": "batch_jitter_sleep", "level": "info"}
{"probe": "mode_switch_trigger", "selector": "button[aria-haspopup='menu']:has(i.google-symbols:text('crop_9_16'))", "event": "ui_automation_video.selector_matched", "level": "info"}
{"probe": "image_mode_tab", "selector": "[role='menu'] [role='tab'][aria-controls*='IMAGE']", "event": "ui_automation_video.selector_matched", "level": "info"}
{"event": "ui_automation.image_mode_entered", "level": "info"}
{"model": "NARWHAL", "via": "[role='menuitem']:has-text('Nano Banana 2')", "event": "ui_automation.image_model_selected", "level": "info"}
{"value": "16:9", "matched_label": "16:9", "event": "ui_automation.aspect_ratio_set", "level": "info"}
{"desired_count": 1, "final_displayed_count": 1, "success": true, "event": "ui_automation.count_setter_completed", "level": "info"}
{"selector": "div[role=\"textbox\"][data-slate-editor=\"true\"]", "event": "ui_automation.prompt_input_found", "level": "info"}
{"via": "button:has(i.google-symbols:text('arrow_forward'))", "event": "ui_automation.prompt_submitted", "level": "info"}
{"status": 200, "url": "https://aisandbox-pa.googleapis.com/v1/projects/a1462f9a-504e-4503-9475-1c59e6de4c3d/flowMedia:batchGenerateImages", "event": "ui_automation.batch_response_captured", "level": "info"}
```

---

## 4. User-Confirmable Outputs

- Batch Manifest: [`tmp/live_verify_batch.json`](file:///C:/development/github/gflow-cli/tmp/live_verify_batch.json)
- Prompt 0 Output: `C:\Users\ffrol\Downloads\gflow-cli\images\2026-08-06\prompt_0_0.jpg`
- Prompt 1 Output: `C:\Users\ffrol\Downloads\gflow-cli\images\2026-08-06\prompt_1_0.jpg`
