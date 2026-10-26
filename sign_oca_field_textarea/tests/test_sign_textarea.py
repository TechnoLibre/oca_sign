# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from io import BytesIO

from pypdf import PdfReader
from reportlab.pdfgen import canvas

from odoo.tools import misc
from odoo.tools.pdf import PdfFileReader, PdfFileWriter

from odoo.addons.base.tests.common import BaseCommon


def text_lines(pdf_bytes):
    """Return the ``(text, y)`` of each text line of the first page."""
    lines = []

    def visitor(text, cm, tm, font_dict, font_size):
        if text.strip():
            lines.append((text.strip(), tm[5] * cm[3] + cm[5]))

    PdfReader(BytesIO(pdf_bytes)).pages[0].extract_text(visitor_text=visitor)
    return lines


class TestSignTextarea(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.field = cls.env["sign.oca.field"].create(
            {"name": "Remarks", "field_type": "textarea"}
        )

    def _box(self):
        stream = BytesIO()
        pdf = canvas.Canvas(stream, pagesize=(600, 800))
        pdf.showPage()
        pdf.save()
        return PdfFileReader(BytesIO(stream.getvalue())).getPage(0).mediaBox

    def _draw(self, value, height=10):
        """Draw ``value`` in a 30 % wide box at (10 %, 50 %) of a 600 x 800
        page and return the PDF bytes; the box is ``height`` % high."""
        item = {
            "field_type": "textarea",
            "value": value,
            "position_x": 10,
            "position_y": 50,
            "width": 30,
            "height": height,
        }
        page = self.env["sign.oca.request.signer"]._get_pdf_page(item, self._box())
        if not page:
            return False
        stream = BytesIO()
        writer = PdfFileWriter()
        writer.addPage(page)
        writer.write(stream)
        return stream.getvalue()

    def test_field_type(self):
        selection = dict(
            self.env["sign.oca.field"]
            ._fields["field_type"]
            ._description_selection(self.env)
        )
        self.assertIn("textarea", selection)
        keys = [key for key, __ in self.field._fields["field_type"].selection]
        self.assertEqual(keys.index("textarea"), keys.index("text") + 1)

    def test_empty_value_draws_nothing(self):
        self.assertFalse(self._draw(""))
        self.assertFalse(self._draw(False))

    def test_lines_kept_from_the_top(self):
        pdf = self._draw("First line\r\nSecond line")
        lines = text_lines(pdf)
        self.assertEqual([text for text, __ in lines], ["First line", "Second line"])
        bottom, top = 800 * 0.4, 800 * 0.5
        first_y, second_y = lines[0][1], lines[1][1]
        self.assertGreater(first_y, second_y)
        # The first line sits in the top line height of the box (12 points
        # of leading), not at its bottom as a one-line text does.
        self.assertTrue(top - 12 <= first_y <= top, first_y)
        self.assertTrue(bottom <= second_y, second_y)

    def test_value_is_escaped(self):
        pdf = self._draw('<img src="/etc/passwd"/> & <b>bold</b>')
        text = " ".join(text for text, __ in text_lines(pdf))
        self.assertIn('<img src="/etc/passwd"/>', text)
        self.assertIn("<b>bold</b>", text)

    def test_long_text_clipped_to_box(self):
        pdf = self._draw("\n".join(f"line {number}" for number in range(30)), 3)
        content = PdfReader(BytesIO(pdf)).pages[0].get_contents().get_data()
        # Clipping path of the box: x 60, y 800 * 0.47, 180 x 24 points.
        self.assertRegex(content.replace(b"\n", b" "), rb"60 376 180 24 re W\*? n")
        first = [y for text, y in text_lines(pdf) if text == "line 0"]
        self.assertTrue(first and 376 <= first[0] <= 400)

    def test_sign_stamps_lines(self):
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        role = self.env.ref("sign_oca.sign_role_customer")
        partner = self.env["res.partner"].create({"name": "Textarea signer"})
        request = self.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Textarea test",
                "signer_ids": [(0, 0, {"partner_id": partner.id, "role_id": role.id})],
            }
        )
        item = request.add_item(
            {
                "role_id": role.id,
                "field_id": self.field.id,
                "page": 1,
                "position_x": 10,
                "position_y": 10,
                "width": 40,
                "height": 10,
            }
        )
        self.assertEqual(item["field_type"], "textarea")
        request.action_send()
        signer = request.signer_ids
        signer.action_sign(
            {str(item["id"]): dict(item, value="Approved as is\nDelivery in May")}
        )
        self.assertEqual(request.state, "2_signed")
        stored = request.signatory_data[str(item["id"])]
        self.assertEqual(stored["value"], "Approved as is\nDelivery in May")
        texts = [text for text, __ in text_lines(base64.b64decode(request.data))]
        self.assertIn("Approved as is", texts)
        self.assertIn("Delivery in May", texts)
