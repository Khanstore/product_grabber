from odoo import models, fields, api
from odoo.exceptions import UserError
from .phonetic_utils import similarity

# Minimum similarity to report as a possible duplicate. Set a bit above
# the import-time matching thresholds (which favour recall, since a
# missed auto-match just means one more manual click) - a retroactive
# report the user has to read through benefits more from precision.
DEDUP_REPORT_THRESHOLD = 0.72

# How many phonetic-key-sorted neighbours to compare each record against.
# Comparing every record to every other (O(n^2)) doesn't scale; sorting by
# the already-computed phonetic_key and only comparing nearby neighbours
# ("sorted neighbourhood" method) catches the vast majority of real
# duplicates - which fold to near-identical keys and so end up next to
# each other after sorting - for a fraction of the cost.
DEDUP_SCAN_WINDOW = 8


class ProductGrabberDuplicate(models.Model):
    _name = 'product.grabber.duplicate'
    _description = 'Possible Duplicate (Author/Publisher/Product) found by a catalog scan'
    _order = 'similarity desc, id desc'

    kind = fields.Selection([
        ('author', 'Author'),
        ('publisher', 'Publisher'),
        ('product', 'Product'),
    ], required=True, string="Type")
    partner_1_id = fields.Many2one('res.partner', string="Record 1 (Author/Publisher)")
    partner_2_id = fields.Many2one('res.partner', string="Record 2 (Author/Publisher)")
    product_1_id = fields.Many2one('product.template', string="Record 1 (Product)")
    product_2_id = fields.Many2one('product.template', string="Record 2 (Product)")
    name_1 = fields.Char(string="Name 1")
    name_2 = fields.Char(string="Name 2")
    similarity = fields.Float(string="Similarity", digits=(3, 2))
    state = fields.Selection([
        ('pending', 'Needs Review'),
        ('merged', 'Merged'),
        ('ignored', 'Not a Duplicate'),
    ], default='pending', required=True)

    def action_open_record_1(self):
        self.ensure_one()
        record = self.partner_1_id if self.kind != 'product' else self.product_1_id
        if not record:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': record._name,
            'res_id': record.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_open_record_2(self):
        self.ensure_one()
        record = self.partner_2_id if self.kind != 'product' else self.product_2_id
        if not record:
            return False
        return {
            'type': 'ir.actions.act_window',
            'res_model': record._name,
            'res_id': record.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def action_mark_ignored(self):
        self.write({'state': 'ignored'})

    def action_mark_pending(self):
        self.write({'state': 'pending'})

    def action_merge_partners(self):
        """Merge partner_2 into partner_1 - but ONLY for the relations this
        module itself owns (author_ids/publisher_ids on product.template).
        This deliberately does NOT touch sale orders, invoices, contacts,
        or anything else that might reference the duplicate partner
        elsewhere in your database - repointing references safely across
        every possible module would need Odoo's own contact-merge tooling
        (Contacts app), which this module doesn't assume the specifics of.
        After repointing, partner_2 is archived (not deleted), so nothing
        is lost if this turns out to be wrong."""
        self.ensure_one()
        if self.kind not in ('author', 'publisher'):
            raise UserError("Merging is only available for Author/Publisher duplicates - "
                             "product duplicates need manual review, see the note on this tool.")
        primary, duplicate = self.partner_1_id, self.partner_2_id
        if not primary or not duplicate:
            return False

        field_name = 'author_ids' if self.kind == 'author' else 'publisher_ids'
        Product = self.env['product.template']
        affected = Product.search([(field_name, 'in', duplicate.id)])
        for product in affected:
            current = getattr(product, field_name)
            new_ids = (current - duplicate) | primary
            product.write({field_name: [(6, 0, new_ids.ids)]})

        duplicate.active = False
        self.write({'state': 'merged'})
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': "Merged",
                'message': "Repointed %d product(s) from '%s' to '%s', and archived '%s'. "
                           "This only updated this module's Authors/Publishers links - if "
                           "'%s' is referenced elsewhere (contacts, invoices, etc.), please "
                           "use Odoo's Contacts merge tool for those." % (
                               len(affected), duplicate.name, primary.name, duplicate.name, duplicate.name
                           ),
                'type': 'success',
                'sticky': True,
            },
        }

    @api.model
    def _scan_partners(self, is_writer=False, is_publisher=False):
        kind = 'author' if is_writer else 'publisher'
        domain = [('active', '=', True), ('phonetic_key', '!=', False)]
        domain.append(('is_writer', '=', True) if is_writer else ('is_publisher', '=', True))
        partners = self.env['res.partner'].search(domain, order='phonetic_key')
        records = list(partners)
        seen_pairs = set()
        found = 0
        for i in range(len(records)):
            for j in range(i + 1, min(i + 1 + DEDUP_SCAN_WINDOW, len(records))):
                a, b = records[i], records[j]
                pair_key = (a.id, b.id)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                score = similarity(a.name, b.name)
                if score < DEDUP_REPORT_THRESHOLD:
                    continue
                existing = self.search([
                    ('kind', '=', kind),
                    ('partner_1_id', 'in', [a.id, b.id]),
                    ('partner_2_id', 'in', [a.id, b.id]),
                ], limit=1)
                if existing:
                    continue
                self.create({
                    'kind': kind,
                    'partner_1_id': a.id,
                    'partner_2_id': b.id,
                    'name_1': a.name,
                    'name_2': b.name,
                    'similarity': score,
                })
                found += 1
        return found

    @api.model
    def _scan_products(self):
        domain = [('publisher_link', '!=', False), ('phonetic_key', '!=', False)]
        products = self.env['product.template'].search(domain, order='phonetic_key')
        records = list(products)
        seen_pairs = set()
        found = 0
        for i in range(len(records)):
            for j in range(i + 1, min(i + 1 + DEDUP_SCAN_WINDOW, len(records))):
                a, b = records[i], records[j]
                pair_key = (a.id, b.id)
                if pair_key in seen_pairs:
                    continue
                seen_pairs.add(pair_key)
                score = similarity(a.name, b.name)
                if score < DEDUP_REPORT_THRESHOLD:
                    continue
                existing = self.search([
                    ('kind', '=', 'product'),
                    ('product_1_id', 'in', [a.id, b.id]),
                    ('product_2_id', 'in', [a.id, b.id]),
                ], limit=1)
                if existing:
                    continue
                self.create({
                    'kind': 'product',
                    'product_1_id': a.id,
                    'product_2_id': b.id,
                    'name_1': a.name,
                    'name_2': b.name,
                    'similarity': score,
                })
                found += 1
        return found


class ProductGrabberDuplicateScanWizard(models.TransientModel):
    _name = 'product.grabber.duplicate.scan.wizard'
    _description = 'Scan the catalog for possible duplicate authors/publishers/products'

    scan_authors = fields.Boolean(default=True, string="Authors")
    scan_publishers = fields.Boolean(default=True, string="Publishers")
    scan_products = fields.Boolean(default=True, string="Products")

    def action_run_scan(self):
        self.ensure_one()
        Duplicate = self.env['product.grabber.duplicate']
        found = 0
        if self.scan_authors:
            found += Duplicate._scan_partners(is_writer=True)
        if self.scan_publishers:
            found += Duplicate._scan_partners(is_publisher=True)
        if self.scan_products:
            found += Duplicate._scan_products()

        return {
            'type': 'ir.actions.act_window',
            'name': 'Possible Duplicates',
            'res_model': 'product.grabber.duplicate',
            'view_mode': 'list,form',
            'domain': [('state', '=', 'pending')],
            'target': 'current',
        }
