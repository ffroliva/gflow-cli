from gflow_cli.api import routes


def test_character_editor_url():
    url = routes.character_editor_url("pt", "pid-1", "eid-1")
    assert url.endswith("/pt/tools/flow/project/pid-1/character/eid-1")
    assert "labs.google" in url


def test_character_editor_url_exact():
    url = routes.character_editor_url("en", "proj-abc", "entity-xyz")
    assert url == "https://labs.google/en/tools/flow/project/proj-abc/character/entity-xyz"


def test_character_editor_url_various_locales():
    for locale in ("en", "pt", "de", "ja"):
        url = routes.character_editor_url(locale, "proj-1", "ent-1")
        assert url.startswith("https://labs.google/")
        assert f"/{locale}/tools/flow/project/proj-1/character/ent-1" in url
