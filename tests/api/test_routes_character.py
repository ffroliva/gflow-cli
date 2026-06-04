from gflow_cli.api import routes


def test_character_editor_url():
    url = routes.character_editor_url("pt", "pid-1", "eid-1")
    assert url.endswith("/fx/pt/tools/flow/project/pid-1/character/eid-1")
    assert "labs.google" in url


def test_character_editor_url_exact():
    # Verified live via Phase-2 spike T-A: the Flow UI lives under /fx/.
    url = routes.character_editor_url("en", "proj-abc", "entity-xyz")
    assert url == "https://labs.google/fx/en/tools/flow/project/proj-abc/character/entity-xyz"


def test_character_editor_url_various_locales():
    for locale in ("en", "pt", "de", "ja"):
        url = routes.character_editor_url(locale, "proj-1", "ent-1")
        assert url.startswith("https://labs.google/fx/")
        assert f"/fx/{locale}/tools/flow/project/proj-1/character/ent-1" in url


def test_character_editor_url_normalizes_bcp47_region():
    # #153: a full BCP-47 tag like "en-US" 404s the Flow editor page — only the
    # short primary subtag is a valid /fx/<seg>/ segment. The builder must reduce
    # the tag so callers can pass the genuine CLI default ("en-US") safely.
    assert routes.character_editor_url("en-US", "p", "e").endswith(
        "/fx/en/tools/flow/project/p/character/e"
    )
    assert "/fx/pt/tools/flow/" in routes.character_editor_url("pt-BR", "p", "e")


def test_character_editor_url_lowercases_segment():
    # Case-insensitive: "EN-us" -> "/fx/en/".
    assert "/fx/en/tools/flow/" in routes.character_editor_url("EN-us", "p", "e")


def test_character_editor_url_empty_locale_falls_back_to_en():
    # A degenerate/empty tag must not produce a double-slash URL (/fx//...).
    assert "/fx/en/tools/flow/" in routes.character_editor_url("", "p", "e")
    assert "/fx/en/tools/flow/" in routes.character_editor_url("-US", "p", "e")
