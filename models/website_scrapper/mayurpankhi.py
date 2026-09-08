from odoo import models, fields, api
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from bs4 import BeautifulSoup
import re, logging, time
from odoo.exceptions import UserError
from .base_extractor import BaseBookExtractor


class importProductFromMayurpankhi(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import Mayurpankhi product from website'

    def mayurpankhi_products(self):
        url = self.source_url
        if not url or 'mayurpankhi.com' not in url:
            raise ValueError("Invalid Mayurpankhi URL")

        driver = None
        try:
            options = Options()
            options.add_argument('--headless')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument(
                'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/120.0.0.0 Safari/537.36'
            )

            driver = webdriver.Chrome(options=options)
            driver.get(url)

            # Wait until the product title is visible
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.TAG_NAME, 'h1'))
            )
            time.sleep(2)  # allow Wix JS to fully paint the page

            soup = BeautifulSoup(driver.page_source, 'html.parser')
            extractor = MayurpankhiExtractor(soup)

            self.product_name          = extractor.get_title()
            self.face_value            = extractor.get_original_price()
            self.price                 = extractor.get_current_price()
            self.ecommerce_description = extractor.get_description()
            self.image_url             = extractor.get_image_url()

            specs = extractor.get_specifications()
            self.authors     = specs.get('author', '')
            self.isbn        = specs.get('isbn', '')
            self.pages       = specs.get('pages', '')
            self.editions    = specs.get('edition', '')
            self.language    = specs.get('language', '')
            self.publishers  = specs.get('publisher', 'Mayurpankhi')

            print(f"✓ Successfully scraped: {self.product_name}")
            return True

        except Exception as e:
            logging.exception(f"Error scraping {url}: {e}")
            self.product_name = getattr(self, 'product_name', 'Unknown Product')
            self.price        = getattr(self, 'price', 0.0)
            return False

        finally:
            if driver:
                driver.quit()


class MayurpankhiExtractor(BaseBookExtractor):
    """Helper class to extract data from Mayurpankhi (Wix) product pages"""

    def __init__(self, soup):
        self.soup = soup
        self._page_text = soup.get_text(separator=' ', strip=True)

    def get_title(self):
        """Product title from first <h1>"""
        h1 = self.soup.find('h1')
        return h1.get_text(strip=True) if h1 else 'Unknown Product'

    def get_original_price(self):
        """
        Wix renders price as plain text blocks:
        'BDT 450.00 Regular Price'
        """
        match = re.search(
            r'BDT\s*([\d,]+\.?\d*)\s*Regular\s*Price',
            self._page_text,
            re.IGNORECASE
        )
        if match:
            return float(match.group(1).replace(',', ''))
        return 0.0

    def get_current_price(self):
        """
        Wix renders sale price as:
        'BDT 338.00Sale Price'
        """
        match = re.search(
            r'BDT\s*([\d,]+\.?\d*)\s*Sale\s*Price',
            self._page_text,
            re.IGNORECASE
        )
        if match:
            return float(match.group(1).replace(',', ''))

        # Fallback: if no discount, just grab the only price
        match = re.search(r'BDT\s*([\d,]+\.?\d*)', self._page_text)
        if match:
            return float(match.group(1).replace(',', ''))
        return 0.0

    def get_description(self):
        """
        Description block sits between the price and the spec list.
        It's a <pre> or <p> block with the book summary text.
        """
        # Wix often wraps description text in <pre> or a data-testid div
        pre = self.soup.find('pre')
        if pre:
            return pre.get_text(strip=True)

        # Fallback: look for a long <p> that isn't nav text
        for p in self.soup.find_all('p'):
            text = p.get_text(strip=True)
            if len(text) > 80:
                return text
        return ''

    def get_image_url(self):
        """
        Main product image is hosted on wixstatic.com CDN.
        Pick the highest-quality (largest w= parameter) image.
        """
        best_url   = ''
        best_width = 0

        for img in self.soup.find_all('img'):
            src = img.get('src', '')
            if 'wixstatic.com' not in src or 'blur' in src:
                continue
            # skip tiny thumbnails
            width_match = re.search(r'w_(\d+)', src)
            width = int(width_match.group(1)) if width_match else 0
            if width > best_width:
                best_width = width
                best_url   = src

        return best_url

    def get_specifications(self):
        """
        Wix renders specs as <li> items, each containing an <h2> label
        and a following text node / <p> with the value.

        Structure seen on page:
          <li>
            <h2>গল্প</h2>        ← label
            <p>ইভান জাগীরদার</p> ← value
          </li>
        """
        specs = {}
        label_map = {
            'গল্প':        'author',       # story / author
            'লেখক':        'author',
            'অলংকরণ':      'illustrator',
            'অনুবাদ':      'translator',
            'সম্পাদনা':    'editor',
            'isbn':        'isbn',
            'পৃষ্ঠা':      'pages',
            'প্রকাশকাল':   'edition',
            'বইয়ের ধরন':  'book_type',
            'ভাষা':        'language',
            'প্রকাশনী':    'publisher',
        }

        for li in self.soup.find_all('li'):
            h2 = li.find('h2')
            if not h2:
                continue
            label_raw = h2.get_text(strip=True).lower()
            value_tag = li.find('p') or li.find('span')
            value = value_tag.get_text(strip=True) if value_tag else ''

            if not value:
                # value may be a text node right after h2
                value = h2.next_sibling
                value = value.strip() if isinstance(value, str) else ''

            for keyword, field in label_map.items():
                if keyword in label_raw:
                    specs[field] = value
                    break

        # Normalize pages to integer string
        if 'pages' in specs:
            m = re.search(r'\d+', specs['pages'])
            specs['pages'] = m.group() if m else specs['pages']

        return specs

