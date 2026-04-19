"""Tests for the legal-source classifier."""

from fandomforge.sources.legal_sources import classify_url


def test_pexels_is_allowlist():
    r = classify_url("https://www.pexels.com/video/12345/")
    assert r.tier == "allowlist"
    assert r.license_description and "Pexels" in r.license_description


def test_archive_org_is_allowlist():
    r = classify_url("https://archive.org/details/night_of_the_living_dead")
    assert r.tier == "allowlist"


def test_incompetech_is_allowlist():
    r = classify_url("https://incompetech.com/music/royalty-free/mp3-royaltyfree/Hitman.mp3")
    assert r.tier == "allowlist"


def test_youtube_requires_license_note():
    r = classify_url("https://www.youtube.com/watch?v=someTrailerId")
    assert r.tier == "requires_license_note"
    assert r.reason and "license_note" in r.reason


def test_netflix_is_denied():
    r = classify_url("https://www.netflix.com/title/80057281")
    assert r.tier == "denied"


def test_disney_plus_is_denied():
    r = classify_url("https://www.disneyplus.com/video/xyz")
    assert r.tier == "denied"


def test_unknown_domain_is_unknown():
    r = classify_url("https://example-unknown-domain-xyz.com/foo.mp4")
    assert r.tier == "unknown"
    assert r.reason and "not in any allowlist" in r.reason


def test_malformed_url_classifies_as_unknown():
    r = classify_url("not a url at all")
    assert r.tier == "unknown"


def test_subdomain_matches_parent():
    r = classify_url("https://beta.crunchyroll.com/watch/G/episode")
    assert r.tier == "denied"


def test_pixabay_allowlist():
    r = classify_url("https://pixabay.com/videos/xyz/")
    assert r.tier == "allowlist"
