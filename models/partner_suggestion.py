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
