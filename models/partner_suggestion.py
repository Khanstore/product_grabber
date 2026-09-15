from odoo import models, fields


class ImportProductPartnerSuggestion(models.TransientModel):
    _name = 'import.product.partner.suggestion'
    _description = 'A candidate author/publisher suggested during import, with a checkbox to select it'
    _order = 'id'

    wizard_id = fields.Many2one('import.product.from.website', ondelete='cascade', required=True)
    kind = fields.Selection([('author', 'Author'), ('publisher', 'Publisher')], required=True)
    partner_id = fields.Many2one('res.partner', required=True, readonly=True)
    partner_name = fields.Char(related='partner_id.name', string="Name", readonly=True)
    selected = fields.Boolean(string="Select")


class ImportProductDuplicateSuggestion(models.TransientModel):
    _name = 'import.product.duplicate.suggestion'
    _description = (
        'A candidate existing product (confident duplicate or just a close '
        'title match) suggested during import, with a button to update it '
        'directly'
    )
    _order = 'match_type, id'

    wizard_id = fields.Many2one('import.product.from.website', ondelete='cascade', required=True)
    match_type = fields.Selection(
        [('duplicate', 'Likely Duplicate'), ('nearest', 'Closest Match')],
        required=True, default='duplicate',
    )
    product_id = fields.Many2one('product.template', required=True, readonly=True)
    product_name = fields.Char(related='product_id.name', string="Name", readonly=True)

    def action_update_this_product(self):
        """Row-level shortcut: sets this row's product as the wizard's
        'Product to Update' and immediately runs the update - one click
        instead of ticking a row then pressing a separate button."""
        self.ensure_one()
        self.wizard_id.selected_duplicate_id = self.product_id
        return self.wizard_id.update_existing_product()

    def action_view_product(self):
        """Open this row's existing product in a popup so it can be
        reviewed without navigating away from (or losing progress on)
        the import wizard. Opens in Odoo's normal read-only display mode
        - click 'Edit' on the popup itself if you actually need to
        change something there."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': self.product_id.display_name,
            'res_model': 'product.template',
            'res_id': self.product_id.id,
            'view_mode': 'form',
            'target': 'new',
        }
