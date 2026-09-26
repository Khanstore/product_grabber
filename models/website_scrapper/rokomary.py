from odoo import models
from bs4 import BeautifulSoup
import json
import logging
import os
import re
import time
from urllib.parse import urljoin, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .base_extractor import BaseBookExtractor
from ..unicode_utils import clean_text

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
except ImportError:  # pragma: no cover
    webdriver = None
    By = None
    Options = None
    Service = None

_logger = logging.getLogger(__name__)


class ImportProductFromRokomari(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import Rokomari product from website'

    def rokomari_products(self):
        url = (self.source_url or '').strip()
        if not url:
            return False

        parsed = urlparse(url if '://' in url else 'https://' + url)
        host = (parsed.hostname or '').lower()
        if host != 'rokomari.com' and not host.endswith('.rokomari.com'):
            return False

        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/128.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'bn-BD,bn;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://www.rokomari.com/',
        }

        try:
            session = requests.Session()
            retry = Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset(['GET']),
            )
            adapter = HTTPAdapter(max_retries=retry)
            session.mount('https://', adapter)
            session.mount('http://', adapter)

            response = session.get(url, headers=headers, timeout=20)
            response.raise_for_status()
            response.encoding = 'utf-8'

            extractor = RokomariExtractor(
                BeautifulSoup(response.text, 'html.parser')
            )
            self._apply_extractor(extractor)

            # The current Rokomari page can render the Description and
            # Specification UI only in the browser. Use Selenium only when
            # those parts are still missing.
            specs = extractor.get_specifications()
            need_browser = not extractor.get_description() or any(
                not specs.get(key) for key in (
                    'isbn', 'edition', 'pages', 'weight', 'publication_date',
                    'language', 'country', 'category',
                )
            )

            if need_browser:
                rendered = self._fetch_with_browser(url, headers)
                if rendered:
                    browser_html = rendered.get('html') or ''
                    if browser_html:
                        browser_extractor = RokomariExtractor(
                            BeautifulSoup(browser_html, 'html.parser')
                        )
                        # Keep the normal header extraction for Author/Publisher.
                        # Browser-rendered Specification values fill the same
                        # existing wizard fields without changing the app API.
                        self._apply_extractor(
                            browser_extractor,
                            overwrite=False,
                            exclude_fields={'authors', 'publishers', 'ecommerce_description'},
                        )
                    # The static page can expose a short OG description while
                    # the rendered product summary contains the real details.
                    # Prefer that actual summary when the browser path was used.
                    browser_description = rendered.get('description') or ''
                    if browser_description:
                        self.ecommerce_description = browser_description
                    self._apply_browser_spec_values(rendered.get('specs'))

            _logger.info(
                'Rokomari scrape: title=%r author=%r publisher=%r isbn=%r pages=%r weight=%r',
                self.product_name,
                self.authors,
                self.publishers,
                self.isbn,
                self.pages,
                self.weight,
            )
            return bool(self.product_name)
        except Exception:
            _logger.exception('Failed to scrape Rokomari URL %s', url)
            return False

    def _apply_extractor(self, extractor, overwrite=False, exclude_fields=None):
        specs = extractor.get_specifications()
        values = {
            'face_value': extractor.get_original_price(),
            'price': extractor.get_current_price(),
            'stock_qty': extractor.get_stock_quantity(),
            'ecommerce_description': extractor.get_description(),
            'image_url': extractor.get_image_url(),
            'product_name': extractor.get_title(),
            'isbn': specs.get('isbn', ''),
            'authors': extractor.get_author(),
            'publishers': extractor.get_publisher(),
            'pages': specs.get('pages', ''),
            'editions': specs.get('edition', ''),
            'publication_date': specs.get('publication_date', ''),
            'language': specs.get('language', ''),
            'country': specs.get('country', ''),
            'category_text': extractor.get_category(),
            'weight': specs.get('weight', 0.0),
        }

        excluded = set(exclude_fields or ())
        for field, value in values.items():
            if field in excluded or value in (None, ''):
                continue
            if isinstance(value, str):
                value = clean_text(value)
            if overwrite or not getattr(self, field, False):
                setattr(self, field, value)

    def _apply_browser_spec_values(self, browser_specs):
        mapping = {
            'isbn': 'isbn',
            'edition': 'editions',
            'publication_date': 'publication_date',
            'pages': 'pages',
            'weight': 'weight',
            'category': 'category_text',
            'language': 'language',
            'country': 'country',
        }
        for key, field in mapping.items():
            value = (browser_specs or {}).get(key)
            if value in (None, ''):
                continue
            if field == 'pages':
                value = RokomariExtractor._valid_pages(value)
                if value is None:
                    continue
            elif field == 'weight':
                value = RokomariExtractor._weight_value(value)
                if value is None or not 0 < value <= 30:
                    continue
            elif field == 'isbn':
                compact = RokomariExtractor._bn_digits(value)
                compact = re.sub(r'[^0-9]', '', compact)
                if not 10 <= len(compact) <= 17:
                    continue
                value = compact
            elif field == 'publication_date':
                value = RokomariExtractor._extract_publication_date(value) or clean_text(value)
            else:
                value = clean_text(value)
            setattr(self, field, value)

    @staticmethod
    def _click_specification_tab(driver):
        """Click the visible Specification tab/button used by current Rokomari."""
        if driver is None or By is None:
            return False
        try:
            elements = driver.find_elements(
                By.CSS_SELECTOR,
                'button, [role="tab"], a, li'
            )
            for element in elements:
                try:
                    text = clean_text(element.text or '')
                    if text.lower() not in ('specification', 'specifications'):
                        continue
                    if not element.is_displayed():
                        continue
                    driver.execute_script(
                        'arguments[0].scrollIntoView({block:"center"});', element
                    )
                    element.click()
                    return True
                except Exception:
                    continue
        except Exception:
            return False
        return False

    def _fetch_with_browser(self, url, headers):
        if webdriver is None or Options is None:
            _logger.warning('Rokomari browser extraction skipped: selenium is not installed')
            return None

        chrome = os.environ.get('ROKOMARI_CHROME_BINARY')
        driver_path = os.environ.get('ROKOMARI_CHROMEDRIVER_PATH')

        options = Options()
        options.add_argument('--headless=new')
        options.add_argument('--no-sandbox')
        options.add_argument('--disable-dev-shm-usage')
        options.add_argument('--disable-gpu')
        options.add_argument('--window-size=1920,2200')
        options.add_argument('--lang=bn-BD')
        options.add_argument('--disable-notifications')
        options.add_argument('--ignore-certificate-errors')
        options.add_argument('user-agent=' + headers.get('User-Agent', 'Mozilla/5.0'))
        if chrome and os.path.isfile(os.path.expanduser(chrome)):
            options.binary_location = os.path.abspath(os.path.expanduser(chrome))

        driver = None
        try:
            if driver_path and Service and os.path.isfile(os.path.expanduser(driver_path)):
                driver = webdriver.Chrome(
                    service=Service(os.path.abspath(os.path.expanduser(driver_path))),
                    options=options,
                )
            else:
                # Selenium Manager handles a compatible installed Chrome/Chromium.
                driver = webdriver.Chrome(options=options)

            driver.set_page_load_timeout(40)
            driver.get(url)
            time.sleep(2)

            # Capture the Summary/Description before switching to Specification.
            # On some builds the Summary panel is hidden/unmounted after the tab
            # changes, so reading it first is important.
            before_spec_html = driver.execute_script(
                'return document.documentElement.outerHTML;'
            )
            before_spec_soup = BeautifulSoup(before_spec_html, 'html.parser')
            before_spec_extractor = RokomariExtractor(before_spec_soup)
            description = before_spec_extractor.get_description()

            self._click_specification_tab(driver)
            time.sleep(1.5)

            # Bring the lower specification area into the DOM and let any
            # lazy-loaded rows render.
            for y in (900, 1800, 3000, 5000, 7000):
                driver.execute_script('window.scrollTo(0, arguments[0]);', y)
                time.sleep(0.4)

            # One more click after scrolling handles tabs that move during hydration.
            self._click_specification_tab(driver)
            time.sleep(1)

            html = driver.execute_script(
                'return document.documentElement.outerHTML;'
            )
            page_soup = BeautifulSoup(html, 'html.parser')
            extractor = RokomariExtractor(page_soup)
            specs = extractor.get_specifications()

            _logger.info(
                'Rokomari browser specification extraction: clicked=%s specs=%s description=%s',
                True,
                specs,
                bool(description),
            )
            return {'html': html, 'specs': specs, 'description': description}
        except Exception as exc:
            _logger.warning(
                'Rokomari browser specification extraction failed for %s: %s',
                url,
                exc,
            )
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass


class RokomariExtractor(BaseBookExtractor):
    base_domain = 'https://www.rokomari.com'

    LABELS = {
        'isbn': {'isbn', 'isbn 10', 'isbn 13'},
        'edition': {'edition', 'সংস্করণ'},
        'pages': {
            'number of pages', 'number of page', 'no of pages',
            'no of page', 'no. of pages', 'no. of page', 'page count', 'পৃষ্ঠা',
        },
        'publication_date': {
            'publication date', 'publication', 'publish date',
            'published date', 'publication year', 'প্রকাশকাল', 'প্রকাশের তারিখ',
        },
        'weight': {'weight', 'ওজন'},
        'language': {'language', 'ভাষা'},
        'country': {'country', 'দেশ'},
        'category': {'category', 'categories', 'বিষয়', 'বিভাগ'},
        'author': {'author', 'লেখক'},
        'publisher': {'publisher', 'প্রকাশক'},
    }

    def __init__(self, soup):
        super().__init__(soup)

    @staticmethod
    def _bn_digits(value):
        return str(value or '').translate(
            str.maketrans(
                '০১২৩৪৫৬৭৮৯٠١٢٣٤٥٦٧٨٩',
                '01234567890123456789',
            )
        )

    @classmethod
    def _number(cls, value):
        text = cls._bn_digits(value).replace(',', '')
        match = re.search(r'[-+]?\d+(?:\.\d+)?', text)
        return float(match.group()) if match else None

    @classmethod
    def _valid_pages(cls, value):
        number = cls._number(value)
        if number is None or not number.is_integer():
            return None
        number = int(number)
        return number if 1 <= number <= 20000 else None

    @classmethod
    def _weight_value(cls, value):
        if value in (None, ''):
            return None
        text = cls._bn_digits(value).strip().lower()
        number = cls._number(text)
        if number is None:
            return None
        if re.search(r'\b(?:kg|kgs|kilogram|kilograms)\b|কেজি|কিলোগ্রাম', text):
            return number
        if re.search(r'\b(?:g|gm|gms|gram|grams)\b|গ্রাম', text):
            return number / 1000.0
        return number

    @staticmethod
    def _norm_key(value):
        value = clean_text(value).lower()
        return re.sub(r'[\s._/-]+', ' ', value).strip()

    @classmethod
    def _canonical(cls, value):
        key = cls._norm_key(value)
        for canonical, names in cls.LABELS.items():
            if key in names:
                return canonical
        return None

    @classmethod
    def _clean_spec_value(cls, key, value):
        value = clean_text(str(value or ''))
        if not value:
            return None
        if key == 'isbn':
            value = re.sub(r'[^0-9]', '', cls._bn_digits(value))
            return value if 10 <= len(value) <= 17 else None
        if key == 'pages':
            pages = cls._valid_pages(value)
            return str(pages) if pages is not None else None
        if key == 'weight':
            weight = cls._weight_value(value)
            return weight if weight is not None and 0 < weight <= 30 else None
        if key == 'publication_date':
            return cls._extract_publication_date(value) or value
        return value

    @classmethod
    def _extract_publication_date(cls, value):
        text = clean_text(str(value or ''))
        if not text:
            return ''
        match = re.search(r'[0-9০-৯]{4}', text)
        if not match:
            return ''
        return cls._bn_digits(match.group())

    def _jsonld(self):
        items = []
        for script in self.soup.select('script[type="application/ld+json"]'):
            raw = script.string or script.get_text()
            if not raw:
                continue
            try:
                import json
                data = json.loads(raw)
            except Exception:
                continue
            if isinstance(data, list):
                items.extend(x for x in data if isinstance(x, dict))
            elif isinstance(data, dict):
                if isinstance(data.get('@graph'), list):
                    items.extend(x for x in data['@graph'] if isinstance(x, dict))
                else:
                    items.append(data)
        return items

    def _meta(self, *names):
        for name in names:
            node = self.soup.find('meta', attrs={'property': name})
            node = node or self.soup.find('meta', attrs={'name': name})
            if node and node.get('content'):
                return clean_text(node.get('content'))
        return ''

    def _product_jsonld(self):
        for item in self._jsonld():
            typ = item.get('@type')
            types = typ if isinstance(typ, list) else [typ]
            if any(str(x).lower() == 'product' for x in types):
                return item
        return {}

    def get_title(self):
        node = self.soup.find('h1')
        if node:
            text = clean_text(node.get_text(' ', strip=True))
            if text:
                return text
        return self._meta('og:title', 'twitter:title')

    def get_current_price(self):
        price = self._meta('product:price:amount')
        if price:
            value = self._parse_price(price)
            if value:
                return value
        data = self._product_jsonld()
        offers = data.get('offers') if isinstance(data, dict) else None
        if isinstance(offers, list):
            offers = offers[0] if offers else None
        if isinstance(offers, dict):
            value = self._parse_price(offers.get('price'))
            if value:
                return value
        node = self.soup.find(class_=lambda c: c and 'sell-price' in c)
        return self._parse_price(node.get_text(' ', strip=True) if node else '')

    def get_original_price(self):
        current = self.get_current_price()
        discount = self._meta('product:custom_label_2')
        match = re.search(r'(\d+(?:\.\d+)?)\s*%', discount or '')
        if match and current:
            pct = float(match.group(1))
            if 0 < pct < 100:
                return round(current / (1 - pct / 100), 2)
        node = self.soup.find(class_=lambda c: c and 'original-price' in c)
        return self._parse_price(node.get_text(' ', strip=True) if node else '') or current

    def get_stock_quantity(self):
        for selector in (
            '[data-available-quantity]', '[data-stock-quantity]',
            '[data-stock]', '#available-quantity', 'input[name="quantity"]',
        ):
            for node in self.soup.select(selector):
                raw = (
                    node.get('data-available-quantity') or
                    node.get('data-stock-quantity') or
                    node.get('data-stock') or
                    node.get('max') or
                    node.get_text(' ', strip=True)
                )
                number = self._number(raw)
                if number is not None:
                    return max(0, int(number))

        text = clean_text(self.soup.get_text(' ', strip=True))
        for pattern in (
            r'in\s*stock\s*\(\s*only\s*([0-9০-৯]+)\s*(?:copies?|copy)\s*left',
            r'স্টকে\s*\(\s*মাত্র\s*([0-9০-৯]+)\s*(?:টি|কপি)',
            r'শুধু\s*([0-9০-৯]+)\s*(?:টি|কপি)\s*(?:বাকি|অবশিষ্ট)',
        ):
            match = re.search(pattern, text, re.I)
            if match:
                return max(0, int(self._number(match.group(1)) or 0))
        return 1 if re.search(r'\bin\s*stock\b|স্টকে\s*আছে|স্টক\s*আছে', text, re.I) else 0

    def _next_payload_texts(self):
        """Yield decoded Next.js flight payload text blocks."""
        for script in self.soup.find_all('script'):
            raw = script.string or script.get_text() or ''
            if 'self.__next_f.push' not in raw:
                continue
            match = re.search(
                r'self\.__next_f\.push\(\[1,("(?:\\.|[^"\\])*")\]\)',
                raw,
                re.S,
            )
            if not match:
                continue
            try:
                decoded = json.loads(match.group(1))
            except Exception:
                continue
            if decoded:
                yield decoded

    def _next_detail_bangla(self):
        """Read Rokomari's real product-summary HTML from Next.js data."""
        pattern = re.compile(
            r'"detailBangla"\s*:\s*"((?:\\.|[^"\\])*)"',
            re.S,
        )
        for payload in self._next_payload_texts():
            match = pattern.search(payload)
            if not match:
                continue
            try:
                value = json.loads('"' + match.group(1) + '"')
            except Exception:
                value = match.group(1)
                value = value.replace('\\"', '"')
            value = clean_text(value).strip()
            if value:
                return value
        return ''

    def get_description(self):
        # Exact current Rokomari rendered description container.
        node = self.soup.select_one('div[class^="productSummary_summeryText__"]')
        if node:
            return clean_text(node.get_text(separator='\n', strip=True))

        # The initial HTML often carries the same description as Rokomari's
        # `productSummery.detailBangla` Next.js data, even when the React div
        # itself has not been rendered into the static DOM. Use that full detail
        # before falling back to the shorter social/meta description.
        detail = self._next_detail_bangla()
        if detail:
            detail_soup = BeautifulSoup(detail, 'html.parser')
            text = clean_text(detail_soup.get_text(separator='\n', strip=True))
            if text:
                return text

        node = self.soup.find('div', id='js--summary-description')
        if not node:
            node = self.soup.select_one('.summary-description')
        if node:
            return clean_text(node.get_text(separator='\n', strip=True))

        meta = self._meta('og:description', 'description')
        if meta:
            return clean_text(meta)

        data = self._product_jsonld()
        return clean_text(str(data.get('description') or ''))

    def get_image_url(self):
        candidates = [self._meta('og:image', 'twitter:image')]
        data = self._product_jsonld()
        image = data.get('image') if isinstance(data, dict) else None
        if isinstance(image, list):
            candidates.extend(image)
        elif image:
            candidates.append(image)

        for node in self.soup.select('img[src], img[data-src]'):
            candidates.extend([node.get('src'), node.get('data-src'), node.get('data-original')])

        for candidate in candidates:
            if isinstance(candidate, dict):
                candidate = candidate.get('url') or candidate.get('content')
            if not candidate:
                continue
            candidate = str(candidate).strip()
            if candidate.startswith('//'):
                return 'https:' + candidate
            if candidate.startswith(('http://', 'https://')):
                return candidate
            return urljoin(self.base_domain + '/', candidate)
        return ''

    def get_author(self):
        data = self._product_jsonld()
        author = data.get('author')
        if isinstance(author, dict):
            author = author.get('name')
        if isinstance(author, list):
            author = ', '.join(
                str(x.get('name') if isinstance(x, dict) else x)
                for x in author if x
            )
        author = clean_text(str(author or ''))
        if author:
            return author

        for node in self.soup.select('a[href*="/book/author/"]'):
            text = clean_text(node.get_text(' ', strip=True))
            if text:
                return text
        return ''

    def get_publisher(self):
        data = self._product_jsonld()
        publisher = data.get('publisher')
        if isinstance(publisher, dict):
            publisher = publisher.get('name')
        publisher = clean_text(str(publisher or ''))
        if publisher:
            return publisher
        return self._meta('product:brand')

    def get_category(self):
        data = self._product_jsonld()
        category = data.get('category') or data.get('articleSection')
        if isinstance(category, list):
            category = ', '.join(str(x) for x in category if x)
        category = clean_text(str(category or ''))
        if category:
            return category
        for node in self.soup.select('a[href*="/book/categories/"], a[href*="/book/category/"]'):
            text = clean_text(node.get_text(' ', strip=True))
            if text:
                return text
        return ''

    def _spec_pairs(self):
        """Read visible Specification rows, not arbitrary page numbers/scripts."""
        pairs = {}

        # Normal table/row layout.
        for row in self.soup.select('tr, [role="row"]'):
            cells = [
                clean_text(x.get_text(' ', strip=True))
                for x in row.find_all(['th', 'td'], recursive=False)
            ]
            if len(cells) >= 2:
                key = self._canonical(cells[0])
                if key:
                    pairs.setdefault(key, cells[1])

        # Current React layouts often use adjacent div/span nodes.
        for container in self.soup.select('[role="tabpanel"], .spec, .spec-table, .spec-area, section'):
            children = list(container.find_all(recursive=False))
            texts = [clean_text(x.get_text(' ', strip=True)) for x in children]
            for i, text in enumerate(texts):
                key = self._canonical(text)
                if key and i + 1 < len(texts):
                    value = texts[i + 1]
                    if value and not self._canonical(value):
                        pairs.setdefault(key, value)
                if '|' in text:
                    left, right = text.split('|', 1)
                    key = self._canonical(left)
                    if key and right.strip():
                        pairs.setdefault(key, right.strip())

        # Simple pipe-separated rows are common in the rendered specification.
        for node in self.soup.find_all(['div', 'p', 'li']):
            text = clean_text(node.get_text(' ', strip=True))
            if '|' not in text:
                continue
            left, right = text.split('|', 1)
            key = self._canonical(left)
            if key and right.strip():
                pairs.setdefault(key, right.strip())

        return pairs

    def _specs_from_rendered_text(self, text):
        """Parse the visible text sequence returned after Specification is clicked."""
        lines = [clean_text(x) for x in str(text or '').splitlines() if clean_text(x)]
        specs = {}
        for i, line in enumerate(lines):
            if '|' in line:
                left, right = line.split('|', 1)
                key = self._canonical(left)
                value = self._clean_spec_value(key, right)
                if key and value not in (None, ''):
                    specs.setdefault(key, value)
                    if key == 'edition' and 'publication_date' not in specs:
                        date = cls._extract_publication_date(value)
                        if date:
                            specs['publication_date'] = date
                continue
            key = self._canonical(line)
            if key and i + 1 < len(lines) and not self._canonical(lines[i + 1]):
                value = self._clean_spec_value(key, lines[i + 1])
                if value not in (None, ''):
                    specs.setdefault(key, value)
                    if key == 'edition' and 'publication_date' not in specs:
                        date = self._extract_publication_date(value)
                        if date:
                            specs['publication_date'] = date
        return specs

    def get_specifications(self):
        specs = {}

        # Read the visible specification rows first.
        for key, raw in self._spec_pairs().items():
            value = self._clean_spec_value(key, raw)
            if value not in (None, ''):
                specs[key] = value

        # JSON-LD provides stable metadata for ISBN/author/publisher/category/weight,
        # but it is never used as a generic page-number source.
        data = self._product_jsonld()
        if data:
            for key, source in (
                ('isbn', data.get('isbn')), ('author', data.get('author')),
                ('publisher', data.get('publisher')), ('category', data.get('category')),
                ('weight', data.get('weight')),
            ):
                if key in specs or source in (None, ''):
                    continue
                if isinstance(source, dict):
                    source = source.get('name') or source.get('value') or source.get('valueReference')
                value = self._clean_spec_value(key, source)
                if value not in (None, ''):
                    specs[key] = value

        if 'edition' in specs and 'publication_date' not in specs:
            date = self._extract_publication_date(specs['edition'])
            if date:
                specs['publication_date'] = date

        if 'publication_date' not in specs:
            meta = self._meta('book:release_date', 'article:published_time')
            if meta:
                specs['publication_date'] = self._extract_publication_date(meta) or meta

        if 'weight' not in specs:
            # Product metadata may also be present in a plain text data attribute.
            for node in self.soup.select('[itemprop="weight"], [data-weight]'):
                raw = node.get('content') or node.get('data-weight') or node.get_text(' ', strip=True)
                value = self._clean_spec_value('weight', raw)
                if value not in (None, ''):
                    specs['weight'] = value
                    break

        return specs
