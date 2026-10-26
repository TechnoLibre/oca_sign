# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from urllib.parse import urlsplit

from odoo.tests.common import HttpCase, tagged
from odoo.tools import misc


@tagged("post_install", "-at_install")
class TestSignSignedPage(HttpCase):
    """The page a signer sees once signed, and the document it links to."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        role = cls.env.ref("sign_oca.sign_role_customer")
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Signed page test",
                "signer_ids": [
                    (
                        0,
                        0,
                        {"partner_id": cls.env.user.partner_id.id, "role_id": role.id},
                    )
                ],
            }
        )
        cls.request.add_item(
            {
                "role_id": role.id,
                "field_id": cls.env.ref("sign_oca.sign_field_name").id,
                "page": 1,
                "position_x": 10,
                "position_y": 10,
                "width": 20,
                "height": 5,
            }
        )
        cls.request.action_send()
        cls.signer = cls.request.signer_ids
        items = {
            key: dict(item, value="Sample Signer")
            for key, item in cls.request.signatory_data.items()
        }
        cls.signer.sudo().action_sign(items)
        cls.download_url = (
            f"/sign_oca/download/{cls.signer.id}/{cls.signer.access_token}"
        )

    def test_signed_page(self):
        """The page fits a phone screen and links to the signed document."""
        response = self.url_open(self.signer.access_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="viewport"', response.text)
        self.assertIn(self.download_url, response.text)
        self.assertIn("Download the signed document", response.text)

    def test_download(self):
        response = self.url_open(self.download_url, allow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, base64.b64decode(self.request.data))
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertIn("Signed page test.pdf", response.headers["Content-Disposition"])

    def test_download_wrong_token(self):
        response = self.url_open(
            f"/sign_oca/download/{self.signer.id}/wrong-token", allow_redirects=False
        )
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(urlsplit(response.headers["Location"]).path, "/my")

    def test_cancelled_request_no_link(self):
        self.request.sudo().cancel()
        response = self.url_open(self.signer.access_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.download_url, response.text)
