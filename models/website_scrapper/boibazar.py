from odoo import models, fields, api
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
from odoo.exceptions import UserError
import re
import json
import logging
import time
from .base_extractor import BaseBookExtractor

_logger = logging.getLogger(__name__)


class ImportProductFromBoibazar(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import BoiBazar product from website'

    def boibazar_products(self):
        url = self.source_url
        if not url or 'boibazar.com' not in url:
            raise UserError("Invalid BoiBazar URL. Expected: https://www.boibazar.com/book/<slug>")

        driver = None
        try:
            driver = self._get_selenium_driver()
            driver.get(url)

            # Wait for the price heading — confirms product content is rendered
            WebDriverWait(driver, 25).until(
                EC.presence_of_element_located((By.CLASS_NAME, 'details-title-23'))
            )
            time.sleep(2)

            soup = BeautifulSoup(driver.page_source, 'html.parser')
            extractor = BoibazarExtractor(soup)

            self.product_name          = extractor.get_title()
            self.authors               = extractor.get_authors()
            self.price                 = extractor.get_current_price()
            self.face_value            = extractor.get_original_price()
            self.publishers            = extractor.get_publisher()
            self.stock_qty             = extractor.get_stock_quantity()
            self.ecommerce_description = extractor.get_description()
            self.image_url             = extractor.get_image_url()

            specs = extractor.get_specifications()
            self.isbn     = specs.get('isbn', '')
            self.pages    = specs.get('pages', '')
            self.editions = specs.get('edition', '')
            self.language = specs.get('language', '')
            self.country  = specs.get('country', '')
            self.weight   = specs.get('weight', 0.0)

            _logger.info(f"✓ Successfully scraped from BoiBazar: {self.product_name}")
            return True

        except Exception as e:
            _logger.exception(f"Error scraping {url}: {e}")
            self.product_name = getattr(self, 'product_name', 'Unknown Product')
            self.price        = getattr(self, 'price', 0.0)
            self.stock_qty    = getattr(self, 'stock_qty', 0)
            return False

        finally:
            if driver:
                driver.quit()

    def _get_selenium_driver(self):
        opts = Options()
        opts.add_argument('--headless')
        opts.add_argument('--no-sandbox')
        opts.add_argument('--disable-dev-shm-usage')
        opts.add_argument('--disable-gpu')
        opts.add_argument('--window-size=1920,1080')
        opts.add_argument(
            'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        return webdriver.Chrome(options=opts)


class BoibazarExtractor(BaseBookExtractor):
    """
    Extracts product data from a rendered BoiBazar book page.

    Primary data source — hidden input#product-values (comma-separated):
        [0] product_id
        [1] some flag
        [2] sell price (negative, e.g. -255)
        [3] original price (negative, e.g. -300)
        [4] slug
        [5] title
        [6] image path  (/images/product/.../250X360/ID.png)
        [7] author
        [8] publisher

    Secondary sources (DOM) used for fallback and extra fields:
        Title      → div.details-title-23 > h1 > span
        Author     → div.details-author > a > h2 > span
        Sell price → h4.prod-kir-font[contains "বইবাজার মূল্য"] > span.price-font
        Orig price → h4.prod-kir-font[contains "মুদ্রিত মূল্য"] > span.inner.price-font
        Publisher  → div.details-publisher span > a > span
        Specs      → table rows / dl / li patterns for ISBN, pages, edition etc.
    """

    IMAGE_BASE = 'https://www.boibazar.com'

    def __init__(self, soup: BeautifulSoup):
        self.soup = soup
        self._product_values = self._parse_product_values()

    # ------------------------------------------------------------------ #
    #  Hidden input — most reliable single source for core fields
    # ------------------------------------------------------------------ #

    def _parse_product_values(self) -> list:
        """
        Parses the comma-separated hidden input#product-values field.
        Example value:
            696f2c..,-false,-255,-300,-slug,-Title,-/images/.../id.png,-Author,-Publisher
        Returns a clean list with leading dashes stripped from each part.
        """
        inp = self.soup.find('input', {'id': 'product-values'})
        if not inp or not inp.get('value'):
            return []
        parts = inp['value'].split(',')
        # Each part except the first starts with '-'; strip it
        cleaned = []
        for i, part in enumerate(parts):
            cleaned.append(part.lstrip('-') if i > 0 else part)
        return cleaned

    def _pv(self, index: int, default='') -> str:
        """Safe accessor for product_values list."""
        try:
            val = self._product_values[index].strip()
            return val if val not in ('', 'false', 'true') else default
        except IndexError:
            return default

    # ------------------------------------------------------------------ #
    #  Public extraction methods
    # ------------------------------------------------------------------ #

    def get_title(self) -> str:
        # Primary: hidden input index 5
        title = self._pv(5)
        if title:
            return title
        # Fallback: div.details-title-23 h1 span
        container = self.soup.find('div', class_='details-title-23')
        if container:
            span = container.select_one('h1 span')
            if span:
                return span.get_text(strip=True)
        # Last resort: og:title
        og = self.soup.find('meta', {'property': 'og:title'})
        if og and og.get('content'):
            return og['content'].split('|')[0].strip()
        return 'Unknown Product'

    def get_authors(self) -> str:
        # Primary: hidden input index 7
        author = self._pv(7)
        if author:
            return author
        # Fallback: collect all author spans from div.details-author
        container = self.soup.find('div', class_='details-author')
        if container:
            names = [span.get_text(strip=True) for span in container.select('h2 span')]
            if names:
                return ', '.join(names)
        return ''

    def get_current_price(self) -> float:
        # Primary: hidden input index 2 (stored as positive after lstrip('-'))
        pv_price = self._pv(2)
        if pv_price:
            price = self._parse_price(pv_price)
            if price > 0:
                return price
        # Fallback: h4.prod-kir-font containing "বইবাজার মূল্য" → span.price-font
        for h4 in self.soup.find_all('h4', class_='prod-kir-font'):
            if 'বইবাজার মূল্য' in h4.get_text():
                span = h4.find('span', class_='price-font')
                if span:
                    return self._parse_price(span.get_text(strip=True))
        return 0.0

    def get_original_price(self) -> float:
        # Primary: hidden input index 3
        pv_price = self._pv(3)
        if pv_price:
            price = self._parse_price(pv_price)
            if price > 0:
                return price
        # Fallback: h4.prod-kir-font containing "মুদ্রিত মূল্য" → span.inner.price-font
        for h4 in self.soup.find_all('h4', class_='prod-kir-font'):
            if 'মুদ্রিত মূল্য' in h4.get_text():
                span = h4.find('span', class_='price-font')
                if span:
                    return self._parse_price(span.get_text(strip=True))
        return 0.0

    def get_publisher(self) -> str:
        # Primary: hidden input index 8
        pub = self._pv(8)
        if pub:
            return pub
        # Fallback: div.details-publisher span a span
        container = self.soup.find('div', class_='details-publisher')
        if container:
            span = container.select_one('a span')
            if span:
                return span.get_text(strip=True)
        return ''

    def get_image_url(self) -> str:
        # Primary: hidden input index 6 (/images/product/.../id.png)
        img_path = self._pv(6)
        if img_path:
            return self._normalize_url(img_path)
        # Fallback: og:image
        og = self.soup.find('meta', {'property': 'og:image'})
        if og and og.get('content'):
            return og['content'].strip()
        # Fallback: JSON-LD
        img = self._from_jsonld('image')
        if img:
            return img if isinstance(img, str) else (img[0] if img else '')
        return ''

    def get_stock_quantity(self) -> int:
        page_text = self.soup.get_text()
        m = re.search(r'(stock|quantity|পরিমাণ|স্টক)[^\d]*(\d+)', page_text, re.I)
        if m:
            return int(m.group(2))
        if re.search(r'out[\s-]?of[\s-]?stock|স্টক\s*নেই|অনুপলব্ধ', page_text, re.I):
            return 0
        if re.search(r'in[\s-]?stock|available|স্টকে\s*আছে|পাওয়া\s*যাচ্ছে', page_text, re.I):
            return 1
        return 0

    def get_description(self) -> str:
        selectors = [
            ('div', {'class': "book-details-section-inline"}),
         ]
        elem = self._find_element(selectors)
        return elem.text.strip() if elem else ''

    def get_image_url(self) -> str:
        # 1. og:image
        og = self.soup.find('meta', {'property': 'og:image'})
        if og and og.get('content'):
            return og['content'].strip()
        # 2. JSON-LD image
        img = self._from_jsonld('image')
        if img:
            return img if isinstance(img, str) else (img[0] if isinstance(img, list) else '')
        # 3. itemprop=image
        ip = self.soup.find(attrs={'itemprop': 'image'})
        if ip:
            return self._normalize_url(ip.get('src') or ip.get('content', ''))
        # 4. img with recognisable class/alt
        img_tag = self.soup.find('img', {
            'class': lambda c: c and any(k in ' '.join(c).lower() for k in ('book', 'cover', 'product'))
        }) or self.soup.find('img', {'alt': re.compile(r'book|cover|বই', re.I)})
        if img_tag and img_tag.get('src'):
            return self._normalize_url(img_tag['src'])
        return ''

    def get_specifications(self) -> dict:
        """
        Parses extra book metadata (ISBN, pages, edition, language, etc.)
        from table/dl/li structures elsewhere on the page.
        Publisher and author are handled by dedicated methods above.
        """
        specs = {}
        _SPEC_MAP = {
            'isbn':     ['isbn'],
            'pages':    ['page', 'পৃষ্ঠা', 'pages'],
            'edition':  ['edition', 'সংস্করণ'],
            'language': ['language', 'ভাষা'],
            'country':  ['country', 'দেশ'],
            'weight':   ['weight', 'ওজন'],
        }

        def classify(raw_key, value):
            key = raw_key.lower().strip()
            for canonical, aliases in _SPEC_MAP.items():
                if any(alias in key for alias in aliases):
                    if canonical == 'pages':
                        m = re.search(r'\d+', value)
                        return {canonical: m.group() if m else value}
                    if canonical == 'weight':
                        m = re.search(r'[\d.]+', value)
                        return {canonical: float(m.group()) if m else 0.0}
                    return {canonical: value}
            return {}

        # Table rows
        for row in self.soup.select('table tr'):
            cells = row.find_all('td')
            if len(cells) >= 2:
                specs.update(classify(cells[0].get_text(strip=True), cells[1].get_text(strip=True)))
        # dl
        if not specs:
            for dt in self.soup.select('dl dt'):
                dd = dt.find_next_sibling('dd')
                if dd:
                    specs.update(classify(dt.get_text(strip=True), dd.get_text(strip=True)))
        # li colon-split
        if not specs:
            for li in self.soup.select('li'):
                text = li.get_text(separator=':', strip=True)
                if ':' in text:
                    k, _, v = text.partition(':')
                    specs.update(classify(k.strip(), v.strip()))

        return specs

    # ------------------------------------------------------------------ #
    #  Helpers
    # ------------------------------------------------------------------ #

    def _from_jsonld(self, field: str):
        script = self.soup.find('script', {'type': 'application/ld+json'})
        if script and script.string:
            try:
                data = json.loads(script.string.strip())
                if isinstance(data, list):
                    data = next((d for d in data if isinstance(d, dict)), {})
                return data.get(field, '')
            except (json.JSONDecodeError, AttributeError):
                pass
        return ''

    def _find_element(self, selectors):
        for tag, attrs in selectors:
            try:
                elem = self.soup.find(tag, attrs)
                if elem:
                    return elem
            except Exception:
                pass
        return None

    def _normalize_url(self, url: str) -> str:
        if not url:
            return ''
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith('/'):
            return self.IMAGE_BASE + url
        return url