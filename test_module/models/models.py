import base64
import io
import json
import logging

from odoo import models, fields, api
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

try:
    import easyocr
    EASYOCR_AVAILABLE = True
except ImportError:
    EASYOCR_AVAILABLE = False
    _logger.warning("easyocr is not installed. Invoice OCR will not work.")

try:
    from PIL import Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    _logger.warning("Pillow is not installed. Invoice OCR will not work.")

try:
    import ollama
    OLLAMA_AVAILABLE = True
except ImportError:
    OLLAMA_AVAILABLE = False
    _logger.warning("ollama package is not installed. LLM extraction will not work.")

try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
    _logger.warning("pymupdf is not installed. PDF extraction will not work.")

try:
    import docx
    DOCX_AVAILABLE = True
except ImportError:
    DOCX_AVAILABLE = False
    _logger.warning("python-docx is not installed. Word extraction will not work.")


# ── Prompt sent to the LLM ───────────────────────────────────────────────────
EXTRACTION_PROMPT = """You are an intelligent invoice data extractor.

Below is the raw OCR text from an invoice. Identify and return these fields as a strict JSON object:

{{
  "invoice_number": "<invoice number or null>",
  "vendor_name": "<vendor / supplier / company name or null>",
  "invoice_date": "<date in YYYY-MM-DD format or null>",
  "due_date": "<due / payment date in YYYY-MM-DD format or null>",
  "total_amount": <numeric total amount as a float, or null>,
  "currency": "<currency code e.g. USD, EUR, INR or null>",
  "items": [
    {{
      "name": "<description of the item or service>",
      "quantity": <numeric quantity as float or 1.0 if not listed>,
      "unit_price": <numeric rate/price per unit as float or null>,
      "total_price": <numeric total for this line as float>
    }}
  ]
}}

CRITICAL RULES FOR TOTAL_AMOUNT & ITEMS:
1. "total_amount" must be the full value of the goods/services (Grand Total).
2. DO NOT extract "Balance Due", "Amount Due", or "Remaining Balance" as the total. 
3. Return ONLY plain numbers (e.g. 1564.00) for all amounts, quantities, and prices. No currency symbols or commas.
4. Extract every line item listed on the invoice and add it to the "items" array. Include description, qty, rate, and amount.

OTHER RULES:
- Return ONLY the JSON object. No explanation, no markdown, no code fences.
- If a field is missing, set it to null.
- Convert all dates to YYYY-MM-DD format.

RAW OCR TEXT:
{ocr_text}
"""


class InvoiceExtractor(models.Model):
    _name = 'test.model'
    _description = 'Invoice Extractor'
    _order = 'create_date desc'

    # ── Upload field (Universal File Upload) ──────────────────────────────
    invoice_file = fields.Binary(
        string="Invoice File",
        help="Upload an invoice (PDF, DOCX, PNG, JPG).",
    )
    invoice_filename = fields.Char(string="Filename")

    # ── Extracted fields ─────────────────────────────────────────────────────
    name = fields.Char(string="Invoice Number")
    vendor_name = fields.Char(string="Vendor / Supplier")
    invoice_date = fields.Date(string="Invoice Date")
    total_amount = fields.Float(
        string="Total Amount", 
        digits=(12, 2), 
        compute='_compute_total_amount', 
        store=True
    )
    currency_id = fields.Many2one('res.currency', string="Currency")
    due_date = fields.Date(string="Due Date")
    
    # ── One2many for Lines ───────────────────────────────────────────────────
    invoice_lines = fields.One2many(
        comodel_name='test.model.line',
        inverse_name='invoice_id',
        string="Invoice Lines"
    )

    is_image = fields.Boolean(compute='_compute_file_types')
    is_pdf = fields.Boolean(compute='_compute_file_types')

    @api.depends('invoice_filename')
    def _compute_file_types(self):
        for record in self:
            ext = (record.invoice_filename or "").lower().split('.')[-1]
            record.is_image = ext in ['png', 'jpg', 'jpeg', 'webp']
            record.is_pdf = ext == 'pdf'

    @api.depends('invoice_lines.total_price')
    def _compute_total_amount(self):
        for record in self:
            record.total_amount = sum(record.invoice_lines.mapped('total_price'))

    # ── State ─────────────────────────────────────────────────────────────────
    state = fields.Selection([
        ('draft', 'Draft'),
        ('extracted', 'Extracted'),
    ], string="Status", default='draft', readonly=True)

    # ─────────────────────────────────────────────────────────────────────────
    #  Main action: Extract → LLM → Store
    # ─────────────────────────────────────────────────────────────────────────
    def action_extract_invoice(self):
        self.ensure_one()

        if not self.invoice_file:
            raise UserError("Please upload an invoice file first.")
        if not EASYOCR_AVAILABLE:
            raise UserError("easyocr is not installed in Odoo's Python environment.")
        if not PIL_AVAILABLE:
            raise UserError("Pillow is not installed in Odoo's Python environment.")
        if not OLLAMA_AVAILABLE:
            raise UserError("ollama package is not installed in Odoo's Python environment.")

        # ── Step 1: Detect File Type & Extract Text ───────────────────────
        file_bytes = base64.b64decode(self.invoice_file)
        ext = (self.invoice_filename or "").lower().split('.')[-1]
        raw_text = ""

        if ext == 'pdf':
            if not PYMUPDF_AVAILABLE:
                raise UserError("pymupdf is not installed to handle PDFs.")
            raw_text = self._extract_text_from_pdf(file_bytes)
        elif ext in ['doc', 'docx']:
            if not DOCX_AVAILABLE:
                raise UserError("python-docx is not installed to handle Word files.")
            raw_text = self._extract_text_from_docx(file_bytes)
        elif ext in ['png', 'jpg', 'jpeg', 'webp']:
            if not EASYOCR_AVAILABLE:
                raise UserError("easyocr is not installed to handle images.")
            _logger.info("Running EasyOCR on image…")
            reader = easyocr.Reader(['en'], gpu=False)
            results = reader.readtext(file_bytes, detail=0, paragraph=True)
            raw_text = "\n".join(results)
        else:
            # Try OCR as fallback for unknown common image types
            if EASYOCR_AVAILABLE:
                _logger.info("Unknown extension '%s', attempting EasyOCR fallback…", ext)
                reader = easyocr.Reader(['en'], gpu=False)
                results = reader.readtext(file_bytes, detail=0, paragraph=True)
                raw_text = "\n".join(results)
            else:
                raise UserError(f"Unsupported file format (.{ext}) and OCR is unavailable.")

        if not raw_text or not raw_text.strip():
            raise UserError(
                "Could not extract any text from the document. "
                "Please ensure the file is clear and not password protected."
            )

        _logger.info("Extraction complete. Raw text length: %d", len(raw_text))

        # ── Step 2: LLM parsing via Ollama ────────────────────────────────
        parsed = self._extract_with_llm(raw_text)
        _logger.info("LLM parsed result: %s", parsed)

        # ── Step 3: Write fields & clear image ────────────────────────────
        def _safe_float(val, default=0.0):
            try:
                return float(val) if val is not None else default
            except (ValueError, TypeError):
                return default

        def _safe_str(val):
             return str(val) if val is not None else ''

        def _safe_date(val):
             return val if val and str(val).lower() != 'null' else False
             
        # Parse items into Odoo command format (0, 0, {values})
        line_commands = [(5, 0, 0)]  # clear existing lines first
        
        items = parsed.get('items')
        if isinstance(items, list):
            for item in items:
                line_commands.append((0, 0, {
                    'name': _safe_str(item.get('name', '')),
                    'quantity': _safe_float(item.get('quantity'), default=1.0),
                    'unit_price': _safe_float(item.get('unit_price')),
                    'total_price': _safe_float(item.get('total_price')),
                }))

        currency_code = _safe_str(parsed.get('currency')).upper()
        currency_rec = self.env['res.currency'].search([('name', '=', currency_code)], limit=1)

        self.write({
            'name':          _safe_str(parsed.get('invoice_number')),
            'vendor_name':   _safe_str(parsed.get('vendor_name')),
            'invoice_date':  _safe_date(parsed.get('invoice_date')),
            'due_date':      _safe_date(parsed.get('due_date')),
            'total_amount':  _safe_float(parsed.get('total_amount')),
            'currency_id':   currency_rec.id if currency_rec else False,
            'invoice_lines': line_commands,
            'state':         'extracted',
            # File is cleared — we only needed it for extraction
            'invoice_file':     False,
            'invoice_filename': False,
        })

        # Return a simple client reload action so the form updates immediately
        return {
            'type': 'ir.actions.client',
            'tag': 'reload',
        }

    # ─────────────────────────────────────────────────────────────────────────
    #  Format Helpers
    # ─────────────────────────────────────────────────────────────────────────
    def _extract_text_from_pdf(self, pdf_bytes):
        """Extract text from PDF. Falls back to OCR if no digital text layer is found."""
        full_text = []
        try:
            with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
                reader = None
                for page_index, page in enumerate(doc):
                    page_text = page.get_text().strip()
                    
                    if not page_text and EASYOCR_AVAILABLE:
                        _logger.info("No text layer on PDF page %d, falling back to OCR…", page_index)
                        # Render page to image (pixmap)
                        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # 2x zoom for better OCR
                        img_bytes = pix.tobytes("png")
                        
                        if not reader:
                            reader = easyocr.Reader(['en'], gpu=False)
                        
                        results = reader.readtext(img_bytes, detail=0, paragraph=True)
                        page_text = "\n".join(results)
                    
                    if page_text:
                        full_text.append(page_text)
                        
        except Exception as e:
            _logger.error("Data extraction from PDF failed: %s", e)
            raise UserError(f"Error reading PDF: {e}")
            
        return "\n".join(full_text)

    def _extract_text_from_docx(self, docx_bytes):
        """Extract text from a Word document using python-docx"""
        try:
            doc = docx.Document(io.BytesIO(docx_bytes))
            return "\n".join([para.text for para in doc.paragraphs])
        except Exception as e:
            _logger.error("python-docx failed to read DOCX: %s", e)
            raise UserError(f"Error reading Word document: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    #  LLM helper
    # ─────────────────────────────────────────────────────────────────────────
    @api.model
    def _extract_with_llm(self, raw_text):
        """
        Send OCR text to Ollama (qwen2.5:7b) and return a parsed dict
        """
        prompt = EXTRACTION_PROMPT.format(ocr_text=raw_text)

        _logger.info("Sending OCR text to Ollama (qwen2.5:7b)…")
        try:
            response = ollama.chat(
                model='qwen2.5:7b',
                messages=[{'role': 'user', 'content': prompt}],
                options={'temperature': 0},   # deterministic output
            )
        except Exception as e:
            raise UserError(
                f"Could not reach Ollama. Make sure Ollama is running locally and the model is pulled.\n"
                f"Try running 'ollama run qwen2.5:7b' in your terminal.\n"
                f"Error: {e}"
            )

        content = response.get('message', {}).get('content', '').strip()
        _logger.info("Raw LLM response: %s", content)

        # ── Parse JSON from LLM response ──────────────────────────────────
        if content.startswith('```'):
            lines = content.split('\n')
            if len(lines) > 1 and lines[0].startswith('```'):
                content = '\n'.join(lines[1:])
            if content.endswith('```'):
                content = '\n'.join(content.split('\n')[:-1])
        
        content = content.strip('`').strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            _logger.error("LLM returned non-JSON content: %s\nError: %s", content, e)
            import re
            json_match = re.search(r'\{.*\}', content, re.DOTALL)
            if json_match:
                 try:
                     data = json.loads(json_match.group(0))
                 except json.JSONDecodeError:
                     data = {}
            else:
                 data = {}

        return data

    # ─────────────────────────────────────────────────────────────────────────
    #  Reset to draft
    # ─────────────────────────────────────────────────────────────────────────
    def action_reset_draft(self):
        self.ensure_one()
        self.write({
            'state': 'draft',
            'invoice_lines': [(5, 0, 0)] # Delete all existing lines
        })


class InvoiceLine(models.Model):
    _name = 'test.model.line'
    _description = 'Invoice Line'

    invoice_id = fields.Many2one(
        comodel_name='test.model',
        string='Invoice Reference',
        required=True,
        ondelete='cascade',
        index=True,
    )
    name = fields.Char(string='Description', required=True)
    quantity = fields.Float(string='Quantity', default=1.0, digits=(12, 2))
    unit_price = fields.Float(string='Unit Price', digits=(12, 2))
    total_price = fields.Float(string='Amount', digits=(12, 2))
