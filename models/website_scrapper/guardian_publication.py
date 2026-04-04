from odoo import models, fields, api
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
from odoo.exceptions import UserError
import re
import logging
import time
import json

_logger = logging.getLogger(__name__)


class ImportProductFromGuardianpubs(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import Guardian Publications product from website'

    def guardianpubs_products(self):
        url = self.source_url
        if not url or 'guardianpubs.com' not in url:
            raise UserError("Invalid Guardian Publications URL")

        driver = None
        try:
            driver = self._get_selenium_driver()
            driver.get(url)
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            elem = soup.find("div", {"class": "description"})
            self.ecommerce_description = elem.text() if elem else ''


            description_button = driver.find_element(By.XPATH, "//button[contains(text(), 'বিবরণ')]")
            description_button.click()


            time.sleep(2)  # extra buffer for dynamic content to settle

            soup = BeautifulSoup(driver.page_source, 'html.parser')
            extractor = GuardianpubsExtractor(soup)
            elem = soup.find("meta", attrs={"property": "og:description"})
            self.ecommerce_description = elem.get("content") if elem else ''

            self.product_name    = extractor.get_title()
            self.price           = extractor.get_current_price()
            self.face_value      = extractor.get_original_price()
            self.stock_qty       = extractor.get_stock_quantity()

            self.image_url       = extractor.get_image_url()
            self.publishers = extractor.get_publisher()
            self.isbn       = extractor.get_isbn()
            self.authors       = extractor.get_authors()
            self.pages      = extractor.get_pages()
            self.editions      = extractor.get_editions()
            self.publication_date      = extractor.get_publication_date()

            # specs = extractor.get_specifications()
            # self.language   = specs.get('language', '')
            # self.country    = specs.get('country', '')
            # self.weight     = specs.get('weight', 0.0)

            _logger.info(f"✓ Successfully scraped from Guardianpubs: {self.product_name}")
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
        """Return a headless Chrome WebDriver."""
        chrome_options = Options()
        chrome_options.add_argument('--headless')
        chrome_options.add_argument('--no-sandbox')
        chrome_options.add_argument('--disable-dev-shm-usage')
        chrome_options.add_argument('--disable-gpu')
        chrome_options.add_argument('--window-size=1920,1080')
        chrome_options.add_argument(
            'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
            'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )
        return webdriver.Chrome(options=chrome_options)


class GuardianpubsExtractor:
    """
    Extracts product data from a rendered Guardianpubs.com product page.

    The site is a React/Angular SPA — all data is injected into the DOM
    by JavaScript. This extractor works on the fully-rendered page_source
    obtained via Selenium.

    Typical product page URL pattern:
        https://www.guardianpubs.com/product-details/<slug-or-mongo-id>

    Key fields extracted:
        - Title         → h1 or prominent heading
        - Current price → element with class containing 'sell-price' / 'price'
        - Original price→ element with class containing 'original-price' / 'mrp'
        - Stock qty     → quantity element or in/out-of-stock indicator
        - Description   → summary / description div
        - Image         → og:image meta or first product image
        - Specs table   → key/value pairs for ISBN, author, publisher, pages, etc.
    """

    def __init__(self, soup: BeautifulSoup):
        self.soup = soup

    # ------------------------------------------------------------------ #
    #  Public extraction methods
    # ------------------------------------------------------------------ #

    def get_title(self) -> str:
        """<h1 itemprop="name" class="h2">…</h1>"""
        elem = self.soup.find("div",{"class":"product-title"}).find("h3")
        return elem.contents[0].strip() if elem else "Unknown Product"

    def get_current_price(self) -> float:
        """Discounted / selling price."""
        elem = self.soup.find("div",{"class":"product-price"}).find("h3")
        return self._parse_price(elem.text) if elem else 0.0

    def get_original_price(self) -> float:
        """MRP / original / cover price (before discount)."""

        return self.get_current_price()
    def get_publisher(self) -> float:
        elem=self.soup.find("th",string='Publisher')
        if elem:
            return(elem.find_next_sibling("td").text.strip())
        else:return ""

    def get_isbn(self) -> float:
        elem=self.soup.find("th",string='ISBN')
        if elem:
            return(elem.find_next_sibling("td").text.strip())
        else:return ""

    def get_authors(self) -> float:
        elem=self.soup.find("th",string='Author')
        if elem:
            return(elem.find_next_sibling("td").text.strip())
        else:return ""
    def get_pages(self) -> float:
        elem=self.soup.find("th",string='Number of Pages')
        if elem:
            return(elem.find_next_sibling("td").text.strip())
        else:return ""
    def get_editions(self) -> str:
        elem=self.soup.find("th",string='Edition')
        if elem:
            return(elem.find_next_sibling("td").text.strip())
        else:return ""
    def get_publication_date(self) -> str:
        elem=self.soup.find("th",string='Publish')
        if elem:
            return(elem.find_next_sibling("td").text.strip())
        else:return ""

    def get_stock_quantity(self) -> int:
        """
        Returns numeric stock if shown, 1 for in-stock, 0 for out-of-stock.
        """
        # Explicit quantity element
        qty_elem = self.soup.find(
            lambda tag: tag.name in ("span", "div", "p") and
            re.search(r'available[\s-]?quantity|stock[\s-]?quantity', tag.get_text(), re.I)
        )
        if qty_elem:
            match = re.search(r'\d+', qty_elem.get_text())
            if match:
                return int(match.group())

        # Out-of-stock text
        page_text = self.soup.get_text()
        if re.search(r'out[\s-]?of[\s-]?stock|স্টক নেই|অনুপলব্ধ', page_text, re.I):
            return 0

        # In-stock indicator
        if re.search(r'in[\s-]?stock|available|স্টকে আছে', page_text, re.I):
            return 1

        return 0

    # def get_description(self) -> str:
    #     """Returns the book summary/description as raw HTML."""
    #     selectors = [
    #         ("div", {"class": lambda c: c and any(
    #             k in " ".join(c) for k in ("summary", "description", "about", "details", "synopsis")
    #         )}),
    #         ("p", {"class": lambda c: c and "summary" in " ".join(c)}),
    #         ("div", {"itemprop": "description"}),
    #     ]
    #     elem = self._find_element(selectors)
    #     return elem.decode_contents().strip() if elem else ""

    def get_image_url(self) -> str:
        """Returns the best product image URL."""
        # 1. og:image (most reliable on SPAs)
        og = self.soup.find("meta", {"property": "og:image"})
        if og and og.get("content"):
            return og["content"].strip()

        # 2. JSON-LD structured data
        script = self.soup.find("script", {"type": "application/ld+json"})
        if script:
            try:
                data = json.loads(script.string.strip())
                if isinstance(data, dict) and data.get("image"):
                    return data["image"]
            except (json.JSONDecodeError, AttributeError):
                pass

        # 3. First product image with recognisable class/alt
        img = self.soup.find("img", {
            "class": lambda c: c and any(
                k in " ".join(c) for k in ("product-image", "book-cover", "book-image", "cover")
            )
        })
        if img and img.get("src"):
            return self._normalize_url(img["src"])

        return ""

    def get_specifications(self) -> dict:
        """
        Parses the specification table (key → value rows).
        Guardianpubs uses a <table> or <ul>/<dl> list of book attributes.
        Handles both English and Bengali keys.
        """
        specs = {}

        # --- Strategy 1: <table> rows with two <td> cells ---
        for row in self.soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                key   = cells[0].get_text(strip=True).lower()
                value = cells[1].get_text(strip=True)
                specs.update(self._map_spec(key, value))

        # --- Strategy 2: <li> or <div> pairs (SPA-style key-value blocks) ---
        if not specs:
            for item in self.soup.select("li, [class*='spec-item'], [class*='detail-item']"):
                text = item.get_text(separator=":", strip=True)
                if ":" in text:
                    key, _, value = text.partition(":")
                    specs.update(self._map_spec(key.strip().lower(), value.strip()))

        # --- Strategy 3: <dl> definition lists ---
        for dt in self.soup.select("dl dt"):
            dd = dt.find_next_sibling("dd")
            if dd:
                specs.update(self._map_spec(dt.get_text(strip=True).lower(), dd.get_text(strip=True)))

        return specs

    # ------------------------------------------------------------------ #
    #  Private helpers
    # ------------------------------------------------------------------ #

    def _map_spec(self, key: str, value: str) -> dict:
        """Maps a raw key/value pair to the canonical spec dict."""
        result = {}
        if not value:
            return result

        if "title" in key or "নাম" in key:
            result['title'] = value
        if "isbn" in key:
            result['isbn'] = value
        if "author" in key or "লেখক" in key or "writer" in key:
            result['author'] = value
        if "publisher" in key or "প্রকাশক" in key or "publication" in key:
            result['publisher'] = value
        if "page" in key or "পৃষ্ঠা" in key:
            m = re.search(r'\d+', value)
            result['pages'] = m.group() if m else value
        if "edition" in key or "সংস্করণ" in key or "edition" in key:
            result['edition'] = value
        if "language" in key or "ভাষা" in key:
            result['language'] = value
        if "country" in key or "দেশ" in key:
            result['country'] = value
        if "weight" in key or "ওজন" in key:
            m = re.search(r'[\d.]+', value)
            result['weight'] = float(m.group()) if m else 0.0
        return result

    def _extract_text(self, selectors, default="") -> str:
        for tag, attrs in selectors:
            try:
                elem = self.soup.find(tag, attrs)
                if elem:
                    return elem.get_text(strip=True)
            except Exception:
                continue
        return default

    def _find_element(self, selectors):
        for tag, attrs in selectors:
            try:
                elem = self.soup.find(tag, attrs)
                if elem:
                    return elem
            except Exception:
                continue
        return None

    def _parse_price(self, price_text: str) -> float:
        if not price_text:
            return 0.0
        cleaned = price_text.replace('৳', '').replace('Tk', '').replace('TK', '').strip()
        match = re.search(r'[\d,]+\.?\d*', cleaned)
        return float(match.group().replace(',', '')) if match else 0.0

    def _normalize_url(self, url: str) -> str:
        if not url:
            return ""
        if url.startswith('//'):
            return 'https:' + url
        if url.startswith('/'):
            return 'https://www.guardianpubs.com' + url
        return url