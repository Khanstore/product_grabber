import json, xmlrpc.client
import requests
import re, logging
import time
import difflib
import urllib.parse
from urllib.parse import urlparse
from odoo import models, fields, api
from odoo.exceptions import UserError
import base64
from .phonetic_utils import phonetic_key

_logger = logging.getLogger(__name__)

# Politeness delay (seconds) between consecutive requests when importing
# many URLs in one go, so we don't hammer the source sites.
BULK_IMPORT_DELAY = 1.5

# Similarity thresholds (difflib ratio, 0-1) used for the local "smart
# matching" of authors/publishers/categories/duplicate products. No
# external AI service or API key is involved - these all compare scraped
# text against records already in your own database.
PARTNER_SIMILARITY_THRESHOLD = 0.60
CATEGORY_SIMILARITY_THRESHOLD = 0.55
PRODUCT_TITLE_SIMILARITY_THRESHOLD = 0.82
# Looser bar used only for the informational "closest matching products"
# list shown when nothing crosses the duplicate threshold above - this is
# a "you might want to eyeball these" signal, not a block.
PRODUCT_NEAREST_SIMILARITY_FLOOR = 0.40
PRODUCT_NEAREST_MAX_RESULTS = 5


def _phonetic_shingles(text, n=3, max_shingles=40):
    """Break the phonetic-folded form of `text` into overlapping
    n-character shingles (trigrams by default). Used to pre-filter DB
    candidates via plain SQL ILIKE, without needing a real fuzzy-search
    extension like PostgreSQL's pg_trgm.

    Matching on a whole folded word (the earlier approach) misses cases
    where the folding differs by even one internal character - e.g. a
    Bengali-derived key like 'gardijan pablikesns' vs an English key like
    'guardian publication' share no literal whole-word substring, even
    though they're clearly the same name. Shingles fix this: they still
    share plenty of 3-character fragments ('ard', 'rdi', 'bli', ...), so
    a candidate search that OR's together ILIKE on each shingle finds the
    record - the *actual* accept/reject decision is still made afterward
    by the real similarity score, this step only makes sure a true match
    doesn't get eliminated before it's ever scored."""
    key = (phonetic_key(text) or '').replace(' ', '')
    if not key:
        return []
    if len(key) <= n:
        return [key]
    shingles = []
    for i in range(len(key) - n + 1):
        s = key[i:i + n]
        if s not in shingles:
            shingles.append(s)
    if len(shingles) > max_shingles:
        # Sample evenly across the string rather than truncating, so a
        # long title's ending isn't ignored entirely.
        step = len(shingles) / max_shingles
        shingles = [shingles[int(i * step)] for i in range(max_shingles)]
    return shingles


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    publisher_link = fields.Char("publisher link")
    phonetic_key = fields.Char(
        compute='_compute_phonetic_key', store=True, index=True,
        help="Auto-generated, folds English/Banglish/Bengali-script spelling "
             "variants of the product name to a comparable form, so imports "
             "can find this product even if it was catalogued under a "
             "differently-scripted or spelled title."
    )

    @api.depends('name')
    def _compute_phonetic_key(self):
        for rec in self:
            rec.phonetic_key = phonetic_key(rec.name or '')

    def action_refresh_from_source(self):
        """Re-scrape each product's original source page and refresh its
        price/stock/description. Used by the manual button on the product
        form and by the scheduled cron job. Products with no
        'publisher_link' (i.e. not imported through this module) are
        skipped."""
        Wizard = self.env['import.product.from.website']
        Log = self.env['import.product.log']
        refreshed = self.browse()
        for product in self:
            if not product.publisher_link:
                continue
            wiz = Wizard.create({'source_url': product.publisher_link})
            try:
                wiz.fetch_data()
            except Exception as e:
                _logger.warning(
                    "Refresh failed for %s (%s): %s",
                    product.display_name, product.publisher_link, e
                )
                Log.create({
                    'source_url': product.publisher_link,
                    'status': 'error',
                    'message': str(e),
                    'product_id': product.id,
                })
                wiz.unlink()
                continue

            vals = {}
            if wiz.price:
                vals['list_price'] = wiz.price
            if wiz.face_value:
                vals['compare_list_price'] = wiz.face_value
            if wiz.ecommerce_description:
                vals['description_ecommerce'] = wiz.ecommerce_description
            for fname, fval in wiz._optional_fields_map().items():
                if fname in product._fields and fval:
                    vals[fname] = fval

            if vals:
                product.write(vals)
                refreshed |= product
                Log.create({
                    'source_url': product.publisher_link,
                    'status': 'updated',
                    'product_id': product.id,
                    'message': "Refreshed via scheduled/manual re-scrape.",
                })
            wiz.unlink()
        return refreshed


class ResPartner(models.Model):
    _inherit = 'res.partner'

    phonetic_key = fields.Char(
        compute='_compute_phonetic_key', store=True, index=True,
        help="Auto-generated, folds English/Banglish/Bengali-script spelling "
             "variants of the name to a comparable form, so author/publisher "
             "matching can find this partner regardless of which script or "
             "spelling the source site used."
    )

    @api.depends('name')
    def _compute_phonetic_key(self):
        for rec in self:
            rec.phonetic_key = phonetic_key(rec.name or '')


class importProductFromWebsite(models.TransientModel):
    _name = 'import.product.from.website'
    _description = 'import product from website'

    # Define fields (if needed)
    categ_id = fields.Many2one("product.category", string="category")
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
    authors = fields.Char("author (proposed)")
    publishers = fields.Char("Publisher (proposed)")
    force_duplicate = fields.Boolean(
        string="Create Anyway (ignore duplicate warning)",
        help="Tick this if you have checked the possible duplicate product(s) below and still want to import this as a new product."
    )
    duplicate_product_ids = fields.Many2many(
        'product.template',
        'import_product_duplicate_rel',
        'wizard_id',
        'product_tmpl_id',
        string="Possible Duplicates",
        readonly=True,
    )
    selected_duplicate_id = fields.Many2one(
        'product.template',
        string="Product to Update",
        domain="[('id', 'in', duplicate_product_ids)]",
        help="When exactly one duplicate is found, this is filled in automatically. "
             "When more than one is found, pick which one 'Update Existing Product' "
             "should edit."
    )
    nearest_product_ids = fields.Many2many(
        'product.template',
        'import_product_nearest_rel',
        'wizard_id',
        'product_tmpl_id',
        string="Closest Matching Products",
        readonly=True,
        help="No confident duplicate was found, but these existing products have "
             "the closest-matching names, in case one of them is actually the same "
             "item under a different title."
    )
    author_suggestion_ids = fields.Many2many(
        'res.partner',
        'import_product_author_suggestion_rel',
        'wizard_id',
        'partner_id',
        string="Similar Authors Found",
        readonly=True,
        help="More than one existing author looks similar to the proposed name, "
             "so none was auto-selected. Pick the right one (or none, if it's "
             "genuinely new) in the Authors field above."
    )
    publisher_suggestion_ids = fields.Many2many(
        'res.partner',
        'import_product_publisher_suggestion_rel',
        'wizard_id',
        'partner_id',
        string="Similar Publishers Found",
        readonly=True,
        help="More than one existing publisher looks similar to the proposed name, "
             "so none was auto-selected. Pick the right one (or none, if it's "
             "genuinely new) in the Publishers field above."
    )
    category_text = fields.Char("Category (proposed)")
    category_suggestion_ids = fields.Many2many(
        'product.category',
        'import_product_category_suggestion_rel',
        'wizard_id',
        'categ_id',
        string="Similar Categories Found",
        readonly=True,
        help="More than one existing category looks like a plausible fit, "
             "so none was auto-selected. Pick the right one in the Category "
             "field above."
    )
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
    weight = fields.Float(string="weight")
    language = fields.Char(string="Language")
    country = fields.Char(string="Country")

    bulk_urls = fields.Text(
        string="Bulk URLs (one per line)",
        help="Paste one product URL per line from any supported site. Each will be "
             "fetched, checked for duplicates (by ISBN, then source URL, then name) "
             "and imported automatically."
    )
    bulk_import_summary = fields.Text(string="Last Bulk Import Result", readonly=True)

    google_search_url = fields.Char(compute='_compute_google_search_url')

    @api.depends('image_url')
    def _compute_google_search_url(self):
        base_url = "https://lens.google.com/uploadbyurl?url="
        for record in self:
            if record.image_url:
                # Encodes the string to be URL friendly
                encoded_image_url = urllib.parse.quote(record.image_url, safe='')
                record.google_search_url = f"{base_url}{encoded_image_url}"
            else:
                record.google_search_url = False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _split_names(text):
        """Split a proposed 'authors'/'publishers' string like
        'Humayun Ahmed, Muhammed Zafar Iqbal and Anisul Hoque'
        into a clean list of individual names."""
        if not text:
            return []
        # Normalise English "and" / Bengali "ও" used as a separator into a comma
        normalized = re.sub(r'\s+(and|ও)\s+', ',', text, flags=re.IGNORECASE)
        # Split on Latin comma, Bengali/Arabic comma variants
        parts = re.split(r'[,،、]', normalized)
        return [p.strip() for p in parts if p.strip()]

    # A small, curated Bangla -> English dictionary for common book
    # genre/category words. This deliberately does NOT try to cover
    # author names, publisher names, or book titles - those are proper
    # nouns and get *transliterated* (see phonetic_utils.phonetic_key),
    # not translated. A genre word like 'উপন্যাস' and its English
    # equivalent 'Novel' are simply different words tied together by
    # meaning, so only a real translation lookup - not phonetics - can
    # connect them, which is what this table is for.
    _BN_EN_GENRE_TERMS = {
        'উপন্যাস': 'novel', 'উপন্যাসিকা': 'novella',
        'ছোটগল্প': 'short story', 'গল্প': 'story',
        'কবিতা': 'poetry', 'কাব্য': 'poetry',
        'ইতিহাস': 'history', 'জীবনী': 'biography',
        'আত্মজীবনী': 'autobiography', 'স্মৃতিকথা': 'memoir',
        'ভ্রমণ': 'travel', 'রম্য': 'humor',
        'বিজ্ঞান': 'science', 'কল্পবিজ্ঞান': 'science fiction',
        'ধর্ম': 'religion', 'ইসলামিক': 'islamic',
        'রাজনীতি': 'politics', 'অর্থনীতি': 'economics',
        'দর্শন': 'philosophy', 'মনোবিজ্ঞান': 'psychology',
        'রহস্য': 'mystery', 'গোয়েন্দা': 'detective',
        'থ্রিলার': 'thriller', 'ভৌতিক': 'horror',
        'শিশুতোষ': "children's", 'কিশোর': 'young adult',
        'কমিক্স': 'comics', 'অনুবাদ': 'translation',
        'কৃষি': 'agriculture', 'স্বাস্থ্য': 'health',
        'রান্না': 'cooking', 'নাটক': 'drama',
        'উপন্যাস সমগ্র': 'novel collection', 'গণিত': 'mathematics',
        'শিক্ষা': 'education', 'আইন': 'law',
        'সাহিত্য': 'literature', 'প্রবন্ধ': 'essay',
    }

    def _translate_known_terms(self, text):
        """Best-effort word/phrase substitution using the curated genre
        dictionary above. Only meaningful for category/genre text - see
        the note on _BN_EN_GENRE_TERMS for why this isn't applied to
        names."""
        if not text:
            return text
        translated = text
        for bn, en in self._BN_EN_GENRE_TERMS.items():
            if bn in translated:
                translated = translated.replace(bn, en)
        return translated

    def _similarity(self, a, b):
        """0-1 similarity score between two strings. Combines a plain
        character comparison with a phonetic-key comparison (see
        phonetic_utils.phonetic_key) and takes the better of the two, so
        it catches both simple typos and script/spelling variants
        (Bengali script vs Banglish vs English) of the same name. No
        external service or API key - closed-set lookup against your own
        catalog."""
        if not a or not b:
            return 0.0
        raw_score = difflib.SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()
        phon_a, phon_b = phonetic_key(a), phonetic_key(b)
        phon_score = difflib.SequenceMatcher(None, phon_a, phon_b).ratio() if phon_a and phon_b else 0.0
        return max(raw_score, phon_score)

    def _find_matching_partners(self, names_str, is_writer=False, is_publisher=False, populate_ambiguous=True):
        """Search existing res.partner records (authors/publishers) that
        look similar to each proposed name, so we reuse them instead of
        creating duplicate author/publisher partners.

        Similarity is scored with difflib against a candidate pool
        (pre-filtered in the DB by shared significant words, since we
        can't run a similarity score across an entire partner table
        without loading it). For each proposed name:
        - exactly one candidate scores above the similarity threshold
          -> auto-selected
        - more than one scores above threshold -> too ambiguous to guess
          which one is right, so (when populate_ambiguous is True, the
          interactive single-import case) ALL of them are added to the
          real field - the user then just removes whichever ones are
          wrong using the tag's own '×', no separate read-only picker
          needed. In bulk import (populate_ambiguous=False) that would
          mean silently attaching several wrong authors with no human
          around to fix it, so there none are added - they're only
          returned as suggestions, for the summary/log to flag instead.
        - none score above threshold -> nothing to match, create new as
          before

        Returns a tuple: (auto_matched, suggestions, ambiguous_names)
        """
        self.ensure_one()
        Partner = self.env['res.partner']
        auto_matched = Partner
        suggestions = Partner
        ambiguous_names = []

        base_domain = []
        if is_writer:
            base_domain.append(('is_writer', '=', True))
        if is_publisher:
            base_domain.append(('is_publisher', '=', True))

        for name in self._split_names(names_str):
            shingles = _phonetic_shingles(name)
            if not shingles:
                continue
            # Search the stored, indexed phonetic_key column via
            # overlapping shingles - already folded to a
            # script/spelling-independent form, and shingle-based so
            # small internal folding differences (a dropped vowel, a
            # slightly different consonant) don't cause a real match to
            # be missed the way a whole-word substring search would.
            shingle_domain = ['|'] * (len(shingles) - 1) + [('phonetic_key', 'ilike', s) for s in shingles]
            candidates = self.env['res.partner'].search(base_domain + shingle_domain, limit=50)
            if not candidates:
                continue

            scored = [(p, self._similarity(name, p.name)) for p in candidates]
            above_threshold = [p for p, score in scored if score >= PARTNER_SIMILARITY_THRESHOLD]

            if len(above_threshold) == 1:
                auto_matched |= above_threshold[0]
            elif len(above_threshold) > 1:
                suggestions |= Partner.browse([p.id for p in above_threshold])
                ambiguous_names.append(
                    "%s (%d similar matches: %s)"
                    % (name, len(above_threshold), ', '.join(p.name for p in above_threshold))
                )
                if populate_ambiguous:
                    auto_matched |= Partner.browse([p.id for p in above_threshold])
            # zero above threshold: leave it for manual creation

        return auto_matched, suggestions, ambiguous_names

    def _suggest_category(self):
        """Suggest a product.category using only text already scraped from
        the source page (the proposed category/genre text where the site
        provides one, plus the product title/description as a fallback)
        matched against your own existing categories.

        This is deliberately a local, closed-set similarity match rather
        than a live web search: the goal is picking the right entry out
        of *your own* category list, which a search engine has no way to
        know about anyway - matching scraped text against your own
        records locally is both simpler and more reliable for that.

        Returns a tuple: (auto_category, suggestion_categories)
        """
        self.ensure_one()
        Category = self.env['product.category']
        combined_text = ' '.join(
            t for t in [self.category_text, self.product_name] if t
        ).lower()
        if not combined_text:
            return Category, Category

        # Translate any recognised Bangla genre words in the scraped text
        # to English, so an English category name (e.g. "Novel") can be
        # matched even when the source page only gave a Bangla genre word
        # (e.g. "উপন্যাস") - phonetics alone can't bridge that, since
        # they're different words, not different spellings of one word.
        translated_text = self._translate_known_terms(combined_text)

        categories = Category.search([], limit=500)
        scored = []
        for cat in categories:
            cname = (cat.name or '').strip()
            if len(cname) < 3:
                continue
            cname_l = cname.lower()
            if cname_l in combined_text or cname_l in translated_text:
                # The category name literally appears in the scraped
                # text (as given, or after translating known genre
                # words) - treat as a strong match.
                scored.append((cat, 1.0))
            elif self.category_text:
                translated_category_text = self._translate_known_terms(self.category_text.lower())
                ratio = max(
                    self._similarity(cname, self.category_text),
                    self._similarity(cname, translated_category_text),
                )
                if ratio >= CATEGORY_SIMILARITY_THRESHOLD:
                    scored.append((cat, ratio))

        if not scored:
            return Category, Category

        scored.sort(key=lambda pair: pair[1], reverse=True)
        best_score = scored[0][1]
        # Anything within a small margin of the best score is treated as
        # part of the same "top tier" of candidates.
        top_tier = [cat for cat, score in scored if score >= best_score - 0.05]

        if len(top_tier) == 1:
            return top_tier[0], Category
        return Category, Category.browse([cat.id for cat in top_tier[:5]])

    def _find_duplicate_products(self):
        """Search existing product.template records that look like the same
        product, checked in order of reliability:

        1. ISBN - the strongest signal, when the source page has one.
        2. Source URL - the exact page was already imported before.
        3. Product name - the only signal left for items with no ISBN
           (common for older titles, pamphlets, non-book products, etc.),
           so this is always tried, not just as a last resort when the
           first two come up empty-handed for books that never had an
           ISBN to begin with.
        """
        self.ensure_one()
        Product = self.env['product.template']
        matches = Product

        if self.isbn and self.isbn.strip():
            matches |= Product.search([('isbn', '=', self.isbn.strip())], limit=5)

        if self.source_url and self.source_url.strip():
            matches |= Product.search(
                [('publisher_link', '=', self.source_url.strip())], limit=5
            )

        # Only fall back to name matching when nothing more reliable matched -
        # a name match alone is the weakest signal (titles can collide) so we
        # don't want it piling on top of a confident ISBN/URL match, but we
        # do want it to run for the (common, for books) case where there's
        # simply no ISBN and no prior import of this exact URL to check.
        if not matches and self.product_name:
            matches |= Product.search(
                [('name', '=ilike', self.product_name.strip())], limit=5
            )

        # Still nothing? Try a fuzzy title match (catches near-duplicates -
        # typos, "Vol. 1" vs "Volume 1", punctuation differences - that an
        # exact/ilike match would miss). Uses a high similarity threshold
        # since a false-positive "duplicate" here would block a real import.
        if not matches and self.product_name:
            candidates = self._search_products_by_words(self.product_name, limit=30)
            fuzzy_matches = [
                p for p in candidates
                if self._similarity(self.product_name, p.name) >= PRODUCT_TITLE_SIMILARITY_THRESHOLD
            ]
            if fuzzy_matches:
                matches |= Product.browse([p.id for p in fuzzy_matches])

        return matches

    def _search_products_by_words(self, name, limit=50):
        """Pre-filter product.template using the stored, indexed
        phonetic_key column via overlapping shingles (see
        _phonetic_shingles) - finds candidates regardless of whether the
        scraped title and the catalog entry are in Bengali script,
        Banglish, or English, and survives small transliteration/folding
        differences that a whole-word substring search would miss."""
        shingles = _phonetic_shingles(name)
        if not shingles:
            return self.env['product.template']
        shingle_domain = ['|'] * (len(shingles) - 1) + [('phonetic_key', 'ilike', s) for s in shingles]
        return self.env['product.template'].search(shingle_domain, limit=limit)

    def _find_nearest_products(self):
        """When no confident duplicate was found, surface the
        closest-matching existing products by title similarity anyway -
        purely informational, doesn't block anything - so a human can
        glance and catch a same-book-different-title case the duplicate
        check's higher bar missed."""
        self.ensure_one()
        Product = self.env['product.template']
        if not self.product_name:
            return Product

        candidates = self._search_products_by_words(self.product_name, limit=50)
        scored = [
            (p, self._similarity(self.product_name, p.name)) for p in candidates
        ]
        scored = [pair for pair in scored if pair[1] >= PRODUCT_NEAREST_SIMILARITY_FLOOR]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        top = scored[:PRODUCT_NEAREST_MAX_RESULTS]
        return Product.browse([p.id for p, score in top])

    def _optional_fields_map(self):
        """Fields that were scraped but aren't guaranteed to exist on
        product.template (they're defined by the book_shop module this
        addon depends on). Callers check field existence before writing."""
        self.ensure_one()
        return {
            'stock_qty': self.stock_qty,
            'editions': self.editions,
            'language': self.language,
            'country': self.country,
        }

    def _download_image_b64(self, url):
        """Download an image and return it base64-encoded, or False if it
        can't be fetched - never raises, since a missing product image
        shouldn't stop the rest of the import."""
        if not url:
            return False
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            return base64.b64encode(response.content).decode("utf-8")
        except Exception as e:
            _logger.warning("Could not download image from %s: %s", url, e)
            return False

    # ------------------------------------------------------------------
    # Remote (xmlrpc) product creation
    # ------------------------------------------------------------------

    def create_remote_product(self):
        self.ensure_one()
        if not (self.target_url and self.target_db and self.user_name and self.password):
            raise UserError(
                "Please fill in the target URL, database, user name and password "
                "before creating a remote product."
            )

        url = self.target_url.rstrip('/')
        db = self.target_db
        username = self.user_name
        password = self.password

        try:
            common = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/common')
            uid = common.authenticate(db, username, password, {})
        except Exception as e:
            raise UserError(f"Could not connect to {url}: {e}")

        if not uid:
            raise UserError(
                "Authentication failed - please check the target URL, database, "
                "user name and password."
            )

        models_proxy = xmlrpc.client.ServerProxy(f'{url}/xmlrpc/2/object')
        encoded_image = self._download_image_b64(self.image_url)

        product_fields = {
            'name': self.product_name,
            'list_price': self.price,
            'is_published': True,
            'description_ecommerce': self.ecommerce_description,
        }
        if encoded_image:
            product_fields['image_1920'] = encoded_image

        try:
            product_template_id = models_proxy.execute_kw(
                db, uid, password,
                'product.template', 'create',
                [product_fields]
            )
        except xmlrpc.client.Fault as e:
            raise UserError(f"Error creating remote product: {e.faultString}")

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "Remote product created",
                'message': f"Product '{self.product_name}' was created on {url} "
                           f"(ID {product_template_id}).",
                'type': 'success',
                'sticky': False,
            },
        }

    # ------------------------------------------------------------------
    # Fetch / scrape
    # ------------------------------------------------------------------

    def _scrape_source_url(self, populate_ambiguous=True):
        """Detect which site self.source_url belongs to and run the
        matching *_products() scraper method, populating the fields on
        this record. Shared by the single-URL 'Fetch Data' button and the
        bulk-import loop (which passes populate_ambiguous=False, since
        there's no one to review an author/publisher field that got
        several unreviewed candidates stuffed into it)."""
        self.ensure_one()
        url = urlparse(self.source_url)
        host = url.hostname or ''
        domain_part = host.split('.')
        domain_name = None
        for i, part in enumerate(domain_part):
            if part == 'com':
                domain_name = domain_part[i - 1]

        if not domain_name or not hasattr(self, '%s_products' % domain_name):
            raise UserError(f"Cannot import product data from {host}")

        self.publisher_ids = False
        self.author_ids = False
        self.author_suggestion_ids = False
        self.publisher_suggestion_ids = False
        self.category_suggestion_ids = False
        self.duplicate_product_ids = False
        self.nearest_product_ids = False
        self.force_duplicate = False
        result = getattr(self, '%s_products' % domain_name)()

        # Auto-link proposed authors/publishers to existing partners where
        # unambiguous. When more than one similar match is found: in the
        # interactive case (populate_ambiguous=True) all candidates are
        # added directly to Authors/Publishers, so picking the right one
        # is just a matter of removing the wrong tag(s) with their '×' -
        # no separate, unclickable "suggestions" list to fight with.
        self.author_ids, self.author_suggestion_ids, _author_ambiguous = \
            self._find_matching_partners(self.authors, is_writer=True, populate_ambiguous=populate_ambiguous)
        self.publisher_ids, self.publisher_suggestion_ids, _publisher_ambiguous = \
            self._find_matching_partners(self.publishers, is_publisher=True, populate_ambiguous=populate_ambiguous)

        # Same idea for category: only auto-fill it if the user hasn't
        # already picked one themselves (e.g. re-fetching after a manual
        # override), and only overwrite a category we ourselves suggested
        # on a previous fetch.
        auto_category, self.category_suggestion_ids = self._suggest_category()
        if auto_category and not self.categ_id:
            self.categ_id = auto_category

        self.duplicate_product_ids = self._find_duplicate_products()
        self.selected_duplicate_id = (
            self.duplicate_product_ids[0] if len(self.duplicate_product_ids) == 1 else False
        )
        if not self.duplicate_product_ids:
            self.nearest_product_ids = self._find_nearest_products()
        return result

    def fetch_data(self):
        result = self._scrape_source_url()
        warnings = []

        duplicates = self.duplicate_product_ids
        if len(duplicates) == 1:
            warnings.append(
                "An identical product already exists: '%s'. Click 'Update Existing "
                "Product' to edit it directly, or tick 'Create Anyway' if this is "
                "genuinely a different product."
                % duplicates.name
            )
        elif len(duplicates) > 1:
            warnings.append(
                "Multiple possible duplicates found: %s. Pick the correct one in "
                "'Product to Update' below, then click 'Update Existing Product' - "
                "or tick 'Create Anyway' if none of them are actually the same product."
                % ', '.join(duplicates.mapped('name'))
            )

        if self.author_suggestion_ids:
            warnings.append(
                "Multiple existing authors looked similar to the proposed name(s), so "
                "all of them were added to the Authors field below - remove whichever "
                "one(s) don't actually belong using the '×' on each tag."
            )
        if self.publisher_suggestion_ids:
            warnings.append(
                "Multiple existing publishers looked similar to the proposed name(s), "
                "so all of them were added to the Publishers field below - remove "
                "whichever one(s) don't actually belong using the '×' on each tag."
            )
        if self.category_suggestion_ids:
            warnings.append(
                "Multiple existing categories look like a plausible fit - none were "
                "auto-selected. Check 'Similar Categories Found' and pick the right "
                "one in the Category field."
            )

        if warnings:
            return {
                'warning': {
                    'title': "Please review before importing",
                    'message': '\n\n'.join(warnings),
                }
            }
        return result

    # ------------------------------------------------------------------
    # Create / update product
    # ------------------------------------------------------------------

    def _build_product_vals(self):
        self.ensure_one()
        vals = {
            'name': self.product_name,
            'list_price': self.price,
            'compare_list_price': self.face_value,
            'description_ecommerce': self.ecommerce_description,
            'image_url_template': self.image_url,
            'is_storable': True,
            'pages': self.pages,
            'publisher_link': self.source_url,
            'weight': self.weight,
            'categ_id': self.categ_id.id if self.categ_id else False,
        }

        if self.author_ids:
            vals['author_ids'] = [(6, 0, self.author_ids.ids)]
        if self.publisher_ids:
            vals['publisher_ids'] = [(6, 0, self.publisher_ids.ids)]

        if self.isbn:
            vals['isbn'] = self.isbn
        if self.publication_date:
            vals['last_edition'] = self.publication_date

        # Fields defined by the book_shop module that this addon depends
        # on - only set them if they actually exist on the model, so this
        # keeps working even against a product.template that doesn't
        # define all of them.
        target_fields = self.env['product.template']._fields
        for fname, fval in self._optional_fields_map().items():
            if fname in target_fields and fval:
                vals[fname] = fval

        image_b64 = self._download_image_b64(self.image_url)
        if image_b64:
            vals['image_1920'] = image_b64

        return vals

    def _create_product_record(self):
        self.ensure_one()
        product = self.env['product.template'].create(self._build_product_vals())

        # Create the Vendor entry in product.supplierinfo - only the first
        # publisher, matching the original behaviour.
        for publisher in self.publisher_ids:
            self.env['product.supplierinfo'].create({
                'product_tmpl_id': product.id,
                'partner_id': publisher.id,
                'price': self.price,
                'currency_id': self.env.company.currency_id.id,
            })
            break

        return product

    def create_product(self):
        self.ensure_one()
        duplicates = self._find_duplicate_products()
        if duplicates and not self.force_duplicate:
            if len(duplicates) == 1:
                raise UserError(
                    "An identical product already exists: '%s'.\n\n"
                    "Use 'Update Existing Product' to edit it directly, or tick "
                    "'Create Anyway' if this is genuinely a different product."
                    % duplicates.name
                )
            raise UserError(
                "Multiple possible duplicates found: %s.\n\n"
                "Pick the correct one in 'Product to Update' and use 'Update Existing "
                "Product', or tick 'Create Anyway' if none of them are actually the "
                "same product."
                % ', '.join(duplicates.mapped('name'))
            )

        product = self._create_product_record()
        self.env['import.product.log'].create({
            'source_url': self.source_url,
            'status': 'created',
            'product_id': product.id,
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'res_id': product.id,
            'view_mode': 'form',
            'target': 'current',
            'context': self.env.context,
        }

    def update_existing_product(self):
        """Instead of creating a new product, refresh the fields on an
        existing product - either the one the user explicitly picked in
        'Product to Update' (required when more than one duplicate was
        found), or the single unambiguous duplicate."""
        self.ensure_one()
        product = self.selected_duplicate_id
        if not product:
            duplicates = self._find_duplicate_products()
            if not duplicates:
                raise UserError(
                    "No matching existing product found to update - use 'Import Product' "
                    "to create a new one instead."
                )
            if len(duplicates) > 1:
                raise UserError(
                    "Multiple possible duplicates found: %s.\n\n"
                    "Please pick the one you want to update in the 'Product to Update' "
                    "field first."
                    % ', '.join(duplicates.mapped('name'))
                )
            product = duplicates[0]

        vals = {}
        if self.price:
            vals['list_price'] = self.price
        if self.face_value:
            vals['compare_list_price'] = self.face_value
        if self.ecommerce_description:
            vals['description_ecommerce'] = self.ecommerce_description
        if self.image_url:
            vals['image_url_template'] = self.image_url
            image_b64 = self._download_image_b64(self.image_url)
            if image_b64:
                vals['image_1920'] = image_b64
        if self.pages:
            vals['pages'] = self.pages
        if self.weight:
            vals['weight'] = self.weight
        if self.isbn:
            vals['isbn'] = self.isbn
        if self.publication_date:
            vals['last_edition'] = self.publication_date
        if self.author_ids:
            vals['author_ids'] = [(4, pid) for pid in self.author_ids.ids]
        if self.publisher_ids:
            vals['publisher_ids'] = [(4, pid) for pid in self.publisher_ids.ids]

        target_fields = self.env['product.template']._fields
        for fname, fval in self._optional_fields_map().items():
            if fname in target_fields and fval:
                vals[fname] = fval

        if vals:
            product.write(vals)

        self.env['import.product.log'].create({
            'source_url': self.source_url,
            'status': 'updated',
            'product_id': product.id,
        })

        return {
            'type': 'ir.actions.act_window',
            'res_model': 'product.template',
            'res_id': product.id,
            'view_mode': 'form',
            'target': 'current',
            'context': self.env.context,
        }

    # ------------------------------------------------------------------
    # Bulk import
    # ------------------------------------------------------------------

    def action_bulk_import(self):
        self.ensure_one()
        urls = [u.strip() for u in (self.bulk_urls or '').splitlines() if u.strip()]
        if not urls:
            raise UserError("Please paste at least one URL, one per line, in 'Bulk URLs'.")

        Log = self.env['import.product.log']
        created = duplicate = failed = 0
        summary_lines = []

        for idx, url in enumerate(urls):
            if idx:
                # Be polite to the source sites between requests.
                time.sleep(BULK_IMPORT_DELAY)

            temp = self.create({
                'source_url': url,
                'categ_id': self.categ_id.id if self.categ_id else False,
            })
            try:
                try:
                    temp._scrape_source_url(populate_ambiguous=False)
                except UserError as e:
                    raise
                except Exception as e:
                    raise UserError(str(e))

                if not temp.product_name:
                    raise UserError("Could not read product data from this page.")

                duplicates = temp._find_duplicate_products()
                if duplicates:
                    duplicate += 1
                    summary_lines.append(
                        "SKIPPED (duplicate)  %s  ->  matches %s"
                        % (url, ', '.join(duplicates.mapped('name')))
                    )
                    Log.create({
                        'source_url': url,
                        'status': 'duplicate',
                        'message': "Matches: %s" % ', '.join(duplicates.mapped('name')),
                        'product_id': duplicates[0].id,
                    })
                    continue

                product = temp._create_product_record()
                created += 1
                note_parts = []
                if temp.author_suggestion_ids or temp.publisher_suggestion_ids:
                    note_parts.append("author/publisher")
                if temp.category_suggestion_ids:
                    note_parts.append("category")
                if temp.nearest_product_ids:
                    note_parts.append("possible near-duplicate by name")
                note = ""
                if note_parts:
                    note = " (review %s - multiple similar matches found, none applied)" % ' & '.join(note_parts)
                summary_lines.append("CREATED  %s  ->  %s%s" % (url, product.display_name, note))
                Log.create({
                    'source_url': url,
                    'status': 'created',
                    'product_id': product.id,
                    'message': note.strip(" ()") or False,
                })

            except UserError as e:
                failed += 1
                summary_lines.append("FAILED  %s  ->  %s" % (url, e))
                Log.create({'source_url': url, 'status': 'error', 'message': str(e)})
            finally:
                temp.unlink()

        self.bulk_import_summary = (
            "%d created, %d skipped as duplicates, %d failed.\n\n%s"
            % (created, duplicate, failed, '\n'.join(summary_lines))
        )

        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "Bulk import finished",
                'message': "%d created, %d skipped as duplicates, %d failed. "
                           "See 'Last Bulk Import Result' for details." % (created, duplicate, failed),
                'type': 'success' if not failed else 'warning',
                'sticky': True,
            },
        }
