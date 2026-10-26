# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.tests.common import HttpCase, new_test_user, tagged
from odoo.tools import misc

# Truthy once the viewer shows the pages and the page posted its fields.
IFRAME_READY = (
    "!!(document.querySelector('.o_sign_oca_iframe') && "
    "document.querySelector('.o_sign_oca_iframe').contentDocument && "
    "document.querySelector('.o_sign_oca_iframe').contentDocument"
    ".querySelector('.o_sign_oca_ready'))"
)


@tagged("post_install", "-at_install")
class TestSignPortalPage(HttpCase):
    """What the signing page shows, in a browser."""

    browser_size = "1366x768"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        cls.user = new_test_user(
            cls.env,
            login="sign_page_signer",
            password="sign_page_signer",
            groups="base.group_portal",
        )
        role = cls.env.ref("sign_oca.sign_role_customer")
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Page test",
                "signer_ids": [
                    (0, 0, {"partner_id": cls.user.partner_id.id, "role_id": role.id})
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
                "required": True,
            }
        )
        cls.signer = cls.request.signer_ids

    def _open_page(self, code):
        self.signer._portal_ensure_token()
        self.browser_js(
            self.signer.access_url,
            code,
            ready=IFRAME_READY,
            login=self.user.login,
        )

    def test_page_read_only(self):
        """A signer who cannot sign (here, the request is still a draft) sees
        the document without fields, navigator or sign button."""
        self.assertFalse(self.signer._can_sign())
        self._open_page(
            """
            const doc = document.querySelector(".o_sign_oca_iframe").contentDocument;
            const footer = document.querySelector(".o_sign_oca_footer");
            if (footer && getComputedStyle(footer).display !== "none") {
                throw new Error("The sign button shows to a signer who cannot sign");
            }
            if (doc.querySelector(".o_sign_oca_field")) {
                throw new Error("Fields show to a signer who cannot sign");
            }
            if (doc.querySelector(".o_sign_sign_item_navigator")) {
                throw new Error("The navigator shows to a signer who cannot sign");
            }
            console.log("test successful");
            """
        )

    def test_page_to_sign(self):
        """A signer who can sign gets the fields of their role and the
        navigator."""
        self.request.action_send()
        self._open_page(
            """
            const doc = document.querySelector(".o_sign_oca_iframe").contentDocument;
            if (!doc.querySelector(".o_sign_oca_field")) {
                throw new Error("The field of the signer does not show");
            }
            if (!doc.querySelector(".o_sign_sign_item_navigator")) {
                throw new Error("The navigator does not show");
            }
            console.log("test successful");
            """
        )
