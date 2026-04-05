from odoo import models, fields, api
from bs4 import BeautifulSoup
from odoo.exceptions import UserError
import requests
import re
import json
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

_logger = logging.getLogger(__name__)


class ImportProductFromWafilife(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import Wafilife product from website'

    def wafilife_products(self):
        url = self.source_url
        if not url or 'wafilife.com' not in url:
            raise UserError("Invalid Wafilife URL. Expected: https://www.wafilife.com/<slug>/pd/<id>")

        session = self._build_session()

        try:
            response = session.get(url, timeout=20)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            extractor = WafilifExtractor(soup)

            self.product_name          = extractor.get_title()
            self.authors               = extractor.get_authors()
            self.publishers            = extractor.get_publisher()
            self.price                 = extractor.get_current_price()
            self.face_value            = extractor.get_original_price()
            self.stock_qty             = extractor.get_stock_quantity()
            self.ecommerce_description = extractor.get_description()
            self.image_url             = extractor.get_image_url()
            self.pages                 = extractor.get_pages()
            self.editions              = extractor.get_edition()
            self.language              = extractor.get_language()
            self.isbn                  = extractor.get_isbn()
            self.country               = extractor.get_country()
            self.weight                = extractor.get_weight()

            _logger.info(f"✓ Successfully scraped from Wafilife: {self.product_name}")
            return True

        except Exception as e:
            _logger.exception(f"Error scraping {url}: {e}")
            self.product_name = getattr(self, 'product_name', 'Unknown Product')
            self.price        = getattr(self, 'price', 0.0)
            self.stock_qty    = getattr(self, 'stock_qty', 0)
            return False

    def _build_session(self):
        session = requests.Session()
        retry = Retry(total=3, backoff_factor=1.5, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'bn-BD,bn;q=0.9,en-US;q=0.8,en;q=0.7',
        })
        return session


class WafilifExtractor:
    """
    Extracts product data from Wafilife.com (Next.js SSR — no Selenium needed).
    URL pattern: https://www.wafilife.com/<slug>/pd/<numeric-id>

    PRIMARY source: application/ld+json block which contains:
        name        → product title
        description → full HTML description
        image       → list of CDN image URLs
        sku / mpn   → product ID
        brand.name  → publisher

    SECONDARY source: DOM elements for price, author, pages, edition, language.

    JSON-LD structure confirmed:
    {
        "@type": "Product",
        "name": "দ্য রুলস অব ওয়েলথ",
        "description": "<p>...</p>",
        "image": ["https://wafilife-media.wafilife.com/.../rules-of-welth.png"],
        "sku": "75690",
        "mpn": "75690",
        "brand": {"@type": "Brand", "name": "চর্চা গ্রন্থ প্রকাশ"},
        ...
    }
    """

    def __init__(self, soup: BeautifulSoup):
        self.soup = soup
        self._ld = self._parse_jsonld()
        self._page_text = soup.get_text(separator='\n')
        self._meta_line = self._find_meta_line()

    # ------------------------------------------------------------------ #
    #  JSON-LD parser
    # ------------------------------------------------------------------ #

    def _parse_jsonld(self) -> dict:
        """
        Finds and parses the application/ld+json script block.
        Returns the Product object dict, or {} if not found.
        """
        for script in self.soup.find_all('script', {'type': 'application/ld+json'}):
            if not script.string:
                continue
            try:
                # The JSON may be truncated in the page — ensure we handle it
                raw = script.string.strip()
                # Wafilife's JSON-LD is sometimes not closed — try to fix
                if not raw.endswith('}'):
                    raw = raw + '}'
                data = json.loads(raw)
                if isinstance(data, list):
                    # Find the Product entry
                    for item in data:
                        if isinstance(item, dict) and item.get('@type') == 'Product':
                            return item
                elif isinstance(data, dict):
                    if data.get('@type') == 'Product':
                        return data
            except (json.JSONDecodeError, AttributeError):
                continue
        return {}

    def _ld_get(self, *keys, default=''):
        """Safe nested getter for JSON-LD dict."""
        obj = self._ld
        for key in keys:
            if not isinstance(obj, dict):
                return default
            obj = obj.get(key, default)
        return obj if obj != '' else default

    # ------------------------------------------------------------------ #
    #  Core fields — JSON-LD primary
    # ------------------------------------------------------------------ #

    def get_title(self) -> str:
        # 1. JSON-LD name
        title = self._ld_get('name')
        if title:
            return str(title).strip()
        # 2. h1
        h1 = self.soup.find('h1')
        if h1:
            return h1.get_text(strip=True)
        # 3. og:title
        og = self.soup.find('meta', {'property': 'og:title'})
        if og and og.get('content'):
            return og['content'].split('|')[0].strip()
        return 'Unknown Product'

    def get_publisher(self) -> str:
        # 1. JSON-LD brand.name
        brand = self._ld_get('brand', 'name')
        if brand:
            return str(brand).strip()
        # 2. DOM: "প্রকাশনী :" label → <a> text
        pubs = self._extract_label_links('প্রকাশনী')
        return ', '.join(pubs) if pubs else ''

    def get_description(self) -> str:
        # 1. JSON-LD description — contains full HTML
        desc = self._ld_get('description')
        if desc:
            return str(desc).strip()
        # 2. og:description
        og = self.soup.find('meta', {'property': 'og:description'})
        if og and og.get('content'):
            return og['content'].strip()
        return ''

    def get_image_url(self) -> str:
        # 1. JSON-LD image list — first entry is the full-size image
        images = self._ld_get('image')
        if images:
            if isinstance(images, list) and images:
                return str(images[0]).strip()
            if isinstance(images, str):
                return images.strip()
        # 2. img alt="thumbnail"
        img = self.soup.find('img', {'alt': 'thumbnail'})
        if img and img.get('src'):
            return img['src'].strip()
        # 3. og:image
        og = self.soup.find('meta', {'property': 'og:image'})
        if og and og.get('content'):
            return og['content'].strip()
        return ''

    def get_isbn(self) -> str:
        # JSON-LD sku/mpn are the internal product ID, not ISBN
        # Search page text for actual ISBN pattern
        m = re.search(r'ISBN\s*[:\-]?\s*([\dXx\-]{9,17})', self._page_text, re.I)
        if m:
            return m.group(1).strip()
        return self._spec_from_table('isbn')

    # ------------------------------------------------------------------ #
    #  Fields from DOM (not in JSON-LD)
    # ------------------------------------------------------------------ #

    def get_authors(self) -> str:
        # DOM only: "লেখক :" label → <a> text
        authors = self._extract_label_value('লেখক')
        return authors if authors else ''

    def get_current_price(self) -> float:
        """
        Sell price is plain text e.g. "৪২০৳".
        Strategy: clone soup, strip <del>/<s>, find first price > 10.
        """
        soup_copy = BeautifulSoup(str(self.soup), 'html.parser')
        for tag in soup_copy.find_all(['del', 's']):
            tag.decompose()
        prices = re.findall(r'([\d,]+(?:\.\d+)?)\s*৳', soup_copy.get_text())
        for p in prices:
            val = self._parse_price(p)
            if val > 10:
                return val
        # JSON-LD offers fallback
        offers = self._ld_get('offers')
        if isinstance(offers, dict):
            price = offers.get('price') or offers.get('lowPrice')
            if price:
                return self._parse_price(str(price))
        return 0.0

    def get_original_price(self) -> float:
        """MRP is inside <del> or <s> tags."""
        for tag in self.soup.find_all(['del', 's']):
            price = self._parse_price(tag.get_text(strip=True))
            if price > 0:
                return price
        # JSON-LD highPrice fallback
        offers = self._ld_get('offers')
        if isinstance(offers, dict):
            high = offers.get('highPrice')
            if high:
                return self._parse_price(str(high))
        return 0.0

    def get_stock_quantity(self) -> int:
        # JSON-LD offers availability
        offers = self._ld_get('offers')
        if isinstance(offers, dict):
            avail = offers.get('availability', '')
            if 'InStock' in avail:
                return 1
            if 'OutOfStock' in avail or 'Discontinued' in avail:
                return 0

        text = self._page_text
        if re.search(r'out[\s-]?of[\s-]?stock|স্টক\s*নেই|অনুপলব্ধ', text, re.I):
            return 0
        if re.search(r'প্রি-অর্ডার|pre-?order', text, re.I):
            return 1
        if re.search(r'in[\s-]?stock|অর্ডার\s*করুন', text, re.I):
            return 1
        return 0

    def get_pages(self) -> str:
        m = re.search(r'পৃষ্ঠা\s*:\s*([\d]+)', self._meta_line)
        if m:
            return m.group(1)
        return self._spec_from_table('page', 'পৃষ্ঠা')

    def get_edition(self) -> str:
        m = re.search(r'সংস্করণ\s*:\s*([^,\n]+)', self._meta_line)
        if m:
            return m.group(1).strip()
        return self._spec_from_table('edition', 'সংস্করণ')

    def get_language(self) -> str:
        m = re.search(r'ভাষা\s*:\s*([^\n,]+)', self._meta_line)
        if m:
            return m.group(1).strip()
        return self._spec_from_table('language', 'ভাষা')

    def get_country(self) -> str:
        m = re.search(r'দেশ\s*:\s*([^\n,]+)', self._meta_line)
        if m:
            return m.group(1).strip()
        return self._spec_from_table('country', 'দেশ')

    def get_weight(self) -> float:
        m = re.search(r'ওজন\s*:\s*([\d.]+)', self._meta_line)
        if m:
            return float(m.group(1))
        val = self._spec_from_table('weight', 'ওজন')
        if val:
            n = re.search(r'[\d.]+', val)
            return float(n.group()) if n else 0.0
        return 0.0

    # ------------------------------------------------------------------ #
    #  Private helpers
    # ------------------------------------------------------------------ #

    def _find_meta_line(self) -> str:
        """Locates the text block containing পৃষ্ঠা/সংস্করণ/ভাষা."""
        for elem in self.soup.find_all(string=re.compile('পৃষ্ঠা|সংস্করণ|ভাষা')):
            parent = elem.parent
            if parent:
                return parent.get_text(separator=' ')
        return self._page_text

    def _extract_label_links(self, label: str) -> list:
        """
        Finds elements containing `label :` and returns all <a> link texts nearby.
        Handles: লেখক, প্রকাশনী, বিষয়
        """
        results = []
        for elem in self.soup.find_all(string=re.compile(re.escape(label))):
            parent = elem.parent
            if parent:
                container = parent if parent.find('a') else parent.parent
                if container:
                    for link in container.find_all('a'):
                        text = link.get_text(strip=True)
                        if text and text not in results:
                            results.append(text)
                if results:
                    break
        return results

    def _extract_label_value(self, label: str) -> str:
        """
        Finds the div inside div.text-base.text-brand-two whose span.font-medium
        contains `label`, then returns the text of the sibling span.text-brand > a.

        Structure:
            <div>
                <span class="font-medium">লেখক :</span>
                <span class="text-brand"><a href="...">রবার্ট টি. কিয়োসাকি</a></span>
            </div>
        """
        container = self.soup.find('div', class_=lambda c: c and 'text-base' in c and 'text-brand-two' in c)
        if not container:
            return ''

        for div in container.find_all('div', recursive=False):
            label_span = div.find('span', class_='font-medium')
            if label_span and label in label_span.get_text():
                value_span = div.find('span', class_='text-brand')
                if value_span:
                    # Collect all <a> texts (handles multiple authors)
                    links = value_span.find_all('a')
                    if links:
                        return ', '.join(a.get_text(strip=True) for a in links)
                    # Plain text fallback (no <a> tag)
                    return value_span.get_text(strip=True)
        return ''

    def _spec_from_table(self, *keywords) -> str:
        """Fallback: search <table> rows for matching keyword."""
        for row in self.soup.select('table tr'):
            cells = row.find_all('td')
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True).lower()
                if any(kw.lower() in key for kw in keywords):
                    return cells[1].get_text(strip=True)
        return ''

    def _parse_price(self, text: str) -> float:
        if not text:
            return 0.0
        cleaned = str(text).replace('৳', '').replace('Tk', '').replace('TK', '').replace(',', '').strip()
        m = re.search(r'[\d.]+', cleaned)
        return float(m.group()) if m else 0.0