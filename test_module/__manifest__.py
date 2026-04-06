{
    'name': 'Invoice OCR Extractor',
    'version': '1.0',
    'summary': 'Extract invoice data from images, PDFs, and Word documents',
    'description': """
        Upload an invoice (Image, PDF, or DOCX), extract key fields
        (Invoice Number, Vendor, Amount, Date, etc.) and store 
        the structured data using AI models (Ollama/Qwen).
    """,
    'depends': ['base'],
    'data': [
        'security/ir.model.access.csv',
        'views/views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'test_module/static/src/scss/style.css',
            'test_module/static/src/js/invoice_extract_form.js',
        ],
    },
    'installable': True,
    'application': True,
    'license': 'LGPL-3',
}