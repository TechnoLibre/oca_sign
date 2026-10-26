# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.tests.common import HttpCase, new_test_user, tagged
from odoo.tools import misc


@tagged("post_install", "-at_install")
class TestSignDownload(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        cls.signer_user = new_test_user(
            cls.env,
            login="sign_download_signer",
            password="sign_download_signer",
            groups="base.group_portal",
        )
        cls.other_user = new_test_user(
            cls.env,
            login="sign_download_other",
            password="sign_download_other",
            groups="base.group_portal",
        )
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Download test",
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": cls.signer_user.partner_id.id,
                            "role_id": cls.env.ref("sign_oca.sign_role_customer").id,
                        },
                    )
                ],
            }
        )
        cls.request.state = "2_signed"
        cls.url = f"/my/sign/{cls.request.id}/download"

    def _attachment(self):
        return (
            self.env["ir.attachment"]
            .sudo()
            .search(
                [
                    ("res_model", "=", "sign.oca.request"),
                    ("res_id", "=", self.request.id),
                    ("res_field", "=", "data"),
                ]
            )
        )

    def test_download_signer(self):
        self.authenticate("sign_download_signer", "sign_download_signer")
        response = self.url_open(self.url, allow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, base64.b64decode(self.request.data))
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn(".pdf", response.headers["Content-Disposition"])
        # The file is streamed: no lasting token opens the attachment.
        self.assertFalse(self._attachment().access_token)

    def test_download_not_signer(self):
        self.authenticate("sign_download_other", "sign_download_other")
        response = self.url_open(self.url, allow_redirects=False)
        self.assertEqual(response.status_code, 404)

    def test_download_reader(self):
        self.authenticate("admin", "admin")
        response = self.url_open(self.url, allow_redirects=False)
        self.assertEqual(response.status_code, 200)

    def test_download_named_by_filename(self):
        self.request.sudo().filename = "Sample document - 0042.pdf"
        self.authenticate("sign_download_signer", "sign_download_signer")
        response = self.url_open(self.url, allow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertIn("0042.pdf", response.headers["Content-Disposition"])
