"""Gemini-backed prompt expansion ("Creative Director" mode).

A lightweight, stdlib-only client that rewrites a terse user prompt into a
richer one following Google's official five-component formula
(Subject + Action + Context/Location + Composition/Camera + Style) before it is
handed to Google Flow's Veo/Imagen endpoints.

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

The API key is read from ``GFLOW_CLI_GEMINI_API_KEY`` (see
:class:`gflow_cli.config.Settings.gemini_api_key`); construct via
:meth:`PromptExpander.from_settings`.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from gflow_cli.config import Settings

log = structlog.get_logger(__name__)

#: Default Gemini model. Overridable via ``GFLOW_CLI_GEMINI_MODEL`` so a newer
#: Flash revision can be selected without a code change.
DEFAULT_MODEL = "gemini-2.5-flash"

_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

#: Input is clipped to this many characters before sending (Risk: runaway prompt).
DEFAULT_MAX_INPUT_CHARS = 4000
#: Expanded output is clipped to this many characters before returning/persisting.
DEFAULT_MAX_OUTPUT_CHARS = 3500

#: HTTP statuses worth retrying with backoff. Auth errors (401/403) are NOT here
#: — retrying a bad key just wastes time, so those fail fast to the fallback.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

_SYSTEM_INSTRUCTION = (
    "You are a prompt engineer for an AI image and video generator. "
    "Rewrite the user's prompt into a single, vivid, self-contained prompt that "
    "follows this five-component formula: Subject, Action, Context/Location, "
    "Composition/Camera, and Style. Keep the user's original intent and any named "
    "subjects intact. Do not ask questions, do not add preamble or explanation, "
    "do not use markdown or bullet points, and do not wrap the result in quotes. "
    "Respond with ONLY the rewritten prompt.\n\nUser prompt: "
)

#: Transport contract: ``(url, payload, timeout) -> parsed-json-dict``. Raises
#: :class:`GeminiHttpError` on a non-2xx response. Injectable for tests.
Transport = Callable[[str, "dict[str, object]", float], "dict[str, object]"]


class GeminiHttpError(Exception):
    """Raised by a transport when the Gemini API returns a non-2xx status."""

    def __init__(self, status: int, detail: str = "") -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"Gemini API returned HTTP {status}: {detail}".rstrip(": "))


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


def _default_transport(
    url: str, payload: dict[str, object], timeout: float, api_key: str
) -> dict[str, object]:
    """Real :mod:`urllib` transport. The key travels in a header, not the URL,
    so it never lands in request logs."""
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 — fixed https Gemini endpoint
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:  # noqa: BLE001 — best-effort detail extraction
            detail = exc.reason or ""
        raise GeminiHttpError(exc.code, detail) from exc


class PromptExpander:
    """Expand a user prompt via the public Gemini ``generateContent`` endpoint."""

    def __init__(
        self,
        api_key: str | None,
        *,
        model: str = DEFAULT_MODEL,
        system_instruction: str | None = None,
        max_retries: int = 3,
        timeout: float = 30.0,
        max_input_chars: int = DEFAULT_MAX_INPUT_CHARS,
        max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._instruction = system_instruction or _SYSTEM_INSTRUCTION
        self._max_retries = max_retries
        self._timeout = timeout
        self._max_input_chars = max_input_chars
        self._max_output_chars = max_output_chars
        self._sleep = sleep
        self._transport: Transport
        if transport is not None:
            self._transport = transport
        else:
            key = api_key or ""

            def _bound(url: str, payload: dict[str, object], timeout: float) -> dict[str, object]:
                return _default_transport(url, payload, timeout, key)

            self._transport = _bound

    @classmethod
    def from_settings(cls, settings: Settings, **overrides: object) -> PromptExpander:
        """Build an expander from :class:`gflow_cli.config.Settings`.

        Reads the API key (``GFLOW_CLI_GEMINI_API_KEY``) and model
        (``GFLOW_CLI_GEMINI_MODEL``, default :data:`DEFAULT_MODEL`) from the
        centralized pydantic settings layer.
        """
        return cls(settings.gemini_api_key, model=settings.gemini_model, **overrides)  # type: ignore[arg-type]

    def expand(self, prompt: str) -> ExpansionResult:
        """Return an :class:`ExpansionResult`. Never raises for API/network faults."""
        if not self._api_key:
            log.info("prompt_expander_no_key", reason="GFLOW_CLI_GEMINI_API_KEY unset")
            return ExpansionResult(original=prompt, expanded=prompt, was_expanded=False)

        truncated = prompt[: self._max_input_chars]
        url = _ENDPOINT.format(model=self._model)
        payload = self._build_payload(truncated)

        delay = 1.0
        for attempt in range(self._max_retries + 1):
            try:
                data = self._transport(url, payload, self._timeout)
            except GeminiHttpError as exc:
                if exc.status in _RETRYABLE_STATUS and attempt < self._max_retries:
                    log.warning(
                        "prompt_expander_retry",
                        status=exc.status,
                        attempt=attempt + 1,
                        max_retries=self._max_retries,
                    )
                    self._sleep(delay)
                    delay *= 2
                    continue
                log.warning("prompt_expander_failed", status=exc.status, detail=exc.detail)
                return ExpansionResult(prompt, prompt, was_expanded=False)
            except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                log.warning("prompt_expander_error", error=str(exc))
                return ExpansionResult(prompt, prompt, was_expanded=False)

            expanded = self._extract_text(data)
            if not expanded:
                log.warning("prompt_expander_empty_response")
                return ExpansionResult(prompt, prompt, was_expanded=False)

            cleaned = _clean(expanded)[: self._max_output_chars]
            if not cleaned:
                # Whitespace/quote-only candidate cleans to empty. Returning it
                # would feed an empty prompt to the generator and abort a valid
                # run — fall back to the original to honor the "never fatal" contract.
                log.warning("prompt_expander_empty_response")
                return ExpansionResult(prompt, prompt, was_expanded=False)
            log.info(
                "prompt_expanded",
                original_len=len(prompt),
                expanded_len=len(cleaned),
                model=self._model,
            )
            return ExpansionResult(prompt, cleaned, was_expanded=True)

        # Unreachable: the loop always returns. Kept for type-checker exhaustiveness.
        return ExpansionResult(prompt, prompt, was_expanded=False)  # pragma: no cover

    def _build_payload(self, prompt: str) -> dict[str, object]:
        return {
            "contents": [{"parts": [{"text": self._instruction + prompt}]}],
            "generationConfig": {"temperature": 0.7, "maxOutputTokens": 1024},
        }

    @staticmethod
    def _extract_text(data: dict[str, object]) -> str:
        """Pull the candidate text out of a ``generateContent`` response, defensively.

        Returns ``""`` for any shape that does not carry a string candidate, which
        the caller treats as a fallback-to-original signal.
        """
        node: Any = data
        try:
            text: Any = node["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError):
            return ""
        return text if isinstance(text, str) else ""


def _clean(text: str) -> str:
    """Strip whitespace and a single layer of *wrapping* quotes from model output.

    Only strips when the quote char does not also appear inside, so a prompt whose
    content legitimately starts and ends with a quote (e.g. ``"A" vs "B"``) is left
    intact rather than mangled.
    """
    cleaned = text.strip()
    if (
        len(cleaned) >= 2
        and cleaned[0] == cleaned[-1]
        and cleaned[0] in {'"', "'"}
        and cleaned[0] not in cleaned[1:-1]
    ):
        cleaned = cleaned[1:-1].strip()
    return cleaned
