---
name: verification-ledger-5-layer
description: "Never claim a credit-spending Flow run succeeded without all five verification layers — file count alone is the classic trap that led to the 'Phase 7e produced 4 unique images' false claim"
---

Rule: Before declaring a Flow credit-spending run successful, verify ALL FIVE layers. Missing any one of them is the failure mode that produced the 2026-05-23 "Phase 7e produced 4 unique images" retraction:

1. **File count** — `len(image_files) == sum(p.count for p in prompts)` (with `_diagnostics/` excluded from rglob).
2. **Magic bytes** per saved file — `head -c 16 file | od -An -tx1` should start with one of: `89504e47` (PNG), `ffd8ffe0` / `ffd8ffe1` (JPEG), `52494646...57454250` (WebP). NOT html / NOT mp4 (`66747970` ftyp). The previous wrong claim was based on file sizes alone — sizes differ between WAF rejection HTML pages, real PNGs, and partial downloads, so size-difference is no evidence of "different images."
3. **Pillow dimensions** per file — `Image.open(p).size` within 2 % of the declared aspect ratio. Catches video-mode confusion (768×1376 OK for 9:16; 1280×720 = wrong-mode video frame).
4. **structlog event invariants** — `image_batch.row_completed` count == sum(p.count); `image_batch.submission_attempt` count == len(prompts); shared `project_id` across all `submission_attempt` events.
5. **User gallery confirmation** — ask the user to open Flow gallery on the relevant profile and confirm the project tile contains exactly N images of the expected aspect ratios. The gallery is the only authoritative source for "did Flow actually generate this?" — disk artifacts can be WAF rejection pages saved with a `.png` suffix.

**Why:** The 2026-05-23 mode-switch bug ran for hours before being caught because layers 1+2 (file existed, looked PNG-ish on disk) were treated as sufficient. Layer 3 (Pillow dims) would have flagged a 1280×720 frame as wrong-aspect on a 9:16 request. Layer 5 (gallery check) would have shown a VIDEO not an image. The "different file sizes mean different images" reasoning is a known cognitive bias — sizes differ for trivial reasons (compression, metadata, HTTP error body length).

**How to apply:**
- The image-batch e2e test (`tests/e2e/test_image_batch_e2e.py`) already enforces 1–4 automatically. Trust it; do not bypass.
- For ad-hoc verification scripts under `tmp/`, write the 5-layer check explicitly. Do NOT print "OK" until all five fire.
- For multi-image batches, ALSO assert that the image files are pairwise distinct (sha256 differs) — a transport bug can write the same image N times if the listener cross-contaminates, and "N files of the same size" would pass file-count + magic-byte + dims while still being wrong. Use [`assert len({f.read_bytes()[:1024] for f in files}) == len(files)`].
- The user gallery check (layer 5) cannot be skipped on first verification of a new code path. It's cheap (one screenshot) and catches whole categories of bug the local file checks miss.

**Reference:** PR #40 docs/LIVE_VERIFICATION_image_batch.md § Post-mode-switch-fix verification — canonical ledger format. Earlier retraction in the same file documents what the absence of layers 2+5 cost.
