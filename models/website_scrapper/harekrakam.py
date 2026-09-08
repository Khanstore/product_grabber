from odoo import models, fields, api
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from bs4 import BeautifulSoup
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
import json
import requests
import re
import logging
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from odoo.exceptions import UserError
from .base_extractor import BaseBookExtractor


# ===========================================================================
# Harekrokom scraper
# ===========================================================================

class ImportProductFromHarekrokom(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import Harekrokom product from website'

    def harekrokom_products(self):
        url = self.source_url
        if not url or 'harekrokom.com' not in url:
            raise ValueError("Invalid Harekrokom URL")

        session = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)
        session.headers.update({
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            ),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        })

        try:
            session.get('https://harekrokom.com/', timeout=10)   # warm-up / cookie
            response = session.get(url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            extractor = HarekrokomExtractor(soup)

            self.product_name          = extractor.get_title()
            self.face_value            = extractor.get_original_price()
            self.price                 = extractor.get_current_price()
            self.stock_qty             = extractor.get_stock_quantity()
            self.ecommerce_description = extractor.get_description()
            self.image_url             = extractor.get_image_url()

            specs = extractor.get_specifications()
            self.isbn       = specs.get('isbn', '')
            self.authors    = specs.get('author', '')
            self.publishers = specs.get('publisher', '')
            self.pages      = specs.get('pages', '')
            self.editions   = specs.get('edition', '')
            self.language   = specs.get('language', '')
            self.country    = specs.get('country', '')
            self.weight     = specs.get('weight', 0.0)

            print(f"✓ Successfully scraped: {self.product_name}")
            return True

        except Exception as e:
            logging.exception(f"Error scraping {url}: {e}")
            self.product_name = getattr(self, 'product_name', 'Unknown Product')
            self.price        = getattr(self, 'price', 0.0)
            self.stock_qty    = getattr(self, 'stock_qty', 0)
            return False


class HarekrokomExtractor(BaseBookExtractor):
    """
    Extract product data from a Harekrokom.com book page.

    Page layout (SSR — no JavaScript required):
      • Title         : first <h1> on the page
      • Prices        : two ৳ amounts in the price block — current then original
                        (also shown as "-XX% ৳current ৳original")
      • Author        : labeled "Writer:"   in the meta/info block
      • Publisher     : labeled "Publication:" in the meta/info block
      • Tabs          : Description | Specification | Author | Review
      • Spec table    : key-value rows inside the Specification tab
      • Image         : og:image meta tag or first product <img>
    """

    BASE_URL = 'https://harekrokom.com'

    def __init__(self, soup: BeautifulSoup):
        self.soup = soup

    # ------------------------------------------------------------------
    # Public extractors
    # ------------------------------------------------------------------

    def get_title(self):
        # 1. <h1> tag (most reliable on SSR pages)
        h1 = self.soup.find('h1')
        if h1:
            text = h1.get_text(strip=True)
            if text:
                return text

        # 2. Open Graph title
        og = self.soup.find('meta', property='og:title')
        if og and og.get('content'):
            return og['content'].strip()

        # 3. <title> tag — strip " - Harekrokom" suffix if present
        title_tag = self.soup.find('title')
        if title_tag:
            raw = title_tag.get_text(strip=True)
            return re.sub(r'\s*[|\-–]\s*Harekrokom.*$', '', raw).strip()

        # 4. JSON-LD
        ld = self._get_ld_json()
        return ld.get('name', 'Unknown Product') if ld else 'Unknown Product'

    def get_current_price(self):
        """
        Harekrokom shows discounted price first, MRP second.
        Pattern: ৳<sell>  ৳<original>   or   -XX% ৳<sell> ৳<original>
        """
        prices = self._extract_all_prices()
        return prices[0] if prices else 0.0

    def get_original_price(self):
        prices = self._extract_all_prices()
        return prices[1] if len(prices) >= 2 else 0.0

    def get_stock_quantity(self):
        # Common patterns: "In Stock", "Out of Stock", quantity badge
        availability_elem = self.soup.find(
            string=re.compile(r'in\s*stock|out\s*of\s*stock|available', re.I)
        )
        if availability_elem:
            if re.search(r'out\s*of\s*stock', availability_elem, re.I):
                return 0
            # "in stock" or "available" — return 1 as a signal (quantity unknown)
            return 1

        # Numeric quantity tag (e.g. "10 in stock")
        qty_elem = self.soup.find(
            string=re.compile(r'\d+\s*(in\s*stock|available|copies|পিস|টি)', re.I)
        )
        if qty_elem:
            match = re.search(r'\d+', qty_elem)
            return int(match.group()) if match else 1

        # JSON-LD availability
        ld = self._get_ld_json()
        if ld:
            avail = ld.get('offers', {}).get('availability', '')
            if 'InStock' in avail:
                return 1
            if 'OutOfStock' in avail:
                return 0

        return 0

    def get_description(self):
        """
        Content inside the "Description" tab section.
        BeautifulSoup has all tabs in the DOM; we look for the tab panel
        that follows the "Description" heading/tab link.
        """
        # Strategy 1: find a div/section with id or class containing "description"
        for tag in ('div', 'section', 'article'):
            elem = self.soup.find(tag, id=re.compile(r'description', re.I))
            if elem:
                return elem.decode_contents().strip()
            elem = self.soup.find(
                tag,
                class_=lambda c: c and 'description' in ' '.join(c).lower()
            )
            if elem:
                return elem.decode_contents().strip()

        # Strategy 2: find heading with text "Description" then grab next sibling
        for heading in self.soup.find_all(
            ['h2', 'h3', 'h4', 'span', 'a'],
            string=re.compile(r'^description$', re.I)
        ):
            sibling = heading.find_next_sibling()
            if sibling:
                return sibling.decode_contents().strip()

        # Strategy 3: JSON-LD
        ld = self._get_ld_json()
        return ld.get('description', '') if ld else ''

    def get_image_url(self):
        """
        Priority order:
          1. JSON-LD  (most specific — references the exact product)
          2. Product-container <img>  (first image inside a product/item wrapper)
          3. og:image  (last resort — often the site logo on smaller shops)

        Also handles lazy-loaded images where the real URL is in data-src / data-lazy
        instead of src.
        """

        # ── 1. JSON-LD ──────────────────────────────────────────────────────
        ld = self._get_ld_json()
        if ld:
            image = ld.get('image')
            if image:
                url = image if isinstance(image, str) else (image[0] if image else None)
                if url:
                    return self._normalize_url(url)

        # ── 2. Product-container img ────────────────────────────────────────
        # Look for a wrapper that semantically belongs to the product detail
        container = None
        for css in (
            '[class*="product"]',
            '[class*="item-detail"]',
            '[class*="book"]',
            '[id*="product"]',
            '[id*="item"]',
            'main',
            'article',
        ):
            container = self.soup.select_one(css)
            if container:
                break

        search_root = container if container else self.soup

        # Collect all img candidates from the container; check src AND lazy attrs
        for img in search_root.find_all('img'):
            url = self._best_img_src(img)
            if url and self._is_product_image(url):
                return self._normalize_url(url)

        # ── 3. og:image fallback ─────────────────────────────────────────────
        og = self.soup.find('meta', property='og:image')
        if og and og.get('content'):
            content = og['content'].strip()
            # Reject if it looks like a generic logo / favicon
            if not re.search(r'logo|favicon|icon|banner|placeholder', content, re.I):
                return content

        return None

    # ── Image helpers ──────────────────────────────────────────────────────

    def _best_img_src(self, img_tag) -> str | None:
        """
        Return the real image URL from an <img> tag, checking both eager (src)
        and lazy-loaded attributes (data-src, data-lazy, data-original, etc.)
        """
        # Lazy-load attrs take priority — src on those tags is usually a placeholder gif
        for attr in ('data-src', 'data-lazy', 'data-original', 'data-url', 'data-image'):
            val = img_tag.get(attr, '').strip()
            if val and not val.startswith('data:'):
                return val

        # Eager src — skip obvious placeholders
        src = img_tag.get('src', '').strip()
        if src and not src.startswith('data:'):
            placeholder_pats = ('placeholder', 'blank', 'transparent', 'loading', 'spinner', '1x1', 'spacer')
            if not any(p in src.lower() for p in placeholder_pats):
                return src

        return None

    def _is_product_image(self, url: str) -> bool:
        """
        Return True if the URL looks like a real product/book-cover image.
        Rejects icons, logos, payment-method badges, and tracking pixels.
        """
        url_lower = url.lower()

        # Must have an image extension (or storage path without extension)
        has_ext = bool(re.search(r'\.(jpg|jpeg|png|webp|gif)', url_lower))
        # OR looks like a storage/CDN path (harekrokom uses /storage/ like many Laravel apps)
        is_storage = bool(re.search(r'/storage/|/uploads?/|/products?/|/items?/|/books?/', url_lower))

        if not (has_ext or is_storage):
            return False

        # Reject obvious non-product images
        reject_keywords = (
            'logo', 'favicon', 'icon', 'banner', 'badge',
            'placeholder', 'spinner', 'loading', 'payment',
            'bkash', 'nagad', 'visa', 'mastercard', 'ssl',
            '1x1', 'pixel', 'tracker',
        )
        if any(kw in url_lower for kw in reject_keywords):
            return False

        return True

    def get_specifications(self):
        """
        Harekrokom has two data sources for specs:
          A) Inline meta block — labeled text like "Writer: ..." / "Publication: ..."
          B) "Specification" tab — a table with key-value rows
        """
        specs = {}

        # --- A: labeled fields in the product info block ---
        self._parse_labeled_fields(specs)

        # --- B: specification table rows ---
        self._parse_spec_table(specs)

        # --- C: JSON-LD supplement ---
        if not specs.get('author') or not specs.get('publisher'):
            ld = self._get_ld_json()
            if ld:
                specs.setdefault('author', self._ld_author(ld))
                publisher = ld.get('publisher', {})
                specs.setdefault(
                    'publisher',
                    publisher.get('name', '') if isinstance(publisher, dict) else str(publisher)
                )
                specs.setdefault('isbn', ld.get('isbn', ''))

        return specs

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _extract_all_prices(self):
        """
        Return all ৳-denominated prices found in the price section as a list
        of floats, in document order (sell price first, MRP second).
        """
        # Look for a container that holds price info
        price_container = (
            self.soup.find(class_=re.compile(r'price', re.I))
            or self.soup.find(id=re.compile(r'price', re.I))
            or self.soup.body   # fallback: whole page
        )
        if not price_container:
            return []

        text = price_container.get_text(' ', strip=True)

        # Extract all numeric values adjacent to ৳ or Tk
        # Pattern: ৳<number>  or  Tk<number>  or  <number>৳
        raw_prices = re.findall(r'৳\s*([\d,]+\.?\d*)|Tk\s*([\d,]+\.?\d*)|([\d,]+\.?\d*)\s*৳', text)
        prices = []
        for groups in raw_prices:
            raw = next((g for g in groups if g), None)
            if raw:
                try:
                    prices.append(float(raw.replace(',', '')))
                except ValueError:
                    continue

        # Deduplicate while preserving order, drop zeros
        seen = set()
        result = []
        for p in prices:
            if p > 0 and p not in seen:
                seen.add(p)
                result.append(p)

        return result

    def _parse_labeled_fields(self, specs: dict):
        """
        Parse inline labeled metadata, e.g.:
            Writer: লেখকের নাম
            Publication: প্রকাশকের নাম
            Categories: বিভাগ
        These appear as plain text siblings inside a product-info block.
        """
        # Find all elements whose text starts with a known label
        label_map = {
            'writer':      'author',
            'author':      'author',
            'লেখক':        'author',
            'publication': 'publisher',
            'publisher':   'publisher',
            'প্রকাশক':     'publisher',
            'isbn':        'isbn',
            'edition':     'edition',
            'সংস্করণ':    'edition',
            'language':    'language',
            'ভাষা':        'language',
            'pages':       'pages',
            'পৃষ্ঠা':      'pages',
            'country':     'country',
            'দেশ':         'country',
        }

        # Walk all text-bearing elements
        for elem in self.soup.find_all(['p', 'span', 'div', 'li', 'td']):
            text = elem.get_text(' ', strip=True)
            for label, field in label_map.items():
                # Match "Writer: value" or "Writer : value"
                pattern = rf'^{re.escape(label)}\s*:\s*(.+)$'
                m = re.match(pattern, text, re.I)
                if m:
                    value = m.group(1).strip()
                    if value and field not in specs:
                        specs[field] = value
                    break

    def _parse_spec_table(self, specs: dict):
        """Parse key-value rows from the Specification tab table."""
        for row in self.soup.select('table tr'):
            cells = row.find_all(['td', 'th'])
            if len(cells) < 2:
                continue
            key   = cells[0].get_text(strip=True).lower()
            value = cells[1].get_text(strip=True)
            if not value:
                continue

            if 'isbn' in key:
                specs.setdefault('isbn', value)
            elif 'author' in key or 'writer' in key or 'লেখক' in key:
                specs.setdefault('author', value)
            elif 'publisher' in key or 'publication' in key or 'প্রকাশক' in key:
                specs.setdefault('publisher', value)
            elif 'page' in key or 'পৃষ্ঠা' in key:
                m = re.search(r'\d+', value)
                specs.setdefault('pages', m.group() if m else value)
            elif 'edition' in key or 'সংস্করণ' in key:
                specs.setdefault('edition', value)
            elif 'language' in key or 'ভাষা' in key:
                specs.setdefault('language', value)
            elif 'country' in key or 'দেশ' in key:
                specs.setdefault('country', value)
            elif 'weight' in key or 'ওজন' in key:
                m = re.search(r'[\d.]+', value)
                specs.setdefault('weight', float(m.group()) if m else 0.0)

    def _get_ld_json(self):
        """Return first Book/Product JSON-LD block as a dict, or None."""
        for tag in self.soup.find_all('script', {'type': 'application/ld+json'}):
            # FIX: guard against script_tag.string being None
            if not tag.string:
                continue
            try:
                data = json.loads(tag.string.strip())
                if '@graph' in data:
                    for item in data['@graph']:
                        if item.get('@type') in ('Book', 'Product'):
                            return item
                if data.get('@type') in ('Book', 'Product'):
                    return data
            except (json.JSONDecodeError, AttributeError):
                continue
        return None

    def _ld_author(self, ld: dict) -> str:
        author = ld.get('author', '')
        if isinstance(author, list):
            return ', '.join(
                a.get('name', '') if isinstance(a, dict) else str(a) for a in author
            )
        if isinstance(author, dict):
            return author.get('name', '')
        return str(author) if author else ''

    def _normalize_url(self, url):
        if not url:
            return None
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith('/'):
            return self.BASE_URL + url
        return url