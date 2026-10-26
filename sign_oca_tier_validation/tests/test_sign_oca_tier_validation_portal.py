# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from urllib.parse import urlsplit

from odoo_test_helper import FakeModelLoader

from odoo.tests.common import HttpCase, tagged

from .common import SIGNATURE_PNG, SignOcaTierMixin


@tagged("post_install", "-at_install")
class TestSignOcaTierValidationPortal(SignOcaTierMixin, HttpCase):
    browser_size = "1366x768"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.loader = FakeModelLoader(cls.env, cls.__module__)
        cls.loader.backup_registry()
        from odoo.addons.base_tier_validation.tests.tier_validation_tester import (
            TierDefinition,
            TierValidationTester,
        )

        cls.loader.update_registry((TierValidationTester, TierDefinition))
        cls.test_model = cls.env[TierValidationTester._name]
        cls.tester_model = cls.env["ir.model"]._get(TierValidationTester._name)
        cls.env["ir.model.access"].create(
            {
                "name": "access tier.validation.tester",
                "model_id": cls.tester_model.id,
                "perm_read": 1,
                "perm_write": 1,
                "perm_create": 1,
                "perm_unlink": 1,
            }
        )
        cls._setup_sign_oca_tier()

    @classmethod
    def tearDownClass(cls):
        cls.loader.restore_registry()
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self.record, self.sign_request = self._start()
        self.signer_1 = self._signer(self.sign_request, self.definition_1)
        self.signer_2 = self._signer(self.sign_request, self.definition_2)

    def _url(self, route, signer):
        return f"/sign_oca/{route}/{signer.id}/{signer.access_token}"

    def _login(self, user):
        self.authenticate(user.login, user.login)

    def test_login_required(self):
        response = self.url_open(
            self._url("document", self.signer_1), allow_redirects=False
        )
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(urlsplit(response.headers["Location"]).path, "/web/login")

    def test_other_user_refused(self):
        self._login(self.outsider)
        response = self.url_open(
            self._url("document", self.signer_1), allow_redirects=False
        )
        self.assertIn(response.status_code, (302, 303))
        self.assertEqual(urlsplit(response.headers["Location"]).path, "/my")

    def test_page_and_info(self):
        self._login(self.reviewer_1)
        response = self.url_open(self._url("document", self.signer_1))
        self.assertEqual(response.status_code, 200)
        response = self.url_open(self._url("content", self.signer_1))
        self.assertEqual(response.status_code, 200)
        info = self.make_jsonrpc_request(self._url("info", self.signer_1))
        self.assertEqual(info["role_id"], self.signer_1.role_id.id)
        self.assertIn("Manager approval", info["tier_steps_html"])
        self.assertIn("To sign", info["tier_steps_html"])

    def test_next_tier_waits(self):
        self._login(self.reviewer_3)
        response = self.url_open(self._url("document", self.signer_2))
        self.assertEqual(response.status_code, 200)
        info = self.make_jsonrpc_request(self._url("info", self.signer_2))
        self.assertFalse(info["role_id"])
        self.assertIn("not expected yet", info["tier_steps_html"])

    def test_sign_over_json(self):
        self._login(self.reviewer_1)
        items = self._browser_items(
            self.signer_1, signature=SIGNATURE_PNG, comment="Fine by me"
        )
        result = self.make_jsonrpc_request(
            self._url("sign", self.signer_1), {"items": items}
        )
        self.assertEqual(result["type"], "ir.actions.act_url")
        self.env.invalidate_all()
        review = self.signer_1.tier_review_id
        self.assertEqual(review.status, "approved")
        self.assertEqual(review.done_by, self.reviewer_1)
        self.assertEqual(review.comment, "Fine by me")
        # The page of a signed tier thanks the signer and shows the steps.
        response = self.url_open(self._url("document", self.signer_1))
        self.assertEqual(response.status_code, 200)
        self.assertIn("o_sign_oca_tier_steps", response.text)
        self.assertIn("Your signature is recorded", response.text)

    def test_panel_next_to_document(self):
        self.browser_js(
            self._url("document", self.signer_1),
            """
            const panel = document.querySelector(".o_sign_oca_tier_panel");
            const frame = document.querySelector(".o_sign_oca_iframe");
            if (!panel.textContent.includes("Manager approval")) {
                throw new Error("The steps panel does not list the tiers");
            }
            if (frame.getBoundingClientRect().right >
                    panel.getBoundingClientRect().left + 1) {
                throw new Error("The document is not left of the panel");
            }
            console.log("test successful");
            """,
            ready="!!document.querySelector('.o_sign_oca_tier_panel')",
            login=self.reviewer_1.login,
        )

    def test_waiting_reviewer_reads_only(self):
        """A reviewer whose tier waits for an earlier one sees the document
        without any field, navigator or sign button."""
        self.browser_js(
            self._url("document", self.signer_2),
            """
            const doc = document.querySelector(".o_sign_oca_iframe").contentDocument;
            const footer = document.querySelector(".o_sign_oca_footer");
            if (getComputedStyle(footer).display !== "none") {
                throw new Error("The sign button shows to a waiting reviewer");
            }
            if (doc.querySelector(".o_sign_oca_field")) {
                throw new Error("Fields show to a waiting reviewer");
            }
            if (doc.querySelector(".o_sign_sign_item_navigator")) {
                throw new Error("The navigator shows to a waiting reviewer");
            }
            console.log("test successful");
            """,
            ready=(
                "!!(document.querySelector('.o_sign_oca_iframe') && "
                "document.querySelector('.o_sign_oca_iframe').contentDocument"
                ".querySelector('.o_sign_oca_ready'))"
            ),
            login=self.reviewer_3.login,
        )

    def test_signed_page_links_document(self):
        """Once signed, the page links to the document through the signer's
        route, and its intro no longer asks to read before signing."""
        self._login(self.reviewer_1)
        items = self._browser_items(self.signer_1, signature=SIGNATURE_PNG)
        self.make_jsonrpc_request(self._url("sign", self.signer_1), {"items": items})
        response = self.url_open(self._url("document", self.signer_1))
        self.assertEqual(response.status_code, 200)
        self.assertIn(self._url("download", self.signer_1), response.text)
        self.assertNotIn("before signing", response.text)
        response = self.url_open(
            self._url("download", self.signer_1), allow_redirects=False
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Type"], "application/pdf")

    def test_bypassed_tier_not_listed(self):
        """A tier approved by bypass has nothing to sign: the portal list of
        its reviewer leaves it out."""
        definition_3 = self._bypass_definition(self.reviewer_1)
        _record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        signer_3 = self._signer(sign_request, definition_3)
        self._sign(signer_1, self.reviewer_1)
        self.assertTrue(signer_3.tier_bypassed)
        self._login(self.reviewer_1)
        response = self.url_open("/my/sign_requests")
        self.assertIn(f"/my/sign/{sign_request.id}/download", response.text)
        self.assertNotIn(f"/sign_oca/document/{signer_3.id}/", response.text)

    def test_group_reviewers_list_their_tier(self):
        """Every reviewer of a group tier finds it in /my/sign_requests, not
        only the one whose contact holds the signer."""
        link = f"/sign_oca/document/{self.signer_2.id}/"
        for user, listed in (
            (self.reviewer_2, True),
            (self.reviewer_3, True),
            (self.outsider, False),
        ):
            self._login(user)
            response = self.url_open("/my/sign_requests")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(link in response.text, listed, user.login)
