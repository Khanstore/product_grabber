{
    'name': 'product_grabber',
    'version': '18.0.0.1',
    'author': 'SM Ashraf',
    'category': 'Reporting',
    'summary': 'grab Product data to odoo from Rokomary, PBS, Sottayan etc',
    'depends': ['book_shop'],
    'data': [
        # 'wizard/business_overview_wizard.xml',
        'views/product_from_website.xml',
        'views/menu.xml',
        'security/ir.model.access.csv'
    ],
    'installable': True,
    'license': 'LGPL-3',
    'application': True,
}
