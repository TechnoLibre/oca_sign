# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import json

from odoo.tests.common import HttpCase, tagged
from odoo.tools import misc


@tagged("post_install", "-at_install")
class TestSignPortalToken(HttpCase):
    """A user who can read a signer still needs its token to act on it."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        partner = cls.env["res.partner"].create({"name": "Token signer"})
        role = cls.env.ref("sign_oca.sign_role_customer")
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Token test",
                "signer_ids": [(0, 0, {"partner_id": partner.id, "role_id": role.id})],
            }
        )
        cls.request.add_item(
            {
                "role_id": role.id,
                "field_id": cls.env.ref("sign_oca.sign_field_name").id,
                "page": 1,
                "position_x": 10,
                "position_y": 10,
                "width": 10,
                "height": 10,
            }
        )
        cls.request.action_send()
        cls.signer = cls.request.signer_ids

    def test_reader_with_wrong_token_redirected(self):
        self.authenticate("admin", "admin")
        for route in ("document", "content"):
            response = self.url_open(
                f"/sign_oca/{route}/{self.signer.id}/WRONG", allow_redirects=False
            )
            self.assertIn(response.status_code, (302, 303), route)
            self.assertTrue(response.headers["Location"].endswith("/my"), route)

    def test_reader_with_wrong_token_cannot_sign(self):
        self.authenticate("admin", "admin")
        items = {
            key: dict(item, value="Forged name")
            for key, item in self.request.signatory_data.items()
        }
        self.url_open(
            f"/sign_oca/sign/{self.signer.id}/WRONG",
            data=json.dumps({"params": {"items": items}}),
            headers={"Content-Type": "application/json"},
        )
        self.signer.invalidate_recordset()
        self.assertFalse(self.signer.signed_on)

    def test_reader_with_token_opens_page(self):
        self.authenticate("admin", "admin")
        response = self.url_open(
            f"/sign_oca/content/{self.signer.id}/{self.signer.access_token}",
            allow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
