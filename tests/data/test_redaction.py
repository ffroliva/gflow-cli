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
