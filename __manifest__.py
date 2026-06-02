{
    'name': 'AI Invoice Payment Validator',
    'version': '17.0.1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Upload a proof of payment — AI finds and validates the matching invoice automatically.',
    'author': 'Manzi Osee',
    'website': 'https://github.com/manziosee',
    'license': 'LGPL-3',
    'depends': ['base', 'account', 'mail'],
    'data': [
        'security/ir.model.access.csv',
        'data/sequence.xml',
        'views/menu.xml',
        'views/payment_proof_views.xml',
        'views/res_config_settings_views.xml',
        'wizard/validate_payment_wizard_views.xml',
    ],
    'installable': True,
    'application': True,
    'auto_install': False,
    # Optional dependencies — module loads without them, shows install hint to user
    # pip install groq pdfminer.six PyMuPDF pillow
}
