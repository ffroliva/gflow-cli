from gflow_cli.data.redaction import prompt_fields, redact_error_detail, redact_metadata


def test_prompt_fields_store_mode_stores_text_and_hash() -> None:
    fields = prompt_fields("hello", mode="store")
    assert fields.prompt == "hello"
    assert fields.prompt_hash is not None
    assert fields.prompt_redacted is False


def test_prompt_fields_redacted_mode_stores_hash_only() -> None:
    fields = prompt_fields("hello", mode="redacted")
    assert fields.prompt is None
    assert fields.prompt_hash is not None
    assert fields.prompt_redacted is True


def test_redact_metadata_removes_signed_urls_and_tokens() -> None:
    raw = {
        "fifeUrl": "https://flow-content.google/path?Signature=abc",
        "publicUrl": "https://example.com/public.png",
        "clientContext": {"recaptchaContext": {"token": "secret"}},
        "nested": [{"authorization": "Bearer abc"}],
        "safe": "kept",
    }
    redacted = redact_metadata(raw)
    assert redacted["fifeUrl"] == "<redacted:url>"
    assert redacted["publicUrl"] == "https://example.com/public.png"
    assert redacted["clientContext"]["recaptchaContext"]["token"] == "<redacted:token>"
    assert redacted["nested"][0]["authorization"] == "<redacted:secret>"
    assert redacted["safe"] == "kept"


def test_redact_metadata_masks_snake_case_fife_url() -> None:
    """The recorder stores snake_case ``fife_url`` (recorder.py) — the key set
    only covered the camelCase spelling, so the stored value leaked (#542)."""
    raw = {"fife_url": "https://lh3.googleusercontent.com/fife/abc"}
    assert redact_metadata(raw)["fife_url"] == "<redacted:url>"


def test_redact_metadata_drops_a_signed_url_under_any_key() -> None:
    raw = {"note": "https://flow-content.google/x?Expires=1&Signature=abc"}
    assert redact_metadata(raw)["note"] == "<redacted:url>"


def test_session_id_is_redacted() -> None:
    """The extend request body carries `clientContext.sessionId`. It is not a
    credential, but it is account-correlatable and appears in any body we log
    or persist, so it must not survive into a diagnostics bundle."""
    body = {
        "clientContext": {
            "projectId": "7d3d6bd9-a39f-4c2d-b772-146e73e539cf",
            "sessionId": ";1788200574949",
            "recaptchaContext": {"token": "03AFcW"},
        }
    }
    out = redact_metadata(body)
    ctx = out["clientContext"]
    assert ctx["sessionId"] == "<redacted:token>"
    assert ctx["recaptchaContext"]["token"] == "<redacted:token>"
    # The project id is a plain resource identifier and stays readable — it is
    # what makes a bundle diagnosable at all.
    assert ctx["projectId"] == "7d3d6bd9-a39f-4c2d-b772-146e73e539cf"


class TestEmailRedactionScope:
    """The `<redacted:email>` pattern must scrub addresses without eating paths.

    Added with the account-chooser feature so chooser errors do not persist an
    operator's address, and shipped with no test. It runs on EVERY persisted
    error detail (`data/recorder.py`), every transport response snippet
    (`transports/_common.py`, `transports/batchexecute.py`) and both worker paths
    (`worker/codec.py`, `worker/daemon.py`) — so an over-match silently rewrites
    the one artifact left for debugging a failure, with nothing to distinguish a
    redaction from the original text.
    """

    def test_a_real_address_is_scrubbed(self) -> None:
        assert redact_error_detail("contact me@example.com now") == ("contact <redacted:email> now")

    def test_a_filesystem_path_is_not_an_address(self) -> None:
        # Verified failing before the fix: '<redacted:email>' replaced 'x@y.co'.
        assert redact_error_detail("file at C:/x@y.co/path") == "file at C:/x@y.co/path"

    def test_a_posix_path_is_not_an_address(self) -> None:
        assert redact_error_detail("read /var/x@y.io/cache") == "read /var/x@y.io/cache"


def test_wire_read_secrets_are_redacted() -> None:
    """#1229: the cookies and form fields the direct credits read carries (fake values)."""
    detail = (
        "cookie: __Secure-1PAPISID=fake1papisid; __Secure-3PAPISID=fake3papisid; "
        "SIDCC=fakesidcc; __Secure-1PSIDCC=fake1psidcc; NID=fakenid; AEC=fakeaec "
        "body f.req=%5B%5D&at=AFAKEAT%3A1700000000000&f.sid=-123456789&bl=boq_x"
    )

    out = redact_error_detail(detail)

    for secret in (
        "fake1papisid",
        "fake3papisid",
        "fakesidcc",
        "fake1psidcc",
        "fakenid",
        "fakeaec",
        "AFAKEAT",
        "-123456789",
    ):
        assert secret not in out
    assert "bl=boq_x" in out  # a build label is not a secret


def test_ordinary_words_ending_in_at_are_not_redacted() -> None:
    assert redact_error_detail("format=json data=ok") == "format=json data=ok"
    assert redact_error_detail("chat=1 at=AFAKE") == "chat=1 at=<redacted:secret>"


class TestBatchexecuteSecrets:
    """flow.google.com secrets the free-text scrub must catch (character-portability Task 1).

    Shapes measured 2026-10-16 (docs/superpowers/spikes/
    2026-10-16-migrated-character-lifecycle-wire.md): ``maseQ`` carries a reCAPTCHA
    Enterprise token (``0cAF…``, 1 000+ chars); every batchexecute POST carries the
    ``at`` XSRF value, 28 token chars + ``:`` + a 13-digit ms timestamp, URL-encoded
    in the form body; ``Zzl0ze`` media rows carry ``lh3.googleusercontent.com/asb/…``
    links that grant access by path alone, with no ``Signature=`` to key on.
    """

    _RECAPTCHA = "0cAFcWeA" + "x1Y_-z" * 60
    _AT = "AFfakeXSRF1fake-2fake34fakefake:1000000000000"  # obviously fake

    def test_a_recaptcha_enterprise_token_is_scrubbed(self) -> None:
        out = redact_error_detail(f'maseQ body [null,22,[\\"{self._RECAPTCHA}\\",1]]')
        assert "0cAF" not in out
        assert "<redacted:token>" in out

    def test_the_at_xsrf_value_is_scrubbed_plain_and_url_encoded(self) -> None:
        encoded = self._AT.replace(":", "%3A")
        # Bare tokens (e.g. in JSON/headers/prose) without at= prefix scrub to <redacted:token>
        out = redact_error_detail(f"token {encoded} and {self._AT}")
        assert "1000000000000" not in out
        assert "AFfakeXSRF" not in out
        assert out.count("<redacted:token>") == 2

        # Form body with at= prefix is also scrubbed (by develop's #1229 form-field rule)
        out_form = redact_error_detail(f"f.req=...&at={encoded}& and at={self._AT}")
        assert "1000000000000" not in out_form
        assert "AFfakeXSRF" not in out_form

    def test_an_asb_capability_link_is_scrubbed(self) -> None:
        url = "https://lh3.googleusercontent.com/asb/ANfakeASBlink0000-fake_0000"
        out = redact_error_detail(f"download {url}=s0 failed")
        assert "ANfakeASB" not in out
        assert out == "download <redacted:url> failed"

    def test_a_flow_media_id_survives(self) -> None:
        """Review of slice 1: a generic "120+ token chars" rule erased Flow media ids
        (162 chars, the token alphabet), and the billed-but-not-downloaded message names
        exactly that id for `gflow_download_media`. Only credential SHAPES are scrubbed."""
        media_id = "CAUSJGI" + "x2Y_-z" * 26  # 163 chars, fake
        text = f"Flow took segment 2 ({media_id}), so it may have billed"
        assert redact_error_detail(text) == text

    def test_a_long_bearer_token_is_redacted_whole(self) -> None:
        # Review of slice 1: a token rule placed before the Bearer rule replaced the
        # first run, and the Bearer rule could no longer reach the tail after the ".".
        out = redact_error_detail("Bearer " + "A" * 150 + "." + "B" * 80)
        assert out == "<redacted:secret>"

    def test_an_address_with_a_long_local_part_is_redacted_whole(self) -> None:
        out = redact_error_detail("user." + "n" * 130 + "@example.com failed")
        assert out == "<redacted:email> failed"

    def test_an_asb_link_inside_compact_json_keeps_the_next_field(self) -> None:
        url = "https://lh3.googleusercontent.com/asb/ANfakeASBlink0000-fake_0000"
        out = redact_error_detail(f'["{url}",null,"rpc rzMKMb"]')
        assert out == '["<redacted:url>",null,"rpc rzMKMb"]'

    def test_the_scrub_stays_linear_on_a_long_token_run(self) -> None:
        """Found by measuring, before review: the first `at` pattern let every position
        inside a token run re-scan the run looking for its ':' — 2k chars 36 ms, 4k 139 ms,
        8k 610 ms, a clean 4x per doubling, so a 160 KB body took minutes. This scrub
        runs on every persisted error detail (same failure class as #852). The bound is
        far above the linear cost and far below the old curve: a regression fails loudly
        without making this a stopwatch race."""
        import time

        for blob in (
            "A" * 220_000,
            "abcDEF012_-" * 20_000,
            "a" * 160_000,
            "a-" * 80_000,  # the URL-credential scheme scan (review of slice 1)
            "x@" + "a." * 80_000,  # the address domain scan (review of slice 1)
        ):
            started = time.perf_counter()
            redact_error_detail(blob)
            assert time.perf_counter() - started < 1.0

    def test_the_signed_query_scrub_stays_linear_on_a_long_url_like_run(self) -> None:
        """Council D3 (branch review 2026-10-16): the signed-query pattern was unanchored,
        so on one long run with no whitespace every start re-scanned the run — 2.5k chars
        0.10 s, 5k 0.47 s, 10k 1.88 s. URL-like text ('/', '.', '?') is not collapsed by
        the token-run rule first, and discovery_head now scrubs whole replies."""
        import time

        blob = "a/b.c?d=" * 20_000  # 160 000 chars, no whitespace, no signature marker
        started = time.perf_counter()
        redact_error_detail(blob)
        assert time.perf_counter() - started < 1.0

    def test_a_signed_url_is_still_dropped_whole(self) -> None:
        url = "https://flow-content.google/image/x?Expires=1&Signature=abc"
        assert redact_error_detail(f"get {url} failed") == "get <redacted:url> failed"

    def test_ordinary_ids_and_timestamps_survive(self) -> None:
        text = (
            "entity 1035317b-6c80-466f-b4f4-f568900fcd8d at [1792170598, 989086000] "
            "rpcid=rzMKMb status=200"
        )
        assert redact_error_detail(text) == text

    def test_near_miss_uuid_survives(self) -> None:
        uuid_text = "project id: 1035317b-6c80-466f-b4f4-f568900fcd8d"
        assert redact_error_detail(uuid_text) == uuid_text

    def test_near_miss_media_and_workflow_ids_survive(self) -> None:
        ids_text = "media_id=media-12345678_abcdef workflow_id=wf-98765432_fedcba"
        assert redact_error_detail(ids_text) == ids_text

    def test_near_miss_file_paths_survive(self) -> None:
        path_text = r"file at C:\dev\projects\data\image_01.png and /var/log/app.log"
        assert redact_error_detail(path_text) == path_text

    def test_near_miss_short_base64_survives(self) -> None:
        # Base64 string under 120 chars must not be scrubbed as a credential
        b64 = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWYwMTIzNDU2Nzg5YWJjZGVm"  # 64 chars
        text = f"encoded data: {b64} checksum"
        assert redact_error_detail(text) == text

    def test_near_miss_short_recaptcha_prefix_survives(self) -> None:
        # 0cAF prefix but under 100 characters must not trigger the 0cAF token rule
        short_recaptcha = "0cAF" + "x" * 50
        text = f"identifier {short_recaptcha} in payload"
        assert redact_error_detail(text) == text

    def test_near_miss_at_token_without_timestamp_survives(self) -> None:
        # 28 chars but no 13-digit timestamp after colon
        token_no_ts = "AFfakeXSRF1fake-2fake34fakefake:12345"
        text = f"reference {token_no_ts} in response"
        assert redact_error_detail(text) == text
