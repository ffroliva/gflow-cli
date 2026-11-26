"""Render docs/assets/social/card.html to the two PNG sizes the project publishes.

social-preview.png (1280x640) is uploaded by hand in repo Settings; og-image.png
(1200x630) is served from the site root for og:image / twitter:image.
Fonts are the host's system fonts, so render on one machine and review the PNGs.
Run: uv run python scripts/dev/render_social_card.py
"""

from pathlib import Path

from playwright.sync_api import sync_playwright

ASSETS = Path(__file__).resolve().parents[2] / "docs" / "assets" / "social"
SIZES = {"social-preview.png": (1280, 640), "og-image.png": (1200, 630)}

with sync_playwright() as p:
    browser = p.chromium.launch()
    for name, (w, h) in SIZES.items():
        page = browser.new_page(viewport={"width": w, "height": h})
        page.goto((ASSETS / "card.html").as_uri())
        page.screenshot(path=str(ASSETS / name))
        page.close()
    browser.close()
