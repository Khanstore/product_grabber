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
from odoo import models, fields, api
from odoo.exceptions import UserError
import base64


class ProductTemplate(models.Model):
    _inherit='product.template'

    publisher_link=fields.Char("publisher link")

class importProductFromWebsite(models.TransientModel):
    _name = 'import.product.from.website'
    _description = 'import product from website'

    # Define fields (if needed)
    categ_id=fields.Many2one("product.category",string="category")
    author_ids = fields.Many2many(
        'res.partner',
        'import_product_author_rel',
        'wizard_id',
        'partner_id',
        string="Authors"
    )

    publisher_ids = fields.Many2many(
        'res.partner',
        'import_product_publisher_rel',
        'wizard_id',
        'partner_id',
        string="Publishers"
    )
    authors=fields.Char("author (proposed)")
    publishers=fields.Char("Publisher (proposed)")
    target_url = fields.Char(string="target URL")
    target_db = fields.Char(string="database")
    user_name = fields.Char(string="user name")
    password = fields.Char(string="Password")
    source_url = fields.Char(string="Source")
    product_name = fields.Char(string="Product Name")
    image_url = fields.Char(string="Image URL")
    ecommerce_description = fields.Text(string="E-commerce Description")
    face_value = fields.Float(string="Printed Price")
    stock_qty = fields.Integer(string="Stock Quantity")
    price = fields.Float(string="Sale Price")
    isbn = fields.Char(string="ISBN")
    pages = fields.Integer(string="Pages")
    editions = fields.Char(string="Editions")
    publication_date = fields.Char(string="Publication Date")
    weight=fields.Float(string="weight")
    language=fields.Char(string="Language")
    country=fields.Char(string="Country")

    def create_remote_product(self):
        url = self.target_url  # Replace with your Odoo instance URL
        db = self.target_db # Replace with your Odoo database name
        username = self.user_name  # Replace with your Odoo username
        password = self.password  # Replace with your Odoo password

        # Establish connection
        common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
        models = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')

        # Authenticate
        uid = common.authenticate(db, username, password, {})
        response = requests.get(self.image_url)
        if response.status_code == 200:
            encoded_image = base64.b64encode(response.content).decode("utf-8")
            print(encoded_image[:200])  # print first 200 chars only
        else:
            print("Failed to download image:", response.status_code)
        if uid:
            print(f"Authenticated successfully with UID: {uid}")

            # Define product fields
            product_fields = {
                'name': self.product_name,
                'list_price': self.face_value,
                'standard_price': self.price,
                'type': 'consu',  # 'product' for storable, 'service' for service, 'consu' for consumable
                'is_published': True,
                'image_1920': encoded_image,
                'description_ecommerce': self.ecommerce_description,
                # 'default_code': 'RPCPROD001',
                # 'categ_id': self.categ_id,  # Replace with an existing product category ID
                # Add other relevant fields as needed
            }

            try:
                # Create the product template
                product_template_id = models.execute_kw(
                    db, uid, password,
                    'product.template', 'create',
                    [product_fields]
                )
                print(f"Product template created with ID: {product_template_id}")

                # Example of creating a product variant (if applicable)
                # This often involves creating attribute lines on the product template first
                # and then Odoo automatically generates variants or you can create them explicitly.
                # For a simple product without variants, 'product.template' is sufficient.

            except xmlrpc.client.Fault as e:
                print(f"Error creating product: {e}")

        else:
            print("Authentication failed.")



    def fetch_data(self):
        url= urlparse(self.source_url)
        host= url.hostname
        domain_part=host.split('.')
        i=0
        for part in domain_part:
            if domain_part[i]=='com':
                domain_name=domain_part[i-1]
            i=i+1

        if hasattr(self, '%s_products' % domain_name):
            self.publisher_ids=False
            self.author_ids=False
            return getattr(self, '%s_products' % domain_name)()
        else :
            raise UserError(f"Cannot import product data from {host}")

#
#
#     # def guardianpubs_products(self):
#     #     url = self.source_url
#     #     driver = webdriver.Chrome()  # or webdriver.Firefox()
#     #     driver.get(url)
#     #     wait = WebDriverWait(driver, 10)
#     #     time.sleep(3)  # wait for Angular to load content
#     #     soup = BeautifulSoup(driver.page_source, "html.parser")
#     #     stock_div = soup.find("div", class_="stock")
#     #
#     #     if stock_div:
#     #         stock_text = stock_div.get_text(strip=True)
#     #         self.stock_qty=int(re.findall(r'\d+', stock_text) [0])
#     #     name_div = soup.find("div", class_="product-title")
#     #     if name_div:
#     #         self.product_name=name_div.get_text(strip=True)
#     #     price_div = soup.find("div", class_="product-price")
#     #     if price_div:
#     #         price_text = price_div.get_text(strip=True)
#     #         # Mapping Bengali digits to English
#     #         bangla_to_english = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
#     #
#     #         # Find all Bengali numbers
#     #         matches = re.findall(r"[০-৯]+", price_text)
#     #
#     #         # Convert to English + int
#     #         prices = [int(m.translate(bangla_to_english)) for m in matches]
#     #
#     #         self.face_value=prices[0]
#     #         if len (prices) > 1:
#     #             self.price=prices[1]
#     #         else:
#     #             self.price=prices[0]
#     #
#     #
#     #     description_div = soup.find("div", class_="description")
#     #     if description_div:
#     #         self.ecommerce_description=description_div.get_text(strip=True)
#     #     img_tag = soup.select_one("div.product-image-box img")
#     #
#     #     if img_tag and img_tag.has_attr("src"):
#     #         self.image_url = img_tag["src"]
#     #
#     #     desc_button = wait.until(EC.presence_of_element_located((By.XPATH, '//button[contains(text(), "বিবরণ")]')))
#     #     driver.execute_script("arguments[0].click();", desc_button)
#     #
#     #     time.sleep(2)  # allow Angular to load content
#     #
#     #     soup = BeautifulSoup(driver.page_source, "html.parser")
#     #
#     #     isbn_td = None
#     #     for row in soup.select("div.specification table tr"):
#     #         th = row.find("th")
#     #         td = row.find("td")
#     #         if th and "ISBN" in th.get_text(strip=True):
#     #             self.isbn = td.get_text(strip=True)
#     #         if th and "Publish" in th.get_text(strip=True):
#     #             self.publication_date = td.get_text(strip=True)
#     #         if th and "Publisher" in th.get_text(strip=True):
#     #             self.publishers = td.get_text(strip=True)
#     #         if th and "Number of Pages" in th.get_text(strip=True):
#     #             self.pages = td.get_text(strip=True)
#     #
#     #         if th and "Edition" in th.get_text(strip=True):
#     #             self.editions = td.get_text(strip=True)
#     #
#     #         if th and "Title" in th.get_text(strip=True):
#     #             self.product_name = td.get_text(strip=True)
#     #     #set authors
#     #     for p in soup.find_all("p"):
#     #         if "লেখক" in p.get_text():
#     #             writer_name = p.find("a").get_text(strip=True)
#     #             self.authors=writer_name
#     #             break
#     #
#     #
#     #
#     #     driver.quit()
#
#
#     def khoshrozltd_products(self):
#         url = self.source_url
#         driver = webdriver.Chrome()  # or webdriver.Firefox()
#         driver.get(url)
#         wait = WebDriverWait(driver, 10)
#         time.sleep(3)  # wait for Angular to load content
#         soup = BeautifulSoup(driver.page_source, "html.parser")
#         stock_div = soup.find("span", id="available-quantity")
#
#         if stock_div:
#             stock_text = stock_div.get_text(strip=True)
#             self.stock_qty=int(stock_text)
#         name_div = soup.find("h1", class_="mb-2 fs-20 fw-600")
#         if name_div:
#             self.product_name=name_div.get_text(strip=True)
#         price_div = soup.find("div", class_="fs-16 opacity-60")
#         if price_div:
#             price_text = price_div.get_text(strip=True)
#             # Remove currency sign, commas, and text after slash
#             price = re.sub(r'[^\d.]', '', price_text)  # keep only digits and dot
#             if price:
#                 self.face_value = price
#         price_div = soup.find("strong", class_="h4 fw-700 text-primary")
#         if price_div:
#             price_text = price_div.get_text(strip=True)
#             match = re.search(r"[\d.]+", price_text)
#             if match:
#                 price = float(match.group())
#             self.price = price
#
#         description_div = soup.find("div", class_="mw-100 text-left")
#         if description_div:
#             self.ecommerce_description=description_div.decode_contents() # decode_context() get the inner html
#
#         img_tag = soup.find("img",role="presentation")
#
#         if img_tag and img_tag.has_attr("src"):
#             self.image_url = img_tag["src"]
#
#         desc_button = wait.until(EC.presence_of_element_located((By.XPATH, '//a[contains(text(), "Specification ")]')))
#         driver.execute_script("arguments[0].click();", desc_button)
#
#         time.sleep(2)  # allow Angular to load content
#
#         soup = BeautifulSoup(driver.page_source, "html.parser")
#
#         isbn_td = None
#         for row in soup.select("#spec-table tr"):
#             cells = row.find_all("td")
#             print(cells[0].get_text(strip=True).lower())
#             if len(cells) == 2 and "Author" in cells[0].get_text(strip=True):
#                 self.authors = cells[1].get_text(strip=True)
#             if len(cells) == 2 and "isbn" in cells[0].get_text(strip=True).lower():
#                 self.isbn = cells[1].get_text(strip=True)
#             if len(cells) == 2 and "Number of Pages" in cells[0].get_text(strip=True):
#                 self.pages = cells[1].get_text(strip=True)
#
#             if len(cells) == 2 and "Last Edition" in cells[0].get_text(strip=True):
#                 self.publication_date = cells[1].get_text(strip=True)
#
#         #     if th and "Publish" in th.get_text(strip=True):
#         #         self.publication_date = td.get_text(strip=True)
#         #     if th and "Title" in th.get_text(strip=True):
#         #         self.product_name = td.get_text(strip=True)
#         #
#
#
#
#         driver.quit()
#
#
#
#     def sottayon_products(self):
#         url = self.source_url
#         driver = webdriver.Chrome()  # or webdriver.Firefox()
#         driver.get(url)
#         wait = WebDriverWait(driver, 10)
#         time.sleep(3)  # wait for Angular to load content
#         soup = BeautifulSoup(driver.page_source, "html.parser")
#         stock_div = soup.find("span", id="available-quantity")
#
#         if stock_div:
#             stock_text = stock_div.get_text(strip=True)
#             self.stock_qty=int(stock_text)
#         name_div = soup.find("h1", class_="product_title")
#         if name_div:
#             self.product_name=name_div.get_text(strip=True)
#         del_price = soup.select_one("del .woocommerce-Price-amount bdi")
#         if del_price:
#             original_price = re.sub(r"[^\d.]", "", del_price.get_text())
#             self.face_value = float(original_price)
#
#         # Extract discounted price (inside <ins>)
#         ins_price = soup.select_one("ins .woocommerce-Price-amount bdi")
#         if ins_price:
#             discounted_price = re.sub(r"[^\d.]", "", ins_price.get_text())
#             self.price = float(discounted_price)
#
#
#
#         description_div = soup.find("div", class_="woocommerce-product-details__short-description")
#         if description_div:
#             self.ecommerce_description=description_div.decode_contents() # decode_context() get the inner html
#
#         img_tag = soup.find('img', class_='wp-post-image')
#         image_src = img_tag['src'] if img_tag else None
#
#         self.image_url = image_src
#         # # fields that is shown after pressing specification Button
#         # desc_button = wait.until(EC.presence_of_element_located((By.XPATH, '//button[contains(text(), "Specification")]')))
#         # driver.execute_script("arguments[0].click();", desc_button)
#         #
#         # time.sleep(2)  # allow Angular to load content
#         #
#         # soup = BeautifulSoup(driver.page_source, "html.parser")
#         #
#         # isbn_td = None
#         # for row in soup.select("table tr"):
#         #     cells = row.find_all("td")
#         #     if len(cells) == 2 and "isbn" in cells[0].get_text(strip=True).lower():
#         #         self.isbn = cells[1].get_text(strip=True)
#         #     if len(cells) == 2 and "Name" in cells[0].get_text(strip=True):
#         #         self.product_name = cells[1].get_text(strip=True)
#         #     if len(cells) == 2 and "Edition" in cells[0].get_text(strip=True):
#         #         self.publication_date = cells[1].get_text(strip=True)
#         #
#         #     if len(cells) == 2 and "Weight" in cells[0].get_text(strip=True):
#         #         weight_txt = cells[1].get_text(strip=True)
#         #         match = re.search(r"[\d.]+", weight_txt)
#         #         if match:
#         #             self.weight = float(match.group())
#         #
#         #
#         #     if len(cells) == 2 and "Author" in cells[0].get_text(strip=True):
#         #         self.authors = cells[1].get_text(strip=True)
#         #
#         #     if len(cells) == 2 and "Publisher" in cells[0].get_text(strip=True):
#         #         self.publishers = cells[1].get_text(strip=True)
#         #
#         #     if len(cells) == 2 and "No of Page" in cells[0].get_text(strip=True):
#         #         self.pages = cells[1].get_text(strip=True)
#
#
#
#
#
#
#         driver.quit()
#
#
#
#
#     def esquireelectronicsltd_products(self):
#         url = self.source_url
#         driver = webdriver.Chrome()  # or webdriver.Firefox()
#         driver.get(url)
#         wait = WebDriverWait(driver, 10)
#         time.sleep(3)  # wait for Angular to load content
#         soup = BeautifulSoup(driver.page_source, "html.parser")
#         extractor = esquireelectronicsltdExtractor(soup)
#
#         # self.product_name = extractor.get_title()
#         self.face_value = extractor.get_original_price()
#         self.price = extractor.get_current_price()
#         self.stock_qty = extractor.get_stock_quantity()
#         self.ecommerce_description = extractor.get_description()
#         self.image_url = extractor.get_image_url()
#         self.product_name=extractor.get_title()
#
#         specs = extractor.get_specifications()
#         self.isbn = specs.get('isbn', '')
#         self.authors = specs.get('author', '')
#         self.publishers = specs.get('publisher', '')
#         self.pages = specs.get('pages', '')
#         self.editions = specs.get('edition', '')
#         self.language = specs.get('language', '')
#         self.country = specs.get('country', '')
#         self.weight = specs.get('weight', 0.0)
#
#         print(f"✓ Successfully scraped: {self.product_name}")
#         return True
#
#     def gadgetandgear_products(self):
#         url = self.source_url
#         driver = webdriver.Chrome()  # or webdriver.Firefox()
#         driver.get(url)
#         wait = WebDriverWait(driver, 10)
#         time.sleep(3)  # wait for Angular to load content
#         soup = BeautifulSoup(driver.page_source, "html.parser")
#         extractor = gadgetandgearExtractor(soup)
#
#         # self.product_name = extractor.get_title()
#         self.face_value = extractor.get_original_price()
#         self.price = extractor.get_current_price()
#         self.stock_qty = extractor.get_stock_quantity()
#         self.ecommerce_description = extractor.get_description()
#         self.image_url = extractor.get_image_url()
#         self.product_name=extractor.get_title()
#
#         specs = extractor.get_specifications()
#         self.isbn = specs.get('isbn', '')
#         self.authors = specs.get('author', '')
#         self.publishers = specs.get('publisher', '')
#         self.pages = specs.get('pages', '')
#         self.editions = specs.get('edition', '')
#         self.language = specs.get('language', '')
#         self.country = specs.get('country', '')
#         self.weight = specs.get('weight', 0.0)
#
#         print(f"✓ Successfully scraped: {self.product_name}")
#         return True
#
#
#     def create_product(self):
#         vals={}
#         if len(self.author_ids)>0:
#             vals['author_ids']= [(6, 0, self.author_ids.ids)]
#
#
#         if len(self.categ_id) > 0:
#             vals['categ_id']=self.categ_id.id
#         vals['description_ecommerce']=self.ecommerce_description
#         vals['image_url_template']=self.image_url
#         vals['is_storable']=True
#         if self.isbn:
#             vals['isbn']=self.isbn
#         if self.publication_date:
#             vals['last_edition']=self.publication_date
#         vals['list_price']=self.price
#         vals['compare_list_price']=self.face_value
#         vals['name']=self.product_name
#         vals['pages']=self.pages
#         vals['publisher_link']=self.source_url
#         if len(self.publisher_ids)>0:
#             vals['publisher_ids']= [(6, 0, self.publisher_ids.ids)]
#         vals['weight']= self.weight
#
#
#
#
#         product=self.env['product.template'].create(vals)
#         return {
#             'type': 'ir.actions.act_window',
#             'res_model': 'product.template',
#             'res_id': product.id,
#             'view_mode': 'form',
#             'view_type': 'form',
#             'target': 'new',  # or 'new' for popup
#             'context': self.env.context,
#         }
#
#     def action_open_google_image_search(self):
#         self.ensure_one()
#         query = self.name or ""
#         return {
#             'type': 'ir.actions.act_url',
#             'url': f"https://www.google.com/search?tbm=isch&q={query}",
#             'target': 'new',  # open in new tab
#         }
#
#
#
#
#
#
#
# class gadgetandgearExtractor:
#     """Helper class to extract data from gadgetandgear product pages"""
#
#     def __init__(self, soup):
#         self.soup = soup
#
#     def get_title(self):
#         title_elem=self.soup.select_one('h1.Top_productName__i6Zp2')
#         if title_elem:
#             return title_elem.text
#
#
#     def get_current_price(self):
#         price_elem = self.soup.select_one('div.product-price')
#         if price_elem:
#             return self._parse_price(price_elem.find('h3').text)
#         return 0
#
#     def get_original_price(self):
#         price_elem = self.soup.select_one('span.ProductCard_price__t9DLm')
#         if price_elem:
#             return self._parse_price(price_elem.text)
#         return 0
#
#     def get_stock_quantity(self):
#         stock_elem = self.soup.find("span", id="available-quantity")
#         if stock_elem:
#             stock_text = stock_elem.get_text(strip=True)
#             match = re.search(r'\d+', stock_text)
#             return int(match.group()) if match else 0
#         return 0  # safer default
#
#     def get_description(self):
#         desc_elem=self.soup.select_one('div.Top_attribute__uEqlP').find_previous_sibling()
#         return desc_elem.decode_contents().strip() if desc_elem else ""
#
#     def get_image_url(self):
#         image_url=self.soup.select_one('img.Top_image__3b3Bd')['src']
#         return image_url
#
#
#
#     def get_specifications(self):
#         specs = {}
#         for row in self.soup.select("table tr"):
#             cells = row.find_all("td")
#             if len(cells) >= 2:
#                 key = cells[0].get_text(strip=True).lower()
#                 value = cells[1].get_text(strip=True)
#
#                 if "title" in key:
#                     specs['title'] = value
#                 if "isbn" in key:
#                     specs['isbn'] = value
#                 elif "author" in key or "লেখক" in key:
#                     specs['author'] = value
#                 elif "publisher" in key or "প্রকাশক" in key:
#                     specs['publisher'] = value
#                 elif "page" in key or "পৃষ্ঠা" in key:
#                     match = re.search(r'\d+', value)
#                     specs['pages'] = match.group() if match else value
#                 elif "edition" in key or "সংস্করণ" in key:
#                     specs['edition'] = value
#                 elif "language" in key or "ভাষা" in key:
#                     specs['language'] = value
#                 elif "country" in key or "দেশ" in key:
#                     specs['country'] = value
#                 elif "weight" in key:
#                     match = re.search(r'[\d.]+', value)
#                     specs['weight'] = float(match.group()) if match else 0.0
#         return specs
#
#     # --- helpers ---
#     def _extract_text(self, selectors, default=""):
#         for tag, attrs in selectors:
#             elem = self.soup.find(tag, attrs)
#             if elem:
#                 return elem.get_text(strip=True)
#         return default
#
#     def _find_element(self, selectors):
#         for tag, attrs in selectors:
#             elem = self.soup.find(tag, attrs)
#             if elem:
#                 return elem
#         return None
#
#     def _parse_price(self, price_text):
#         if not price_text:
#             return 0.0
#         match = re.search(r'[\d,]+\.?\d*', price_text.replace('৳', '').replace('Tk', ''))
#         return float(match.group().replace(',', '')) if match else 0.0
#
#     def _normalize_url(self, url):
#         if not url:
#             return None
#         if url.startswith('//'):
#             return 'https:' + url
#         elif url.startswith('/'):
#             return 'https://www.rokomari.com' + url
#         return url
#
# class esquireelectronicsltdExtractor:
#     """Helper class to extract data from gadgetandgear product pages"""
#
#     def __init__(self, soup):
#         self.soup = soup
#
#     def get_title(self):
#         title_elem=self.soup.select_one('div.product-title')
#         if title_elem:
#             return title_elem.text
#
#
#     def get_current_price(self):
#         price_elem = self.soup.select_one('div.product-price').find('h3')
#         if price_elem:
#             return self._parse_price(price_elem.text)
#         return 0
#
#     def get_original_price(self):
#         price_elem = self.soup.select_one('div.product-price').find('del')
#         if price_elem:
#             return self._parse_price(price_elem.text)
#         return 0
#
#     def get_stock_quantity(self):
#         stock_elem = self.soup.find("span", id="available-quantity")
#         if stock_elem:
#             stock_text = stock_elem.get_text(strip=True)
#             match = re.search(r'\d+', stock_text)
#             return int(match.group()) if match else 0
#         return 0  # safer default
#
#     def get_description(self):
#         price_elem=self.soup.select_one('div.product-price')
#         desc_elem = price_elem.find_next('div', class_='note-section')
#         return desc_elem.decode_contents().strip() if desc_elem else ""
#
#     def get_image_url(self):
#         image_url=self.soup.select_one('div.product-img-area').find('img')['src']
#         return image_url
#
#
#
#     def get_specifications(self):
#         specs = {}
#         for row in self.soup.select("table tr"):
#             cells = row.find_all("td")
#             if len(cells) >= 2:
#                 key = cells[0].get_text(strip=True).lower()
#                 value = cells[1].get_text(strip=True)
#
#                 if "title" in key:
#                     specs['title'] = value
#                 if "isbn" in key:
#                     specs['isbn'] = value
#                 elif "author" in key or "লেখক" in key:
#                     specs['author'] = value
#                 elif "publisher" in key or "প্রকাশক" in key:
#                     specs['publisher'] = value
#                 elif "page" in key or "পৃষ্ঠা" in key:
#                     match = re.search(r'\d+', value)
#                     specs['pages'] = match.group() if match else value
#                 elif "edition" in key or "সংস্করণ" in key:
#                     specs['edition'] = value
#                 elif "language" in key or "ভাষা" in key:
#                     specs['language'] = value
#                 elif "country" in key or "দেশ" in key:
#                     specs['country'] = value
#                 elif "weight" in key:
#                     match = re.search(r'[\d.]+', value)
#                     specs['weight'] = float(match.group()) if match else 0.0
#         return specs
#
#     # --- helpers ---
#     def _extract_text(self, selectors, default=""):
#         for tag, attrs in selectors:
#             elem = self.soup.find(tag, attrs)
#             if elem:
#                 return elem.get_text(strip=True)
#         return default
#
#     def _find_element(self, selectors):
#         for tag, attrs in selectors:
#             elem = self.soup.find(tag, attrs)
#             if elem:
#                 return elem
#         return None
#
#     def _parse_price(self, price_text):
#         if not price_text:
#             return 0.0
#         match = re.search(r'[\d,]+\.?\d*', price_text.replace('৳', '').replace('Tk', ''))
#         return float(match.group().replace(',', '')) if match else 0.0
#
#     def _normalize_url(self, url):
#         if not url:
#             return None
#         if url.startswith('//'):
#             return 'https:' + url
#         elif url.startswith('/'):
#             return 'https://www.rokomari.com' + url
#         return url
