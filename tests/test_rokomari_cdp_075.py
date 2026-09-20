from pathlib import Path

def _source():
    return (Path(__file__).resolve().parents[1] / "models" / "website_scrapper" / "rokomary.py").read_text(encoding="utf-8")

def test_cdp_fallback_present():
    source = _source()
    assert "def run_chromium_cdp(path):" in source
    assert "Runtime.evaluate" in source
    assert "remote-debugging-port=%s" in source

def test_cdp_is_used_when_driver_unavailable():
    source = _source()
    assert "cdp_result = run_chromium_cdp(bpath)" in source
