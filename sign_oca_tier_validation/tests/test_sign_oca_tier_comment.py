# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from io import BytesIO

from pypdf import PdfReader

from odoo.tests import tagged

from odoo.addons.base_tier_validation.tests.common import CommonTierValidation

from .common import SignOcaTierMixin


@tagged("post_install", "-at_install")
class TestSignOcaTierComment(SignOcaTierMixin, CommonTierValidation):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_sign_oca_tier()

    def test_comment_field_is_multiline(self):
        comment_field = self.env.ref("sign_oca_tier_validation.sign_field_comment")
        self.assertEqual(comment_field.field_type, "textarea")
        __, sign_request = self._start()
        comments = [
            item
            for item in sign_request.signatory_data.values()
            if item.get("tier_item") == "comment"
        ]
        self.assertTrue(comments)
        self.assertTrue(all(item["field_type"] == "textarea" for item in comments))

    def test_multiline_comment_kept_and_printed(self):
        __, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        self._sign(
            signer_1,
            self.reviewer_1,
            comment="  Prices checked\nDelivery next month  ",
        )
        review = signer_1.tier_review_id
        self.assertEqual(review.status, "approved")
        self.assertEqual(review.comment, "Prices checked\nDelivery next month")
        self.assertEqual(
            self._role_values(signer_1)["comment"],
            "Prices checked\nDelivery next month",
        )
        reader = PdfReader(BytesIO(base64.b64decode(sign_request.data)))
        lines = reader.pages[0].extract_text().splitlines()
        self.assertIn("Prices checked", lines)
        self.assertIn("Delivery next month", lines)
