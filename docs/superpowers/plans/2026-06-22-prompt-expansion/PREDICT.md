# Predict: Prompt Expansion ("Creative Director" mode)

## Verdict: GO
**Confidence:** 8.6/10

## Summary
The five personas agree that integrating a prompt expansion flag (`--expand` / `-e`) powered by Gemini is a high-value, low-risk enhancement. Using standard library `urllib` to make direct REST calls keeps the dependencies lightweight and decoupled. The primary risks are database migration drift and API rate limits (HTTP 429). Mitigations include safe SQL migrations, exponential backoff retries, and graceful fallback to the raw prompt on failure.

---

## Persona findings

### Architect — GO (9/10)
- Expose the expander as a lightweight class in `src/gflow_cli/api/prompt_expander.py`.
- Keep it free of heavy external dependencies (like `google-genai` or `google-generativeai` packages) by querying the `generativelanguage.googleapis.com` REST endpoint directly via `urllib.request`.
- This ensures we do not introduce package conflicts or complicate the `uv.lock` dependency graph.

### Security / reCAPTCHA — GO (9/10)
- **Secrets:** The key (`GFLOW_CLI_GEMINI_API_KEY` or fallback `GOOGLE_API_KEY`) must be read from settings and never exposed in logs or exception messages.
- **Privacy:** If the user has configured prompt redaction (`GFLOW_CLI_HISTORY_PROMPTS=redacted`), both the original prompt and the expanded prompt must be redacted (hashed or not saved) in the operations database.

### Performance / Playwright — CAUTION (8/10)
- **API Latency:** Querying Gemini adds a round-trip delay (~1-2 seconds). This is acceptable for interactive generation, but batch runs need rate-limit safety pauses to avoid hitting API RPM/TPM thresholds.
- **Graceful Fallback:** If the Gemini API is down, rate-limited, or unauthorized, the command must not crash. It should log a warning to `stderr` and continue generation using the original, unexpanded prompt.

### CLI UX / Cross-platform — GO (8/10)
- Expose the `--expand` / `-e` flag in Click commands for T2I, T2V, and batch generation.
- Log the expanded prompt in the console/structured logs to give the user visibility.
- Ensure the `GFLOW_CLI_GEMINI_API_KEY` is documented in `CONFIGURATION.md` and added to `.env.template`.

### Devil's Advocate — GO (9/10)
- **Why build it inside the CLI?** Integrating expansion allows the user to write simple prompts like "neon cat" and get beautifully composed, detailed outputs directly from the terminal. It also allows developers to build simple lists of prompts in manifests and have them expanded automatically during overnight batches.
- We must make sure database migration to add `expanded_prompt` to SQLite is safe and doesn't break existing local databases.

---

## High-confidence risks (flagged by 2+ personas)
1. **Gemini API failures interrupting generations:** If a network issue or missing API key crashes the CLI, it degrades the user experience.
   - *Mitigation:* Catch all HTTP, parse, and timeout errors in the prompt expander client and gracefully fall back to returning the original prompt.

---

## Conflicts resolved
- *SDK dependency vs REST client:* The Architect advocated for a simple `urllib` client, whereas Devil's Advocate checked if the official SDK was easier. We resolved to use `urllib` to keep the project's dependency surface tiny and completely avoid SDK version lock conflicts.

---

## Required mitigations before EXECUTE
1. Safe database schema migration helper for SQLite.
2. Graceful exception boundary inside the CLI run loop to log warning and fallback to raw prompt.
3. Exponential backoff retry logic inside the prompt expander.

---

## Recommended next step
Proceed to `/gflow:scenario` and write tests verifying the fallback mechanism on mock HTTP errors.
