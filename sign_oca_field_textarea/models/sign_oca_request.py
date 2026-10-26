# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from io import BytesIO

from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from odoo import models
from odoo.tools.pdf import PdfFileReader


class SignOcaRequestSigner(models.Model):
    _inherit = "sign.oca.request.signer"

    def _get_pdf_page_textarea(self, item, box):
        """Return the overlay page that draws the multi-line text of ``item``.

        :param dict item: a ``signatory_data`` item of type ``textarea``, its
            position and size in % of the page
        :param box: the media box of the page the item is drawn on
        :return: a one-page PDF page to merge, or False when the item is empty

        The value goes through ``_get_pdf_text_markup``, as sign_oca's text
        fields do: it is escaped and keeps its line breaks. Lines wrap to
        the box width and start at the top of the box; what passes its
        bottom is clipped, so nothing is drawn outside the box.
        """
        if not item["value"]:
            return False
        page_width = float(box.getWidth())
        page_height = float(box.getHeight())
        width = item["width"] / 100 * page_width
        height = item["height"] / 100 * page_height
        x = item["position_x"] / 100 * page_width
        y = (100 - item["position_y"] - item["height"]) / 100 * page_height
        paragraph = Paragraph(
            self._get_pdf_text_markup(item["value"]),
            style=self._get_pdf_textarea_style(),
        )
        __, text_height = paragraph.wrap(width, height)
        packet = BytesIO()
        can = canvas.Canvas(packet, pagesize=(page_width, page_height))
        can.saveState()
        clip = can.beginPath()
        clip.rect(x, y, width, height)
        can.clipPath(clip, stroke=0, fill=0)
        paragraph.drawOn(can, x, y + height - text_height)
        can.restoreState()
        can.save()
        packet.seek(0)
        return PdfFileReader(packet).getPage(0)

    def _get_pdf_textarea_style(self):
        """Return the reportlab ``ParagraphStyle`` of multi-line texts; by
        default the one of sign_oca's text fields."""
        return self._getParagraphStyle()
