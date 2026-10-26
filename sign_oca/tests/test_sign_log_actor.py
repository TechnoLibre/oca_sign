# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.tests.common import HttpCase, new_test_user, tagged
from odoo.tools import misc


@tagged("post_install", "-at_install")
class TestSignLogActor(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        cls.portal_user = new_test_user(
            cls.env,
            login="sign_log_actor",
            password="sign_log_actor",
            groups="base.group_portal",
        )
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Log actor test",
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": cls.portal_user.partner_id.id,
                            "role_id": cls.env.ref("sign_oca.sign_role_customer").id,
                        },
                    )
                ],
            }
        )
        cls.request.action_send()
        cls.signer = cls.request.signer_ids
        cls.info_url = f"/sign_oca/info/{cls.signer.id}/{cls.signer.access_token}"

    def _open_info(self, headers=None):
        response = self.url_open(
            self.info_url,
            data="{}",
            headers=dict(headers or {}, **{"Content-Type": "application/json"}),
        )
        response.raise_for_status()
        return self.env["sign.oca.request.log"].search(
            [("signer_id", "=", self.signer.id), ("action", "=", "view")],
            order="id desc",
            limit=1,
        )

    def test_forwarded_header_ignored(self):
        log = self._open_info({"X-Forwarded-For": "203.0.113.77"})
        self.assertEqual(log.ip, "127.0.0.1")

    def test_logged_in_user_logged(self):
        self.authenticate("sign_log_actor", "sign_log_actor")
        log = self._open_info()
        self.assertEqual(log.uid, self.portal_user)
        self.assertEqual(log.partner_id, self.portal_user.partner_id)

    def test_anonymous_visitor_logged_as_public(self):
        log = self._open_info()
        public_user = self.env.ref("base.public_user")
        self.assertEqual(log.uid, public_user)
        self.assertEqual(log.partner_id, public_user.partner_id)

    def test_server_call_logs_environment_user(self):
        user = new_test_user(
            self.env, login="sign_log_actor_internal", groups="base.group_user"
        )
        log = self.request.with_user(user).sudo()._set_action_log("view")
        self.assertEqual(log.uid, user)
        self.assertEqual(log.ip, "0.0.0.0")
