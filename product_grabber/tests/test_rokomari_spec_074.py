from pathlib import Path


def _source():
    return (Path(__file__).resolve().parents[1] / "models" / "website_scrapper" / "rokomary.py").read_text(encoding="utf-8")


def test_driver_free_dump_dom_fallback_exists():
    source = _source()
    assert "def run_chromium_dump_dom(path):" in source
    assert "--dump-dom" in source


def test_browser_extractor_has_exact_label_row_fallback():
    source = _source()
    assert "const exactLabels=[];" in source
    assert "const k=canonExact(allTextLines[i]);" in source
