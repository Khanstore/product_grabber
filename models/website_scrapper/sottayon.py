from odoo import models, fields, api
from bs4 import BeautifulSoup
import json, logging, re, requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from odoo.exceptions import UserError


class importProductFromSottayon(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import Sottayon product from website'

    def sottayon_products(self):
        url = self.source_url
        if not url or 'sottayon.com' not in url:
            raise ValueError("Invalid Sottayon URL")

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
            response = session.get(url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            extractor = SottayonExtractor(soup)

            self.product_name         = extractor.get_title()
            self.face_value           = extractor.get_original_price()
            self.price                = extractor.get_current_price()
            self.ecommerce_description = extractor.get_description()
            self.image_url            = extractor.get_image_url()

            # Sottayon does not expose a structured spec table,
            # but we extract what we can from JSON-LD if present
            specs = extractor.get_specifications()
            self.authors    = specs.get('author', '')
            self.publishers = specs.get('publisher', '')
            self.isbn       = specs.get('isbn', '')
            self.language   = specs.get('language', '')

            print(f"✓ Successfully scraped: {self.product_name}")
            return True

        except Exception as e:
            logging.exception(f"Error scraping {url}: {e}")
            self.product_name = getattr(self, 'product_name', 'Unknown Product')
            self.price        = getattr(self, 'price', 0.0)
            return False


class SottayonExtractor:
    """Helper class to extract data from Sottayon WooCommerce product pages"""

    def __init__(self, soup):
        self.soup = soup

    def get_title(self):
        """Product title from <h1> inside .product_title or first <h1>"""
        h1 = (
            self.soup.find('h1', class_='product_title')
            or self.soup.find('h1')
        )
        return h1.get_text(strip=True) if h1 else 'Unknown Product'

    def get_original_price(self):
        """
        WooCommerce puts the crossed-out MRP inside <del>.
        e.g. ~~150.00৳~~ → <del><span class="woocommerce-Price-amount">150.00৳</span></del>
        """
        del_tag = self.soup.find('del')
        if del_tag:
            return self._parse_price(del_tag.get_text(strip=True))
        return 0.0

    def get_current_price(self):
        """
        WooCommerce puts the sale/current price inside <ins>.
        e.g. 113.00৳ → <ins><span class="woocommerce-Price-amount">113.00৳</span></ins>
        """
        ins_tag = self.soup.find('ins')
        if ins_tag:
            return self._parse_price(ins_tag.get_text(strip=True))

        # Fallback: if no discount, price is in .woocommerce-Price-amount directly
        price_span = self.soup.find('span', class_='woocommerce-Price-amount')
        if price_span:
            return self._parse_price(price_span.get_text(strip=True))
        return 0.0

    def get_description(self):
        """
        Description is inside .woocommerce-product-details__short-description
        or the main product summary div.
        """
        desc = (
            self.soup.find('div', class_='woocommerce-product-details__short-description')
            or self.soup.find('div', class_='product-short-description')
            or self.soup.find('div', id='tab-description')
        )
        return desc.decode_contents().strip() if desc else ''

    def get_image_url(self):
        """Main product image from JSON-LD or og:image meta tag."""
        # Try JSON-LD first
        script = self.soup.find('script', type='application/ld+json')
        if script:
            try:
                data = json.loads(script.string.strip())
                # data can be a list or dict
                entries = data if isinstance(data, list) else [data]
                for entry in entries:
                    if entry.get('image'):
                        img = entry['image']
                        return img[0] if isinstance(img, list) else img
            except Exception:
                pass

        # Fallback: og:image meta
        og_image = self.soup.find('meta', property='og:image')
        if og_image:
            return og_image.get('content', '')

        # Fallback: first product image tag
        img = self.soup.find('img', class_='wp-post-image')
        return img['src'] if img else ''

    def get_specifications(self):
        """
        Sottayon does not have a structured table like Rokomari.
        Extract what is available from JSON-LD (author, publisher, isbn).
        """
        specs = {}
        script = self.soup.find('script', type='application/ld+json')
        if script:
            try:
                data = json.loads(script.string.strip())
                entries = data if isinstance(data, list) else [data]
                for entry in entries:
                    if entry.get('author'):
                        author = entry['author']
                        specs['author'] = (
                            author.get('name', '') if isinstance(author, dict) else str(author)
                        )
                    if entry.get('publisher'):
                        pub = entry['publisher']
                        specs['publisher'] = (
                            pub.get('name', '') if isinstance(pub, dict) else str(pub)
                        )
                    if entry.get('isbn'):
                        specs['isbn'] = entry['isbn']
                    if entry.get('inLanguage'):
                        specs['language'] = entry['inLanguage']
            except Exception:
                pass
        return specs

    # --- helpers ---
    def _parse_price(self, price_text):
        if not price_text:
            return 0.0
        cleaned = price_text.replace('৳', '').replace('Tk', '').replace(',', '')
        match = re.search(r'[\d.]+', cleaned)
        return float(match.group()) if match else 0.0