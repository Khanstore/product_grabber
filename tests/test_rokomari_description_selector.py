import unittest

from bs4 import BeautifulSoup


class TestRokomariDescriptionSelector(unittest.TestCase):
    def test_exact_current_summary_class(self):
        html = '''<div class="productSummary_summeryText__Pd_tX">Current visible summary</div>
<div id="js--summary-description">Wrong fallback</div>'''
        soup = BeautifulSoup(html, "html.parser")
        elem = soup.select_one("div.productSummary_summeryText__Pd_tX")
        self.assertEqual(elem.get_text(" ", strip=True), "Current visible summary")

    def test_generated_css_module_suffix(self):
        html = '''<div class="productSummary_summeryText__AbC123">Current visible summary</div>'''
        soup = BeautifulSoup(html, "html.parser")
        elem = soup.select_one('div[class*="productSummary_summeryText__"]')
        self.assertEqual(elem.get_text(" ", strip=True), "Current visible summary")


if __name__ == "__main__":
    unittest.main()
