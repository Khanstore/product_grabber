from odoo import models, fields, api
from bs4 import BeautifulSoup
import requests, re, logging, json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from odoo.exceptions import UserError
from .base_extractor import BaseBookExtractor


class importProductFromAnannyaBooks(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import product from AnannyaBooks.com website'

    def anannyabooks_products(self):
        url = self.source_url
        if not url or 'anannyabooks.com' not in url:
            raise ValueError("Invalid AnannyaBooks URL")

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
            'Referer': 'https://www.anannyabooks.com/',
        }
        session.headers.update(headers)

        try:
            session.get('https://www.anannyabooks.com/', timeout=10)
            response = session.get(url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            extractor = AnannyaBooksExtractor(soup)

            self.product_name = extractor.get_title()
            self.face_value   = extractor.get_printed_price()
            self.price        = extractor.get_sale_price()
            self.stock_qty    = extractor.get_stock_quantity()
            self.ecommerce_description = extractor.get_description()
            self.image_url    = extractor.get_image_url()

            specs = extractor.get_specifications()
            self.authors    = specs.get('লেখক', '')
            self.isbn       = specs.get('আইএসবিএন', '')
            self.publishers = specs.get('প্রকাশনী', '')
            self.pages      = specs.get('পৃষ্ঠা', '')
            self.editions   = specs.get('সংস্করণ', '')
            self.language   = specs.get('ভাষা', '')
            self.country    = specs.get('country', '')

            logging.info(f"✓ Successfully scraped: {self.product_name}")
            return True

        except Exception as e:
            logging.exception(f"Error scraping {url}: {e}")
            return False


class AnannyaBooksExtractor(BaseBookExtractor):
    """Extract product data from AnannyaBooks.com (WooCommerce) product pages."""

    def __init__(self, soup: BeautifulSoup):
        self.soup = soup

    # ------------------------------------------------------------------ #
    #  Public extractors                                                   #
    # ------------------------------------------------------------------ #

    def get_title(self) -> str:
        """
        WooCommerce standard: <h1 class="product_title entry-title">…</h1>
        Fallback: <meta property="og:title"> or <title> tag.
        """
        # WooCommerce default
        elem = self.soup.find("h1", {"class": lambda c: c and "product_title" in c})
        if elem:
            return elem.get_text(strip=True)

        # og:title fallback
        og = self.soup.find("meta", {"property": "og:title"})
        if og and og.get("content"):
            return og["content"].strip()

        # <title> tag as last resort
        title_tag = self.soup.find("title")
        if title_tag:
            # Strip trailing " - AnannyaBooks" or similar site suffix
            raw = title_tag.get_text(strip=True)
            return re.split(r"\s*[–\-|]\s*", raw)[0].strip()

        return "Unknown Product"

    def get_sale_price(self) -> float:
        """
        WooCommerce sale price (active/discounted price):
        <ins><span class="woocommerce-Price-amount amount">
               <bdi><span class="woocommerce-Price-currencySymbol">৳</span>360.00</bdi>
             </span></ins>
        Falls back to regular price if no sale is active.
        """
        price_div = self.soup.find("div", {'class': "price-wrap product-details-price-wrap"})
        if price_div:
            amount = price_div.find("span", {"class": "current-price"})
            if amount:
                return self._parse_price(amount.get_text(strip=True))
        return 0.0
    def get_authors(self) :
        author_div =self.soup.find("div",{'class':"product-details-list"})
        if author_div:
            author = author_div.find("span", {"class": "previ-price"})
            if amount:
                return self._parse_price(amount.get_text(strip=True))
        return 0.0
    def get_printed_price(self) -> float:
        """
        Original crossed-out price sits inside <del>:
        <del><span class="woocommerce-Price-amount amount">…450.00…</span></del>
        Returns 0.0 when no discount is present.
        """
        price_div =self.soup.find("div",{'class':"price-wrap product-details-price-wrap"})
        if price_div:
            amount = price_div.find("span", {"class": "previ-price"})
            if amount:
                return self._parse_price(amount.get_text(strip=True))
        return 0.0

    def get_stock_quantity(self) -> int:
        """
        WooCommerce stock indicator patterns:
          • <p class="stock in-stock">In stock</p>          → 1
          • <p class="stock out-of-stock">Out of stock</p>  → 0
          • <p class="stock in-stock">5 in stock</p>        → 5
        Fallback: check if Add-to-Cart button is present (implies in stock).
        """
        stock_elem = self.soup.find("p", {"class": lambda c: c and "stock" in c})
        if stock_elem:
            classes = stock_elem.get("class", [])
            text = stock_elem.get_text(strip=True).lower()

            if "out-of-stock" in classes:
                return 0

            if "in-stock" in classes:
                # Try to extract a numeric quantity from text, e.g. "5 in stock"
                m = re.search(r"(\d+)\s+in\s+stock", text)
                return int(m.group(1)) if m else 1

        # Fallback: button present → assume 1 in stock
        add_btn = self.soup.find("button", {"class": lambda c: c and "single_add_to_cart_button" in c})
        if add_btn and "disabled" not in add_btn.get("class", []):
            return 1

        return 0

    def get_description(self) -> str:

        # Short description (sidebar summary)
        description = self.soup.find("div", {"class": "product-details-initial-text"})
        if description:
            text = description.get_text(strip=True)
            if text:
                return text



    def get_image_url(self) -> str:
        """
        Primary product image from <meta property="og:image">.
        Fallback: WooCommerce product gallery main image
        <figure class="woocommerce-product-gallery__image"> … <img> …</figure>
        """
        og = self.soup.find("meta", {"property": "og:image"})
        if og and og.get("content"):
            return og["content"]

        # WooCommerce gallery main image
        figure = self.soup.find("figure", {"class": lambda c: c and "woocommerce-product-gallery__image" in c})
        if figure:
            img = figure.find("img")
            if img:
                # Prefer full-size src over scaled thumbnail
                src = img.get("data-large_image") or img.get("src") or ""
                return src

        return ""

    def get_specifications(self) -> dict:
        """
        AnannyaBooks displays book details in one of two ways:

        1. WooCommerce "Additional Information" tab — attribute table:
           <div id="tab-additional_information">
             <table class="woocommerce-product-attributes shop_attributes">
               <tr>
                 <th class="woocommerce-product-attributes-item__label">লেখক / Author</th>
                 <td class="woocommerce-product-attributes-item__value">…</td>
               </tr>
               …
             </table>
           </div>

        2. Custom detail table anywhere in the product content with <th>/<td> or <td>/<td> rows.

        Mapped fields:
          লেখক / author / writer    → author
          প্রকাশক / publisher       → publisher
          isbn                      → isbn
          ভাষা / language           → language
          দেশ / country             → country
          পৃষ্ঠা / pages            → pages
          সংস্করণ / edition         → edition
          বিন্যাস / format          → format
        """
        book_data = {}

        list_items = self.soup.select('.product-details-list-items li')
        for li in list_items:
            # Split text by ':' to separate label from value
            text = li.get_text(separator="|", strip=True)
            if ":" in text:
                parts = text.split(":")
                key = parts[0].replace("|", "").strip()
                value = parts[1].replace("|", "").strip()
                book_data[key] = value

        return book_data

    # ------------------------------------------------------------------ #
    #  Private helpers                                                     #
    # ------------------------------------------------------------------ #

    def _extract_regular_price(self) -> float:
        """Read the standard (non-sale) WooCommerce price span."""
        amount = self.soup.find("span", {"class": "woocommerce-Price-amount"})
        if amount:
            return self._parse_price(amount.get_text(strip=True))
        return 0.0

    def _map_spec(self, specs: dict, key: str, value: str):
        """Map a raw table key → canonical spec field."""
        if not value:
            return

        if any(k in key for k in ("লেখক", "writer", "author")):
            specs.setdefault("author", value)
        elif any(k in key for k in ("প্রকাশক", "publisher", "publication")):
            specs.setdefault("publisher", value)
        elif "isbn" in key:
            specs.setdefault("isbn", value)
        elif any(k in key for k in ("ভাষা", "language")):
            specs.setdefault("language", value)
        elif any(k in key for k in ("দেশ", "country")):
            specs.setdefault("country", value)
        elif any(k in key for k in ("পৃষ্ঠা", "page")):
            m = re.search(r"\d+", value)
            specs.setdefault("pages", m.group() if m else value)
        elif any(k in key for k in ("সংস্করণ", "edition", "first published", "প্রথম প্রকাশ")):
            specs.setdefault("edition", value)
        elif "format" in key or "বিন্যাস" in key:
            specs.setdefault("format", value)

