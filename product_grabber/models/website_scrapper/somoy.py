from odoo import models, fields, api
from bs4 import BeautifulSoup
import requests, re, logging, json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from odoo.exceptions import UserError
from .base_extractor import BaseBookExtractor


class importProductFromSomoy(models.TransientModel):
    _inherit = 'import.product.from.website'
    _description = 'Import product from Somoy.com website'

    def somoy_products(self):
        url = self.source_url
        if not url or 'somoy.com' not in url:
            raise ValueError("Invalid Somoy URL")

        # ── Shopify also exposes a clean JSON endpoint: append .json ──
        json_url = url.rstrip('/') + '.json'

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
            'Referer': 'https://somoy.com/',
        }
        session.headers.update(headers)

        try:
            # ── Try Shopify JSON API first (cleanest data) ────────────
            specs = {}
            json_data = None
            try:
                jr = session.get(json_url, timeout=10)
                if jr.status_code == 200:
                    json_data = jr.json().get('product', {})
            except Exception:
                pass

            # ── Always fetch HTML for fields not in JSON ──────────────
            response = session.get(url, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            extractor = SomoyExtractor(soup, json_data)

            self.product_name              = extractor.get_title()
            self.face_value                = extractor.get_original_price()
            self.price                     = extractor.get_current_price()
            self.stock_qty                 = extractor.get_stock_quantity()
            self.ecommerce_description     = extractor.get_description()
            self.image_url                 = extractor.get_image_url()

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


class SomoyExtractor(BaseBookExtractor):
    """
    Extract product data from Somoy.com (Shopify store).

    Page structure observed (product: লীলাবতী):
    ─────────────────────────────────────────────────────────────────────
    <h1>  লীলাবতী (Lilaboti)                ← product title
    By হুমায়ূন আহমেদ                        ← author (plain text / <a>)

    Regular price  Tk 600.00                 ← original price
                   Tk 420.00                 ← sale / current price

    ── Info block (label + value pairs, each in its own <div> or <p>) ──
    প্রথম প্রকাশিত   8th Edition, 2020       ← edition
    পৃষ্ঠার সংখ্য    240                     ← pages
    ISBN             9848683453              ← isbn

    ### বইয়ের তথ্য                           ← description heading
    <p> …book description… </p>

    Tags: সমকালীন উপন্যাস                   ← category / tags

    Shopify JSON endpoint (/products/<handle>.json) also available and
    used as a higher-priority source where applicable.
    ─────────────────────────────────────────────────────────────────────
    """

    # Bengali label aliases (lowercase, stripped of whitespace / colons)
    _LABEL_MAP = {
        # author
        "লেখক":            "author",
        "রচয়িতা":          "author",
        "সম্পাদক":          "author",
        # publisher
        "প্রকাশক":          "publisher",
        "প্রকাশনী":         "publisher",
        "প্রকাশনা":         "publisher",
        # edition / first published
        "প্রথম প্রকাশিত":   "edition",
        "প্রথম প্রকাশ":     "edition",
        "সংস্করণ":          "edition",
        "প্রকাশকাল":        "edition",
        "edition":          "edition",
        "first published":  "edition",
        # pages
        "পৃষ্ঠার সংখ্য":   "pages",
        "পৃষ্ঠা সংখ্যা":   "pages",
        "পৃষ্ঠা":           "pages",
        "মোট পৃষ্ঠা":      "pages",
        "pages":            "pages",
        "page":             "pages",
        # isbn
        "isbn":             "isbn",
        # language
        "ভাষা":             "language",
        "language":         "language",
        # country
        "দেশ":              "country",
        "country":          "country",
        # format
        "বিন্যাস":          "format",
        "আবরণ":             "format",
        "format":           "format",
        "cover":            "format",
        # category
        "বিষয়":             "category",
        "ধরন":              "category",
        "genre":            "category",
        "category":         "category",
    }

    def __init__(self, soup: BeautifulSoup, json_data: dict | None = None) -> None:
        self.soup      = soup
        self.json_data = json_data or {}

    # ================================================================== #
    #  PUBLIC EXTRACTORS                                                   #
    # ================================================================== #

    def get_title(self) -> str:
        """
        1. Shopify JSON  → product.title
        2. <h1 class="…product__title…"> or first <h1> in .product
        3. <meta property="og:title">
        """
        if self.json_data.get('title'):
            return self.json_data['title'].strip()

        h1 = self.soup.find("h1", {"class": lambda c: c and "product" in c})
        if not h1:
            # fallback: first h1 inside the main content area
            main = self.soup.find("main") or self.soup.find("div", {"id": "MainContent"})
            h1 = main.find("h1") if main else self.soup.find("h1")
        if h1:
            return h1.get_text(strip=True)

        og = self.soup.find("meta", {"property": "og:title"})
        if og and og.get("content"):
            return og["content"].strip()

        return "Unknown Product"

    def get_current_price(self) -> float:
        """
        Shopify sale price (the lower, active price):
        1. JSON  → variants[0].price
        2. HTML  → second price amount on the page (sale price comes after
                   the original in Shopify's default markup)

        Page text observed:
          "Regular price  Tk 600.00  Tk 420.00"
          The SECOND amount (Tk 420.00) is the active sale price.
        """
        price_div = self.soup.find("div", {"class": "hdt-price__sale"})
        sale_div = price_div.find('span', style=lambda x: x and '#db1215' in x)
        amounts=sale_div.get_text(strip=True)
        # If two different prices, first = original; if one = no discount
        return self._parse_price(amounts)

    def get_original_price(self) -> float:
        """
        Shopify compare-at price (original / crossed-out price):
        1. JSON  → variants[0].compare_at_price
        2. HTML  → first price amount (regular price shown before sale price)

        Returns 0.0 if there is no discount.
        """

        amounts = self.soup.find("span", {"class": "hdt-compare-at-price"})
        # If two different prices, first = original; if one = no discount
        return self._parse_price(amounts.get_text(strip=True))

    def get_stock_quantity(self) -> int:
        """
        1. JSON  → sum of available inventory across variants
        2. HTML  → look for "out of stock" text or disabled add-to-cart button
        """
        if self.json_data:
            variants = self.json_data.get('variants', [])
            total = sum(
                v.get('inventory_quantity', 0)
                for v in variants
                if v.get('inventory_management')   # only count managed stock
            )
            if total > 0:
                return total
            # If not managed or all zero, check available flag
            if any(v.get('available') for v in variants):
                return 1

        # HTML fallback
        page_text = self.soup.get_text().lower()
        if 'out of stock' in page_text or 'stock out' in page_text:
            return 0

        btn = self.soup.find(
            "button",
            {"class": lambda c: c and any(
                k in c for k in ("add-to-cart", "product-form__submit")
            )}
        )
        if btn:
            disabled = btn.get("disabled") or "disabled" in btn.get("class", [])
            return 0 if disabled else 1

        return 1  # default: assume in-stock

    def get_description(self) -> str:
        """
        Somoy places the full description under the heading
        '### বইয়ের তথ্য' (Book Information).

        1. JSON  → product.body_html (strip tags)
        2. HTML  → <div class="…product__description…"> or the section
                   after the 'বইয়ের তথ্য' heading.
        """
        if self.json_data.get('body_html'):
            body_soup = BeautifulSoup(self.json_data['body_html'], 'html.parser')
            text = body_soup.get_text(separator="\n", strip=True)
            if text:
                return text

        # Look for the বইয়ের তথ্য section
        for heading in self.soup.find_all(["h2", "h3", "h4", "strong", "b"]):
            if "বইয়ের তথ্য" in heading.get_text():
                # Collect all sibling/following paragraph text
                parts = []
                for sib in heading.find_next_siblings():
                    tag = sib.name
                    if tag in ("h2", "h3", "h4") and sib != heading:
                        break
                    parts.append(sib.get_text(separator="\n", strip=True))
                if parts:
                    return "\n".join(parts).strip()

        # Generic Shopify product description div
        for cls in ("product__description", "product-description",
                    "rte", "description"):
            div = self.soup.find("div", {"class": lambda c: c and cls in c})
            if div:
                text = div.get_text(separator="\n", strip=True)
                if text:
                    return text

        return ""

    def get_image_url(self) -> str:
        """
        1. JSON  → images[0].src
        2. HTML  → <meta property="og:image">
        3. HTML  → first <img> in the product gallery / media
        """
        if self.json_data.get('images'):
            src = self.json_data['images'][0].get('src', '')
            if src:
                return src

        og = self.soup.find("meta", {"property": "og:image"})
        if og and og.get("content"):
            return og["content"]

        for cls in ("product__media", "product-single__photo",
                    "product__image", "featured-image"):
            div = self.soup.find(["div", "figure"],
                                 {"class": lambda c: c and cls in c})
            if div:
                img = div.find("img")
                if img:
                    src = (img.get("data-src") or img.get("data-srcset") or
                           img.get("src") or "")
                    # Shopify srcset → grab the plain URL before the space
                    return src.split()[0] if src else ""

        return ""

    def get_specifications(self) -> dict:
        """
        Somoy shows book details as a block of label-value pairs
        immediately below the add-to-cart button.

        Observed HTML pattern (simplified):
        ┌─────────────────────────────────────────────────┐
        │  <div/p>  প্রথম প্রকাশিত                        │
        │  <div/p>  8th Edition, 2020                     │
        │  <div/p>  পৃষ্ঠার সংখ্য                         │
        │  <div/p>  240                                    │
        │  <div/p>  ISBN                                   │
        │  <div/p>  9848683453                             │
        └─────────────────────────────────────────────────┘

        The author appears near the title as "By হুমায়ূন আহমেদ".

        Cascade of strategies:
          A. Shopify JSON metafields / tags
          B. Paired sibling <div>/<p> label-value blocks (primary layout)
          C. <table> rows (some Shopify themes use tables)
          D. <dl> definition lists
          E. Bold-label inline paragraphs
          F. Colon-separated text lines in the product summary area
        """
        specs: dict = {}
        specs["publisher"]="Anyaprakash"
        specs['authors']=self.soup.find('div',{"class":"hdt-product-author_name"}).get_text(strip=True).replace('By ',"")

        # ── Author from "By …" line near the title ────────────────────
        self._extract_author_by_line(specs)

        # ── B: Paired sibling div/p blocks (Somoy's actual layout)
        self._parse_label_value_pairs(specs)
        if self._has_key_specs(specs):
            return specs

        # ── C: <table> rows ──────────────────────────────────────────
        for table in self.soup.find_all("table"):
            self._parse_table(table, specs)
        if self._has_key_specs(specs):
            return specs

        # ── D: <dl> definition lists ─────────────────────────────────
        for dl in self.soup.find_all("dl"):
            for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
                self._map_spec(specs, dt.get_text(strip=True).lower(),
                               dd.get_text(strip=True))
        if self._has_key_specs(specs):
            return specs

        # ── E: Bold-label paragraphs ──────────────────────────────────
        self._parse_bold_label_paragraphs(specs)
        if self._has_key_specs(specs):
            return specs

        # ── F: Colon-separated text lines ────────────────────────────
        self._parse_colon_lines(specs)

        return specs

    # ================================================================== #
    #  PRIVATE HELPERS                                                     #
    # ================================================================== #

    def _extract_author_by_line(self, specs: dict):
        """
        Somoy shows the author as:
          "By হুমায়ূন আহমেদ"   (plain text node or <a> near the <h1>)

        Looks for any element whose text starts with "By " (case-insensitive)
        within the product summary / header area.
        """
        pattern = re.compile(r"^[Bb]y\s+(.+)$", re.UNICODE)

        # Search in .product-single, .product__info, #MainContent, or <main>
        search_root = (
            self.soup.find("div", {"class": lambda c: c and "product" in c})
            or self.soup.find("main")
            or self.soup
        )

        for tag in search_root.find_all(["p", "div", "span", "a", "h2", "h3"]):
            text = tag.get_text(strip=True)
            m = pattern.match(text)
            if m:
                author = m.group(1).strip()
                # Skip if the "author" looks like it's really a full sentence
                if len(author) < 80:
                    specs.setdefault('author', author)
                    break

    def _parse_label_value_pairs(self, specs: dict):
        """
        Somoy's primary book-info layout:

        Labels and values are adjacent siblings (or parent-child) in a
        container block.  Three common sub-patterns:

        Pattern 1 – consecutive <p> or <div> tags:
          <p>প্রথম প্রকাশিত</p>
          <p>8th Edition, 2020</p>

        Pattern 2 – a two-column flex/grid row:
          <div class="book-info-row">
            <span class="label">পৃষ্ঠার সংখ্য</span>
            <span class="value">240</span>
          </div>

        Pattern 3 – value-only text nodes with label in a preceding element
          (handled by sibling scan)
        """
        # -- Pattern 2: labelled span pairs inside a wrapper div --------
        for wrapper in self.soup.find_all(
            ["div", "li", "tr"],
            {"class": lambda c: c and any(
                k in c for k in ("book-info", "product-info", "meta",
                                 "detail", "specs", "attribute")
            )}
        ):
            children = [t for t in wrapper.children
                        if getattr(t, 'name', None)]
            if len(children) == 2:
                self._map_spec(
                    specs,
                    children[0].get_text(strip=True).lower(),
                    children[1].get_text(strip=True)
                )

        # -- Pattern 1: consecutive sibling p/div scan ------------------
        # Find the block that contains the known Bengali labels
        known_labels = set(self._LABEL_MAP.keys())

        for container in self.soup.find_all(
            ["div", "section", "article"],
            {"class": lambda c: c and any(
                k in c for k in ("product", "summary", "detail", "info")
            )}
        ):
            children = [
                t for t in container.children
                if getattr(t, 'name', None) in ("p", "div", "span", "li")
            ]
            i = 0
            while i < len(children) - 1:
                label_text = children[i].get_text(strip=True).lower().rstrip(":")
                value_text = children[i + 1].get_text(strip=True)

                # Is the current element a known label?
                matched = False
                for lbl in known_labels:
                    if lbl in label_text:
                        self._map_spec(specs, label_text, value_text)
                        matched = True
                        break

                i += (2 if matched else 1)

        # -- Direct text search in the whole page for ISBN / pages ------
        # These are often just plain text nodes; scan all text nodes
        # near known Bengali headings.
        for tag in self.soup.find_all(["p", "div", "span", "li", "td"]):
            text = tag.get_text(strip=True)
            # Skip long texts (they're paragraphs, not spec values)
            if len(text) > 120:
                continue
            text_lower = text.lower().rstrip(":")
            for lbl, field in self._LABEL_MAP.items():
                if lbl == text_lower:
                    # Next sibling = value
                    nxt = tag.find_next_sibling()
                    if nxt:
                        val = nxt.get_text(strip=True)
                        if val and len(val) < 120:
                            self._map_spec(specs, lbl, val)
                    break

    def _parse_table(self, table, specs: dict):
        """Parse <table> with <th>/<td> or <td>/<td> rows."""
        for row in table.find_all("tr"):
            th = row.find("th")
            tds = row.find_all("td")
            if th and tds:
                self._map_spec(specs,
                               th.get_text(strip=True).lower(),
                               tds[0].get_text(strip=True))
            elif len(tds) >= 2:
                self._map_spec(specs,
                               tds[0].get_text(strip=True).lower(),
                               tds[1].get_text(strip=True))

    def _parse_bold_label_paragraphs(self, specs: dict):
        """Handle <p><strong>Label:</strong> Value</p> patterns."""
        for tag in self.soup.find_all(["p", "li", "div"]):
            bold = tag.find(["strong", "b"])
            if not bold:
                continue
            raw_key = bold.get_text(strip=True).rstrip(":").strip().lower()
            bold_copy = bold.extract()
            value = tag.get_text(strip=True).lstrip(":").strip()
            tag.insert(0, bold_copy)
            if raw_key and value:
                self._map_spec(specs, raw_key, value)

    def _parse_colon_lines(self, specs: dict):
        """Last-resort: scan 'Key : Value' lines in the product area."""
        for sel in [
            {"class": lambda c: c and "product" in c},
            {"id": "MainContent"},
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

        # Direct match first (most precise)
        field = self._LABEL_MAP.get(key)

        # Substring match if no direct hit
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
        return bool(specs.get('author') or specs.get('isbn') or
                    specs.get('pages') or specs.get('publisher'))

    def _all_price_amounts(self) -> list[float]:
        """Return all price floats found on the page, in document order."""
        amounts = []
        for span in self.soup.find_all(
            "span", {"class": lambda c: c and "price" in c}
        ):
            text = span.get_text(strip=True)
            val = self._parse_price(text)
            if val > 0:
                amounts.append(val)

        # Also try <s>, <del> and <ins> for sale price markup
        if not amounts:
            for tag in self.soup.find_all(["s", "del", "ins"]):
                val = self._parse_price(tag.get_text(strip=True))
                if val > 0:
                    amounts.append(val)

        return amounts

