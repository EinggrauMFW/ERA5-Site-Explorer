import json

import app as appmodule


def test_the_page_links_the_brand_icons_and_every_manifest_icon_resolves():
    client = appmodule.app.test_client()
    html = client.get("/").get_data(as_text=True)
    for name in ("favicon.ico", "favicon.svg", "apple-touch-icon.png", "site.webmanifest"):
        assert f"brand/{name}" in html, name
    manifest = client.get("/static/brand/site.webmanifest").get_json()
    paths = [icon["src"] for icon in manifest["icons"]] + ["/static/brand/era5-black.svg"]  # the last is the header mask
    for path in paths:
        assert client.get(path).status_code == 200, path
