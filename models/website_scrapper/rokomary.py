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

class importProductFromRokomari(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'import rokomari product from website'

    def rokomari_products(self):
        url = self.source_url
        if not url or 'rokomari.com' not in url:
            raise ValueError("Invalid Rokomari URL")

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
            session.get('https://www.rokomari.com/', timeout=10)  # warm-up
            response = session.get(url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            extractor = RokomariExtractor(soup)

            # self.product_name = extractor.get_title()
            self.face_value = extractor.get_original_price()
            self.price = extractor.get_current_price()
            self.stock_qty = extractor.get_stock_quantity()
            self.ecommerce_description = extractor.get_description()
            self.image_url = extractor.get_image_url()

            specs = extractor.get_specifications()
            self.product_name=specs.get('title','')
            self.isbn = specs.get('isbn', '')
            self.authors = specs.get('author', '')
            self.publishers = specs.get('publisher', '')
            self.pages = specs.get('pages', '')
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

class RokomariExtractor:
    """Helper class to extract data from Rokomari product pages"""

    def __init__(self, soup):
        self.soup = soup

    def get_title(self):
        selectors = [
            ("h1", {"class": "mb-2 fs-20 fw-600"}),
            ("h1", {"class": lambda x: x and "book-title" in x.lower()}),
            ("h1", {"itemprop": "name"}),
        ]
        return self._extract_text(selectors, "Unknown Product")

    def get_current_price(self):
        selectors = [
            ("div", {"class": "fs-16 opacity-60"}),
            ("span", {"class": "price-current"}),
            ("span", {"class": lambda x: x and "price" in x.lower()}),
        ]
        return self._parse_price(self._extract_text(selectors))

    def get_original_price(self):
        selectors = [
            ("del", {"class": "original-price"}),
            ("span", {"class": "price-original"}),
            ("del", {}),
        ]
        return self._parse_price(self._extract_text(selectors))

    def get_stock_quantity(self):
        stock_elem = self.soup.find("span", id="available-quantity")
        if stock_elem:
            stock_text = stock_elem.get_text(strip=True)
            match = re.search(r'\d+', stock_text)
            return int(match.group()) if match else 0
        return 0  # safer default

    def get_description(self):
        selectors = [
            ("div", {"class": "shortSummery_summeryText__ycsRa"}),
            ("div", {"class": lambda x: x and "summary" in x.lower()}),
            ("div", {"itemprop": "description"}),
        ]
        desc_elem = self._find_element(selectors)
        return desc_elem.decode_contents().strip() if desc_elem else ""

    def get_image_url(self):
        script_tag = self.soup.find("script", {"type": "application/ld+json"})

        if script_tag:
            data = json.loads(script_tag.string.strip())
            image_url = data.get("image")
            return image_url



    def get_specifications(self):
        specs = {}
        for row in self.soup.select("table tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                key = cells[0].get_text(strip=True).lower()
                value = cells[1].get_text(strip=True)

                if "title" in key:
                    specs['title'] = value
                if "isbn" in key:
                    specs['isbn'] = value
                elif "author" in key or "লেখক" in key:
                    specs['author'] = value
                elif "publisher" in key or "প্রকাশক" in key:
                    specs['publisher'] = value
                elif "page" in key or "পৃষ্ঠা" in key:
                    match = re.search(r'\d+', value)
                    specs['pages'] = match.group() if match else value
                elif "edition" in key or "সংস্করণ" in key:
                    specs['edition'] = value
                elif "language" in key or "ভাষা" in key:
                    specs['language'] = value
                elif "country" in key or "দেশ" in key:
                    specs['country'] = value
                elif "weight" in key:
                    match = re.search(r'[\d.]+', value)
                    specs['weight'] = float(match.group()) if match else 0.0
        return specs

    # --- helpers ---
    def _extract_text(self, selectors, default=""):
        for tag, attrs in selectors:
            elem = self.soup.find(tag, attrs)
            if elem:
                return elem.get_text(strip=True)
        return default

    def _find_element(self, selectors):
        for tag, attrs in selectors:
            elem = self.soup.find(tag, attrs)
            if elem:
                return elem
        return None

    def _parse_price(self, price_text):
        if not price_text:
            return 0.0
        match = re.search(r'[\d,]+\.?\d*', price_text.replace('৳', '').replace('Tk', ''))
        return float(match.group().replace(',', '')) if match else 0.0

    def _normalize_url(self, url):
        if not url:
            return None
        if url.startswith('//'):
            return 'https:' + url
        elif url.startswith('/'):
            return 'https://www.rokomari.com' + url
        return url