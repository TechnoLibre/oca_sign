# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from base64 import b64decode, b64encode
from io import BytesIO

from pypdf import PdfReader
from reportlab.lib.colors import white
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas

from odoo.exceptions import UserError
from odoo.tools import mute_logger

from odoo.addons.base.tests.common import BaseCommon

LOGGER = "odoo.addons.sign_oca_anchor.models.sign_oca_request"
WIDTH, HEIGHT = A4
# Tolerance, in % of the page, between computed and expected positions.
TOLERANCE = 0.05


def _pct_x(x):
    return x / WIDTH * 100


def _pct_y(top):
    return (HEIGHT - top) / HEIGHT * 100


def _build_pdf(pages):
    """Return a PDF with one page per list of ``(x, y, size, text, white)``.

    ``x`` and ``y`` are the start of the baseline in points, origin at the
    bottom-left corner of an A4 page; a ``"scaled"`` entry
    ``("scaled", tx, ty, scale, x, y, size, text)`` draws under a
    translation and a scale.
    """
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=A4)
    for lines in pages:
        for line in lines:
            pdf.saveState()
            if line[0] == "scaled":
                _tag, tx, ty, scale, x, y, size, text = line
                pdf.translate(tx, ty)
                pdf.scale(scale, scale)
                is_white = False
            else:
                x, y, size, text, is_white = line
            if is_white:
                pdf.setFillColor(white)
            pdf.setFont("Helvetica", size)
            pdf.drawString(x, y, text)
            pdf.restoreState()
        pdf.showPage()
    pdf.save()
    return stream.getvalue()


class TestSignOcaAnchor(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.prefix = "Approved by: "
        cls.pdf = _build_pdf(
            [
                [
                    (100, 700, 2, "[[sig:customer:signature:w=25:h=6:req=1]]", True),
                    (50, 200, 12, cls.prefix + "[[sig:employee:check:dx=-1]]", False),
                ],
                [
                    ("scaled", 200, 300, 2, 10, 10, 5, "[[sig:2:name]]"),
                    (400, 100, 8, "[[sig:customer:text:w=30]]", True),
                ],
            ]
        )
        cls.role_customer = cls.env.ref("sign_oca.sign_role_customer")
        cls.role_employee = cls.env.ref("sign_oca.sign_role_employee")
        cls.field_signature = cls.env.ref("sign_oca.sign_field_signature")
        cls.field_check = cls.env.ref("sign_oca.sign_field_check")
        cls.field_name = cls.env.ref("sign_oca.sign_field_name")
        cls.field_text = cls.env.ref("sign_oca.sign_field_text")
        cls.partner_a = cls.env["res.partner"].create({"name": "Signer A"})
        cls.partner_b = cls.env["res.partner"].create({"name": "Signer B"})

    def _create_request(self, pdf=None, auto=False):
        return (
            self.env["sign.oca.request"]
            .with_context(sign_oca_anchor_auto=auto)
            .create(
                {
                    "name": "Anchored document",
                    "data": b64encode(pdf or self.pdf),
                    "signer_ids": [
                        (
                            0,
                            0,
                            {
                                "partner_id": self.partner_a.id,
                                "role_id": self.role_customer.id,
                            },
                        ),
                        (
                            0,
                            0,
                            {
                                "partner_id": self.partner_b.id,
                                "role_id": self.role_employee.id,
                            },
                        ),
                    ],
                }
            )
        )

    def _items_by_field(self, request):
        return {item["field_id"]: item for item in request.signatory_data.values()}

    def test_find_anchors_positions(self):
        anchors = self.env["sign.oca.request"]._find_pdf_anchors(self.pdf)
        self.assertEqual(len(anchors), 4)
        by_field = {anchor["field_ref"]: anchor for anchor in anchors}
        signature = by_field["signature"]
        self.assertEqual(signature["page"], 1)
        self.assertEqual(signature["role_ref"], "customer")
        self.assertEqual(signature["options"], {"w": 25.0, "h": 6.0, "req": 1.0})
        self.assertAlmostEqual(signature["position_x"], _pct_x(100), delta=TOLERANCE)
        self.assertAlmostEqual(
            signature["position_y"], _pct_y(700 + 2), delta=TOLERANCE
        )
        # The anchor starts after visible text on the same run.
        check = by_field["check"]
        prefix_width = pdfmetrics.stringWidth(self.prefix, "Helvetica", 12)
        self.assertAlmostEqual(
            check["position_x"], _pct_x(50 + prefix_width), delta=TOLERANCE
        )
        self.assertAlmostEqual(check["position_y"], _pct_y(212), delta=TOLERANCE)
        # Translated and scaled twice: baseline at (220, 320), font 10.
        name = by_field["name"]
        self.assertEqual(name["page"], 2)
        self.assertEqual(name["role_ref"], "2")
        self.assertAlmostEqual(name["position_x"], _pct_x(220), delta=TOLERANCE)
        self.assertAlmostEqual(name["position_y"], _pct_y(330), delta=TOLERANCE)
        self.assertAlmostEqual(name["line_height"], 10 / HEIGHT * 100, delta=0.01)

    def test_place_items(self):
        request = self._create_request()
        self.assertFalse(request.signatory_data)
        original = request.data
        action = request.action_place_from_anchors()
        self.assertEqual(action["params"]["type"], "success")
        items = self._items_by_field(request)
        self.assertEqual(len(items), 4)

        signature = items[self.field_signature.id]
        self.assertEqual(signature["role_id"], self.role_customer.id)
        self.assertEqual(signature["field_type"], "signature")
        self.assertEqual(signature["page"], 1)
        self.assertEqual((signature["width"], signature["height"]), (25.0, 6.0))
        self.assertTrue(signature["required"])
        self.assertAlmostEqual(signature["position_x"], _pct_x(100), delta=TOLERANCE)
        self.assertAlmostEqual(signature["position_y"], _pct_y(702), delta=TOLERANCE)

        check = items[self.field_check.id]
        self.assertEqual(check["role_id"], self.role_employee.id)
        self.assertFalse(check["required"])
        prefix_width = pdfmetrics.stringWidth(self.prefix, "Helvetica", 12)
        self.assertAlmostEqual(
            check["position_x"], _pct_x(50 + prefix_width) - 1, delta=TOLERANCE
        )

        name = items[self.field_name.id]
        self.assertEqual(name["role_id"], self.role_employee.id)
        self.assertEqual(name["page"], 2)
        self.assertEqual((name["width"], name["height"]), (20.0, 1.5))

        text = items[self.field_text.id]
        self.assertEqual(text["role_id"], self.role_customer.id)
        self.assertEqual(text["width"], 30.0)

        # Reading order: page, then top, then left.
        order = sorted(items.values(), key=lambda item: item["tabindex"])
        self.assertEqual(
            [item["field_id"] for item in order],
            [
                self.field_signature.id,
                self.field_check.id,
                self.field_name.id,
                self.field_text.id,
            ],
        )
        # Placement only: the document and its anchor texts are unchanged.
        self.assertEqual(request.data, original)
        reader = PdfReader(BytesIO(b64decode(request.data)))
        self.assertIn("[[sig:customer:signature", reader.pages[0].extract_text())

    def test_place_again_keeps_items(self):
        request = self._create_request()
        request._place_items_from_anchors()
        key = next(
            key
            for key, item in request.signatory_data.items()
            if item["field_id"] == self.field_text.id
        )
        request.set_item_data(key, {"position_x": 1.0, "position_y": 2.0})
        action = request.action_place_from_anchors()
        self.assertEqual(action["params"]["type"], "warning")
        self.assertEqual(len(request.signatory_data), 4)
        self.assertEqual(request.signatory_data[key]["position_x"], 1.0)
        request.delete_item(key)
        self.assertEqual(len(request._place_items_from_anchors()), 1)
        self.assertEqual(len(request.signatory_data), 4)

    def test_place_on_given_pages(self):
        request = self._create_request()
        items = request._place_items_from_anchors(pages={1})
        self.assertEqual(len(items), 2)
        self.assertEqual({item["page"] for item in items}, {1})
        # The anchors of page 2 stay available to a later call.
        self.assertEqual(len(request._place_items_from_anchors()), 2)

    def test_place_with_key(self):
        """With a key, only the anchors carrying it as their ``k`` option
        place a field; the others are logged and ignored."""
        pdf = _build_pdf(
            [
                [
                    (100, 700, 8, "[[sig:customer:signature:k=123456789]]", True),
                    (100, 600, 8, "[[sig:customer:text:w=30]]", True),
                    (100, 500, 8, "[[sig:employee:check:k=987654321]]", True),
                ]
            ]
        )
        request = self._create_request(pdf)
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            items = request._place_items_from_anchors(key=123456789)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["role_id"], self.role_customer.id)
        self.assertIn("without the document's key", logs.output[0])
        self.assertIn("k=987654321", logs.output[0])
        # Without a key, every anchor places a field, the key included.
        self.assertEqual(len(request._place_items_from_anchors()), 2)

    def test_unresolved_anchors(self):
        pdf = _build_pdf(
            [
                [
                    (50, 700, 4, "[[sig:customer:signature]]", True),
                    (50, 600, 4, "[[sig:nobody:signature]]", True),
                    (50, 500, 4, "[[sig:customer:nothing]]", True),
                    (50, 400, 4, "[[sig:customer:text:zz=1]]", True),
                    (50, 300, 4, "[[sig:9:text]]", True),
                ]
            ]
        )
        request = self._create_request(pdf=pdf)
        with self.assertRaisesRegex(UserError, "nobody") as error:
            request.action_place_from_anchors()
        message = str(error.exception)
        self.assertIn("unknown role", message)
        self.assertIn("unknown field", message)
        self.assertIn("unknown option zz", message)
        self.assertIn("[[sig:9:text]]", message)
        self.assertNotIn("[[sig:customer:signature]]", message)
        self.assertFalse(request.signatory_data)
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            items = request._place_items_from_anchors(raise_if_unresolved=False)
        self.assertIn("[[sig:nobody:signature]]", logs.output[0])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["field_id"], self.field_signature.id)

    def test_auto_place_on_create(self):
        request = self._create_request(auto=True)
        self.assertEqual(len(request.signatory_data), 4)
        with (
            mute_logger("pypdf"),
            self.assertLogs(LOGGER, level="WARNING"),
        ):
            broken = self._create_request(pdf=b"%PDF-1.4 not a document", auto=True)
        self.assertFalse(broken.signatory_data)

    def test_mapping_and_resolution(self):
        role_lead = self.env["sign.oca.role"].create({"name": "Team Lead"})
        request = self._create_request()
        self.assertEqual(request._resolve_anchor_role("team_lead"), role_lead)
        self.assertEqual(request._resolve_anchor_role("TEAM-LEAD"), role_lead)
        self.assertEqual(request._resolve_anchor_role("1"), self.role_customer)
        self.assertEqual(request._resolve_anchor_role("2"), self.role_employee)
        self.assertFalse(request._resolve_anchor_role("3"))
        self.assertEqual(
            request._resolve_anchor_role("sign_oca.sign_role_employee"),
            self.role_employee,
        )
        self.assertEqual(
            request._resolve_anchor_field("sign_oca.sign_field_email"),
            self.env.ref("sign_oca.sign_field_email"),
        )
        self.assertEqual(
            request._resolve_anchor_field("signature"), self.field_signature
        )
        mapping = {
            "roles": {"customer": role_lead, "employee": "sign_oca.sign_role_customer"},
            "fields": {"name": self.field_text.id},
        }
        items = request._place_items_from_anchors(mapping=mapping)
        by_field = {item["field_id"]: item for item in items}
        self.assertEqual(by_field[self.field_signature.id]["role_id"], role_lead.id)
        self.assertEqual(
            by_field[self.field_check.id]["role_id"], self.role_customer.id
        )
        # "name" is mapped to the Text field; "2" still names the employee.
        texts = [item for item in items if item["field_id"] == self.field_text.id]
        self.assertEqual(len(texts), 2)

    def test_sign_placed_items(self):
        pdf = _build_pdf([[(80, 500, 2, "[[sig:customer:text:req=1]]", True)]])
        request = self.env["sign.oca.request"].create(
            {
                "name": "Anchored document",
                "data": b64encode(pdf),
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": self.partner_a.id,
                            "role_id": self.role_customer.id,
                        },
                    )
                ],
            }
        )
        self.assertEqual(len(request.signatory_data), 1)
        request.action_send()
        key = next(iter(request.signatory_data))
        request.signer_ids.action_sign({key: {"value": "Neutral text"}})
        self.assertEqual(request.state, "2_signed")
        reader = PdfReader(BytesIO(b64decode(request.data)))
        self.assertIn("Neutral text", reader.pages[0].extract_text())

    def test_html_to_pdf_anchors(self):
        report = self.env["ir.actions.report"]
        if report.get_wkhtmltopdf_state() != "ok":
            self.skipTest("wkhtmltopdf is not available")
        style = "position: absolute; left: 50px; color: white; font-size: 2px"
        html = (
            "<html><head><meta charset='utf-8'/></head><body>"
            f"<div style='{style}; top: 100px'>[[sig:customer:signature]]</div>"
            f"<div style='{style}; top: 600px'>[[sig:1:name]]</div>"
            "</body></html>"
        )
        pdf = report._run_wkhtmltopdf([html])
        anchors = self.env["sign.oca.request"]._find_pdf_anchors(pdf)
        self.assertEqual(
            [anchor["field_ref"] for anchor in anchors], ["signature", "name"]
        )
        first, second = anchors
        self.assertEqual(first["page"], 1)
        self.assertAlmostEqual(first["position_x"], second["position_x"], delta=0.1)
        self.assertGreater(second["position_y"], first["position_y"] + 10)
        for anchor in anchors:
            self.assertTrue(0 < anchor["position_x"] < 100)
            self.assertTrue(0 < anchor["position_y"] < 100)
