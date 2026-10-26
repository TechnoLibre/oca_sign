# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from urllib.parse import parse_qs, urlsplit

from odoo.tests import new_test_user
from odoo.tests.common import HttpCase, tagged
from odoo.tools import misc


@tagged("post_install", "-at_install")
class TestSignOcaLoginPortal(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            cls.data = base64.b64encode(pdf.read())
        cls.role = cls.env.ref("sign_oca.sign_role_customer")
        cls.signer_user = new_test_user(
            cls.env,
            login="sign_login_portal_signer",
            password="sign_login_portal_signer",
            groups="base.group_portal",
        )
        cls.other_user = new_test_user(
            cls.env,
            login="sign_login_portal_other",
            password="sign_login_portal_other",
            groups="base.group_portal",
        )
        cls.login_request = cls._create_request(require_login=True)
        cls.token_request = cls._create_request(require_login=False)

    @classmethod
    def _create_request(cls, require_login):
        request = cls.env["sign.oca.request"].create(
            {
                "data": cls.data,
                "name": "Portal login request",
                "require_login": require_login,
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": cls.signer_user.partner_id.id,
                            "role_id": cls.role.id,
                        },
                    )
                ],
            }
        )
        request.action_send()
        return request

    def _url(self, route, request):
        signer = request.signer_ids
        return f"/sign_oca/{route}/{signer.id}/{signer.access_token}"

    def _assert_redirects_to_login(self, response, request):
        self.assertIn(response.status_code, (302, 303))
        location = urlsplit(response.headers["Location"])
        self.assertEqual(location.path, "/web/login")
        self.assertEqual(
            parse_qs(location.query)["redirect"], [self._url("document", request)]
        )

    def _assert_redirects_to_my(self, response):
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(urlsplit(response.headers["Location"]).path, "/my")

    def test_anonymous_redirected_to_login(self):
        for route in ("document", "content"):
            response = self.url_open(
                self._url(route, self.login_request), allow_redirects=False
            )
            self._assert_redirects_to_login(response, self.login_request)

    def test_anonymous_cannot_sign_over_json(self):
        self.make_jsonrpc_request(self._url("sign", self.login_request), {"items": {}})
        self.env.invalidate_all()
        self.assertFalse(self.login_request.signer_ids.signed_on)

    def test_other_user_refused(self):
        self.authenticate("sign_login_portal_other", "sign_login_portal_other")
        for route in ("document", "content"):
            response = self.url_open(
                self._url(route, self.login_request), allow_redirects=False
            )
            self._assert_redirects_to_my(response)
        self.make_jsonrpc_request(self._url("sign", self.login_request), {"items": {}})
        self.env.invalidate_all()
        self.assertFalse(self.login_request.signer_ids.signed_on)

    def test_wrong_token_refused_to_signer(self):
        self.authenticate("sign_login_portal_signer", "sign_login_portal_signer")
        signer = self.login_request.signer_ids
        response = self.url_open(
            f"/sign_oca/document/{signer.id}/wrong-token", allow_redirects=False
        )
        self._assert_redirects_to_my(response)

    def test_signer_opens_and_signs(self):
        self.authenticate("sign_login_portal_signer", "sign_login_portal_signer")
        response = self.url_open(
            self._url("document", self.login_request), allow_redirects=False
        )
        self.assertEqual(response.status_code, 200)
        response = self.url_open(
            self._url("content", self.login_request), allow_redirects=False
        )
        self.assertEqual(response.status_code, 200)
        info = self.make_jsonrpc_request(self._url("info", self.login_request))
        self.assertEqual(info["role_id"], self.role.id)
        self.make_jsonrpc_request(self._url("sign", self.login_request), {"items": {}})
        self.env.invalidate_all()
        self.assertTrue(self.login_request.signer_ids.signed_on)
        self.assertEqual(self.login_request.state, "2_signed")

    def test_flag_off_keeps_token_access(self):
        for route in ("document", "content"):
            response = self.url_open(
                self._url(route, self.token_request), allow_redirects=False
            )
            self.assertEqual(response.status_code, 200)
        info = self.make_jsonrpc_request(self._url("info", self.token_request))
        self.assertEqual(info["role_id"], self.role.id)
        self.make_jsonrpc_request(self._url("sign", self.token_request), {"items": {}})
        self.env.invalidate_all()
        self.assertTrue(self.token_request.signer_ids.signed_on)
