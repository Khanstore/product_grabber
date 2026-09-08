{
    'name': 'product_grabber',
    'version': '18.0.0.29',
    'author': 'SM Ashraf',
    'category': 'Reporting',
    'summary': 'grab Product data to odoo from Rokomary, PBS, Sottayan etc',
    'depends': ['book_shop'],
    'data': [
        # 'wizard/business_overview_wizard.xml',
        'security/ir.model.access.csv',
        'views/product_from_website.xml',
        'views/product_template.xml',
        'views/import_log.xml',
        'views/product_gallery.xml',
        'views/duplicate_scan.xml',
        'views/site_search.xml',
        'views/dashboard.xml',
        'views/menu.xml',
        'data/ir_cron.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'product_grabber/static/src/css/product_grabber.css',
        ],
    },
    'installable': True,
    'license': 'LGPL-3',
    'application': True,
}
