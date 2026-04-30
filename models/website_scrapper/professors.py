from odoo import models, fields, api
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from bs4 import BeautifulSoup
import json, logging, re, time
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class ImportProductFromProfessorsProkashon(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import Professors Prokashon product from website'

    def professorsprokashon_products(self):
        url = self.source_url
        if not url or 'professorsprokashon.com' not in url:
            raise ValueError("Invalid Professors Prokashon URL")

        driver = None
        try:
            driver = _make_driver()

            # Strategy 1: intercept the XHR/fetch API call Vue.js makes
            api_data = _load_with_network_capture(driver, url)
            if api_data:
                _logger.info("professorsprokashon: data from CDP network capture")
                self._fill_from_api(api_data)
                return True

            # Strategy 2: parse the rendered DOM with Selenium + BS4
            _logger.info("professorsprokashon: falling back to DOM scraping")
            soup = BeautifulSoup(driver.page_source, 'html.parser')
            ex   = ProfessorsProkashonExtractor(soup, driver)

            self.product_name          = ex.get_title()
            self.face_value            = ex.get_original_price()
            self.price                 = ex.get_current_price()
            self.stock_qty             = ex.get_stock_quantity()
            self.ecommerce_description = ex.get_description()
            self.image_url             = ex.get_image_url()

            specs = ex.get_specifications()
            self.isbn       = specs.get('isbn', '')
            self.authors    = specs.get('author', '')
            self.publishers = specs.get('publisher', "প্রফেসর'স প্রকাশন")
            self.pages      = specs.get('pages', '')
            self.editions   = specs.get('edition', '')
            self.language   = specs.get('language', '')
            self.country    = specs.get('country', '')
            self.weight     = specs.get('weight', 0.0)

            _logger.info(f"Scraped: {self.product_name}")
            return True

        except Exception as e:
            _logger.exception(f"professorsprokashon scrape failed for {url}: {e}")
            self.product_name = getattr(self, 'product_name', 'Unknown Product')
            self.price        = getattr(self, 'price', 0.0)
            self.stock_qty    = getattr(self, 'stock_qty', 0)
            return False

        finally:
            if driver:
                driver.quit()

    def _fill_from_api(self, data: dict):
        """Fill Odoo fields from a raw API JSON dict."""
        def g(*keys, default=''):
            for k in keys:
                v = data.get(k)
                if v not in (None, '', [], {}):
                    return v
            return default

        self.product_name = g('name', 'title', 'book_name', 'book_title')
        self.isbn         = g('isbn', 'isbn13', 'isbn10')
        self.authors      = g('author', 'author_name', 'writer', 'writer_name')
        self.publishers   = g('publisher', 'publisher_name', 'publication',
                              default="প্রফেসর'স প্রকাশন")
        self.pages        = str(g('pages', 'page', 'page_count', 'num_pages', default=''))
        self.editions     = g('edition', 'edition_no')
        self.language     = g('language', 'lang')
        self.country      = g('country', 'country_of_origin')
        self.ecommerce_description = g('description', 'short_description', 'summary', 'details')
        self.image_url    = g('image', 'image_url', 'cover', 'cover_image', 'thumbnail')

        offers    = data.get('offers') or data.get('price_info') or {}
        raw_price = (g('price', 'sell_price', 'selling_price', 'discount_price', 'sale_price')
                     or offers.get('price') or offers.get('sell_price') or 0)
        raw_mrp   = (g('mrp', 'regular_price', 'original_price', 'face_value', 'list_price')
                     or offers.get('regular_price') or offers.get('mrp') or 0)

        self.price      = _to_float(raw_price)
        self.face_value = _to_float(raw_mrp)
        self.weight     = _to_float(g('weight', 'book_weight', default=0))

        stock = g('stock', 'quantity', 'stock_quantity', 'available_quantity', default=0)
        avail = str(g('availability', 'in_stock', 'is_available', default='')).lower()
        if stock:
            self.stock_qty = int(_to_float(stock))
        elif avail in ('true', '1', 'instock', 'in_stock', 'available'):
            self.stock_qty = 1
        else:
            self.stock_qty = 0


# ---------------------------------------------------------------------------
# Network capture
# ---------------------------------------------------------------------------

def _load_with_network_capture(driver, url: str):
    """
    Capture every JSON API response the Vue.js page makes via XHR/fetch.
    Uses Chrome performance logs (works without CDP listener support).
    Returns a book-like dict or None.
    """
    driver.get(url)

    # Wait for Vue to mount and make its data request
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, 'h1'))
        )
    except Exception:
        pass
    time.sleep(3)

    # Read performance logs (contains network request/response info)
    try:
        logs = driver.get_log('performance')
    except Exception:
        return None

    request_ids = []
    for entry in logs:
        try:
            msg = json.loads(entry['message'])['message']
            if msg.get('method') == 'Network.responseReceived':
                params = msg.get('params', {})
                resp   = params.get('response', {})
                mime   = resp.get('mimeType', '')
                rurl   = resp.get('url', '')
                if 'json' in mime and _looks_like_api_url(rurl):
                    request_ids.append(params.get('requestId'))
        except Exception:
            continue

    for req_id in request_ids:
        try:
            body = driver.execute_cdp_cmd(
                'Network.getResponseBody', {'requestId': req_id}
            ).get('body', '')
            data = json.loads(body)
            # Unwrap Laravel resource wrapper {"data": {...}}
            if isinstance(data, dict) and 'data' in data and isinstance(data['data'], dict):
                data = data['data']
            if _looks_like_book(data):
                return data
        except Exception:
            continue

    return None


def _looks_like_api_url(url: str) -> bool:
    return bool(re.search(r'/api/|\.json(\?|$)', url, re.I))


def _looks_like_book(data) -> bool:
    if not isinstance(data, dict):
        return False
    book_keys = {'name', 'title', 'book_name', 'book_title',
                 'isbn', 'author', 'writer', 'publisher',
                 'price', 'pages', 'description', 'image', 'cover'}
    return len(book_keys & set(data.keys())) >= 3


# ---------------------------------------------------------------------------
# DOM extractor (Selenium-first, BeautifulSoup-assisted)
# ---------------------------------------------------------------------------

class ProfessorsProkashonExtractor:
    BASE = 'https://professorsprokashon.com'

    TITLE_CSS = [
        'h1',
        '[class*="book-title"]', '[class*="product-title"]',
        '[class*="title"] h1',   '[class*="title"] h2',
        '[class*="name"]',
    ]
    SELL_CSS = [
        '[class*="sell-price"]',     '[class*="selling-price"]',
        '[class*="discount-price"]', '[class*="sale-price"]',
        '[class*="current-price"]',  '[class*="offer-price"]',
        '[class*="price"] [class*="new"]',
    ]
    MRP_CSS = [
        '[class*="original-price"]', '[class*="regular-price"]',
        '[class*="list-price"]',     '[class*="mrp"]',
        '[class*="price"] [class*="old"]', 'del', 's',
    ]
    DESC_CSS = [
        '[class*="description"]', '[class*="summary"]',
        '[class*="details"]',     '[class*="about"]',
        '[class*="overview"]',    '[class*="content"]',
    ]
    IMG_CSS = [
        '[class*="cover"] img',        '[class*="book-img"] img',
        '[class*="product-img"] img',  '[class*="product-image"] img',
        '[class*="thumbnail"] img',    'main img',
        'article img',
    ]

    def __init__(self, soup: BeautifulSoup, driver):
        self.soup   = soup
        self.driver = driver

    # ── Title ────────────────────────────────────────────────────────────────
    def get_title(self) -> str:
        for css in self.TITLE_CSS:
            t = self._sel_text(css)
            if t:
                return t
        h1 = self.soup.find('h1')
        if h1:
            t = h1.get_text(strip=True)
            if t:
                return t
        og = self.soup.find('meta', property='og:title')
        if og and og.get('content'):
            return og['content'].strip()
        title = self.soup.find('title')
        if title:
            return re.sub(r'\s*[|\-–].*$', '', title.get_text(strip=True)).strip()
        return 'Unknown Product'

    # ── Prices ───────────────────────────────────────────────────────────────
    def get_current_price(self) -> float:
        for css in self.SELL_CSS:
            t = self._sel_text(css)
            if t and re.search(r'\d', t):
                return _parse_price(t)
        prices = self._all_taka_prices()
        return min(prices) if prices else 0.0

    def get_original_price(self) -> float:
        for css in self.MRP_CSS:
            t = self._sel_text(css)
            if t and re.search(r'\d', t):
                return _parse_price(t)
        prices = self._all_taka_prices()
        return max(prices) if len(prices) >= 2 else 0.0

    def _all_taka_prices(self) -> list:
        text  = self.soup.get_text(' ')
        found = re.findall(r'৳\s*([\d,]+\.?\d*)', text)
        seen, result = set(), []
        for raw in found:
            v = float(raw.replace(',', ''))
            if v > 0 and v not in seen:
                seen.add(v)
                result.append(v)
        return sorted(result)

    # ── Stock ────────────────────────────────────────────────────────────────
    def get_stock_quantity(self) -> int:
        for tag in self.soup.find_all(['span', 'div', 'p', 'strong', 'small']):
            t = tag.get_text(strip=True).lower()
            if 'out of stock' in t or 'স্টকে নেই' in t:
                return 0
            if 'in stock' in t or 'available' in t or 'স্টকে আছে' in t:
                return 1
            m = re.search(r'(\d+)\s*(copies|পিস|টি)', t, re.I)
            if m:
                return int(m.group(1))
        return 1

    # ── Description ──────────────────────────────────────────────────────────
    def get_description(self) -> str:
        # Selenium innerHTML (captures Vue-rendered HTML)
        for css in self.DESC_CSS:
            try:
                for el in self.driver.find_elements(By.CSS_SELECTOR, css):
                    inner = el.get_attribute('innerHTML') or ''
                    if len(inner.strip()) > 30:
                        return inner.strip()
            except Exception:
                pass
        # BS4 fallback
        for tag in self.soup.find_all(['div', 'section', 'article']):
            combined = ' '.join(tag.get('class') or []).lower() + ' ' + (tag.get('id') or '').lower()
            if any(k in combined for k in ('desc', 'detail', 'about', 'summary', 'overview', 'content')):
                inner = tag.decode_contents().strip()
                if len(inner) > 50:
                    return inner
        return ''

    # ── Image ────────────────────────────────────────────────────────────────
    def get_image_url(self):
        # 1. JSON-LD
        ld = _get_ld_json(self.soup)
        if ld:
            img = ld.get('image')
            if img:
                url = img if isinstance(img, str) else (img[0] if img else None)
                if url:
                    return url

        # 2. Scroll to trigger lazy load, then check Selenium elements
        try:
            self.driver.execute_script("window.scrollTo(0, 400);")
            time.sleep(1)
        except Exception:
            pass

        for css in self.IMG_CSS:
            try:
                for el in self.driver.find_elements(By.CSS_SELECTOR, css):
                    for attr in ('src', 'data-src', 'data-lazy', 'data-original'):
                        val = (el.get_attribute(attr) or '').strip()
                        if val and not val.startswith('data:') and _is_product_image(val):
                            return val
            except Exception:
                pass

        # 3. BS4 all img tags
        for img in self.soup.find_all('img'):
            for attr in ('data-src', 'data-lazy', 'data-original', 'src'):
                val = (img.get(attr) or '').strip()
                if val and not val.startswith('data:') and _is_product_image(val):
                    return self._norm(val)

        # 4. og:image (last resort)
        og = self.soup.find('meta', property='og:image')
        if og and og.get('content'):
            c = og['content'].strip()
            if not re.search(r'logo|favicon|icon|banner', c, re.I):
                return c
        return None

    # ── Specifications ───────────────────────────────────────────────────────
    def get_specifications(self) -> dict:
        specs = {}

        # Tables
        for row in self.soup.select('table tr'):
            cells = row.find_all(['td', 'th'])
            if len(cells) >= 2:
                _map_spec(specs, cells[0].get_text(strip=True).lower(),
                          cells[1].get_text(strip=True))

        # Definition lists
        for dl in self.soup.find_all('dl'):
            for dt, dd in zip(dl.find_all('dt'), dl.find_all('dd')):
                _map_spec(specs, dt.get_text(strip=True).lower(),
                          dd.get_text(strip=True))

        # Labeled children pairs inside li/div/p
        for parent in self.soup.find_all(['li', 'div', 'p']):
            kids = [c for c in parent.children if hasattr(c, 'get_text')]
            if len(kids) >= 2:
                k = kids[0].get_text(strip=True).lower().rstrip(':')
                v = kids[-1].get_text(strip=True)
                if k and v and k != v:
                    _map_spec(specs, k, v)

        # Inline "Key: Value" text patterns
        LABEL_RE = re.compile(
            r'^(isbn|author|লেখক|publisher|প্রকাশক|page|পৃষ্ঠা|'
            r'edition|সংস্করণ|language|ভাষা|weight|ওজন)\s*[:\-]\s*(.+)$',
            re.I
        )
        for tag in self.soup.find_all(['p', 'span', 'div', 'li']):
            m = LABEL_RE.match(tag.get_text(' ', strip=True))
            if m:
                _map_spec(specs, m.group(1).lower(), m.group(2).strip())

        specs.setdefault('publisher', "প্রফেসর'স প্রকাশন")
        return specs

    # ── Helpers ──────────────────────────────────────────────────────────────
    def _sel_text(self, css: str) -> str:
        try:
            for el in self.driver.find_elements(By.CSS_SELECTOR, css):
                t = (el.text or '').strip()
                if t:
                    return t
        except Exception:
            pass
        return ''

    def _norm(self, url: str) -> str:
        if url.startswith('//'): return 'https:' + url
        if url.startswith('/'):  return self.BASE + url
        return url


# ---------------------------------------------------------------------------
# Shared pure helpers
# ---------------------------------------------------------------------------

def _make_driver():
    opts = Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--disable-gpu')
    opts.add_argument('--window-size=1280,900')
    opts.add_argument(
        '--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    )
    opts.set_capability('goog:loggingPrefs', {'performance': 'ALL'})
    return webdriver.Chrome(options=opts)


def _get_ld_json(soup: BeautifulSoup):
    for tag in soup.find_all('script', {'type': 'application/ld+json'}):
        if not tag.string:
            continue
        try:
            data = json.loads(tag.string.strip())
            if '@graph' in data:
                for item in data['@graph']:
                    if item.get('@type') in ('Book', 'Product'):
                        return item
            if data.get('@type') in ('Book', 'Product'):
                return data
        except (json.JSONDecodeError, AttributeError):
            continue
    return None


def _map_spec(specs: dict, key: str, val: str):
    if not key or not val:
        return
    if 'isbn' in key:
        specs.setdefault('isbn', val)
    elif any(k in key for k in ('author', 'writer', 'লেখক')):
        specs.setdefault('author', val)
    elif any(k in key for k in ('publisher', 'publication', 'প্রকাশক')):
        specs.setdefault('publisher', val)
    elif any(k in key for k in ('page', 'পৃষ্ঠা')):
        m = re.search(r'\d+', val)
        specs.setdefault('pages', m.group() if m else val)
    elif any(k in key for k in ('edition', 'সংস্করণ')):
        specs.setdefault('edition', val)
    elif any(k in key for k in ('language', 'ভাষা')):
        specs.setdefault('language', val)
    elif any(k in key for k in ('country', 'দেশ')):
        specs.setdefault('country', val)
    elif any(k in key for k in ('weight', 'ওজন')):
        m = re.search(r'[\d.]+', val)
        specs.setdefault('weight', float(m.group()) if m else 0.0)


def _parse_price(text: str) -> float:
    cleaned = re.sub(r'[৳Tk,\s]', '', str(text))
    m = re.search(r'[\d.]+', cleaned)
    return float(m.group()) if m else 0.0


def _to_float(val) -> float:
    try:
        return float(str(val).replace(',', '').strip())
    except (ValueError, TypeError):
        return 0.0


def _is_product_image(url: str) -> bool:
    url_l = url.lower()
    if not re.search(r'\.(jpg|jpeg|png|webp|gif)(\?|$)|/storage/|/uploads?/|/images?/book', url_l):
        return False
    reject = ('logo', 'favicon', 'icon', 'banner', 'badge',
              'placeholder', 'spinner', 'loading', '1x1',
              'bkash', 'nagad', 'visa', 'mastercard')
    return not any(r in url_l for r in reject)