# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import re
from unittest import mock

import pytz
from lxml import etree

from odoo import Command
from odoo.exceptions import UserError, ValidationError
from odoo.tests import new_test_user, tagged

from odoo.addons.base_tier_validation.tests.common import CommonTierValidation

from .common import TIER_KINDS, SignOcaTierMixin, add_tier_items


@tagged("post_install", "-at_install")
class TestSignOcaTierValidation(SignOcaTierMixin, CommonTierValidation):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._setup_sign_oca_tier()

    def _invitations(self, user):
        return self.env["mail.message"].search(
            [
                ("subject", "=", "New document to sign"),
                ("partner_ids", "in", user.partner_id.ids),
            ]
        )

    def test_create_request(self):
        record, sign_request = self._start()
        self.assertTrue(sign_request.is_tier_validation)
        self.assertTrue(sign_request.require_login)
        self.assertEqual(sign_request.state, "0_sent")
        self.assertEqual(sign_request.record_ref, record)
        self.assertEqual(sign_request.tier_review_ids, record.review_ids)
        signer_1 = self._signer(sign_request, self.definition_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        self.assertEqual(signer_1.role_id, self.definition_1.sign_oca_role_id)
        self.assertEqual(signer_2.role_id, self.definition_2.sign_oca_role_id)
        self.assertEqual(signer_1.role_id.name, "Manager approval")
        # The first reviewer of each tier holds the role until someone signs.
        self.assertEqual(signer_1.partner_id, self.reviewer_1.partner_id)
        self.assertEqual(signer_2.partner_id, self.reviewer_2.partner_id)
        # Only the first tier can sign: only its reviewer is invited.
        self.assertTrue(signer_1.tier_notified)
        self.assertFalse(signer_2.tier_notified)
        self.assertTrue(self._invitations(self.reviewer_1))
        self.assertFalse(self._invitations(self.reviewer_2))
        # The comment of a tier that asks for one is required.
        comments = {
            item["role_id"]: item["required"]
            for item in sign_request.signatory_data.values()
            if item["tier_item"] == "comment"
        }
        self.assertFalse(comments[signer_1.role_id.id])
        self.assertTrue(comments[signer_2.role_id.id])
        # One request at a time per record; the roles are reused.
        self.assertEqual(record._sign_oca_create_request(), sign_request)
        self.assertEqual(record.sign_oca_request_count, 1)
        _other, other_request = self._start()
        self.assertEqual(
            other_request.signer_ids.role_id, sign_request.signer_ids.role_id
        )

    def test_create_request_needs_validation(self):
        record = self.test_model.create({"test_field": 2.5})
        with self.assertRaises(UserError):
            record._sign_oca_create_request()

    def test_order_enforced(self):
        record, sign_request = self._start()
        signer_2 = self._signer(sign_request, self.definition_2)
        self.assertFalse(signer_2.with_user(self.reviewer_2).sudo()._can_sign())
        with self.assertRaisesRegex(ValidationError, "earlier tier"):
            self._sign(signer_2, self.reviewer_2, comment="Too early")
        self.assertFalse(signer_2.signed_on)
        self.assertEqual(record.validation_status, "pending")

    def test_wrong_user_refused(self):
        _record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        for user in (self.outsider, self.reviewer_2, self.requester):
            with self.assertRaises(ValidationError):
                self._sign(signer_1, user)
        self.assertFalse(signer_1.signed_on)
        self.assertFalse(signer_1.with_user(self.outsider).sudo()._can_sign())
        self.assertFalse(signer_1._is_login_user(self.outsider))
        self.assertTrue(signer_1._is_login_user(self.reviewer_1))

    def test_sign_approves_tier(self):
        record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        review_1 = signer_1.tier_review_id
        original = sign_request.data
        self._sign(
            signer_1,
            self.reviewer_1,
            name="Someone else",
            date="1999-01-01 00:00",
            status="Refused",
            comment="  Looks fine  ",
        )
        self.assertTrue(signer_1.signed_on)
        self.assertEqual(review_1.status, "approved")
        self.assertEqual(review_1.done_by, self.reviewer_1)
        self.assertEqual(review_1.comment, "Looks fine")
        self.assertNotEqual(sign_request.data, original)
        # Browser values of the server fields are ignored.
        values = self._role_values(signer_1)
        # The name written is "Name (login)": the login tells the account
        # apart whatever name its user gives themselves.
        self.assertEqual(values["name"], "First Reviewer (sign_tier_reviewer_1)")
        self.assertEqual(values["status"], "Approved")
        self.assertEqual(values["comment"], "Looks fine")
        self.assertRegex(values["date"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertNotEqual(values["date"], "1999-01-01 00:00")
        # The next tier is pending and its reviewers are invited.
        self.assertEqual(signer_2.tier_review_id.status, "pending")
        self.assertTrue(signer_2.tier_notified)
        self.assertTrue(self._invitations(self.reviewer_2))
        self.assertTrue(self._invitations(self.reviewer_3))
        self.assertEqual(sign_request.state, "0_sent")
        self.assertEqual(record.validation_status, "pending")

    def test_comment_required(self):
        record, sign_request = self._start()
        self._sign(self._signer(sign_request, self.definition_1), self.reviewer_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        with (
            self.assertRaisesRegex(ValidationError, "comment"),
            self.env.cr.savepoint(),
        ):
            self._sign(signer_2, self.reviewer_2, comment="   ")
        self.env.invalidate_all()
        self.assertFalse(signer_2.signed_on)
        self._sign(signer_2, self.reviewer_2, comment="Approved within budget")
        self.assertEqual(signer_2.tier_review_id.comment, "Approved within budget")
        self.assertEqual(record.validation_status, "validated")
        self.assertEqual(sign_request.state, "2_signed")

    def test_comment_required_without_field(self):
        _record, sign_request = self._start()
        self._sign(self._signer(sign_request, self.definition_1), self.reviewer_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        data = sign_request.signatory_data
        for key in [
            key
            for key, item in data.items()
            if item["role_id"] == signer_2.role_id.id and item["tier_item"] == "comment"
        ]:
            data.pop(key)
        sign_request.signatory_data = data
        with self.assertRaisesRegex(ValidationError, "no comment field"):
            self._sign(signer_2, self.reviewer_2)

    def test_group_reviewer_holds_role(self):
        _record, sign_request = self._start()
        self._sign(self._signer(sign_request, self.definition_1), self.reviewer_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        self.assertEqual(signer_2.partner_id, self.reviewer_2.partner_id)
        self.assertTrue(signer_2.with_user(self.reviewer_3).is_allow_signature)
        self._sign(signer_2, self.reviewer_3, comment="Agreed")
        self.assertEqual(signer_2.partner_id, self.reviewer_3.partner_id)
        self.assertEqual(signer_2.tier_review_id.done_by, self.reviewer_3)
        self.assertEqual(
            self._role_values(signer_2)["name"], "Third Reviewer (sign_tier_reviewer_3)"
        )
        self.assertFalse(signer_2._is_login_user(self.reviewer_2))
        self.assertTrue(signer_2._is_login_user(self.reviewer_3))
        with self.assertRaises(ValidationError):
            self._sign(signer_2, self.reviewer_2, comment="Again")

    def test_refuse_rejects_tier(self):
        record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        self.assertFalse(signer_2.with_user(self.reviewer_2).sudo()._can_refuse())
        with self.assertRaises(ValidationError):
            signer_2.with_user(self.reviewer_2).sudo().action_refuse("Too early")
        original = sign_request.data
        signer_1.with_user(self.reviewer_1).sudo().action_refuse(" Over budget ")
        review_1 = signer_1.tier_review_id
        self.assertEqual(review_1.status, "rejected")
        self.assertEqual(review_1.done_by, self.reviewer_1)
        self.assertEqual(review_1.comment, "Over budget")
        self.assertEqual(record.validation_status, "rejected")
        self.assertEqual(sign_request.state, "4_refused")
        self.assertNotEqual(sign_request.data, original)
        values = self._role_values(signer_1)
        self.assertEqual(values["status"], "Refused")
        self.assertEqual(values["comment"], "Over budget")
        self.assertEqual(values["name"], "First Reviewer (sign_tier_reviewer_1)")
        self.assertFalse(values["signature"])
        self.assertFalse(signer_2.tier_notified)

    def test_restart_cancels_request(self):
        record, sign_request = self._start()
        record.with_user(self.requester).restart_validation()
        self.assertFalse(record.review_ids)
        self.assertEqual(sign_request.state, "3_cancel")
        # A new validation gets a new request.
        record.with_user(self.requester).request_validation()
        new_request = record.with_user(self.requester)._sign_oca_create_request()
        self.assertNotEqual(new_request, sign_request)
        self.assertEqual(record.sign_oca_request_count, 2)

    def test_backoffice_decisions_refused(self):
        """While the document is out for signature, no tier is decided as
        the user outside it: not by the buttons, the comment wizard nor a
        direct call."""
        record, sign_request = self._start()
        user_record = record.with_user(self.reviewer_1)
        for method in ("validate_tier", "reject_tier"):
            with self.assertRaisesRegex(UserError, "decided in its document"):
                getattr(user_record, method)()
        wizard = (
            self.env["comment.wizard"]
            .with_user(self.reviewer_1)
            .create(
                {
                    "res_model": record._name,
                    "res_id": record.id,
                    "review_ids": [Command.set(record.review_ids.ids)],
                    "validate_reject": "validate",
                    "comment": "Approved outside the document",
                }
            )
        )
        with self.assertRaisesRegex(UserError, "decided in its document"):
            wizard.add_comment()
        with self.assertRaisesRegex(UserError, "decided in its document"):
            user_record._validate_tier(record.review_ids)
        self.assertEqual(record.validation_status, "pending")
        self.assertTrue(
            all(review.status in ("waiting", "pending") for review in record.review_ids)
        )
        self.assertEqual(sign_request.state, "0_sent")
        # Code running as superuser still decides, and a rejection cancels
        # the request.
        record.with_user(self.reviewer_1).sudo()._rejected_tier()
        self.assertEqual(record.validation_status, "rejected")
        self.assertEqual(sign_request.state, "3_cancel")

    def test_tier_request_not_cancelled_by_users(self):
        """Neither the responsible of a tier request nor a sign manager
        cancels or deletes it: it follows its validation."""
        _record, sign_request = self._start()
        manager = new_test_user(
            self.env,
            login="sign_tier_manager",
            groups="base.group_user,sign_oca.sign_oca_group_manager",
        )
        sign_request.user_id = manager
        request_as_manager = sign_request.with_user(manager)
        with self.assertRaisesRegex(UserError, "cannot be set"):
            request_as_manager.cancel()
        with self.assertRaisesRegex(UserError, "restart the validation"):
            request_as_manager.unlink()
        self.assertEqual(sign_request.state, "0_sent")

    def test_date_time_zone(self):
        company_partner = self.env.company.partner_id
        company_partner.tz = "Pacific/Auckland"
        _record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        self._sign(signer_1, self.reviewer_1)
        expected = (
            pytz.utc.localize(signer_1.signed_on)
            .astimezone(pytz.timezone("Pacific/Auckland"))
            .strftime("%Y-%m-%d %H:%M")
        )
        self.assertEqual(self._role_values(signer_1)["date"], expected)
        # The zone is kept on the request: every date of the document is in
        # the same zone, whatever changes afterwards.
        self.assertEqual(sign_request.tier_tz, "Pacific/Auckland")
        company_partner.tz = False
        self.reviewer_2.tz = "Asia/Tokyo"
        signer_2 = self._signer(sign_request, self.definition_2)
        self._sign(signer_2, self.reviewer_2, comment="Fine")
        expected = (
            pytz.utc.localize(signer_2.signed_on)
            .astimezone(pytz.timezone("Pacific/Auckland"))
            .strftime("%Y-%m-%d %H:%M")
        )
        self.assertEqual(self._role_values(signer_2)["date"], expected)

    def test_date_time_zone_of_the_requester(self):
        """Without a company time zone, the zone is the one of the user who
        sends the document, not the one of each reviewer."""
        self.env.company.partner_id.tz = False
        self.requester.tz = "Asia/Tokyo"
        self.reviewer_1.tz = "Europe/Paris"
        _record, sign_request = self._start()
        self.assertEqual(sign_request.tier_tz, "Asia/Tokyo")
        signer_1 = self._signer(sign_request, self.definition_1)
        self._sign(signer_1, self.reviewer_1)
        expected = (
            pytz.utc.localize(signer_1.signed_on)
            .astimezone(pytz.timezone("Asia/Tokyo"))
            .strftime("%Y-%m-%d %H:%M")
        )
        self.assertEqual(self._role_values(signer_1)["date"], expected)

    def test_get_info(self):
        _record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        info = signer_1.with_user(self.reviewer_1).sudo().get_info()
        self.assertEqual(info["role_id"], signer_1.role_id.id)
        visible = {
            item["tier_item"]
            for item in info["items"].values()
            if item["role_id"] == signer_1.role_id.id
        }
        self.assertEqual(visible, {"signature", "comment"})
        # The stored items keep their role.
        self.assertEqual(
            {
                item["tier_item"]
                for item in sign_request.signatory_data.values()
                if item["role_id"] == signer_1.role_id.id
            },
            set(TIER_KINDS),
        )
        html = info["tier_steps_html"]
        self.assertIn("Manager approval", html)
        self.assertIn("Director approval", html)
        self.assertIn("To sign", html)
        self.assertIn("o_sign_oca_tier_step_current", html)
        signer_2 = self._signer(sign_request, self.definition_2)
        info = signer_2.with_user(self.reviewer_2).sudo().get_info()
        self.assertFalse(info["role_id"])
        self.assertIn("not expected yet", info["tier_steps_html"])

    def test_steps_labels_and_dates(self):
        """Each step tells a tier still to come from one that can be signed,
        gives the time of a decision in the zone of the document, and a
        refused document leaves its other tiers undecided."""
        record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        steps = signer_2._sign_oca_tier_steps_values(self.reviewer_2)["steps"]
        self.assertEqual(
            [step["status_label"] for step in steps],
            ["Awaiting signature", "Upcoming"],
        )
        self.assertFalse(any(step["date"] for step in steps))
        self._sign(signer_1, self.reviewer_1, comment="Fine")
        record.invalidate_recordset()
        values = signer_2._sign_oca_tier_steps_values(self.reviewer_2)
        first = values["steps"][0]
        self.assertEqual(first["status_label"], "Approved")
        self.assertEqual(
            first["date"],
            signer_1._sign_oca_tier_format_date(
                first["review"].reviewed_date, self.reviewer_1
            ),
        )
        html = str(signer_2._sign_oca_tier_render_steps(self.reviewer_2))
        self.assertIn("o_sign_oca_tier_step_date", html)
        self.assertIn(first["date"], html)
        signer_2.with_user(self.reviewer_2).sudo().action_refuse("Not now")
        values = signer_1._sign_oca_tier_steps_values(self.reviewer_1)
        self.assertEqual(values["steps"][1]["status_label"], "Refused")
        self.assertEqual(values["message"], "This document was refused.")

    def test_steps_after_refusal(self):
        _record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        signer_1.with_user(self.reviewer_1).sudo().action_refuse("Not now")
        values = signer_1._sign_oca_tier_steps_values(self.reviewer_1)
        self.assertEqual(
            [(step["status"], step["status_label"]) for step in values["steps"]],
            [("rejected", "Refused"), ("closed", "Not decided")],
        )

    def test_placeholders(self):
        """The fields of the signer get placeholders in the language of the
        page, not the names of their sign fields."""
        _record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        info = signer_1.with_user(self.reviewer_1).sudo().get_info()
        placeholders = {
            sign_request._sign_oca_tier_item_kind(item): item.get("placeholder")
            for item in info["items"].values()
            if item["role_id"] == signer_1.role_id.id
        }
        self.assertEqual(placeholders["comment"], "Comment")
        self.assertEqual(placeholders["signature"], "Signature")

    def test_steps_show_comments(self):
        _record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        self._sign(signer_1, self.reviewer_1, comment="See <b>annex</b>")
        signer_2 = self._signer(sign_request, self.definition_2)
        html = str(signer_2._sign_oca_tier_render_steps(self.reviewer_2))
        self.assertIn("o_sign_oca_tier_quote", html)
        self.assertIn("See &lt;b&gt;annex&lt;/b&gt;", html)
        self.assertIn("First Reviewer", html)
        self.assertIn("Your turn", html)
        self.assertIn("required by this tier", html)
        self.assertIn("shown to the next tiers", html)

    def test_smart_button(self):
        record, sign_request = self._start()
        arch = self.test_model.get_view(view_type="form")["arch"]
        buttons = etree.fromstring(arch).xpath(
            "//div[@name='button_box']/button[@name='action_view_sign_oca_requests']"
        )
        self.assertEqual(len(buttons), 1)
        action = record.action_view_sign_oca_requests()
        self.assertEqual(action["res_id"], sign_request.id)
        action = record.with_user(self.requester).action_sign_oca_create_request()
        self.assertEqual(action["res_id"], sign_request.id)

    def test_anchor_placement(self):
        if not hasattr(self.env["sign.oca.request"], "_place_items_from_anchors"):
            self.skipTest("sign_oca_anchor is not installed")
        record = self.test_model.with_user(self.requester).create({"test_field": 2.5})
        record.request_validation()
        vals = record._sign_oca_prepare_request_vals(
            record.review_ids, self.pdf, "approval.pdf"
        )
        sign_request = (
            self.env["sign.oca.request"]
            .with_context(sign_oca_anchor_auto=False)
            .create(vals)
        )
        sign_request._sign_oca_tier_place_items()
        placed = {
            (item["role_id"], item["tier_item"])
            for item in sign_request.signatory_data.values()
        }
        expected = {
            (signer.role_id.id, kind)
            for signer in sign_request.signer_ids
            for kind in TIER_KINDS
        }
        self.assertEqual(placed, expected)

    def test_signed_document_stamped(self):
        _record, sign_request = self._start()
        self._sign(self._signer(sign_request, self.definition_1), self.reviewer_1)
        self._sign(
            self._signer(sign_request, self.definition_2),
            self.reviewer_2,
            comment="Fine",
        )
        pdf = base64.b64decode(sign_request.data)
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertTrue(re.match(r"^[0-9a-f]{64}$", sign_request.current_hash))

    # Integrity of the request

    def test_users_cannot_make_a_tier_request(self):
        record, sign_request = self._start()
        sign_user = new_test_user(
            self.env, login="sign_tier_sign_user", groups="sign_oca.sign_oca_group_user"
        )
        review = record.review_ids[:1]
        requests = self.env["sign.oca.request"].with_user(sign_user)
        base_vals = {"name": "Other document", "data": base64.b64encode(self.pdf)}
        for vals in (
            {"is_tier_validation": True},
            {"tier_review_ids": [Command.set(review.ids)]},
        ):
            with self.assertRaises(UserError):
                requests.create(dict(base_vals, **vals))
        own = requests.create(dict(base_vals, record_ref=record._sign_oca_record_ref()))
        with self.assertRaises(UserError):
            own.write({"is_tier_validation": True})
        with self.assertRaises(UserError):
            self.env["sign.oca.request.signer"].with_user(sign_user).create(
                {
                    "request_id": own.id,
                    "partner_id": sign_user.partner_id.id,
                    "role_id": sign_request.signer_ids[:1].role_id.id,
                    "tier_review_id": review.id,
                }
            )

    def test_request_of_another_record_refused(self):
        record, sign_request = self._start()
        other_record, _other_request = self._start()
        vals = record._sign_oca_prepare_request_vals(
            record.review_ids, self.pdf, "approval.pdf"
        )
        vals["record_ref"] = other_record._sign_oca_record_ref()
        forged = self.env["sign.oca.request"].create(vals)
        add_tier_items(record, forged)
        forged.action_send()
        signer = self._signer(forged, self.definition_1)
        with self.assertRaisesRegex(ValidationError, "not the one sent"):
            self._sign(signer, self.reviewer_1)
        self.assertEqual(record.validation_status, "pending")
        self.assertEqual(other_record.validation_status, "pending")

    def test_completion_needs_every_tier(self):
        record, sign_request = self._start()
        self._signer(sign_request, self.definition_2).sudo().unlink()
        self._sign(self._signer(sign_request, self.definition_1), self.reviewer_1)
        self.assertEqual(sign_request.state, "0_sent")
        self.assertEqual(record.validation_status, "pending")

    def test_signature_required(self):
        record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        signatures = [
            item["required"]
            for item in sign_request.signatory_data.values()
            if item["tier_item"] == "signature"
        ]
        self.assertTrue(signatures and all(signatures))
        for value in (False, "not an image"):
            with (
                self.assertRaises(ValidationError),
                self.env.cr.savepoint(),
            ):
                self._sign(signer_1, self.reviewer_1, signature=value)
        self.env.invalidate_all()
        self.assertFalse(signer_1.signed_on)
        self.assertEqual(record.validation_status, "pending")

    def test_group_reviewer_refusal_is_theirs(self):
        _record, sign_request = self._start()
        self._sign(self._signer(sign_request, self.definition_1), self.reviewer_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        signer_2.with_user(self.reviewer_3).sudo().action_refuse("Not in budget")
        self.assertEqual(signer_2.partner_id, self.reviewer_3.partner_id)
        message = sign_request.message_ids.filtered(
            lambda m: "refused to sign" in (m.body or "")
        )
        self.assertEqual(message.author_id, self.reviewer_3.partner_id)
        self.assertIn("Third Reviewer", message.body)
        self.assertEqual(signer_2.refuse_reason, "Not in budget")

    def test_group_reviewer_reads_and_counts(self):
        _record, sign_request = self._start()
        self._sign(self._signer(sign_request, self.definition_1), self.reviewer_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        sign_request.with_user(self.reviewer_3).check_access("read")
        signer_2.with_user(self.reviewer_3).check_access("read")
        self.assertFalse(
            self.env["sign.oca.request"]
            .with_user(self.outsider)
            .search([("id", "=", sign_request.id)])
        )
        counts = {
            group["model"]: group["total_records"]
            for group in self.env["res.users"]
            .with_user(self.reviewer_3)
            .sign_oca_request_user_count()
        }
        self.assertEqual(counts.get(self.test_model._name), 1)

    def test_bypass_tier_needs_no_signature(self):
        """Signing a tier also approves the bypass tiers of the same
        reviewer, as base_tier_validation does from the record: their
        signer is done without a signature, and stamped as such."""
        definition_3 = self._bypass_definition(self.reviewer_1)
        record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        signer_3 = self._signer(sign_request, definition_3)
        review_3 = signer_3.tier_review_id
        self.assertEqual(review_3.sequence, 3)
        self._sign(signer_1, self.reviewer_1, comment="Fine")
        self.assertEqual(review_3.status, "approved")
        self.assertEqual(review_3.done_by, self.reviewer_1)
        self.assertTrue(signer_3.tier_bypassed)
        self.assertFalse(signer_3.signed_on)
        self.assertEqual(signer_3.partner_id, self.reviewer_1.partner_id)
        values = self._role_values(signer_3)
        self.assertEqual(values["name"], "First Reviewer (sign_tier_reviewer_1)")
        self.assertEqual(values["status"], "Approved (same reviewer)")
        self.assertRegex(values["date"], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")
        self.assertFalse(values["signature"])
        self.assertFalse(values["comment"])
        self.assertFalse(signer_3.with_user(self.reviewer_1).sudo()._can_sign())
        # Nothing is left to sign for the reviewer: the systray counts none.
        counts = {
            group["model"]: group["total_records"]
            for group in self.env["res.users"]
            .with_user(self.reviewer_1)
            .sign_oca_request_user_count()
        }
        self.assertFalse(counts.get(self.test_model._name))
        # The second tier still has to sign; then the request completes
        # without a signature on the third.
        self.assertEqual(sign_request.state, "0_sent")
        html = str(signer_3._sign_oca_tier_render_steps(self.reviewer_1))
        self.assertIn("Approved (same reviewer)", html)
        self.assertIn("without a signature of its own", html)
        self._sign(signer_2, self.reviewer_2, comment="Agreed")
        self.assertEqual(record.validation_status, "validated")
        self.assertEqual(sign_request.state, "2_signed")

    def test_bypass_tier_completes_request(self):
        """A bypass tier left last completes the request when the signature
        that bypasses it is given: ``_on_completed`` runs once."""
        definition_3 = self._bypass_definition(self.reviewer_3)
        record, sign_request = self._start()
        self._sign(self._signer(sign_request, self.definition_1), self.reviewer_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        signer_3 = self._signer(sign_request, definition_3)
        self.assertFalse(signer_3.tier_bypassed)
        completed = []
        request_class = type(self.env["sign.oca.request"])
        original = request_class._on_completed

        def on_completed(sign_request_self):
            completed.append(sign_request_self.id)
            return original(sign_request_self)

        with mock.patch.object(request_class, "_on_completed", on_completed):
            self._sign(signer_2, self.reviewer_3, comment="Agreed")
        self.assertTrue(signer_3.tier_bypassed)
        self.assertEqual(record.validation_status, "validated")
        self.assertEqual(sign_request.state, "2_signed")
        self.assertEqual(completed, [sign_request.id])

    def test_no_bypass_for_another_reviewer(self):
        """A bypass tier of another reviewer waits for its own signature."""
        definition_3 = self._bypass_definition(self.reviewer_3)
        _record, sign_request = self._start()
        self._sign(self._signer(sign_request, self.definition_1), self.reviewer_1)
        signer_3 = self._signer(sign_request, definition_3)
        self.assertFalse(signer_3.tier_bypassed)
        self.assertEqual(signer_3.tier_review_id.status, "waiting")

    def test_steps_intro(self):
        """The line under the panel title gives the order of the tiers to
        the first one, asks a later tier to read the comments before
        signing, and describes the decisions once the signer's tier is
        decided."""
        _record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        before = signer_1._sign_oca_tier_steps_values(self.reviewer_1)
        self.assertIn("in this order", before["intro"])
        signer_2 = self._signer(sign_request, self.definition_2)
        later = signer_2._sign_oca_tier_steps_values(self.reviewer_2)
        self.assertIn("before signing", later["intro"])
        self._sign(signer_1, self.reviewer_1)
        after = signer_1._sign_oca_tier_steps_values(self.reviewer_1)
        self.assertNotIn("before signing", after["intro"])
        self.assertNotIn("in this order", after["intro"])
        html = str(signer_1._sign_oca_tier_render_steps(self.reviewer_1))
        self.assertIn(after["intro"], html)
