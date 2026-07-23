"""Unit tests for the incident-diagnostics sanitization primitives (Task 2).

Every automatic incident artifact is built from these reducers, so this file
carries the S01/S02/S03/S29/S31 canary-based negative assertions: hostile
values must never survive into any primitive's output.
"""

from __future__ import annotations

from dataclasses import asdict

from gflow_cli.diagnostics import (
    CommandHasher,
    classify_title,
    reduce_error_body,
    sanitize_url,
    text_summary,
)

# Hostile fixtures — each unique enough that a leak is unambiguous.
CANARY_TOKEN = "03AFcWeA7CANARYRECAPTCHAxK9"
CANARY_COOKIE = "__Secure-next-auth.session-token=CANARYCOOKIEVALUE"
CANARY_SIGNED = "X-Goog-Signature=CANARYSIGNATUREabc123"
CANARY_PROMPT = "a CANARYPROMPT cat on a synthwave rooftop"
CANARY_EMAIL = "canary.person@example.com"
CANARY_ANSI = "\x1b[31mCANARYANSI\x1b[0m"
CANARY_UNICODE = "CANARY\U0001f512‮SECRET"
ALL_CANARIES = (
    CANARY_TOKEN,
    CANARY_COOKIE,
    CANARY_SIGNED,
    CANARY_PROMPT,
    CANARY_EMAIL,
    CANARY_ANSI,
    CANARY_UNICODE,
)


def _leaked(result: object, needles: tuple[str, ...] = ALL_CANARIES) -> list[str]:
    blob = repr(result)
    return [n for n in needles if n in blob]


class TestSanitizeUrl:
    def test_unknown_hosts_and_routes_become_other(self) -> None:
        """S31: unknown host/path must not be persisted, even reduced."""
        h = CommandHasher()
        out = sanitize_url(f"https://evil.example/acct-12345/{CANARY_TOKEN}", h)
        assert out.host_category == "other"
        assert out.route == "other"
        assert not _leaked(out)

    def test_known_flow_routes_reduce_to_canonical(self) -> None:
        h = CommandHasher()
        uuid = "0bd19956-ae42-4c95-a897-940e0e2c0a63"
        out = sanitize_url(
            f"https://labs.google/fx/tools/flow/project/{uuid}?hl=en&tok={CANARY_TOKEN}#frag",
            h,
        )
        assert out.host_category == "flow_app"
        assert out.route.startswith("/fx/tools/flow/project/")
        assert uuid not in out.route
        assert "hl=en" not in out.route
        assert "frag" not in out.route
        assert not _leaked(out)
        # Same hasher → stable reduction (equality correlation inside a command).
        again = sanitize_url(f"https://labs.google/fx/tools/flow/project/{uuid}", h)
        assert again.route == out.route

    def test_known_aisandbox_method_routes_stay_canonical(self) -> None:
        h = CommandHasher()
        out = sanitize_url(
            "https://aisandbox-pa.googleapis.com/v1/video:batchAsyncGenerateVideoText", h
        )
        assert out.host_category == "aisandbox"
        assert out.route == "/v1/video:batchAsyncGenerateVideoText"
        gen = sanitize_url(
            "https://aisandbox-pa.googleapis.com/v1/projects/abc-123/flowMedia:batchGenerateImages",
            h,
        )
        assert gen.route == "/v1/projects/{id}/flowMedia:batchGenerateImages"

    def test_literal_segments_survive_and_numeric_ids_do_not(self) -> None:
        h = CommandHasher()
        out = sanitize_url("https://aisandbox-pa.googleapis.com/v1/projects/123456789/media", h)
        assert "/v1/projects/" in out.route
        assert "123456789" not in out.route
        assert out.route.endswith("/media")


class TestClassifyTitle:
    def test_flow_app_crash_signature(self) -> None:
        out = classify_title("Application error: a client-side exception has occurred")
        assert out.category == "flow_app_crash"
        assert out.length == len("Application error: a client-side exception has occurred")

    def test_flow_title(self) -> None:
        assert classify_title("Flow - Google Labs").category == "flow"

    def test_unknown_title_never_persists_raw_text(self) -> None:
        """S03: an unknown title reduces to category+length — no raw, no digest."""
        title = f"My private doc about {CANARY_EMAIL}"
        out = classify_title(title)
        assert out.category == "other"
        assert out.length == len(title)
        assert not _leaked(out)


class TestTextSummary:
    def test_text_stored_as_category_and_length_only(self) -> None:
        """S03: console/error text becomes {category, length} — nothing else."""
        for canary in ALL_CANARIES:
            out = text_summary(canary, "console_error")
            assert out.category == "console_error"
            assert out.length == len(canary)
            assert set(asdict(out)) == {"category", "length"}
            assert not _leaked(out)


class TestCommandHasher:
    def test_hmac_identity_uses_random_unpersisted_key(self) -> None:
        """S03: per-command random key — stable within an instance, different
        across instances, and never exposed through repr."""
        a, b = CommandHasher(), CommandHasher()
        assert a.identity("profile-x") == a.identity("profile-x")
        assert a.identity("profile-x") != b.identity("profile-x")
        assert len(a.identity("profile-x")) == 16
        assert repr(a) == "CommandHasher()"

    def test_identity_output_is_not_the_input(self) -> None:
        for canary in ALL_CANARIES:
            assert canary not in CommandHasher().identity(canary)


class TestReduceErrorBody:
    FLOW_400 = {
        "error": {
            "code": 400,
            "message": f"Request contains {CANARY_PROMPT}.",
            "status": "INVALID_ARGUMENT",
            "details": [
                {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "OTHER"},
            ],
        },
    }

    def test_error_body_reduction_is_allowlist_only(self) -> None:
        """S29: numeric code, stable enum, booleans, counts, lengths — nothing raw."""
        out = reduce_error_body(self.FLOW_400)
        assert out.error_code == 400
        assert out.status_enum == "INVALID_ARGUMENT"
        assert out.has_error and out.has_message and out.has_status and out.has_details
        assert out.message_length == len(f"Request contains {CANARY_PROMPT}.")
        assert not _leaked(out)
        # The output is closed: exactly the allowlisted fields, all primitive.
        fields = asdict(out)
        assert set(fields) == {
            "error_code",
            "status_enum",
            "has_error",
            "has_message",
            "has_status",
            "has_details",
            "unknown_key_count",
            "message_length",
            "content_safety_signature",
        }
        assert all(isinstance(v, (int, bool, str, type(None))) for v in fields.values())

    def test_unknown_top_level_keys_reduce_to_count(self) -> None:
        """S02: a prompt/token used as a KEY must not be persisted either."""
        body = {
            f"prompt_{CANARY_PROMPT}": 1,
            CANARY_TOKEN: 2,
            "error": {"code": 500, "weird_key": 3},
        }
        out = reduce_error_body(body)
        assert out.unknown_key_count == 3  # two top-level + one nested unknown
        assert out.error_code == 500
        assert not _leaked(out)

    def test_content_safety_signature_boolean(self) -> None:
        body = {
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "details": [{"reason": "PUBLIC_ERROR_UNSAFE_GENERATION"}],
            },
        }
        assert reduce_error_body(body).content_safety_signature is True
        assert reduce_error_body(self.FLOW_400).content_safety_signature is False

    def test_non_dict_and_hostile_enum_degrade_safely(self) -> None:
        assert reduce_error_body(["not", "a", "dict"]).has_error is False
        # A status that is not enum-shaped must not be persisted as a string.
        out = reduce_error_body({"error": {"status": f"X {CANARY_EMAIL}"}})
        assert out.status_enum is None
        assert not _leaked(out)


def test_canary_secrets_never_survive_reduction() -> None:
    """S01 umbrella: every primitive, fed every canary, leaks nothing."""
    h = CommandHasher()
    results: list[object] = []
    for canary in ALL_CANARIES:
        results.append(sanitize_url(f"https://labs.google/{canary}?q={canary}", h))
        results.append(sanitize_url(f"https://{canary}.example/x", h))
        results.append(classify_title(canary))
        results.append(text_summary(canary, "console_warning"))
        results.append(reduce_error_body({"error": {"message": canary, canary: 1}}))
    for result in results:
        assert not _leaked(result)
