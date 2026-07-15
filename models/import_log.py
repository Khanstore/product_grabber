from odoo import models, fields, api


class ImportProductLog(models.Model):
    _name = 'import.product.log'
    _description = 'Product Import Log'
    _order = 'create_date desc'

    name = fields.Char(string="Summary", compute='_compute_name', store=True)
    source_url = fields.Char(string="Source URL", required=True)
    status = fields.Selection([
        ('created', 'Created'),
        ('updated', 'Updated'),
        ('duplicate', 'Skipped (Duplicate)'),
        ('error', 'Error'),
    ], string="Status", required=True)
    message = fields.Text(string="Details")
    product_id = fields.Many2one('product.template', string="Product")
    user_id = fields.Many2one(
        'res.users', string="Imported By", default=lambda self: self.env.user
    )

    @api.depends('source_url', 'status')
    def _compute_name(self):
        for rec in self:
            rec.name = "[%s] %s" % (rec.status or '', rec.source_url or '')
