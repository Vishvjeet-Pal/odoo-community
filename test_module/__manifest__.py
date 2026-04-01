{
    'name': 'Invoice OCR Extractor',
    'version': '1.0',
    'summary': 'Extract invoice data from images using OCR',
    'description': """
        Upload an invoice image, extract key fields
        (Invoice Number, Vendor, Amount, Date, etc.) using
        EasyOCR, and store the structured data.
    """,
    'depends': ['base'],
    'data': [
        'security/ir.model.access.csv',
        'views/views.xml',
    ],
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}