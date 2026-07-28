"""Spike: does an OpenAI-compatible endpoint support what expander.py needs? (issue #387)

Throwaway diagnostic. Answers four questions before we commit to the transport
rewrite, per AGENTS.md "verify third-party runtime behavior empirically":

  1. Does plain text chat/completions work at all?
  2. Are ``temperature`` + ``max_tokens`` accepted, or does it want
     ``max_completion_tokens``?
  3. Does multimodal ``image_url`` with a data: URI actually reach the model?
  4. Does a bad key produce a real 4xx status, or a 200 with an error body?

Deliberately stdlib ``urllib`` rather than the ``openai`` SDK: this is the
transport we intend to ship, and an SDK would paper over exactly the header /
param quirks we are trying to discover.

Probe 3's pass criterion is *semantic*, not just HTTP 200. A gateway that
silently drops the image part still returns 200 with plausible prose, which is
the failure mode authored test fixtures would hide forever. So we send a solid
red square and ask for the color: a model that never received the image cannot
answer correctly.

Run it against BOTH the default endpoint and the real gateway — a pass on one
is not a pass on the other.

Usage::

    uv run python scripts/diag/spike_387_openai_compat.py \
        --base-url https://generativelanguage.googleapis.com/v1beta/openai \
        --key "$GEMINI_KEY" --model gemini-2.5-flash

    # keyless local gateway
    uv run python scripts/diag/spike_387_openai_compat.py \
        --base-url http://127.0.0.1:8080/v1 --model some-model
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import time
import urllib.error
import urllib.request

TIMEOUT = 120.0  # generous: we want to MEASURE latency, not clip it


def post(base_url: str, key: str | None, payload: dict) -> tuple[int, str, float]:
    """POST to {base_url}/chat/completions. Returns (status, body, elapsed_seconds).

    Never raises — status 0 means a transport-level failure and ``body`` carries
    the exception text.
    """
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(  # noqa: S310 — spike; URL is operator-supplied by design
        url, data=json.dumps(payload).encode("utf-8"), method="POST", headers=headers
    )
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", errors="replace"), time.monotonic() - start
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace"), time.monotonic() - start
    except OSError as exc:
        return 0, f"{type(exc).__name__}: {exc}", time.monotonic() - start


def content(body: str) -> str:
    """Pull choices[0].message.content, or '' for any other shape."""
    try:
        text = json.loads(body)["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError):
        return ""
    return text if isinstance(text, str) else ""


def red_square_data_uri() -> str:
    """A 64x64 solid red JPEG as a data: URI. Unambiguous on purpose."""
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), (220, 20, 20)).save(buffer, format="JPEG")
    return "data:image/jpeg;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def report(name: str, passed: bool, status: int, elapsed: float, body: str, note: str = "") -> bool:
    mark = "PASS" if passed else "FAIL"
    print(f"\n[{mark}] {name}  (HTTP {status}, {elapsed:.1f}s)")
    if note:
        print(f"       {note}")
    if not passed:
        print(f"       body: {body[:500]}")
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="e.g. https://host/v1")
    parser.add_argument("--key", default=None, help="omit for a keyless local gateway")
    parser.add_argument("--model", required=True)
    args = parser.parse_args()

    base, key, model = args.base_url, args.key, args.model
    print(f"endpoint : {base.rstrip('/')}/chat/completions")
    print(f"model    : {model}")
    print(f"auth     : {'Bearer <set>' if key else 'NONE (keyless)'}")

    results: list[bool] = []

    # --- 1. plain text -----------------------------------------------------
    status, body, elapsed = post(base, key, {
        "model": model,
        "messages": [
            {"role": "system", "content": "Reply with only the word OK."},
            {"role": "user", "content": "hi"},
        ],
    })
    text = content(body)
    results.append(report(
        "1. text chat/completions", status == 200 and "OK" in text.upper(), status, elapsed, body,
        f"content={text[:80]!r}",
    ))
    if not results[0]:
        print("\nProbe 1 failed — the whole design is dead against this endpoint. Stopping.")
        return 1

    # --- 2. temperature + max_tokens ---------------------------------------
    status, body, elapsed = post(base, key, {
        "model": model,
        "messages": [
            {"role": "system", "content": "Reply with only the word OK."},
            {"role": "user", "content": "hi"},
        ],
        "temperature": 0.7,
        "max_tokens": 875,  # what expander.py's //4 heuristic produces for 3500 chars
    })
    ok = status == 200 and content(body) != ""
    note = ""
    if not ok:
        # Some endpoints reject max_tokens in favour of max_completion_tokens.
        alt_status, alt_body, alt_elapsed = post(base, key, {
            "model": model,
            "messages": [
                {"role": "system", "content": "Reply with only the word OK."},
                {"role": "user", "content": "hi"},
            ],
            "temperature": 0.7,
            "max_completion_tokens": 875,
        })
        if alt_status == 200 and content(alt_body) != "":
            ok, status, elapsed = True, alt_status, alt_elapsed
            note = "NOTE: rejected max_tokens, accepted max_completion_tokens"
    results.append(report("2. temperature + max_tokens", ok, status, elapsed, body, note))

    # --- 3. multimodal image_url data: URI ----------------------------------
    status, body, elapsed = post(base, key, {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "What color is this square? Reply with one word."},
                {"type": "image_url", "image_url": {"url": red_square_data_uri()}},
            ],
        }],
    })
    text = content(body)
    results.append(report(
        "3. multimodal image_url (SEMANTIC)", status == 200 and "RED" in text.upper(),
        status, elapsed, body, f"content={text[:80]!r}",
    ))

    # --- 4. bad key -> real 4xx, not a 200 with an error body ---------------
    if key:
        status, body, elapsed = post(base, "sk-definitely-not-a-valid-key", {
            "model": model,
            "messages": [{"role": "user", "content": "hi"}],
        })
        # Any 4xx is a pass: what matters is that the failure is knowable from the
        # STATUS. freellmapi answers 401, Google's compat endpoint answers 400.
        # Neither is in _RETRYABLE_STATUS, so both fail fast to the fallback —
        # which is the property under test. A 200 carrying an error body would
        # bypass that rule entirely and parse as an empty response.
        results.append(report(
            "4. bad key yields a 4xx status", 400 <= status < 500, status, elapsed, body,
            "a 200-with-error-body would bypass expander.py's fail-fast rule",
        ))
    else:
        print("\n[SKIP] 4. bad key — endpoint is keyless")

    print(f"\n{sum(results)}/{len(results)} probes passed")
    print("Latency above is the input for the 20s/attempt + 60s total budget question.")
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
