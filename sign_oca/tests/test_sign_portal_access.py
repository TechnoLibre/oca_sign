# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

from odoo.exceptions import AccessError
from odoo.http import request
from odoo.tests.common import HttpCase, tagged
from odoo.tools import misc

from odoo.addons.sign_oca.controllers.main import PortalSign


@tagged("post_install", "-at_install")
class TestSignPortalAccess(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        partner = cls.env["res.partner"].create({"name": "Access signer"})
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Access test",
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": partner.id,
                            "role_id": cls.env.ref("sign_oca.sign_role_customer").id,
                        },
                    )
                ],
            }
        )
        cls.request.action_send()
        cls.signer = cls.request.signer_ids
        cls.content_url = f"/sign_oca/content/{cls.signer.id}/{cls.signer.access_token}"

    def test_valid_token_opens_content(self):
        response = self.url_open(self.content_url, allow_redirects=False)
        self.assertEqual(response.status_code, 200)

    def test_wrong_token_redirects(self):
        response = self.url_open(
            f"/sign_oca/content/{self.signer.id}/wrong-token", allow_redirects=False
        )
        self.assertIn(response.status_code, (302, 303))
        self.assertTrue(response.headers["Location"].endswith("/my"))

    def test_overridden_check_and_response(self):
        message = "Login required"

        def refuse(controller, signer_id, access_token):
            raise AccessError(message)

        def to_login(controller, signer_id, access_token, error):
            return request.redirect("/web/login")

        with (
            patch.object(PortalSign, "_sign_oca_check_access", refuse),
            patch.object(PortalSign, "_sign_oca_access_error_response", to_login),
        ):
            response = self.url_open(self.content_url, allow_redirects=False)
        self.assertIn(response.status_code, (302, 303))
        self.assertIn("/web/login", response.headers["Location"])
