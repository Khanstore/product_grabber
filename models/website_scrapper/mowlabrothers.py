from odoo import models, fields, api
from bs4 import BeautifulSoup
import requests, re, logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from odoo.exceptions import UserError
from .base_extractor import BaseBookExtractor


class importProductFromMowlaBrothers(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import product from MowlaBrothers.com website'

    def mowlabrothers_products(self):
        url = self.source_url
        if not url or 'mowlabrothers.com' not in url:
            raise ValueError("Invalid MowlaBrothers URL")

        session = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'bn-BD,bn;q=0.9,en-US;q=0.8,en;q=0.7',
            'Referer': 'https://mowlabrothers.com/',
        }
        session.headers.update(headers)

        try:
            response = session.get(url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            extractor = MowlaBrothersExtractor(soup)

            self.product_name          = extractor.get_title()
            self.face_value            = extractor.get_original_price()
            self.price                 = extractor.get_current_price()
            self.stock_qty             = extractor.get_stock_quantity()
            self.ecommerce_description = extractor.get_description()
            self.image_url             = extractor.get_image_url()

            specs = extractor.get_specifications()
            self.authors    = specs.get('authors', '')
            self.isbn       = specs.get('isbn', '')
            self.publishers = specs.get('publisher', '')
            self.pages      = specs.get('pages', '')
            self.editions   = specs.get('edition', '')
            self.language   = specs.get('language', '')
            self.country    = specs.get('country', '')
            self.category_text = specs.get('category', '')

            logging.info(f"✓ Successfully scraped: {self.product_name}")
            return True

        except Exception as e:
            logging.exception(f"Error scraping {url}: {e}")
            return False


class MowlaBrothersExtractor(BaseBookExtractor):
    """
    Extract product data from MowlaBrothers.com (WooCommerce / WordPress store).

    Page structure observed (product ID 7967):
    ─────────────────────────────────────────────────────────────────────
    <img>  product cover image

    <h2>  (empty title – filled from <title> or og:title)
    লেখক: হাবিবুল্লাহ সিরাজী        ← author (<a> link)
    বিষয়: কবিতা                     ← category
    প্রকাশনী: মাওলা ব্রাদার্স        ← publisher (<a> link)
    SKU: MBR-7967

    ~~TK.160~~  TK.128              ← original / sale price
    In Stock

    ── Specification tab (<table>) ──────────────────────────────────
    | শিরোনাম  |           |
    | লেখক     | হাবিবুল্লাহ সিরাজী |
    | প্রকাশনী | মাওলা ব্রাদার্স     |
    | ISBN     | 9789849185826      |
    | পৃষ্ঠা   | 64                 |
    | সংস্করণ  | 1st                |
    | দেশ      | বাংলাদেশ           |
    | ভাষা     | বাংলা              |
    ─────────────────────────────────────────────────────────────────────
    """

    # Bengali / English label aliases (lowercase, stripped)
    _LABEL_MAP = {
        # author
        "লেখক":             "author",
        "রচয়িতা":           "author",
        "সম্পাদক":           "author",
        "author":            "author",
        # publisher
        "প্রকাশনী":          "publisher",
        "প্রকাশক":           "publisher",
        "প্রকাশনা":          "publisher",
        "publisher":         "publisher",
        # edition
        "সংস্করণ":           "edition",
        "প্রথম প্রকাশিত":    "edition",
        "প্রথম প্রকাশ":      "edition",
        "প্রকাশকাল":         "edition",
        "edition":           "edition",
        "first published":   "edition",
        # pages
        "পৃষ্ঠা":            "pages",
        "পৃষ্ঠার সংখ্য":    "pages",
        "পৃষ্ঠা সংখ্যা":    "pages",
        "মোট পৃষ্ঠা":       "pages",
        "pages":             "pages",
        "page":              "pages",
        # isbn
        "isbn":              "isbn",
        # language
        "ভাষা":              "language",
        "language":          "language",
        # country
        "দেশ":               "country",
        "country":           "country",
        # category
        "বিষয়":              "category",
        "ধরন":               "category",
        "genre":             "category",
        "category":          "category",
        # format
        "বিন্যাস":           "format",
        "আবরণ":              "format",
        "format":            "format",
        "cover":             "format",
    }

    def __init__(self, soup: BeautifulSoup):
        self.soup = soup

    # ================================================================== #
    #  PUBLIC EXTRACTORS                                                   #
    # ================================================================== #

    def get_title(self) -> str:
        """
        MowlaBrothers uses WooCommerce; the product title is in:
        1. <h1 class="product_title"> or <h2> inside .product
        2. <title> tag  (format: "Book Name - Mowla Brothers")
        3. <meta property="og:title">
        """
        # WooCommerce standard
        h1 = self.soup.find("h1", {"class": lambda c: c and "product_title" in c})
        if h1:
            return h1.get_text(strip=True)

        # Fallback: any h1/h2 in the product wrapper
        product_wrap = self.soup.find("div", {"class": lambda c: c and "product" in (c or "")})
        if product_wrap:
            for tag in ("h1", "h2"):
                el = product_wrap.find(tag)
                if el:
                    text = el.get_text(strip=True)
                    if text:
                        return text

        # <title> tag: strip "- Mowla Brothers" suffix
        title_tag = self.soup.find("title")
        if title_tag:
            text = title_tag.get_text(strip=True)
            text = re.sub(r"\s*[-|–]\s*Mowla Brothers.*$", "", text, flags=re.IGNORECASE).strip()
            if text:
                return text

        og = self.soup.find("meta", {"property": "og:title"})
        if og and og.get("content"):
            return og["content"].strip()

        return "Unknown Product"

    def get_current_price(self) -> float:
        """
        WooCommerce sale price: the <ins> tag holds the active price.
        Falls back to the single price if no discount is present.

        Observed markup:
          <del> TK.160 </del>
          <ins> TK.128 </ins>
        """
        ins = self.soup.find("ins")
        if ins:
            return self._parse_price(ins.get_text(strip=True))

        # No sale — single price
        for span in self.soup.find_all("span", {"class": lambda c: c and "price" in (c or "")}):
            val = self._parse_price(span.get_text(strip=True))
            if val > 0:
                return val

        return 0.0

    def get_original_price(self) -> float:
        """
        WooCommerce compare-at (original) price: the <del> tag.
        Returns 0.0 if there is no discount.
        """
        del_tag = self.soup.find("del")
        if del_tag:
            return self._parse_price(del_tag.get_text(strip=True))
        return 0.0

    def get_stock_quantity(self) -> int:
        """
        1. WooCommerce stock badge: look for "In Stock" / "Out of Stock" text.
        2. Disabled add-to-cart button.
        """
        page_text = self.soup.get_text().lower()

        # Explicit out-of-stock signal
        if any(phrase in page_text for phrase in (
            "out of stock", "stock out", "স্টক নেই", "অনুপলব্ধ"
        )):
            return 0

        # WooCommerce stock status span
        stock_span = self.soup.find("p", {"class": lambda c: c and "stock" in (c or "")})
        if stock_span:
            stock_text = stock_span.get_text(strip=True).lower()
            if "out" in stock_text or "নেই" in stock_text:
                return 0
            return 1

        # "In Stock" text present → available
        if "in stock" in page_text:
            return 1

        # Disabled add-to-cart
        btn = self.soup.find(
            "button",
            {"class": lambda c: c and any(k in (c or "") for k in ("add_to_cart", "single_add_to_cart"))}
        )
        if btn:
            return 0 if btn.get("disabled") else 1

        return 1  # default: assume in stock

    def get_description(self) -> str:
        """
        WooCommerce places the product description in:
        1. #tab-description > div.woocommerce-Tabs-panel  (long description tab)
        2. .woocommerce-product-details__short-description  (short description)
        3. div#tab2 (site-specific tab ID observed on this store)
        """
        # Tab panel by ID
        for tab_id in ("tab-description", "tab2"):
            panel = self.soup.find(id=tab_id)
            if panel:
                text = panel.get_text(separator="\n", strip=True)
                if text:
                    return text

        # WooCommerce short description
        short = self.soup.find(
            "div", {"class": lambda c: c and "short-description" in (c or "")}
        )
        if short:
            text = short.get_text(separator="\n", strip=True)
            if text:
                return text

        return ""

    def get_image_url(self) -> str:
        """
        1. <meta property="og:image">
        2. WooCommerce product gallery: .woocommerce-product-gallery img
        3. First <img> in the product area
        """
        og = self.soup.find("meta", {"property": "og:image"})
        if og and og.get("content"):
            return og["content"]

        gallery = self.soup.find(
            "div", {"class": lambda c: c and "woocommerce-product-gallery" in (c or "")}
        )
        if gallery:
            img = gallery.find("img")
            if img:
                return (img.get("data-src") or img.get("src") or "")

        # Fallback: first img in product area
        product_area = (
            self.soup.find("div", {"class": lambda c: c and "product" in (c or "")})
            or self.soup.find("main")
        )
        if product_area:
            img = product_area.find("img")
            if img:
                return (img.get("data-src") or img.get("src") or "")

        return ""

    def get_specifications(self) -> dict:
        """
        MowlaBrothers exposes specs in a <table> inside the
        'স্পেসিফিকেশন' tab (observed as #tab1 / #tab-additional_information).

        Also reads the meta lines (লেখক, প্রকাশনী, বিষয়) from the
        product header area above the price.

        Cascade:
          A. Specification <table> (primary, cleanest)
          B. Header meta lines  (লেখক: … / প্রকাশনী: … anchors)
          C. Colon-separated text lines in product area
        """
        specs: dict = {}

        # ── A: Specification table ────────────────────────────────────
        for tab_id in ("tab1", "tab-additional_information", "tab-specification"):
            panel = self.soup.find(id=tab_id)
            if panel:
                for table in panel.find_all("table"):
                    self._parse_table(table, specs)
                if self._has_key_specs(specs):
                    break

        # Also scan any table with Bengali headers site-wide
        if not self._has_key_specs(specs):
            for table in self.soup.find_all("table"):
                self._parse_table(table, specs)

        # ── B: Header meta anchors / paragraphs ───────────────────────
        # e.g.  লেখক: <a>হাবিবুল্লাহ সিরাজী</a>
        self._extract_header_meta(specs)

        # ── C: Colon-separated text lines ────────────────────────────
        if not self._has_key_specs(specs):
            self._parse_colon_lines(specs)

        # Rename 'author' → 'authors' to match Odoo field convention
        if 'author' in specs and 'authors' not in specs:
            specs['authors'] = specs.pop('author')

        return specs

    # ================================================================== #
    #  PRIVATE HELPERS                                                     #
    # ================================================================== #

    def _extract_header_meta(self, specs: dict):
        """
        MowlaBrothers shows author, category, publisher as labelled
        lines in the product header, e.g.:

          লেখক: <a href="…">হাবিবুল্লাহ সিরাজী</a>
          প্রকাশনী: <a href="…">মাওলা ব্রাদার্স</a>

        These appear as plain text nodes or <p>/<div> siblings.
        """
        product_area = (
            self.soup.find("div", {"class": lambda c: c and "summary" in (c or "")})
            or self.soup.find("div", {"class": lambda c: c and "product" in (c or "")})
            or self.soup.find("main")
        )
        if not product_area:
            return

        for el in product_area.find_all(["p", "div", "span"]):
            text = el.get_text(separator=" ", strip=True)
            if ":" not in text or len(text) > 200:
                continue
            parts = text.split(":", 1)
            key = parts[0].strip().lower()
            val = parts[1].strip()
            if key and val:
                self._map_spec(specs, key, val)

    def _parse_table(self, table, specs: dict):
        """Parse <table> with <th>/<td> or two-column <td>/<td> rows."""
        for row in table.find_all("tr"):
            th = row.find("th")
            tds = row.find_all("td")
            if th and tds:
                self._map_spec(specs,
                               th.get_text(strip=True).lower(),
                               tds[0].get_text(strip=True))
            elif len(tds) >= 2:
                key = tds[0].get_text(strip=True).lower()
                val = tds[1].get_text(strip=True)
                if key and val:
                    self._map_spec(specs, key, val)

    def _parse_colon_lines(self, specs: dict):
        """Last-resort: scan 'Key : Value' plain text lines in product area."""
        for sel in [
            {"class": lambda c: c and "product" in (c or "")},
            {"id": "main"},
        ]:
            area = self.soup.find("div", sel)
            if not area:
                continue
            for line in area.get_text(separator="\n").splitlines():
                if ":" not in line:
                    continue
                parts = line.split(":", 1)
                if len(parts) == 2:
                    key = parts[0].strip().lower()
                    val = parts[1].strip()
                    if key and val and len(key) < 40:
                        self._map_spec(specs, key, val)

    def _map_spec(self, specs: dict, key: str, value: str):
        """Map a normalised key to its canonical field using _LABEL_MAP."""
        if not key or not value:
            return
        value = value.strip().lstrip(":").strip()
        if not value:
            return

        field = self._LABEL_MAP.get(key)

        if not field:
            for lbl, fld in self._LABEL_MAP.items():
                if lbl in key:
                    field = fld
                    break

        if not field:
            return

        if field == "pages":
            m = re.search(r"\d+", value)
            specs.setdefault(field, m.group() if m else value)
        elif field == "isbn":
            clean = re.sub(r"[^\d\-X]", "", value)
            specs.setdefault(field, clean or value)
        else:
            specs.setdefault(field, value)

    def _has_key_specs(self, specs: dict) -> bool:
        """True when at least one core field is populated."""
        return bool(
            specs.get('author') or specs.get('authors') or
            specs.get('isbn') or specs.get('pages') or
            specs.get('publisher')
        )

