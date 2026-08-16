from odoo import models, fields, api
from bs4 import BeautifulSoup
import requests
import re
import logging
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from .base_extractor import BaseBookExtractor

_logger = logging.getLogger(__name__)


class ImportProductFromRokomari(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import Rokomari product from website'

    def rokomari_products(self):
        """Main method to trigger scraping for the provided URL."""
        url = self.source_url
        if not url or 'rokomari.com' not in url:
            return False

        # Setup session with retry strategy for network reliability
        session = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('https://', adapter)

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        }

        try:
            response = session.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            extractor = RokomariExtractor(soup)

            # Extracting data
            self.face_value = extractor.get_original_price()
            self.price = extractor.get_current_price()
            self.stock_qty = extractor.get_stock_quantity()
            self.ecommerce_description = extractor.get_description()
            self.image_url = extractor.get_image_url()
            self.product_name = extractor.get_title()

            specs = extractor.get_specifications()
            # self.product_name = specs.get('title', 'Unknown Product')
            self.isbn = specs.get('isbn', '')
            self.authors = extractor.get_author() or specs.get('author', '')
            self.publishers = extractor.get_publisher() or specs.get('publisher', '')
            self.pages = specs.get('pages', '')
            self.editions = specs.get('edition', '')
            self.language = specs.get('language', '')
            self.country = specs.get('country', '')
            self.weight = float(specs.get('weight', 0.0))

            _logger.info(f"Successfully scraped: {self.product_name}")
            return True

        except Exception as e:
            _logger.error(f"Failed to scrape {url}: {e}")
            return False


class RokomariExtractor(BaseBookExtractor):
    def __init__(self, soup):
        self.soup = soup

    def get_title(self):
        # 1. Try OpenGraph Meta Tag (Usually most reliable)
        og_title = self.soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"].strip()

        # 2. Try JSON-LD (Schema.org data)
        script = self.soup.find("script", {"type": "application/ld+json"})
        if script:
            try:
                import json
                data = json.loads(script.string.strip())
                # Handle cases where ld+json might be a list or a dict
                if isinstance(data, list): data = data[0]
                return data.get("name", "").strip()
            except:
                pass

        # 3. Fallback: Try specific H1 tag
        h1 = self.soup.find("h1", class_=lambda x: x and ("title" in x.lower() or "name" in x.lower()))
        if h1:
            return h1.get_text(strip=True)

        return "Unknown Product"

    def get_current_price(self):
        # Prefer the structured product:price:amount meta tag - it's
        # unambiguous (appears exactly once per page) and verified
        # correct against a real product page. The old DOM selector
        # (class 'sell-price') is kept as a fallback, but on its own it
        # was unreliable: .find() only returns the FIRST match on the
        # whole page, and this class name isn't necessarily unique to
        # the main product - "you may also like" / "frequently bought
        # together" widgets further down the same page can carry similar
        # markup, silently grabbing a different product's price instead.
        meta = self.soup.find("meta", property="product:price:amount")
        if meta and meta.get("content"):
            parsed = self._parse_price(meta["content"])
            if parsed:
                return parsed
        elem = self.soup.find(class_='sell-price')
        return self._parse_price(elem)

    def get_original_price(self):
        # There's no separate "original price" meta tag, but the
        # discount-percentage meta tag lets the original price be
        # derived reliably from the (already-verified) current price:
        # original = current / (1 - discount%). Falls back to the old
        # DOM selector, and finally to just the current price (no
        # discount), if a discount percentage isn't present.
        current = self.get_current_price()
        discount_meta = self.soup.find("meta", property="product:custom_label_2")
        if discount_meta and discount_meta.get("content") and current:
            match = re.search(r'(\d+(?:\.\d+)?)\s*%', discount_meta["content"])
            if match:
                discount_pct = float(match.group(1))
                if 0 < discount_pct < 100:
                    return round(current / (1 - discount_pct / 100), 2)
        elem = self.soup.find(class_='original-price')
        parsed = self._parse_price(elem)
        return parsed if parsed else current

    def get_stock_quantity(self):
        stock_elem = self.soup.find("span", id="available-quantity")
        if stock_elem:
            match = re.search(r'\d+', stock_elem.get_text())
            return int(match.group()) if match else 0
        return 0

    def get_description(self):
        """
        Extracts description from the specific element provided.
        """
        # Target the specific ID as it is the most reliable selector
        desc_elem = self.soup.find("div", {"id": "js--summary-description"})

        if desc_elem:
            # We use get_text with a separator to maintain readability of paragraphs
            # If you need to keep the HTML tags for Odoo (e.g. for an HTML field),
            # use str(desc_elem) instead of get_text()
            return desc_elem.get_text(separator='\n', strip=True)

        # Fallback to class search if ID is missing
        desc_elem_class = self.soup.select_one(".summary-description")
        if desc_elem_class:
            return desc_elem_class.get_text(separator='\n', strip=True)

        return ""

    def get_image_url(self):
        og_image = self.soup.find("meta", property="og:image")
        return og_image.get("content") if og_image else None

    def get_specifications(self):
        # Table-based extraction - the original approach. Kept as a
        # fallback: real usage showed this table sometimes missing from
        # what a plain (non-JS-executing) HTTP request receives, likely
        # because Rokomari's frontend is being migrated to a
        # JS-rendered architecture (confirmed elsewhere: their search
        # now runs on a separate next.rokomari.io subdomain) - some
        # page sections may only populate after client-side JavaScript
        # runs, which this scraper never executes.
        specs = {}
        for row in self.soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True).lower()
                val = cells[1].get_text(strip=True)
                if "isbn" in key:
                    specs['isbn'] = val
                elif "author" in key or "লেখক" in key:
                    specs['author'] = val
                elif "publisher" in key or "প্রকাশক" in key:
                    specs['publisher'] = val
                elif "page" in key or "পৃষ্ঠা" in key:
                    specs['pages'] = val
                elif "weight" in key:
                    match = re.search(r'[\d.]+', val)
                    specs['weight'] = float(match.group()) if match else 0.0
        return specs

    def get_publisher(self):
        """Prefer the structured product:brand meta tag - confirmed to
        hold the publisher name on a real product page, and (like other
        meta tags) part of the initial server-rendered HTML regardless
        of whether the specs table itself is present."""
        meta = self.soup.find("meta", property="product:brand")
        if meta and meta.get("content"):
            return meta["content"].strip()
        return ''

    def get_author(self):
        """The author byline is a link matching /book/author/<id> with
        NO slug after the id. Confirmed against a real page: Rokomari's
        own 'trending searches' widget (which can appear earlier in the
        DOM than the actual product content) links to author pages
        WITH a slug (e.g. /book/author/1/humayun-ahmed), while the
        current product's own byline, mini-cart preview, and footer
        'Top Writer' links all use the no-slug form
        (/book/author/47902). Filtering to the no-slug pattern and
        taking the first match reliably lands on the current book's
        author rather than an unrelated trending suggestion."""
        for a in self.soup.select('a[href*="/book/author/"]'):
            href = a.get('href', '')
            if re.search(r'/book/author/\d+/?$', href):
                text = a.get_text(strip=True)
                if text:
                    return text
        return ''

