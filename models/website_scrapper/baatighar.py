from odoo import models, fields, api
from bs4 import BeautifulSoup
import requests, re, logging, json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from odoo.exceptions import UserError


class importProductFromBaatighar(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import product from Baatighar.com website'

    def baatighar_products(self):
        url = self.source_url
        if not url or 'baatighar.com' not in url:
            raise ValueError("Invalid Baatighar URL")

        session = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        session.headers.update(headers)

        try:
            session.get('https://baatighar.com/', timeout=10)
            response = session.get(url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            extractor = BaatigharExtractor(soup)

            self.product_name = extractor.get_title()
            self.face_value   = extractor.get_original_price()
            self.price        = extractor.get_current_price()
            self.stock_qty    = extractor.get_stock_quantity()
            self.ecommerce_description = extractor.get_description()
            self.image_url    = extractor.get_image_url()

            specs = extractor.get_specifications()
            self.authors    = specs.get('author', '')
            self.isbn       = specs.get('isbn', '')
            self.publishers = specs.get('publisher', '')
            self.pages      = specs.get('pages', '')
            self.editions   = specs.get('edition', '')
            self.language   = specs.get('language', '')
            self.country    = specs.get('country', '')

            logging.info(f"✓ Successfully scraped: {self.product_name}")
            return True

        except Exception as e:
            logging.exception(f"Error scraping {url}: {e}")
            return False


class BaatigharExtractor:
    """Extract product data from Baatighar.com product pages."""

    def __init__(self, soup: BeautifulSoup):
        self.soup = soup

    # ------------------------------------------------------------------ #
    #  Public extractors                                                   #
    # ------------------------------------------------------------------ #

    def get_title(self) -> str:
        """<h1 itemprop="name" class="h2">…</h1>"""
        elem = self.soup.find("h1", {"itemprop": "name"})
        return elem.get_text(strip=True) if elem else "Unknown Product"

    def get_current_price(self) -> float:
        """
        Discounted price lives in:
        <span class="oe_price text-primary" …>৳ <span class="oe_currency_value">360.00</span></span>
        """
        container = self.soup.find("span", {"class": "oe_price"})
        if container:
            val = container.find("span", {"class": "oe_currency_value"})
            if val:
                return self._parse_price(val.get_text(strip=True))
        return 0.0

    def get_original_price(self) -> float:
        """
        Original (crossed-out) price:
        <span class="oe_default_price …" …>৳ <span class="oe_currency_value">450.00</span></span>
        inside the <div class="product_price"> block.
        """
        price_div = self.soup.find("div", {"class": "product_price"})
        if price_div:
            original = price_div.find("span", {"class": "oe_default_price"})
            if original:
                val = original.find("span", {"class": "oe_currency_value"})
                if val:
                    return self._parse_price(val.get_text(strip=True))
        return 0.0

    def get_stock_quantity(self) -> int:
        """
        Baatighar shows availability per branch in a table.
        We check the online row; if the tick image is present → in stock (return 1),
        otherwise 0.  Adjust logic if you need real quantities.
        """
        table = self.soup.find("table", {"class": "check_availtiy_id_for_xpath"})
        if not table:
            return 0

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                label = cells[0].get_text(strip=True)
                if "অনলাইন" in label or "Online" in label.lower():
                    cell_text = cells[1].get_text(strip=True).lower()
                    return 1 if "available" in cell_text else 0
        return 0

    def get_description(self) -> str:
        """Short book summary from the 'বই সংক্ষেপ' tab."""
        desc = self.soup.find("p", {"class": "book_short_desc"})
        if desc:
            text = desc.get_text(strip=True)
            return text if text else ""
        return ""

    def get_image_url(self) -> str:
        """
        Primary product image URL from <meta property="og:image">.
        Falls back to the <img> inside .carousel-item.active if needed.
        """
        og = self.soup.find("meta", {"property": "og:image"})
        if og and og.get("content"):
            return og["content"]

        # fallback: first carousel image
        carousel = self.soup.find("div", {"class": "carousel-item active"})
        if carousel:
            img = carousel.find("img")
            if img and img.get("src"):
                src = img["src"]
                if src.startswith("/"):
                    src = "https://baatighar.com" + src
                return src
        return ""

    def get_specifications(self) -> dict:
        """
        Parse the details table inside #tp-product-details-tab.

        Expected rows (key → mapped field):
          Writer      → author
          Publisher   → publisher
          ISBN        → isbn
          Language    → language
          Country     → country
          Format      → format
          First Published → edition
          Pages       → pages
        """
        specs = {}

        details_tab = self.soup.find("div", {"id": "tp-product-details-tab"})
        table = details_tab.find("table") if details_tab else None

        # fallback: any table with matching rows
        if not table:
            table = self.soup.find("table", {"class": "table-hover"})

        if not table:
            return specs

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) < 2:
                continue

            key   = cells[0].get_text(strip=True).lower()
            value = cells[1].get_text(strip=True)

            if "writer" in key or "author" in key or "লেখক" in key:
                specs["author"] = value
            elif "publisher" in key or "প্রকাশক" in key:
                specs["publisher"] = value
            elif "isbn" in key:
                specs["isbn"] = value
            elif "language" in key or "ভাষা" in key:
                specs["language"] = value
            elif "country" in key or "দেশ" in key:
                specs["country"] = value
            elif "page" in key or "পৃষ্ঠা" in key:
                m = re.search(r"\d+", value)
                specs["pages"] = m.group() if m else value
            elif "first published" in key or "edition" in key or "সংস্করণ" in key:
                specs["edition"] = value
            elif "format" in key:
                specs["format"] = value

        return specs

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    def _parse_price(self, text: str) -> float:
        if not text:
            return 0.0
        cleaned = text.replace("৳", "").replace("Tk", "").replace(",", "").strip()
        m = re.search(r"[\d.]+", cleaned)
        return float(m.group()) if m else 0.0