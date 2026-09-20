from pathlib import Path

def _source():
    return (Path(__file__).resolve().parents[1] / "models" / "website_scrapper" / "rokomary.py").read_text(encoding="utf-8")

def test_driverless_cdp_fallback_exists():
    source = _source()
    assert "def run_chromium_cdp(path):" in source
    assert "Runtime.evaluate" in source
    assert "remote-debugging-port=%s" in source

def test_weight_spec_label_is_explicit():
    source = _source()
    assert "'weight':'weight'" in source
    assert "const exactLabels=[];" in source
