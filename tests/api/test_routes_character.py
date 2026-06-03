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
