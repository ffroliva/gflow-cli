from gflow_cli.data.redaction import prompt_fields, redact_metadata


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
