from odoo import models, fields, api
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
import json, logging, re, time
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from odoo.exceptions import UserError


class importProductFromBoibari(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import Boibari product from website'

    def boibari_products(self):
        url = self.source_url
        if not url or 'boibari.com' not in url:
            raise ValueError("Invalid Boibari URL")

        # ── 1. Try requests first (fast path) ──────────────────────────────
        soup = self._fetch_with_requests(url)

        # ── 2. Fall back to Selenium if the page is JS-rendered ────────────
        if soup is None or not self._has_product_data(soup):
            logging.info("Boibari: requests returned empty page – switching to Selenium")
            soup = self._fetch_with_selenium(url)

        if soup is None:
            logging.error(f"Boibari: could not fetch {url}")
            return False

        extractor = BoibariExtractor(soup)

        self.face_value  = extractor.get_original_price()
        self.price       = extractor.get_current_price()
        self.stock_qty   = extractor.get_stock_quantity()
        self.ecommerce_description = extractor.get_description()
        self.image_url   = extractor.get_image_url()

        specs = extractor.get_specifications()
        self.product_name = specs.get('title') or extractor.get_title() or ''
        self.isbn         = specs.get('isbn', '')
        self.authors      = specs.get('author', '')
        self.publishers   = specs.get('publisher', '')
        self.pages        = specs.get('pages', '')
        self.editions     = specs.get('edition', '')
        self.language     = specs.get('language', '')
        self.country      = specs.get('country', '')
        self.weight       = specs.get('weight', 0.0)

        logging.info(f"✓ Boibari scraped: {self.product_name}")
        return True

    # ── helpers ────────────────────────────────────────────────────────────

    def _fetch_with_requests(self, url):
        session = requests.Session()
        retry = Retry(total=3, backoff_factor=1,
                      status_forcelist=[429, 500, 502, 503, 504])
        session.mount('http://', HTTPAdapter(max_retries=retry))
        session.mount('https://', HTTPAdapter(max_retries=retry))
        session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9,bn;q=0.8',
            'Referer': 'https://boibari.com/',
        })
        try:
            session.get('https://boibari.com/', timeout=10)  # warm-up / cookie
            r = session.get(url, timeout=15)
            r.raise_for_status()
            return BeautifulSoup(r.text, 'html.parser')
        except Exception as e:
            logging.warning(f"Boibari requests fetch failed: {e}")
            return None

    def _fetch_with_selenium(self, url):
        options = webdriver.ChromeOptions()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-blink-features=AutomationControlled')
        options.add_experimental_option('excludeSwitches', ['enable-automation'])
        try:
            driver = webdriver.Chrome(options=options)
            driver.get(url)
            # Wait for the price element (most reliable signal that the page loaded)
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR,
                     '.product-price, .sell-price, [class*="price"], h1')
                )
            )
            time.sleep(1)  # let any async renders finish
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            driver.quit()
            return soup
        except Exception as e:
            logging.error(f"Boibari Selenium fetch failed: {e}")
            try:
                driver.quit()
            except Exception:
                pass
            return None

    @staticmethod
    def _has_product_data(soup):
        """Return True if the soup contains meaningful product content."""
        return bool(
            soup.find('h1') or
            soup.find(class_=lambda c: c and 'price' in c.lower())
        )


# ── Extractor ──────────────────────────────────────────────────────────────

class BoibariExtractor:
    """
    Extract product data from a boibari.com product page.

    Boibari uses a Laravel-based stack.  The selectors below are ordered
    from most-specific to broadest fallback so the extractor stays robust
    even if Boibari changes minor class names.
    """

    def __init__(self, soup: BeautifulSoup):
        self.soup = soup

    # ── title ──────────────────────────────────────────────────────────────

    def get_title(self):
        selectors = [
            ('h1', {'class': lambda c: c and 'product' in ' '.join(c).lower()}),
            ('h1', {'class': lambda c: c and 'title'   in ' '.join(c).lower()}),
            ('h1', {'itemprop': 'name'}),
            ('h1', {}),                          # any h1
        ]
        return self._extract_text(selectors, '')

    # ── price ──────────────────────────────────────────────────────────────

    def get_current_price(self):
        """Discounted / selling price."""
        # Try common class names used by Boibari / similar BD book sites
        candidates = [
            'sell-price', 'selling-price', 'current-price',
            'product-price', 'offer-price', 'price-new',
        ]
        for cls in candidates:
            el = self.soup.find(class_=cls)
            if el:
                return self._parse_price(el.get_text(strip=True))

        # Broader: first element whose class contains "price" and has ৳ / Tk
        for el in self.soup.find_all(class_=lambda c: c and 'price' in ' '.join(c if isinstance(c, list) else [c]).lower()):
            txt = el.get_text(strip=True)
            if re.search(r'[৳Tt]', txt):
                p = self._parse_price(txt)
                if p:
                    return p

        # LD+JSON fallback
        data = self._ld_json()
        if data:
            offers = data.get('offers', {})
            price  = offers.get('price') or offers.get('lowPrice')
            if price:
                return float(price)
        return 0.0

    def get_original_price(self):
        """MRP / face value before discount."""
        candidates = [
            'original-price', 'regular-price', 'mrp',
            'price-old', 'old-price', 'list-price',
        ]
        for cls in candidates:
            el = self.soup.find(class_=cls)
            if el:
                return self._parse_price(el.get_text(strip=True))

        # del / s tag often wraps the crossed-out price
        for tag in ('del', 's', 'strike'):
            el = self.soup.find(tag)
            if el:
                p = self._parse_price(el.get_text(strip=True))
                if p:
                    return p
        return 0.0

    # ── stock ──────────────────────────────────────────────────────────────

    def get_stock_quantity(self):
        # Boibari often shows stock inline or in a span/div
        stock_candidates = [
            ('span', {'id': 'available-quantity'}),
            ('span', {'id': 'stock-quantity'}),
            ('div',  {'class': lambda c: c and 'stock' in ' '.join(c if isinstance(c, list) else [c]).lower()}),
            ('span', {'class': lambda c: c and 'stock' in ' '.join(c if isinstance(c, list) else [c]).lower()}),
        ]
        for tag, attrs in stock_candidates:
            el = self.soup.find(tag, attrs)
            if el:
                txt = el.get_text(strip=True)
                m = re.search(r'\d+', txt)
                if m:
                    return int(m.group())

        # LD+JSON: availability hint → map to a sensible qty
        data = self._ld_json()
        if data:
            avail = str(data.get('offers', {}).get('availability', ''))
            if 'InStock' in avail:
                return 999   # unknown exact qty but in stock
            if 'OutOfStock' in avail:
                return 0
        return 0

    # ── description ────────────────────────────────────────────────────────

    def get_description(self):
        selectors = [
            # Boibari-specific (inspect and adjust if needed)
            ('div', {'class': lambda c: c and 'description' in ' '.join(c if isinstance(c, list) else [c]).lower()}),
            ('div', {'class': lambda c: c and 'summary'     in ' '.join(c if isinstance(c, list) else [c]).lower()}),
            ('div', {'class': lambda c: c and 'details'     in ' '.join(c if isinstance(c, list) else [c]).lower()}),
            ('div', {'itemprop': 'description'}),
            ('section', {'class': lambda c: c and 'description' in ' '.join(c if isinstance(c, list) else [c]).lower()}),
        ]
        el = self._find_element(selectors)
        if el:
            return el.decode_contents().strip()

        # LD+JSON fallback
        data = self._ld_json()
        if data:
            return data.get('description', '')
        return ''

    # ── image ──────────────────────────────────────────────────────────────

    def get_image_url(self):
        # 1. LD+JSON (most reliable)
        data = self._ld_json()
        if data:
            img = data.get('image')
            if isinstance(img, list):
                img = img[0]
            if img:
                return self._normalize_url(img)

        # 2. og:image meta tag
        og = self.soup.find('meta', property='og:image')
        if og and og.get('content'):
            return self._normalize_url(og['content'])

        # 3. Product image by itemprop / common classes
        for sel in [
            ('img', {'itemprop': 'image'}),
            ('img', {'class': lambda c: c and 'product' in ' '.join(c if isinstance(c, list) else [c]).lower()}),
        ]:
            el = self.soup.find(*sel)
            if el and el.get('src'):
                return self._normalize_url(el['src'])

        return ''

    # ── specifications table ───────────────────────────────────────────────

    def get_specifications(self):
        """
        Parse the book-detail table common on Boibari product pages.
        Supports both Bangla and English key names.
        """
        specs = {}

        # Try <table> rows first
        for row in self.soup.select('table tr'):
            cells = row.find_all('td')
            if len(cells) < 2:
                continue
            key   = cells[0].get_text(strip=True).lower()
            value = cells[1].get_text(strip=True)
            self._map_spec(specs, key, value)

        # Also try definition-list style (dl/dt/dd) used by some themes
        dts = self.soup.find_all('dt')
        for dt in dts:
            dd = dt.find_next_sibling('dd')
            if dd:
                key   = dt.get_text(strip=True).lower()
                value = dd.get_text(strip=True)
                self._map_spec(specs, key, value)

        # Also try label/value pair divs (Bootstrap-style grids)
        # e.g. <div class="label">Author</div><div class="value">...</div>
        for label_el in self.soup.find_all(
            class_=lambda c: c and any(
                x in ' '.join(c if isinstance(c, list) else [c]).lower()
                for x in ('label', 'key', 'attr-name')
            )
        ):
            value_el = label_el.find_next_sibling()
            if value_el:
                key   = label_el.get_text(strip=True).lower()
                value = value_el.get_text(strip=True)
                self._map_spec(specs, key, value)

        # If title not found in table, fall back to h1
        if not specs.get('title'):
            specs['title'] = self.get_title()

        return specs

    # ── private helpers ────────────────────────────────────────────────────

    def _map_spec(self, specs: dict, key: str, value: str):
        """Map a raw key/value pair from the spec table into the specs dict."""
        if not value:
            return
        if 'title' in key or 'বই' in key or 'নাম' in key:
            specs.setdefault('title', value)
        if 'isbn' in key:
            specs['isbn'] = value
        elif 'author' in key or 'লেখক' in key or 'writer' in key:
            specs['author'] = value
        elif 'publisher' in key or 'প্রকাশক' in key or 'publication' in key:
            specs['publisher'] = value
        elif 'page' in key or 'পৃষ্ঠা' in key:
            m = re.search(r'\d+', value)
            specs['pages'] = m.group() if m else value
        elif 'edition' in key or 'সংস্করণ' in key or 'মুদ্রণ' in key:
            specs['edition'] = value
        elif 'language' in key or 'ভাষা' in key:
            specs['language'] = value
        elif 'country' in key or 'দেশ' in key:
            specs['country'] = value
        elif 'weight' in key or 'ওজন' in key:
            m = re.search(r'[\d.]+', value)
            specs['weight'] = float(m.group()) if m else 0.0
        elif 'category' in key or 'বিষয়' in key:
            specs.setdefault('category', value)

    def _ld_json(self):
        """Return the first parsed application/ld+json dict, or None."""
        tag = self.soup.find('script', type='application/ld+json')
        if tag and tag.string:
            try:
                return json.loads(tag.string.strip())
            except json.JSONDecodeError:
                pass
        return None

    def _extract_text(self, selectors, default=''):
        for tag, attrs in selectors:
            el = self.soup.find(tag, attrs)
            if el:
                return el.get_text(strip=True)
        return default

    def _find_element(self, selectors):
        for tag, attrs in selectors:
            el = self.soup.find(tag, attrs)
            if el:
                return el
        return None

    @staticmethod
    def _parse_price(text):
        if not text:
            return 0.0
        # strip currency symbols (৳, Tk, BDT, /-) and parse
        clean = text.replace('৳', '').replace('Tk', '').replace('BDT', '').replace('/-', '')
        m = re.search(r'[\d,]+\.?\d*', clean)
        return float(m.group().replace(',', '')) if m else 0.0

    @staticmethod
    def _normalize_url(url):
        if not url:
            return ''
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith('/'):
            return 'https://boibari.com' + url
        return url