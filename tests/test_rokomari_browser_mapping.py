from pathlib import Path


def _source():
    return (Path(__file__).resolve().parents[1] / "models" / "website_scrapper" / "rokomary.py").read_text(encoding="utf-8")


def test_browser_extractor_excludes_author_and_publisher():
    source = _source()
    assert "exclude_fields={'authors', 'publishers'}" in source
    assert "def _apply_extractor(self, extractor, overwrite=False, exclude_fields=None):" in source


def test_browser_spec_mapping_does_not_map_author_or_publisher():
    source = _source()
    start = source.index("mapping = {", source.index("def _apply_browser_spec_values"))
    end = source.index("        }", start)
    block = source[start:end]
    assert "'author'" not in block
    assert "'publisher'" not in block
