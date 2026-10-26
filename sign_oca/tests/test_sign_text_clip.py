# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from io import BytesIO

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from odoo.tools.pdf import PdfFileReader, PdfFileWriter

from odoo.addons.base.tests.common import BaseCommon


class TestSignTextClip(BaseCommon):
    def _box(self):
        stream = BytesIO()
        pdf = canvas.Canvas(stream, pagesize=(600, 800))
        pdf.showPage()
        pdf.save()
        return PdfFileReader(BytesIO(stream.getvalue())).getPage(0).mediaBox

    def _text_positions(self, value):
        """Draw ``value`` in a 30 x 2.5 % box at (10 %, 50 %) and return the
        ``(text, y)`` of each line, with the box bottom and top in points."""
        item = {
            "field_type": "text",
            "value": value,
            "position_x": 10,
            "position_y": 50,
            "width": 30,
            "height": 2.5,
        }
        page = self.env["sign.oca.request.signer"]._get_pdf_page(item, self._box())
        writer_stream = BytesIO()
        writer = PdfFileWriter()
        writer.addPage(page)
        writer.write(writer_stream)
        lines = []

        def visitor(text, cm, tm, font_dict, font_size):
            if text.strip():
                lines.append((text.strip(), tm[5] * cm[3] + cm[5]))

        reader = PdfReader(BytesIO(writer_stream.getvalue()))
        reader.pages[0].extract_text(visitor_text=visitor)
        content = reader.pages[0].get_contents().get_data()
        return lines, content, 800 * 0.475, 800 * 0.5

    def test_short_text_at_box_bottom(self):
        lines, content, bottom, top = self._text_positions("One line")
        self.assertEqual(len(lines), 1)
        self.assertTrue(bottom <= lines[0][1] <= top)

    def test_long_text_starts_in_box_and_is_clipped(self):
        value = "\n".join(f"line {number}" for number in range(15))
        lines, content, bottom, top = self._text_positions(value)
        first = [y for text, y in lines if text == "line 0"]
        self.assertTrue(first)
        self.assertTrue(bottom <= first[0] <= top)
        # The drawing is clipped to the box: its rectangle, then W n or W* n.
        self.assertRegex(content.replace(b"\n", b" "), rb"60 380 180 20 re W\*? n")
