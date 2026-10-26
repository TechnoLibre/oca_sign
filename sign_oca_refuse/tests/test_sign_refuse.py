# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import new_test_user
from odoo.tools import misc

from odoo.addons.base.tests.common import BaseCommon


class TestSignRefuse(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            cls.data = base64.b64encode(pdf.read())
        cls.role_customer = cls.env.ref("sign_oca.sign_role_customer")
        cls.role_supervisor = cls.env.ref("sign_oca.sign_role_supervisor")
        cls.partner_a = cls.env["res.partner"].create(
            {"name": "First signer", "email": "first.signer@example.com"}
        )
        cls.partner_b = cls.env["res.partner"].create(
            {"name": "Second signer", "email": "second.signer@example.com"}
        )
        cls.responsible = cls.env["res.users"].create(
            {
                "name": "Request responsible",
                "login": "sign_refuse_responsible",
                "email": "responsible@example.com",
                "groups_id": [(6, 0, cls.env.ref("base.group_user").ids)],
            }
        )
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": cls.data,
                "name": "Refuse test",
                "user_id": cls.responsible.id,
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": cls.partner_a.id,
                            "role_id": cls.role_customer.id,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "partner_id": cls.partner_b.id,
                            "role_id": cls.role_supervisor.id,
                        },
                    ),
                ],
            }
        )
        cls.request.action_send()
        cls.signer_a = cls.request.signer_ids.filtered(
            lambda s: s.partner_id == cls.partner_a
        )
        cls.signer_b = cls.request.signer_ids - cls.signer_a

    def test_refuse_closes_request(self):
        action = self.signer_a.action_refuse("  Wrong amount\nin line 2  ")
        self.assertEqual(action["type"], "ir.actions.act_url")
        self.assertEqual(action["url"], self.signer_a.access_url)
        self.assertEqual(self.request.state, "4_refused")
        self.assertTrue(self.signer_a.refused_on)
        self.assertEqual(self.signer_a.refuse_reason, "Wrong amount\nin line 2")
        self.assertFalse(self.signer_b.refused_on)
        self.assertEqual(
            dict(self.request._fields["state"].selection)["4_refused"], "Refused"
        )

    def test_refuse_writes_log(self):
        self.signer_a.action_refuse("Not the right document", access_token="tok")
        log = self.env["sign.oca.request.log"].search(
            [("request_id", "=", self.request.id), ("action", "=", "refuse")]
        )
        self.assertEqual(len(log), 1)
        self.assertEqual(log.signer_id, self.signer_a)
        self.assertEqual(log.access_token, "tok")

    def test_refuse_notifies_responsible(self):
        self.signer_a.action_refuse("Missing <b>page</b>")
        message = self.request.message_ids.filtered(
            lambda m: m.author_id == self.partner_a and m.message_type == "comment"
        )
        self.assertEqual(len(message), 1)
        self.assertIn(self.responsible.partner_id, message.partner_ids)
        self.assertIn(
            self.responsible.partner_id,
            message.notification_ids.res_partner_id,
        )
        self.assertIn("Missing &lt;b&gt;page&lt;/b&gt;", message.body)
        self.assertIn("First signer", message.body)

    def test_reason_required(self):
        for reason in ("", "   \n ", False, None, 42):
            with self.assertRaises(ValidationError):
                self.signer_a.action_refuse(reason)
        self.assertEqual(self.request.state, "0_sent")
        self.assertFalse(self.signer_a.refused_on)

    def test_reason_length_bounded(self):
        """A reason is at most as long as a text signed in the document."""
        with self.assertRaisesRegex(ValidationError, "2000"):
            self.signer_a.action_refuse("x" * 2001)
        self.assertFalse(self.signer_a.refused_on)
        self.signer_a.action_refuse("x" * 2000)
        self.assertTrue(self.signer_a.refused_on)

    def test_refused_request_cannot_be_signed(self):
        self.signer_a.action_refuse("No")
        self.assertFalse(self.signer_b._can_sign())
        self.assertFalse(self.signer_b._can_refuse())
        with self.assertRaisesRegex(ValidationError, "refused"):
            self.signer_b.action_sign({})
        with self.assertRaises(ValidationError):
            self.signer_b.action_refuse("Me neither")
        info = self.signer_b.get_info()
        self.assertFalse(info["role_id"])
        self.assertFalse(info["can_refuse"])
        self.assertFalse(self.signer_b.signed_on)

    def test_signer_cannot_refuse_twice_or_after_signing(self):
        self.signer_a.action_sign({})
        self.assertFalse(self.signer_a._can_refuse())
        with self.assertRaises(ValidationError):
            self.signer_a.action_refuse("Changed my mind")
        self.assertEqual(self.request.state, "0_sent")

    def test_draft_request_cannot_be_refused(self):
        request = self.request.copy()
        self.assertEqual(request.state, "1_draft")
        with self.assertRaises(ValidationError):
            request.signer_ids[0].action_refuse("Too early")

    def test_get_info_can_refuse(self):
        info = self.signer_a.get_info()
        self.assertTrue(info["can_refuse"])
        self.assertEqual(info["role_id"], self.role_customer.id)

    def test_is_allow_signature(self):
        signer = self.signer_a.with_user(self.responsible)
        self.signer_a.partner_id = self.responsible.partner_id
        self.assertTrue(signer.is_allow_signature)
        self.signer_b.action_refuse("No")
        signer.invalidate_recordset(["is_allow_signature"])
        self.assertFalse(signer.is_allow_signature)

    def test_on_refuse_hook(self):
        calls = []
        signer_class = type(self.env["sign.oca.request.signer"])

        def on_refuse(signer, reason):
            calls.append((signer, reason, signer.request_id.state))

        with patch.object(signer_class, "_on_refuse", on_refuse):
            self.signer_a.action_refuse(" Out of scope ")
        self.assertEqual(calls, [(self.signer_a, "Out of scope", "4_refused")])

    def test_on_refuse_hook_can_cancel(self):
        signer_class = type(self.env["sign.oca.request.signer"])

        message = "Blocked by the hook"

        def on_refuse(signer, reason):
            raise ValidationError(message)

        with (
            patch.object(signer_class, "_on_refuse", on_refuse),
            self.assertRaisesRegex(ValidationError, "Blocked"),
            self.env.cr.savepoint(),
        ):
            self.signer_a.action_refuse("No")
        self.request.invalidate_recordset()
        self.signer_a.invalidate_recordset()
        self.assertEqual(self.request.state, "0_sent")
        self.assertFalse(self.signer_a.refused_on)

    def test_before_refuse_hook(self):
        calls = []
        signer_class = type(self.env["sign.oca.request.signer"])

        def before_refuse(signer, reason):
            calls.append(
                (signer, reason, signer.request_id.state, bool(signer.refused_on))
            )

        with patch.object(signer_class, "_before_refuse", before_refuse):
            self.signer_a.action_refuse(" Out of scope ")
        self.assertEqual(calls, [(self.signer_a, "Out of scope", "0_sent", False)])

    def test_refusal_is_server_written(self):
        signer = self.signer_a.with_user(self.responsible)
        with self.assertRaises(UserError):
            signer.write({"refused_on": fields.Datetime.now()})
        with self.assertRaises(UserError):
            self.request.with_user(self.responsible).write({"state": "4_refused"})

    def test_only_the_signer_refuses_through_rpc(self):
        follower = new_test_user(
            self.env,
            login="sign_refuse_follower",
            groups="sign_oca.sign_oca_group_user",
        )
        # A follower reads the signer, which does not let them refuse it.
        self.request.message_subscribe(partner_ids=follower.partner_id.ids)
        with self.assertRaises(ValidationError):
            self.signer_a.with_user(follower).action_refuse("Not mine")
        self.assertEqual(self.request.state, "0_sent")
