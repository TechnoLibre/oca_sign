# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.tests.common import HttpCase, tagged
from odoo.tools import misc


@tagged("post_install", "-at_install")
class TestSignLogUserAgent(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        partner = cls.env["res.partner"].create({"name": "User agent signer"})
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "User agent test",
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

    def test_log_without_http_request(self):
        log = self.request._set_action_log("view")
        self.assertFalse(log.user_agent)

    def test_log_stores_user_agent(self):
        user_agent = "SignOcaTestBrowser/1.0 (test)"
        response = self.url_open(
            f"/sign_oca/info/{self.signer.id}/{self.signer.access_token}",
            data="{}",
            headers={"Content-Type": "application/json", "User-Agent": user_agent},
        )
        response.raise_for_status()
        log = self.env["sign.oca.request.log"].search(
            [("signer_id", "=", self.signer.id), ("action", "=", "view")]
        )
        self.assertEqual(log.user_agent, user_agent)
