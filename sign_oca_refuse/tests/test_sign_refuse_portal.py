# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import json

from odoo.tests.common import HttpCase, tagged
from odoo.tools import misc


@tagged("post_install", "-at_install")
class TestSignRefusePortal(HttpCase):
    browser_size = "1366x768"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        cls.partner_a = cls.env["res.partner"].create(
            {"name": "Portal signer", "email": "portal.signer@example.com"}
        )
        cls.partner_b = cls.env["res.partner"].create(
            {"name": "Other signer", "email": "other.signer@example.com"}
        )
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Portal refuse test",
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": cls.partner_a.id,
                            "role_id": cls.env.ref("sign_oca.sign_role_customer").id,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "partner_id": cls.partner_b.id,
                            "role_id": cls.env.ref("sign_oca.sign_role_supervisor").id,
                        },
                    ),
                ],
            }
        )
        cls.request.action_send()
        cls.signer = cls.request.signer_ids.filtered(
            lambda s: s.partner_id == cls.partner_a
        )
        cls.other_signer = cls.request.signer_ids - cls.signer

    def _refuse_url(self, signer, token=None):
        return f"/sign_oca/refuse/{signer.id}/{token or signer.access_token}"

    def _post_json(self, url, params):
        response = self.url_open(
            url,
            data=json.dumps({"jsonrpc": "2.0", "params": params}),
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    def _assert_not_refused(self):
        self.signer.invalidate_recordset()
        self.request.invalidate_recordset()
        self.assertFalse(self.signer.refused_on)
        self.assertEqual(self.request.state, "0_sent")

    def test_route_refuses(self):
        result = self._post_json(
            self._refuse_url(self.signer), {"reason": "Wrong document"}
        )["result"]
        self.assertEqual(result["type"], "ir.actions.act_url")
        self.signer.invalidate_recordset()
        self.request.invalidate_recordset()
        self.assertEqual(self.request.state, "4_refused")
        self.assertEqual(self.signer.refuse_reason, "Wrong document")
        log = self.env["sign.oca.request.log"].search(
            [("request_id", "=", self.request.id), ("action", "=", "refuse")]
        )
        self.assertEqual(log.access_token, self.signer.access_token)
        self.assertTrue(log.ip)

    def test_route_requires_reason(self):
        response = self._post_json(self._refuse_url(self.signer), {"reason": "  "})
        self.assertIn("error", response)
        self._assert_not_refused()

    def test_route_wrong_token(self):
        self.url_open(
            self._refuse_url(self.signer, "wrong-token"),
            data=json.dumps({"jsonrpc": "2.0", "params": {"reason": "No"}}),
            headers={"Content-Type": "application/json"},
        )
        self._assert_not_refused()

    def test_route_other_signer_token(self):
        self.url_open(
            self._refuse_url(self.signer, self.other_signer.access_token),
            data=json.dumps({"jsonrpc": "2.0", "params": {"reason": "No"}}),
            headers={"Content-Type": "application/json"},
        )
        self._assert_not_refused()

    def test_info_tells_can_refuse(self):
        info = self._post_json(
            f"/sign_oca/info/{self.signer.id}/{self.signer.access_token}", {}
        )["result"]
        self.assertTrue(info["can_refuse"])

    def test_document_page_after_refusal(self):
        self.signer.action_refuse("Amounts do not match")
        response = self.url_open(self.signer.access_url)
        self.assertEqual(response.status_code, 200)
        self.assertIn("o_sign_oca_refused", response.text)
        self.assertIn("Amounts do not match", response.text)
        # The page fits a phone screen and links to the refused document.
        self.assertIn('name="viewport"', response.text)
        self.assertIn(
            f"/sign_oca/download/{self.signer.id}/{self.signer.access_token}",
            response.text,
        )
        response = self.url_open(self.other_signer.access_url)
        self.assertIn("o_sign_oca_refused", response.text)
        self.assertNotIn("Amounts do not match", response.text)

    def test_signed_page_after_refusal(self):
        self.other_signer.action_sign({})
        self.signer.action_refuse("No")
        response = self.url_open(self.other_signer.access_url)
        self.assertIn("refused by another signer", response.text)

    def test_portal_list_keeps_refused(self):
        portal_user = self.env["res.users"].create(
            {
                "name": "Portal list user",
                "login": "sign_refuse_portal",
                "password": "sign_refuse_portal",
                "groups_id": [(6, 0, self.env.ref("base.group_portal").ids)],
            }
        )
        self.signer.partner_id = portal_user.partner_id
        self.signer.action_refuse("No")
        self.authenticate(portal_user.login, portal_user.login)
        response = self.url_open("/my/sign_requests?filterby=refused")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Portal refuse test", response.text)

    def test_refuse_from_signing_page(self):
        """Drive the Refuse button and dialog of the signing page.

        The page loads the ``oca_sign.assets_frontend_sign`` bundle, which has
        no tour service, so ``browser_js`` clicks through it instead of a
        tour. Following the returned action unloads the page, which is the
        success signal; the database tells whether the refusal went through.
        """
        code = """
            const wait = (selector) => new Promise((resolve, reject) => {
                const start = Date.now();
                const check = () => {
                    const el = document.querySelector(selector);
                    if (el && !el.disabled) {
                        resolve(el);
                    } else if (Date.now() - start > 20000) {
                        reject(new Error("Element not found: " + selector));
                    } else {
                        setTimeout(check, 100);
                    }
                };
                check();
            });
            window.addEventListener("beforeunload", () => {
                console.log("test successful");
            });
            (async () => {
                (await wait(".o_sign_oca_refuse_button")).click();
                const textarea = await wait(".o_sign_oca_refuse_reason");
                if (!document.querySelector(".o_sign_oca_refuse_confirm").disabled) {
                    console.error("Confirm enabled without a reason");
                }
                textarea.value = "Refused from the page";
                textarea.dispatchEvent(new Event("input", {bubbles: true}));
                (await wait(".o_sign_oca_refuse_confirm")).click();
            })().catch((error) => console.error(error.message));
            undefined;
        """
        self.browser_js(
            self.signer.access_url,
            code,
            ready="!!document.querySelector('.o_sign_oca_refuse_button')",
        )
        self.signer.invalidate_recordset()
        self.request.invalidate_recordset()
        self.assertEqual(self.request.state, "4_refused")
        self.assertEqual(self.signer.refuse_reason, "Refused from the page")
