"""Provider-agnostic prompt rewriting for the tools layer.

A lightweight, stdlib-only client that rewrites a terse user prompt into a
richer one before it is handed to Google Flow's Veo/Imagen endpoints. The tool
supplies the system instruction; this module only moves bytes.

The wire format is the **OpenAI Chat Completions API** — the de-facto standard
that OpenAI, gateways/proxies (OpenRouter, LiteLLM, freellmapi, ...), local
runtimes (Ollama, LM Studio) and Google's own compatibility endpoint all speak.
Pointing :data:`gflow_cli.config.Settings.llm_base_url` at any of them is the
whole configuration story; provider API keys stay with the gateway.

Design constraints:

* **No new dependencies** — uses :mod:`urllib.request` rather than the project's
  ``httpx`` so the expander stays a self-contained, synchronous pre-processing
  step with a trivially mockable seam (the injected ``transport`` callable).
* **Never fatal** — expansion is a convenience. A missing/invalid API key, a
  rate limit, a network blip, or a malformed response all degrade gracefully:
  the method logs and returns the *original* prompt with ``was_expanded=False``.
  Callers can therefore always use :attr:`ExpansionResult.expanded` verbatim.
* **Bounded** — input is truncated before sending and output is truncated after
  receiving, so a pathological prompt cannot blow up the request or the catalog.
  The retry loop is additionally bounded by an overall wall-clock budget
  (:data:`DEFAULT_MAX_TOTAL_SECONDS`) so sustained rate limiting can never stall a
  batch for the full per-attempt-timeout x retries schedule.

The retry/backoff loop is deliberately hand-rolled rather than delegated to
``tenacity`` (a project dependency): tenacity is built around *re-raising* after
exhausting retries, whereas this client's contract is the opposite — it must
*never* raise, falling back to the original prompt. The custom loop also needs
per-attempt ``structlog`` events and a total-time budget that clamps each
attempt's timeout to the remaining budget; expressing that through tenacity's
stop/wait/retry primitives would be more code, not less, for a ~20-line loop with
fully-injectable ``transport`` / ``sleep`` / ``clock`` seams.

Because ``base_url`` is user-supplied, this module treats the endpoint as a
trust boundary: :func:`gflow_cli.config.Settings._validate_llm_base_url` gates
the scheme, and :func:`_build_opener` refuses to follow redirects (``urllib``
would otherwise re-send the ``Authorization`` header to whatever host a 302
names). Error-response bodies are redacted before they reach the log.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

from gflow_cli.config import DEFAULT_LLM_BASE_URL
from gflow_cli.data.redaction import redact_error_detail

log = structlog.get_logger(__name__)

#: Default endpoint. Re-exported from :mod:`gflow_cli.config` so callers and
#: tests have a single name for "the user did not choose a gateway".
DEFAULT_BASE_URL = DEFAULT_LLM_BASE_URL

#: Model used *only* when running against :data:`DEFAULT_BASE_URL` with no model
#: configured anywhere. Google's compat endpoint has no server-side default and
#: answers ``400 "model is not specified"`` — verified live — so the default
#: endpoint has to ship with a matching default model.
#:
#: It is deliberately NOT applied to a user-chosen gateway: sending a
#: Google-specific model name to a gateway that does not serve it is a silent
#: 400, which is the exact defect that made the prompt tools unusable through a
#: gateway in the first place. An explicit endpoint means the gateway chooses.
DEFAULT_MODEL_FOR_DEFAULT_ENDPOINT = "gemini-2.5-flash"


def resolve_model(pin: str | None, env_model: str | None, base_url: str) -> str | None:
    """Resolve the effective model. Single source of truth for the precedence.

    ``TOML pin > GFLOW_CLI_LLM_MODEL > default-endpoint default > None``.

    ``None`` means "omit ``model`` from the request and let the gateway choose",
    which is only reachable for a user-chosen endpoint — see
    :data:`DEFAULT_MODEL_FOR_DEFAULT_ENDPOINT` for why the default endpoint
    cannot use it.

    Shared with :func:`gflow_cli.tools.invocation.applied_tool_from_spec` so the
    model recorded as provenance is the one actually requested.
    """
    if pin:
        return pin
    if env_model:
        return env_model
    if (base_url or DEFAULT_BASE_URL).rstrip("/") == DEFAULT_BASE_URL:
        return DEFAULT_MODEL_FOR_DEFAULT_ENDPOINT
    return None


#: Input is clipped to this many characters before sending (Risk: runaway prompt).
DEFAULT_MAX_INPUT_CHARS = 4000
#: Expanded output is clipped to this many characters before returning/persisting.
DEFAULT_MAX_OUTPUT_CHARS = 3500

#: Per-attempt socket timeout (seconds). A Flash-class text rewrite normally
#: returns in 1-3s (measured 0.5-1.2s against both a local gateway and Google's
#: compat endpoint); 20s is generous while keeping the worst case low. The
#: overall wall-clock is additionally bounded by :data:`DEFAULT_MAX_TOTAL_SECONDS`.
DEFAULT_TIMEOUT = 20.0
#: Overall wall-clock budget across *all* attempts (initial + retries + backoff
#: sleeps). Without this, sustained 429s could block ~4x the per-attempt timeout
#: plus the backoff schedule (~120s) before falling back; the budget caps that so
#: a tool never stalls a batch for long. Retries stop once the budget is spent and
#: each attempt's timeout is clamped to the remaining budget.
DEFAULT_MAX_TOTAL_SECONDS = 60.0

#: HTTP statuses worth retrying with backoff. Auth errors are NOT here —
#: retrying a bad key just wastes time, so those fail fast to the fallback.
#: Note providers disagree on the code: a bad key is 401 on most gateways but
#: 400 on Google's compat endpoint. Neither is retryable, so both fail fast.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

_SYSTEM_INSTRUCTION = (
    "You are a prompt engineer for an AI image and video generator. "
    "Rewrite the user's prompt into a single, vivid, self-contained prompt that "
    "follows this five-component formula: Subject, Action, Context/Location, "
    "Composition/Camera, and Style. Keep the user's original intent and any named "
    "subjects intact. Do not ask questions, do not add preamble or explanation, "
    "do not use markdown or bullet points, and do not wrap the result in quotes. "
    "Respond with ONLY the rewritten prompt."
)

#: Transport contract: ``(url, payload, timeout) -> parsed-json-dict``. Raises
#: :class:`LlmHttpError` on a non-2xx response. Injectable for tests.
Transport = Callable[[str, "dict[str, object]", float], "dict[str, object]"]


class LlmHttpError(Exception):
    """Raised by a transport when the LLM endpoint returns a non-2xx status."""

    def __init__(self, status: int, detail: str = "") -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"LLM API returned HTTP {status}: {detail}".rstrip(": "))


@dataclass(frozen=True)
class ExpansionResult:
    """Outcome of an expansion attempt.

    :attr:`expanded` is *always* a usable prompt — it equals :attr:`original`
    when expansion was skipped or failed, in which case :attr:`was_expanded` is
    ``False``.
    """

    original: str
    expanded: str
    was_expanded: bool


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuses every redirect.

    ``urllib``'s default handler re-sends request headers — including
    ``Authorization`` — to whatever host a 3xx ``Location`` names, so a hostile
    or compromised gateway could harvest the API key with a single redirect.
    Returning ``None`` from ``redirect_request`` is the documented way to
    decline: urllib then surfaces the 3xx as an ``HTTPError``, which the
    never-raise contract already degrades to "use the original prompt".

    Note this must *replace* the default handler rather than merely be omitted
    from ``build_opener`` — that function installs its default handler set
    regardless, so leaving it out silently leaves redirects enabled.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


def _build_opener() -> urllib.request.OpenerDirector:
    """An opener that does **not** follow redirects. See :class:`_NoRedirect`."""
    return urllib.request.build_opener(_NoRedirect)


def _default_transport(
    url: str, payload: dict[str, object], timeout: float, api_key: str | None
) -> dict[str, object]:
    """Real :mod:`urllib` transport.

    The key travels in a header, never the URL, so it cannot land in request
    logs. When no key is configured the ``Authorization`` header is omitted
    entirely rather than sent empty — keyless local gateways reject a malformed
    bearer token.
    """
    body = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(  # noqa: S310 — scheme gated by Settings._validate_llm_base_url
        url,
        data=body,
        method="POST",
        headers=headers,
    )
    try:
        with _build_opener().open(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:  # noqa: BLE001 — best-effort detail extraction
            detail = exc.reason or ""
        raise LlmHttpError(exc.code, detail) from exc


class PromptExpander:
    """Expand a user prompt via an OpenAI-compatible ``chat/completions`` endpoint."""

    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        model: str | None = None,
        system_instruction: str | None = None,
        max_retries: int = 3,
        timeout: float = DEFAULT_TIMEOUT,
        max_total_seconds: float = DEFAULT_MAX_TOTAL_SECONDS,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_key = api_key
        self._base_url = (base_url or DEFAULT_BASE_URL).rstrip("/")
        self._model = resolve_model(model, None, self._base_url)
        self._instruction = system_instruction or _SYSTEM_INSTRUCTION
        self._max_retries = max_retries
        self._timeout = timeout
        self._max_total_seconds = max_total_seconds
        self._max_input_chars = max_input_chars
        self._max_output_chars = max_output_chars
        self._sleep = sleep
        self._clock = clock
        self._transport: Transport
        if transport is not None:
            self._transport = transport
        else:
            key = api_key

            def _bound(url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
                return _default_transport(url, payload, timeout, key)

            self._transport = _bound
        if self._is_configured:
            # Audit trail: prompts and, for multimodal tools, base64-encoded
            # image bytes leave the machine for this host. Host only — a path or
            # query could carry a token.
            log.info(
                "prompt_expander_endpoint",
                host=urllib.parse.urlsplit(self._base_url).netloc,
            )

    @property
    def _is_configured(self) -> bool:
        """Whether to attempt a call at all.

        A key is enough (``base_url`` defaults to Google's compat endpoint), and
        an explicitly-chosen ``base_url`` is enough on its own because local
        gateways need no credential. Neither ⇒ the user has not set this up, so
        skip the call rather than spend a doomed round trip per prompt.
        """
        return bool(self._api_key) or self._base_url != DEFAULT_BASE_URL

    @property
    def _url(self) -> str:
        return f"{self._base_url}/chat/completions"

    def _execute_expansion_payload(self, url: str, payload: dict[str, object]) -> str | None:
        """Run the request with retry/backoff; return the raw expanded text or None.

        Handles budget management, exponential backoff on retryable HTTP errors,
        and network fault tolerance. Never raises — callers treat ``None`` as
        a signal to fall back to the original prompt.
        """
        start = self._clock()
        delay = 1.0
        for attempt in range(self._max_retries + 1):
            remaining = self._max_total_seconds - (self._clock() - start)
            if remaining <= 0:
                log.warning(
                    "prompt_expander_budget_exhausted",
                    max_total_seconds=self._max_total_seconds,
                )
                return None
            attempt_timeout = min(self._timeout, remaining)
            try:
                data = self._transport(url, payload, attempt_timeout)
            except LlmHttpError as exc:
                if exc.status in _RETRYABLE_STATUS and attempt < self._max_retries:
                    next_delay = self._retry_after_error(exc, attempt, start, delay)
                    if next_delay is None:
                        return None
                    delay = next_delay
                    continue
                # The body is attacker-influenced: a hostile or debug-mode
                # gateway can echo our own Authorization header back in its
                # error text, so redact before it reaches the log.
                log.warning(
                    "prompt_expander_failed",
                    status=exc.status,
                    detail=redact_error_detail(exc.detail),
                )
                return None
            except (OSError, json.JSONDecodeError) as exc:
                # OSError covers urllib.error.URLError, TimeoutError, and socket errors.
                log.warning("prompt_expander_error", error=redact_error_detail(str(exc)))
                return None

            expanded = self._extract_text(data)
            if not expanded:
                log.warning("prompt_expander_empty_response")
                return None
            return expanded

        return None  # pragma: no cover

    def _retry_after_error(
        self, exc: LlmHttpError, attempt: int, start: float, delay: float
    ) -> float | None:
        """Sleep and return the next backoff delay for a retryable error, or None to abort.

        Caller has already confirmed the error is retry-eligible
        (``exc.status in _RETRYABLE_STATUS and attempt < self._max_retries``).
        """
        if (self._clock() - start) + delay >= self._max_total_seconds:
            # No budget left to sleep + run another attempt — fall back
            # now instead of sleeping into a guaranteed-skipped retry.
            log.warning(
                "prompt_expander_budget_exhausted",
                max_total_seconds=self._max_total_seconds,
            )
            return None
        log.warning(
            "prompt_expander_retry",
            status=exc.status,
            attempt=attempt + 1,
            max_retries=self._max_retries,
        )
        self._sleep(delay)
        return delay * 2

    def _not_configured(self, prompt: str) -> ExpansionResult:
        log.info(
            "prompt_expander_not_configured",
            reason="set GFLOW_CLI_LLM_API_KEY or GFLOW_CLI_LLM_BASE_URL",
        )
        return ExpansionResult(original=prompt, expanded=prompt, was_expanded=False)

    def _finish(self, prompt: str, expanded: str | None, event: str) -> ExpansionResult:
        """Clean, truncate and wrap a raw model response."""
        if not expanded:
            return ExpansionResult(prompt, prompt, was_expanded=False)
        cleaned = _clean(expanded)[: self._max_output_chars]
        if not cleaned:
            # Whitespace/quote-only candidate cleans to empty. Returning it
            # would feed an empty prompt to the generator and abort a valid
            # run — fall back to the original to honor the "never fatal" contract.
            log.warning("prompt_expander_empty_response")
            return ExpansionResult(prompt, prompt, was_expanded=False)
        log.info(event, original_len=len(prompt), expanded_len=len(cleaned), model=self._model)
        return ExpansionResult(prompt, cleaned, was_expanded=True)

    def expand(self, prompt: str) -> ExpansionResult:
        """Return an :class:`ExpansionResult`. Never raises for API/network faults."""
        if not self._is_configured:
            return self._not_configured(prompt)

        truncated = prompt[: self._max_input_chars]
        payload = self._build_payload(truncated)
        return self._finish(
            prompt, self._execute_expansion_payload(self._url, payload), "prompt_expanded"
        )

    def expand_multimodal(self, prompt: str, image_paths: list[str]) -> ExpansionResult:
        """Expand a prompt using multimodal input (e.g. video frames or images)."""
        if not self._is_configured:
            return self._not_configured(prompt)

        parts: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        for path in image_paths:
            try:
                with open(path, "rb") as image_file:  # noqa: PTH123
                    encoded = base64.b64encode(image_file.read()).decode("utf-8")
            except OSError as e:
                log.warning("prompt_expander_multimodal_read_error", path=path, error=str(e))
                continue
            parts.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/jpeg;base64,{encoded}"},
                }
            )

        payload = self._build_payload(parts, temperature=0.4)
        return self._finish(
            prompt,
            self._execute_expansion_payload(self._url, payload),
            "prompt_expanded_multimodal",
        )

    def _build_payload(
        self, content: str | list[dict[str, Any]], *, temperature: float = 0.7
    ) -> dict[str, object]:
        """Assemble an OpenAI Chat Completions request body.

        ``content`` is either the plain user prompt or a multimodal parts list.
        The tool's instruction rides its own ``system`` message rather than being
        concatenated onto the user's text, which is how every OpenAI-compatible
        provider expects it.
        """
        # Approximate token budget: 1 token ~= 4 chars. Clamp to [512, 8192].
        token_budget = max(512, min(8192, self._max_output_chars // 4))
        payload: dict[str, object] = {
            "messages": [
                {"role": "system", "content": self._instruction},
                {"role": "user", "content": content},
            ],
            "temperature": temperature,
            "max_tokens": token_budget,
        }
        if self._model:
            # Omitted entirely when unset so the gateway applies its own default
            # (e.g. a fallback chain). Sending a vendor-specific model name to an
            # endpoint that does not serve it is a silent 400.
            payload["model"] = self._model
        return payload

    @staticmethod
    def _extract_text(data: dict[str, object]) -> str:
        """Pull the assistant text out of a ``chat/completions`` response, defensively.

        Returns ``""`` for any shape that does not carry a string message, which
        the caller treats as a fallback-to-original signal. ``content`` is
        legitimately ``None`` on a refusal, hence the isinstance guard.
        """
        node: Any = data
        try:
            text: Any = node["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return ""
        return text if isinstance(text, str) else ""


def _clean(text: str) -> str:
    """Strip whitespace, a markdown code fence, and a single layer of *wrapping*
    quotes from model output.

    Quotes are only stripped when the quote char does not also appear inside, so
    a prompt whose content legitimately starts and ends with a quote (e.g.
    ``"A" vs "B"``) is left intact rather than mangled. The fence strip exists
    because non-Gemini models commonly fence their answer where Gemini quotes
    it — left in place it would be submitted verbatim as part of the prompt.
    """
    cleaned = text.strip()
    if cleaned.startswith("```") and cleaned.endswith("```") and len(cleaned) >= 6:
        inner = cleaned[3:-3]
        # Drop an optional language tag on the opening fence ("```text\n...").
        first_newline = inner.find("\n")
        if first_newline != -1 and " " not in inner[:first_newline]:
            inner = inner[first_newline + 1 :]
        cleaned = inner.strip()
    if (
        len(cleaned) >= 2
        and cleaned[0] == cleaned[-1]
        and cleaned[0] in {'"', "'"}
        and cleaned[0] not in cleaned[1:-1]
    ):
        cleaned = cleaned[1:-1].strip()
    return cleaned
