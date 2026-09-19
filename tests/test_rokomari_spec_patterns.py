"""Lightweight regression tests for the Rokomari specification rules.

These tests intentionally avoid importing Odoo. The same HTML patterns are
used by the extractor in production.
"""
import re
from bs4 import BeautifulSoup


def bn_to_ascii(value):
    return str(value).translate(str.maketrans('০১২৩৪৫৬৭৮৯', '0123456789'))


def number(value):
    match = re.search(r'\d+', bn_to_ascii(value).replace(',', ''))
    return int(match.group()) if match else None


def valid_pages(value):
    n = number(value)
    return n if n is not None and 1 <= n <= 20000 else None


def extract_pipe_specs(html):
    soup = BeautifulSoup(html, 'html.parser')
    values = {}
    labels = {'edition', 'number of pages', 'no of pages', 'পৃষ্ঠা', 'weight'}
    for node in soup.find_all(['div', 'p', 'li']):
        text = node.get_text(' ', strip=True)
        m = re.match(r'^(.+?)\s+\|\s+(.+)$', text)
        if not m:
            continue
        key = re.sub(r'[\s._/-]+', ' ', m.group(1).lower()).strip()
        if key in labels:
            values[key] = m.group(2).strip()
    return values


def extract_script_pages(html):
    soup = BeautifulSoup(html, 'html.parser')
    safe = None
    pattern = re.compile(r'["\']?(?P<key>numberOfPages|pageCount)["\']?\s*[:=]\s*["\']?(?P<value>[0-9০-৯]+)')
    for script in soup.find_all('script'):
        for m in pattern.finditer(script.get_text() or ''):
            safe = valid_pages(m.group('value'))
            if safe is not None:
                return safe
    return None


def extract_publication_date(edition):
    text = bn_to_ascii(edition)
    parts = [x.strip() for x in re.split(r'[,;|]', text) if x.strip()]
    for part in reversed(parts[1:]):
        if re.search(r'\b\d{4}\b', part):
            return part
    m = re.search(r'\b\d{4}\b', text)
    return m.group() if m else ''


def test_current_spec_layout():
    html = '''<div>Edition | 2nd Edition, 2010</div><div>Number of Pages | 287</div><div>Weight | 0.45 Kg</div>'''
    specs = extract_pipe_specs(html)
    assert specs['edition'] == '2nd Edition, 2010'
    assert valid_pages(specs['number of pages']) == 287
    assert extract_publication_date(specs['edition']) == '2010'


def test_bengali_page_validation():
    assert valid_pages('৬৪৮') == 648
    assert valid_pages('42621604') is None


def test_script_page_key_only():
    html = '''<script>var state = {"page": 42621604, "numberOfPages": 287};</script>'''
    assert extract_script_pages(html) == 287


def test_unrelated_page_key_does_not_count():
    html = '''<script>var state = {"page": 42621604};</script>'''
    assert extract_script_pages(html) is None


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            fn()
    print('Rokomari spec regression tests: PASS')


def test_current_rokomari_source_values_are_validated():
    assert valid_pages('96') == 96
    assert valid_pages('42,621,604') is None


def test_current_pipe_spec_rows():
    html = """<div>ISBN | 987984821129</div>
<div>Edition | 1st Published, 2018</div>
<div>Number of Pages | 96</div>
<div>Weight | 0.21 Kg</div>"""
    specs = extract_pipe_specs(html)
    assert specs['edition'] == '1st Published, 2018'
    assert valid_pages(specs['number of pages']) == 96


def test_rendered_rokomari_spec_pairs():
    from bs4 import BeautifulSoup
    from product_grabber.models.website_scrapper.rokomary import RokomariExtractor

    html = """
    <div class="spec-row"><span>Title</span><span>নবীদের কাহিনী ১</span></div>
    <div class="spec-row"><span>Category</span><span>নবি-রাসুল, সাহাবা, তাবেঈ ও অলি-আওলিয়া</span></div>
    <div class="spec-row"><span>Author</span><span>মুহাম্মদ আসাদুল্লাহ আল-গালিব</span></div>
    <div class="spec-row"><span>Edition</span><span>2nd Edition, 2010</span></div>
    <div class="spec-row"><span>ISBN</span><span>987984821129</span></div>
    <div class="spec-row"><span>No of Page</span><span>287</span></div>
    <div class="spec-row"><span>Language</span><span>বাংলা</span></div>
    <div class="spec-row"><span>Publisher</span><span>হাদীছ ফাউণ্ডেশন বাংলাদেশ</span></div>
    <div class="spec-row"><span>Country</span><span>বাংলাদেশ</span></div>
    <div class="spec-row"><span>Weight</span><span>0.45 Kg</span></div>
    """
    ex = RokomariExtractor(BeautifulSoup(html, 'html.parser'))
    specs = ex.get_specifications()
    assert specs['category'] == 'নবি-রাসুল, সাহাবা, তাবেঈ ও অলি-আওলিয়া'
    assert specs['edition'] == '2nd Edition, 2010'
    assert specs['publication_date'] == '2010'
    assert specs['isbn'] == '987984821129'
    assert specs['pages'] == '287'
    assert abs(specs['weight'] - 0.45) < 1e-9


def test_rokomari_specs_when_labels_and_values_are_on_separate_text_lines():
    from bs4 import BeautifulSoup
    from product_grabber.models.website_scrapper.rokomary import RokomariExtractor

    html = """
    <div class="spec-block">
      <div>Edition</div><div>2nd Edition, 2010</div>
      <div>No of Page</div><div>287</div>
      <div>ISBN</div><div>987984821129</div>
      <div>Weight</div><div>0.45 Kg</div>
    </div>
    """
    ex = RokomariExtractor(BeautifulSoup(html, 'html.parser'))
    specs = ex.get_specifications()
    assert specs['edition'] == '2nd Edition, 2010'
    assert specs['publication_date'] == '2010'
    assert specs['pages'] == '287'
    assert specs['isbn'] == '987984821129'
    assert abs(specs['weight'] - 0.45) < 1e-9


def test_rokomari_rejects_unrelated_large_page_number():
    from bs4 import BeautifulSoup
    from product_grabber.models.website_scrapper.rokomary import RokomariExtractor

    html = """
    <div>some unrelated page value 42621604</div>
    <div class="spec-row"><span>No of Page</span><span>287</span></div>
    """
    ex = RokomariExtractor(BeautifulSoup(html, 'html.parser'))
    specs = ex.get_specifications()
    assert specs['pages'] == '287'


def test_rokomari_red_label_mapping():
    from bs4 import BeautifulSoup
    from product_grabber.models.website_scrapper.rokomary import RokomariExtractor

    html = """
    <main>
      <div class="product-title"><h1>নবীদের কাহিনী ১</h1></div>
      <div class="category-line"><span>Category</span><span>নবি-রাসুল, সাহাবা, তাবেঈ ও অলি-আওলিয়া</span></div>
      <div class="spec-table">
        <div>Author</div><div>মুহাম্মদ আসাদুল্লাহ আল-গালিব</div>
        <div>Edition</div><div>2nd Edition, 2010</div>
        <div>ISBN</div><div>987984821129</div>
        <div>No of Page</div><div>287</div>
        <div>Language</div><div>বাংলা</div>
        <div>Publisher</div><div>হাদীছ ফাউণ্ডেশন বাংলাদেশ</div>
        <div>Country</div><div>বাংলাদেশ</div>
        <div>Weight</div><div>0.45 Kg</div>
      </div>
    </main>
    """
    ex = RokomariExtractor(BeautifulSoup(html, 'html.parser'))
    specs = ex.get_specifications()
    assert specs.get('category') == 'নবি-রাসুল, সাহাবা, তাবেঈ ও অলি-আওলিয়া'
    assert specs.get('edition') == '2nd Edition, 2010'
    assert specs.get('publication_date') == '2010'
    assert specs.get('isbn') == '987984821129'
    assert specs.get('pages') == '287'
    assert abs(specs.get('weight') - 0.45) < 1e-9


def test_flattened_current_rokomari_spec_block():
    from bs4 import BeautifulSoup
    from product_grabber.models.website_scrapper.rokomary import RokomariExtractor

    html = """
    <section>
      <h2>বইটির বিস্তারিত দেখুন</h2>
      <div class="spec-area">
        <div>Title | নবীদের কাহিনী ১</div>
        <div>Author | মুহাম্মদ আসাদুল্লাহ আল-গালিব</div>
        <div>Publisher | হাদীছ ফাউণ্ডেশন বাংলাদেশ</div>
        <div>ISBN | 987984821129</div>
        <div>Edition | 2nd Edition, 2010</div>
        <div>Number of Pages | 287</div>
        <div>Country | বাংলাদেশ</div>
        <div>Language | বাংলা</div>
      </div>
    </section>
    """
    ex = RokomariExtractor(BeautifulSoup(html, 'html.parser'))
    specs = ex.get_specifications()
    assert specs['isbn'] == '987984821129'
    assert specs['edition'] == '2nd Edition, 2010'
    assert specs['publication_date'] == '2010'
    assert specs['pages'] == '287'


def test_flattened_spec_block_with_nested_value_nodes():
    from bs4 import BeautifulSoup
    from product_grabber.models.website_scrapper.rokomary import RokomariExtractor

    html = """
    <div class="spec-area">
      <div><span>Edition</span><span>2nd Edition, </span><span>2010</span></div>
      <div><span>Number of Pages</span><span>287</span></div>
      <div><span>Weight</span><span>0.45</span><span> Kg</span></div>
      <div><span>ISBN</span><span>987984821129</span></div>
    </div>
    """
    ex = RokomariExtractor(BeautifulSoup(html, 'html.parser'))
    specs = ex.get_specifications()
    assert specs['edition'] == '2nd Edition, 2010'
    assert specs['publication_date'] == '2010'
    assert specs['pages'] == '287'
    assert abs(specs['weight'] - 0.45) < 1e-9
    assert specs['isbn'] == '987984821129'


def test_rokomari_normal_javascript_label_values():
    from bs4 import BeautifulSoup
    from product_grabber.models.website_scrapper.rokomary import RokomariExtractor

    html = '''
    <html><body>
      <script>
        window.__BOOK__ = {
          "ISBN": "987984821129",
          "Edition": "2nd Edition, 2010",
          "Number of Pages": 287,
          "Weight": "0.45 Kg",
          "Category": "নবি-রাসুল, সাহাবা, তাবেঈ ও অলি-আওলিয়া"
        };
      </script>
    </body></html>
    '''
    ex = RokomariExtractor(BeautifulSoup(html, 'html.parser'))
    specs = ex.get_specifications()
    assert specs['isbn'] == '987984821129'
    assert specs['edition'] == '2nd Edition, 2010'
    assert specs['publication_date'] == '2010'
    assert specs['pages'] == '287'
    assert abs(specs['weight'] - 0.45) < 1e-9
    assert specs['category'] == 'নবি-রাসুল, সাহাবা, তাবেঈ ও অলি-আওলিয়া'


def test_rokomari_browser_clicks_visible_specification_tab():
    from product_grabber.models.website_scrapper.rokomary import ImportProductFromRokomari

    class FakeElement:
        def __init__(self, text, displayed=True):
            self.text = text
            self.displayed = displayed
            self.clicked = False

        def is_displayed(self):
            return self.displayed

        def click(self):
            self.clicked = True

    class FakeDriver:
        def __init__(self):
            self.spec = FakeElement('Specification')
            self.other = FakeElement('Summary')

        def find_elements(self, _by, selector):
            if selector == 'button':
                return [self.other, self.spec]
            return []

        def execute_script(self, script, *args):
            if 'scrollIntoView' in script:
                return None
            return False

    driver = FakeDriver()
    assert ImportProductFromRokomari._click_specification_tab(driver) is True
    assert driver.spec.clicked is True


def test_rokomari_current_url_specification_contract():
    from bs4 import BeautifulSoup
    from product_grabber.models.website_scrapper.rokomary import RokomariExtractor

    html = """
    <button role="tab" aria-controls="spec-panel">Specification</button>
    <div id="spec-panel" role="tabpanel">
      <div>Title | হিন্দু আইন ও উত্তরাধিকার</div>
      <div>Author | পি. এম. সিরাজুল ইসলাম (সিরাজ প্রমানিক)</div>
      <div>Publisher | ইউনিক ল বুক হাউজ</div>
      <div>ISBN | 978984892825</div>
      <div>Edition | 1st Edition March 2022</div>
      <div>Number of Pages | 238</div>
      <div>Country | বাংলাদেশ</div>
      <div>Language | বাংলা</div>
      <div>Weight | 0.32 Kg</div>
    </div>
    """
    ex = RokomariExtractor(BeautifulSoup(html, 'html.parser'))
    specs = ex.get_specifications()
    assert specs['isbn'] == '978984892825'
    assert specs['edition'] == '1st Edition March 2022'
    assert specs['publication_date'] == '2022'
    assert specs['pages'] == '238'
    assert abs(specs['weight'] - 0.32) < 1e-9
    assert specs['country'] == 'বাংলাদেশ'
    assert specs['language'] == 'বাংলা'


def test_rokomari_visible_text_after_specification_tab():
    from bs4 import BeautifulSoup
    from product_grabber.models.website_scrapper.rokomary import RokomariExtractor

    visible_text = """
    Product Specification & Summary
    Title
    হিন্দু আইন ও উত্তরাধিকার
    Author
    পি. এম. সিরাজুল ইসলাম (সিরাজ প্রমানিক)
    Publisher
    ইউনিক ল বুক হাউজ
    ISBN
    978984892825
    Edition
    1st Edition March 2022
    Number of Pages
    238
    Country
    বাংলাদেশ
    Language
    বাংলা
    Weight
    0.32 Kg
    """
    ex = RokomariExtractor(BeautifulSoup('<html><body></body></html>', 'html.parser'))
    specs = ex._specs_from_rendered_text(visible_text)
    assert specs['isbn'] == '978984892825'
    assert specs['edition'] == '1st Edition March 2022'
    assert specs['publication_date'] == '2022'
    assert specs['pages'] == '238'
    assert abs(specs['weight'] - 0.32) < 1e-9


def test_rokomari_browser_visible_text_sequence():
    from bs4 import BeautifulSoup
    from product_grabber.models.website_scrapper.rokomary import RokomariExtractor

    text = """
    Product Specification & Summary
    ISBN | 978984892825
    Edition | 1st Edition March 2022
    Number of Pages | 238
    Country | বাংলাদেশ
    Language | বাংলা
    Weight | 0.32 Kg
    """
    ex = RokomariExtractor(BeautifulSoup('<html><body></body></html>', 'html.parser'))
    specs = ex._specs_from_rendered_text(text)
    assert specs['isbn'] == '978984892825'
    assert specs['edition'] == '1st Edition March 2022'
    assert specs['publication_date'] == '2022'
    assert specs['pages'] == '238'
    assert abs(specs['weight'] - 0.32) < 1e-9
