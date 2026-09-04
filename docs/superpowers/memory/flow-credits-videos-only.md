---
name: flow-credits-videos-only
description: Flow credits are spent ONLY by video generation; image generation (t2i + character face/body) is unlimited/free
---

**Flow charges credits ONLY for VIDEO generation (Veo). Image generation costs ZERO credits** — this covers `t2i`, `image batch`, and `character create` face/body reference images (Nano Banana / Imagen). The user confirmed 2026-06-03: "image generation doesn't spend credits, we have unlimited image generation; what spends credits is videos."
**Refined 2026-09-02 (user):** images are **not unlimited** — they are subject to a **daily cap** — but hitting that cap is a rate limit, NOT a credit charge. So: free, but finite per day.

⚠️ **The repo contradicts this and is WRONG.** `pyproject.toml` (`e2e_image: spends Imagen credits`, `e2e_batch`, `e2e_character`, `smoke`) and `docs/E2E_TESTING.md` (“~1 Imagen”, “1 Imagen credit per test”) all price image e2e in credits. Confirmed stale 2026-09-02. This is actively harmful: it discourages running credit-FREE tests, which is exactly what blocked the #615 guard verification.

**Why:** corrects a wrong assumption baked into earlier notes (e.g. [[flow-character-entity-protocol]] said character image-gen was "reCAPTCHA+credits"; [[rest-path-capability-matrix]] lumped all generation as credit-gated; [[verification-ledger-5-layer]] framed any generation as "credit-spending"). The reCAPTCHA wall on image gen is real, but the **credit** cost is not.

**How to apply:** do NOT gate image-only operations (t2i, character create) behind credit-confirmation prompts — they're free. Only gate `video` generation (Veo: i2v/t2v/r2v, video chain) on credit confirmation. The character-promo capture runs (`record-promo --phases character`) are FREE; only a `--phases video` capture spends credits. Still confirm before VIDEO runs.
