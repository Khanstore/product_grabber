from datetime import datetime
from odoo import models, fields, api


class ProductGrabberDashboard(models.TransientModel):
    _name = 'product.grabber.dashboard'
    _description = 'Grabb Products - Dashboard'

    imports_today = fields.Integer(compute='_compute_stats', string="Imports Today")
    pending_duplicates = fields.Integer(compute='_compute_stats', string="Pending Duplicates")
    total_books = fields.Integer(compute='_compute_stats', string="Books in Catalog")

    def _compute_stats(self):
        Log = self.env['import.product.log']
        Duplicate = self.env['product.grabber.duplicate']
        Product = self.env['product.template']
        now = datetime.now()
        today_start = datetime(now.year, now.month, now.day)
        for rec in self:
            rec.imports_today = Log.search_count([
                ('create_date', '>=', today_start),
                ('status', 'in', ('created', 'updated')),
            ])
            rec.pending_duplicates = Duplicate.search_count([('state', '=', 'pending')])
            rec.total_books = Product.search_count([('publisher_link', '!=', False)])
