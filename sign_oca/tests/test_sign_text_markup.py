# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from io import BytesIO

from odoo.tools import misc
from odoo.tools.pdf import PdfFileReader

from odoo.addons.base.tests.common import BaseCommon


class TestSignTextMarkup(BaseCommon):
    def _render_text(self, value):
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            box = PdfFileReader(BytesIO(pdf.read())).getPage(0).mediaBox
        item = {
            "value": value,
            "position_x": 5,
            "position_y": 5,
            "width": 90,
            "height": 50,
        }
        page = self.env["sign.oca.request.signer"]._get_pdf_page_text(item, box)
        return page.extract_text()

    def test_markup_is_shown_verbatim(self):
        text = self._render_text("<b>bold</b> & <img src='missing.png'/>")
        self.assertIn("<b>bold</b>", text)
        self.assertIn("<img src='missing.png'/>", text)

    def test_line_breaks_are_kept(self):
        text = self._render_text("first line\nsecond line")
        self.assertEqual(
            [line.strip() for line in text.splitlines() if line.strip()],
            ["first line", "second line"],
        )
