from odoo import models, fields, api
from selenium import webdriver
from selenium.webdriver.support.ui import WebDriverWait
from bs4 import BeautifulSoup
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
import json,xmlrpc
import requests
import re,logging
import time
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import urllib.parse
from urllib.parse import urlparse
from odoo.exceptions import UserError
import base64
from .base_extractor import BaseBookExtractor

class importProductFromPBS(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'import rokomari product from website'

    def guardianpubs_products(self):
        url = self.source_url
        if not url or 'guardianpubs.com' not in url:
            raise ValueError("Invalid guardian publication  URL")

        # Session with retries
        session = requests.Session()
        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        session.mount('http://', adapter)
        session.mount('https://', adapter)

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        session.headers.update(headers)

        try:
            session.get('https://www.guardianpubs.com/', timeout=10)  # warm-up
            response = session.get(url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            extractor = guardianpubsExtractor(soup)

            # self.product_name = extractor.get_title()
            # self.face_value = extractor.get_original_price()
            # self.price = extractor.get_current_price()
            # self.stock_qty = extractor.get_stock_quantity()
            self.ecommerce_description = extractor.get_description()
            self.image_url = extractor.get_image_url()

            specs = extractor.get_specifications()
            self.authors = specs.get('author', '')
            self.product_name = specs.get('title', '')
            self.isbn = specs.get('isbn', '')
            self.publishers = specs.get('publisher', '')
            # self.pages = specs.get('pages', 1)
            self.editions = specs.get('edition', '')
            self.language = specs.get('language', '')
            self.country = specs.get('country', '')
            self.weight = specs.get('weight', 0.0)

            print(f"✓ Successfully scraped: {self.product_name}")
            return True

        except Exception as e:
            logging.exception(f"Error scraping {url}: {e}")
            self.product_name = getattr(self, "product_name", "Unknown Product")
            self.price = getattr(self, "price", 0.0)
            self.stock_qty = getattr(self, "stock_qty", 0)
            return False



class guardianpubsExtractor(BaseBookExtractor):
    """Helper class to extract data from PBS product pages"""

    def __init__(self, soup: BeautifulSoup):
        self.soup = soup

    def get_title(self):
        """Extract book title"""
        selectors = [
            ("h1", {"class": lambda x: x and "text-xl" in x}),  # usually main title
            ("h1", {"itemprop": "name"}),
        ]
        return self._extract_text(selectors, "Unknown Title")

    def get_original_price(self):
        """Extract current and original price"""
        # Prices are inside <p class="price"> with <ins> and <del>
        price_container = self.soup.select_one("p del")
        if price_container:
            return self._parse_price(price_container.get_text(strip=True))
        return 0
    def get_current_price(self):
        """Extract current and original price"""
        price_container = self.soup.select_one("p del").parent.parent.find('h5')
        if price_container:
            return self._parse_price(price_container.get_text(strip=True))
        return 0

    def get_description(self):
        """Extract book description (বই সংক্ষেপ)"""
        description_div = self.soup.find("div", class_="description")
        if description_div:
            self.ecommerce_description=description_div.get_text(strip=True)



        heading = self.soup.find("h5", string=lambda t: t and "বই সংক্ষেপ" in t)
        if heading:
            desc_p = heading.find_parent().find_next("p")
            if desc_p:
                return desc_p.get_text(" ", strip=True)
        return ""

    def get_authors(self):
        """Extract book description (বই সংক্ষেপ)"""
        author_tag = self.soup.find('span', string='লেখক')
        if author_tag:
            author_name = author_tag.parent.find_parent().find_next("p")
            if desc_p:
                return desc_p.get_text(" ", strip=True)
        return ""

    def get_image_url(self):
        """Extract main book cover image"""
        img = self.soup.select_one("div.grid img")
        if img:
            src ="https://pbs.com.bd" + img.get("src")

            # Parse the URL
            parsed = urllib.parse.urlparse(src)
            query = urllib.parse.parse_qs(parsed.query)

            # Extract the 'url' parameter and decode it
            if "url" in query:
                clean_url = urllib.parse.unquote(query["url"][0])
                return clean_url

    def get_specifications(self):
        """Extract all specifications from details table"""
        specs = {}
        table = self.soup.find("table")
        if not table:
            return specs

        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True).lower()
                value = cells[-1].get_text(strip=True)

                if "isbn" in key:
                    specs["isbn"] = value
                # elif "Translator" in key or "অনুবাদক" in key:
                #     specs["author"] = value
                # elif "author" in key or "লেখক" in key:
                #     specs["author"] = value
                elif "publisher" in key or "প্রকাশক" in key:
                    specs["publisher"] = value
                elif "title" in key:
                    specs["title"] = value
                elif "edition" in key or "সংস্করণ" in key:
                    specs["edition"] = value
                elif "number of pages" in key or "পৃষ্ঠা" in key:
                    match = re.search(r'\d+', value)
                    specs["pages"] = match.group() if match else value
                elif "language" in key or "ভাষা" in key:
                    specs["language"] = value
                elif "country" in key or "দেশ" in key:
                    specs["country"] = value

        return specs

    # ------------------- Helpers ------------------- #
    def _extract_text(self, selectors, default=""):
        for tag, attrs in selectors:
            elem = self.soup.find(tag, attrs)
            if elem:
                return elem.get_text(strip=True)
        return default

