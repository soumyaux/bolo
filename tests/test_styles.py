from bolo.styles import for_app, for_page

CFG = {
    "styles": {
        "apps": {"com.tinyspeck.slackmacgap": "casual", "Mail": "formal", "Odd": "shouty"},
        "sites": {"github.com": "prompt"},
        "titles": {"Gmail": "formal"},
    }
}


def test_by_bundle_id_then_by_name():
    assert for_app(CFG, "Slack", "com.tinyspeck.slackmacgap") == "casual"
    assert for_app(CFG, "mail", "com.apple.mail") == "formal"
    assert for_app(CFG, "Notes", "com.apple.Notes") is None


def test_an_unknown_style_falls_back_to_default():
    assert for_app(CFG, "Odd", "") == "default"


def test_pages_by_site_and_subdomain_then_title():
    assert for_page(CFG, "https://gist.github.com/x", "") == "prompt"
    assert for_page(CFG, "https://example.com", "Gmail") is None
    assert for_page(CFG, "", "Inbox - Gmail") == "formal"
