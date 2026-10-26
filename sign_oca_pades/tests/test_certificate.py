# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import datetime
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tools import misc

from odoo.addons.base.tests.common import BaseCommon

REPORT = "sign_oca.action_report_signature_certificate"


# Post install: the checks of the fields defined by sign_oca_refuse and
# sign_oca_tier_validation run when those modules are installed too.
@tagged("post_install", "-at_install")
class TestSignOcaPadesCertificate(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        cls.role_customer = cls.env.ref("sign_oca.sign_role_customer")
        cls.role_employee = cls.env.ref("sign_oca.sign_role_employee")
        cls.partner_a = cls.env["res.partner"].create(
            {"name": "First Example Signer", "email": "first@example.com"}
        )
        cls.partner_b = cls.env["res.partner"].create(
            {"name": "Second Example Signer", "email": "second@example.com"}
        )
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Certificate test",
                "signer_ids": [
                    (0, 0, {"partner_id": partner.id, "role_id": role.id})
                    for partner, role in (
                        (cls.partner_a, cls.role_customer),
                        (cls.partner_b, cls.role_employee),
                    )
                ],
            }
        )
        cls.signer_a, cls.signer_b = cls.request.signer_ids.sorted("id")
        cls.request.company_id.partner_id.tz = "America/Toronto"
        cls.certificate = cls.env["sign.oca.pades.certificate"]

    def _render(self):
        html, __ = self.env["ir.actions.report"]._render_qweb_html(
            REPORT, self.request.ids
        )
        return html.decode()

    def _log(self, **values):
        return (
            self.env["sign.oca.request.log"]
            .sudo()
            .create(dict({"request_id": self.request.id, "action": "view"}, **values))
        )

    def test_format_datetime_utc_then_local(self):
        moment = datetime.datetime(2026, 1, 15, 17, 0, 0, 123456)
        self.assertEqual(
            self.certificate._format_datetime(moment, "America/Toronto"),
            {"utc": "2026-01-15 17:00:00 UTC", "local": "2026-01-15 12:00:00 EST"},
        )
        self.assertEqual(
            self.certificate._format_datetime(moment, "UTC"),
            {"utc": "2026-01-15 17:00:00 UTC", "local": False},
        )
        self.assertFalse(self.certificate._format_datetime(False, "UTC"))

    def test_tz_company_contact_else_utc(self):
        if "tier_tz" in self.request._fields:
            self.request.sudo().tier_tz = False
        self.assertEqual(self.certificate._get_tz(self.request), "America/Toronto")
        self.request.company_id.partner_id.tz = False
        self.assertEqual(self.certificate._get_tz(self.request), "UTC")

    def test_tz_of_tier_request_wins(self):
        if "tier_tz" not in self.request._fields:
            self.skipTest("No module defines tier_tz")
        self.request.sudo().tier_tz = "Asia/Tokyo"
        self.assertEqual(self.certificate._get_tz(self.request), "Asia/Tokyo")
        self.request.sudo().tier_tz = "Not/AZone"
        self.assertEqual(self.certificate._get_tz(self.request), "UTC")

    def test_signers_in_signing_order(self):
        self.signer_b.sudo().signed_on = datetime.datetime(2026, 1, 15, 15, 0, 0)
        self.signer_a.sudo().signed_on = datetime.datetime(2026, 1, 15, 16, 0, 0)
        # sign_oca orders signers by signed_on desc: the last one first.
        self.assertEqual(self.request.signer_ids[0], self.signer_a)
        self.assertEqual(
            self.certificate._get_signers(self.request).ids,
            [self.signer_b.id, self.signer_a.id],
        )
        html = self._render()
        self.assertLess(
            html.index("Second Example Signer"), html.index("First Example Signer")
        )
        self.assertIn("2026-01-15 15:00:00 UTC", html)
        self.assertIn("(2026-01-15 10:00:00 EST)", html)

    def test_pending_signers_after_decided_ones(self):
        self.signer_b.sudo().signed_on = datetime.datetime(2026, 1, 15, 15, 0, 0)
        self.assertEqual(
            self.certificate._get_signers(self.request).ids,
            [self.signer_b.id, self.signer_a.id],
        )
        values = self.certificate._get_values(self.request)
        self.assertEqual(
            values["signer_status"],
            {self.signer_a.id: "Pending", self.signer_b.id: "Signed"},
        )
        self.assertEqual(
            [decision["signer"] for decision in values["decisions"]], [self.signer_b]
        )

    def test_header_names_the_local_zone(self):
        html = self._render()
        self.assertIn(
            "Times are in UTC, followed by the local time in parentheses. "
            "Time zone:",
            html,
        )
        self.assertIn("America/Toronto", html)
        self.request.company_id.partner_id.tz = "UTC"
        if "tier_tz" in self.request._fields:
            self.request.sudo().tier_tz = False
        self.assertIn("Times are in UTC.", self._render())

    def test_log_channel(self):
        portal = self._log(access_token="token", ip="192.0.2.10")
        backend = self._log(ip="192.0.2.11")
        server = self._log(ip="0.0.0.0")
        channel = self.certificate._get_log_channel
        self.assertEqual(channel(portal), "Portal")
        self.assertEqual(channel(backend), "Back office")
        self.assertEqual(channel(server), "Server")

    def test_signer_channel_from_its_decision_log(self):
        self.signer_a.sudo().signed_on = datetime.datetime(2026, 1, 15, 15, 0, 0)
        self._log(signer_id=self.signer_a.id, ip="192.0.2.10")
        self._log(
            signer_id=self.signer_a.id,
            action="sign",
            access_token="token",
            ip="192.0.2.10",
        )
        self.assertEqual(
            self.certificate._get_signer_channel(self.request, self.signer_a), "Portal"
        )
        self.assertFalse(
            self.certificate._get_signer_channel(self.request, self.signer_b)
        )

    def test_logs_skip_editor_actions_and_keep_order(self):
        self._log(action="configure", user_agent="EditorAgent/1.0")
        late = self._log(
            date=datetime.datetime(2026, 1, 15, 18, 0, 0), user_agent="LateAgent/1.0"
        )
        early = self._log(
            date=datetime.datetime(2026, 1, 15, 9, 0, 0), user_agent="EarlyAgent/1.0"
        )
        logs = self.certificate._get_logs(self.request)
        self.assertNotIn("configure", logs.mapped("action"))
        self.assertLess(list(logs).index(early), list(logs).index(late))
        html = self._render()
        self.assertNotIn("EditorAgent/1.0", html)
        self.assertLess(html.index("EarlyAgent/1.0"), html.index("LateAgent/1.0"))
        self.assertIn("2026-01-15 09:00:00 UTC", html)
        self.assertIn("(2026-01-15 04:00:00 EST)", html)

    def test_extra_signer_column_hook(self):
        self.signer_a.sudo().signed_on = datetime.datetime(2026, 1, 15, 15, 0, 0)
        with patch.object(
            type(self.certificate),
            "_get_signer_columns",
            lambda certificate, request: [
                ("Audit trail", lambda signer: f"cell-{signer.id}")
            ],
        ):
            html = self._render()
        self.assertIn("Audit trail", html)
        self.assertIn(f"cell-{self.signer_a.id}", html)
        self.assertIn("Channel", self._render())

    def test_refusal_listed_with_reason(self):
        if "refused_on" not in self.signer_a._fields:
            self.skipTest("sign_oca_refuse is not installed")
        self.signer_b.sudo().signed_on = datetime.datetime(2026, 1, 15, 15, 0, 0)
        self.signer_a.sudo().write(
            {
                "refused_on": datetime.datetime(2026, 1, 15, 16, 0, 0),
                "refuse_reason": "Example reason of the refusal",
            }
        )
        values = self.certificate._get_values(self.request)
        self.assertEqual(
            [(d["signer"], d["status"]) for d in values["decisions"]],
            [(self.signer_b, "Signed"), (self.signer_a, "Refused")],
        )
        html = self._render()
        self.assertIn("Example reason of the refusal", html)
        self.assertIn("Refused", html)

    def test_signers_of_a_closed_request_not_decided(self):
        """A request closed before completion leaves its signers without
        decision undecided, not pending."""
        self.signer_b.sudo().signed_on = datetime.datetime(2026, 1, 15, 15, 0, 0)
        self.request.sudo().state = "3_cancel"
        values = self.certificate._get_values(self.request)
        self.assertEqual(values["signer_status"][self.signer_a.id], "Not decided")
        self.assertEqual(values["signer_counts"]["undecided"], 1)
        self.assertEqual(values["signer_counts"]["pending"], 0)
        html = self._render()
        self.assertIn("not decided", html)
