import json, xmlrpc.client
import requests
import re, logging
import time
import urllib.parse
from urllib.parse import urlparse
from odoo import models, fields, api
from odoo.exceptions import UserError
import base64

_logger = logging.getLogger(__name__)

# Politeness delay (seconds) between consecutive requests when importing
# many URLs in one go, so we don't hammer the source sites.
BULK_IMPORT_DELAY = 1.5


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    publisher_link = fields.Char("publisher link")

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

    def _find_matching_partners(self, names_str, is_writer=False, is_publisher=False):
        """Search existing res.partner records (authors/publishers) that
        look similar to each proposed name, so we reuse them instead of
        creating duplicate author/publisher partners.

        For each proposed name:
        - exactly one similar existing partner  -> safe to auto-select
        - more than one similar existing partner -> too ambiguous to guess;
          all candidates are returned separately as suggestions instead,
          for a human to pick the right one (or none, if it's new)
        - no similar partner                    -> nothing to match, the
          user can create a new one via the tag widget as before

        Returns a tuple: (auto_matched, suggestions, ambiguous_names)
        """
        self.ensure_one()
        Partner = self.env['res.partner']
        auto_matched = Partner
        suggestions = Partner
        ambiguous_names = []

        for name in self._split_names(names_str):
            domain = [('name', 'ilike', name)]
            if is_writer:
                domain.append(('is_writer', '=', True))
            if is_publisher:
                domain.append(('is_publisher', '=', True))
            candidates = self.env['res.partner'].search(domain, limit=10)

            if len(candidates) == 1:
                auto_matched |= candidates
            elif len(candidates) > 1:
                suggestions |= candidates
                ambiguous_names.append(
                    "%s (%d similar matches: %s)"
                    % (name, len(candidates), ', '.join(candidates.mapped('name')))
                )
            # zero candidates: leave it for manual creation, nothing to do

        return auto_matched, suggestions, ambiguous_names

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

        return matches

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

    def _scrape_source_url(self):
        """Detect which site self.source_url belongs to and run the
        matching *_products() scraper method, populating the fields on
        this record. Shared by the single-URL 'Fetch Data' button and the
        bulk-import loop."""
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
        self.duplicate_product_ids = False
        self.force_duplicate = False
        result = getattr(self, '%s_products' % domain_name)()

        # Auto-link proposed authors/publishers to existing partners where
        # unambiguous; anything with more than one similar match is left
        # for the user to pick, via *_suggestion_ids.
        self.author_ids, self.author_suggestion_ids, _author_ambiguous = \
            self._find_matching_partners(self.authors, is_writer=True)
        self.publisher_ids, self.publisher_suggestion_ids, _publisher_ambiguous = \
            self._find_matching_partners(self.publishers, is_publisher=True)
        self.duplicate_product_ids = self._find_duplicate_products()
        return result

    def fetch_data(self):
        result = self._scrape_source_url()
        warnings = []

        duplicates = self.duplicate_product_ids
        if duplicates:
            warnings.append(
                "This looks like it might already be in your catalog: %s. "
                "Please check the 'Possible Duplicates' list before importing, "
                "or tick 'Create Anyway' if this is intentional."
                % ', '.join(duplicates.mapped('name'))
            )

        if self.author_suggestion_ids:
            warnings.append(
                "Multiple existing authors look similar to the proposed name(s) - "
                "none were auto-selected. Check 'Similar Authors Found' and pick "
                "the right one in the Authors field."
            )
        if self.publisher_suggestion_ids:
            warnings.append(
                "Multiple existing publishers look similar to the proposed name(s) - "
                "none were auto-selected. Check 'Similar Publishers Found' and pick "
                "the right one in the Publishers field."
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
            raise UserError(
                "This looks like it might already be in your catalog: %s.\n\n"
                "Tick 'Create Anyway' if you still want to import this as a new product, "
                "or use 'Update Existing Product' instead to refresh that record."
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
        """Instead of creating a new product, refresh the fields on the
        best-matching existing product (found the same way duplicates are
        detected: ISBN, then source URL, then name)."""
        self.ensure_one()
        duplicates = self._find_duplicate_products()
        if not duplicates:
            raise UserError(
                "No matching existing product found to update - use 'Import Product' "
                "to create a new one instead."
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
                    temp._scrape_source_url()
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
                note = ""
                if temp.author_suggestion_ids or temp.publisher_suggestion_ids:
                    note = " (review author/publisher - multiple similar matches found, none linked)"
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
