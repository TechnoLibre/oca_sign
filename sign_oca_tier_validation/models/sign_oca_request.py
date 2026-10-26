# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import copy
from base64 import b64decode, b64encode
from hashlib import sha256

import pytz

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError

from .tier_review import OPEN_REQUEST_STATES

# Kinds of the fields a tier's role fills, read from the ``tier_item`` key of
# an item of ``signatory_data`` or, without it, from the item's sign field.
TIER_ITEM_KINDS = ("signature", "name", "date", "status", "comment")
# Kinds whose value the server writes; the value sent by the browser is
# ignored and the signing page does not show them as fields to fill.
SERVER_FILLED_KINDS = ("name", "date", "status")
TIER_ITEM_FIELDS = {
    "signature": "sign_oca.sign_field_signature",
    "name": "sign_oca.sign_field_name",
    "date": "sign_oca_tier_validation.sign_field_date",
    "status": "sign_oca_tier_validation.sign_field_status",
    "comment": "sign_oca_tier_validation.sign_field_comment",
}
DATE_FORMAT = "%Y-%m-%d %H:%M"


class SignOcaRequest(models.Model):
    _inherit = "sign.oca.request"

    is_tier_validation = fields.Boolean(
        string="Tier Validation",
        readonly=True,
        copy=False,
        help="Each signer of this request signs a tier of the validation of "
        "the related record: signing approves the tier, refusing rejects it.",
    )
    tier_review_ids = fields.Many2many(
        comodel_name="tier.review",
        string="Tier Reviews",
        readonly=True,
        copy=False,
    )
    tier_tz = fields.Char(
        string="Tier Time Zone",
        readonly=True,
        copy=False,
        help="Time zone of the dates the signatures write in the document, "
        "set when the request is created.",
    )

    def _get_server_fields(self):
        """The link to the validation is set by ``_sign_oca_create_request``
        only: a user who creates or edits a request cannot make it sign a
        tier."""
        return super()._get_server_fields() | {
            "is_tier_validation",
            "tier_review_ids",
            "tier_tz",
        }

    def _check_signed(self):
        """A tier request is complete once every tier it signs, and every
        signer, is done (see ``_sign_oca_tier_done_signers``): a tier
        approved by ``approve_sequence_bypass`` counts without a signature
        of its own. Other requests keep sign_oca's rule."""
        self.ensure_one()
        if not self.is_tier_validation:
            return super()._check_signed()
        if self.state != "0_sent":
            return False
        sign_request = self.sudo()
        done = sign_request._sign_oca_tier_done_signers()
        if (
            sign_request.tier_review_ids - done.tier_review_id
            or sign_request.signer_ids - done
        ):
            return False
        self.state = "2_signed"
        return True

    def _sign_oca_tier_done_signers(self):
        """Return the signers of this request whose tier is done: signed, or
        approved by ``approve_sequence_bypass`` (``tier_bypassed``)."""
        self.ensure_one()
        return self.sudo().signer_ids.filtered(lambda s: s.signed_on or s.tier_bypassed)

    def _sign_oca_tier_get_record(self):
        """Return the record whose validation this request signs, as superuser,
        or an empty recordset."""
        self.ensure_one()
        review = self.sudo().tier_review_ids[:1]
        if review:
            return review._sign_oca_get_record().sudo()
        record = self.sudo().record_ref
        return record.exists() if record else self.env["tier.validation"]

    def _sign_oca_tier_cancel(self):
        """Cancel these requests, as superuser, whatever the caller's rights."""
        for sign_request in self.sudo():
            sign_request.cancel()

    def _can_set_state(self, state):
        """The state of a tier request follows its validation: it is sent by
        ``_sign_oca_create_request`` and cancelled by ``_sign_oca_tier_cancel``
        when the validation restarts or ends, never by a user, its
        responsible included, who would free the tiers from their document."""
        if self.is_tier_validation:
            return False
        return super()._can_set_state(state)

    def unlink(self):
        """An open tier request is removed with its validation only: a user
        deleting it would free the tiers from their document."""
        if not self.env.su and any(
            sign_request.is_tier_validation
            and sign_request.state in OPEN_REQUEST_STATES
            for sign_request in self
        ):
            raise UserError(
                self.env._(
                    "A document being signed for a validation cannot be "
                    "deleted: restart the validation instead."
                )
            )
        return super().unlink()

    @api.depends("signer_ids", "signer_ids.tier_review_id")
    @api.depends_context("uid")
    def _compute_signer_id(self):
        """On a tier request, the user's signer is the one of a tier they
        review, the tier they can sign now first."""
        result = super()._compute_signer_id()
        user = self.env.user
        for record in self.filtered("is_tier_validation"):
            signers = record.signer_ids.filtered(
                lambda s: user in s.sudo().tier_review_id.reviewer_ids
            )
            record.signer_id = fields.first(
                signers.filtered("is_allow_signature")
            ) or fields.first(signers)
        return result

    def _lock_for_sign(self):
        """Also lock the tier reviews of the records these requests validate.

        Two reviewers of one tier, or a signature and a decision taken from
        the back office, then decide one after the other; the review status
        is read again after the lock.
        """
        result = super()._lock_for_sign()
        for sign_request in self.filtered("is_tier_validation"):
            record = sign_request._sign_oca_tier_get_record()
            reviews = record.review_ids if record else None
            if reviews:
                self.env.cr.execute(
                    "SELECT id FROM tier_review WHERE id IN %s FOR UPDATE",
                    [tuple(reviews.ids)],
                )
                reviews.invalidate_recordset()
                record.invalidate_recordset(["review_ids", "validation_status"])
        return result

    # ------------------------------------------------------------------
    # Fields of the document
    # ------------------------------------------------------------------

    def _sign_oca_tier_item_fields(self):
        """Return ``{kind: sign.oca.field}``, the sign field of each kind of
        tier item (see ``TIER_ITEM_KINDS``)."""
        result = {}
        for kind, xmlid in TIER_ITEM_FIELDS.items():
            field = self.env.ref(xmlid, raise_if_not_found=False)
            if field:
                result[kind] = field
        return result

    def _sign_oca_tier_item_kind(self, item):
        """Return the kind of ``item`` (see ``TIER_ITEM_KINDS``), or False.

        The ``tier_item`` key of the item wins; without it, the kind is the
        one of the item's sign field in ``_sign_oca_tier_item_fields``.
        """
        kind = item.get("tier_item")
        if kind in TIER_ITEM_KINDS:
            return kind
        for kind, field in self._sign_oca_tier_item_fields().items():
            if field.id == item.get("field_id"):
                return kind
        return False

    def _sign_oca_tier_anchor_mapping(self):
        """Return the ``mapping`` of ``_place_items_from_anchors``.

        Anchors name a tier's role ``tier<n>``, where ``n`` is the tier
        number of the review (the ``sequence`` of ``tier.review``, 1 for the
        first tier to decide), and the field by its kind:
        ``[[sig:tier1:signature]]``, ``[[sig:tier2:comment]]``.
        """
        self.ensure_one()
        roles = {}
        for signer in self.signer_ids:
            if signer.tier_review_id:
                roles[f"tier{signer.tier_review_id.sequence}"] = signer.role_id
        return {"roles": roles, "fields": self._sign_oca_tier_item_fields()}

    def _sign_oca_tier_place_items(self, pages=None, key=None):
        """Place the fields of this draft request and tag those of the tiers.

        With ``sign_oca_anchor`` installed, the anchors of the document are
        placed with ``_sign_oca_tier_anchor_mapping``; an anchor that names
        an unknown tier is logged and skipped. ``_sign_oca_tier_tag_items``
        runs in every case.

        :param pages: optional collection of the page numbers (from 1) whose
            anchors are read; a document that embeds files written by
            others gives the pages it prints itself, so that an anchor typed
            in such a file places no field.
        :param int key: optional key the anchors must carry (see the ``key``
            of ``_place_items_from_anchors``)
        """
        self.ensure_one()
        if hasattr(self, "_place_items_from_anchors"):
            self._place_items_from_anchors(
                mapping=self._sign_oca_tier_anchor_mapping(),
                raise_if_unresolved=False,
                pages=pages,
                key=key,
            )
        self._sign_oca_tier_tag_items()

    def _sign_oca_tier_tag_items(self):
        """Set ``tier_item`` on the fields of the tiers' roles.

        Signature fields are required, so a tier is never approved without
        a signature image. The comment field becomes required on a tier
        whose definition asks for a comment (``has_comment``).
        """
        self.ensure_one()
        signers = {s.role_id.id: s for s in self.signer_ids if s.tier_review_id}
        signatory_data = self.signatory_data
        changed = False
        for item in signatory_data.values():
            signer = signers.get(item.get("role_id"))
            kind = signer and self._sign_oca_tier_item_kind(item)
            if not kind:
                continue
            vals = {"tier_item": kind}
            if kind == "signature" or (
                kind == "comment" and signer.tier_review_id.has_comment
            ):
                vals["required"] = True
            if any(item.get(key) != value for key, value in vals.items()):
                item.update(vals)
                changed = True
        if changed:
            self.signatory_data = signatory_data

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def action_send(self, sign_now=False, message=""):
        """Send a tier request: only the reviewers who can sign now get an
        email (see ``_sign_oca_tier_notify_signers``)."""
        if not self.is_tier_validation:
            return super().action_send(sign_now=sign_now, message=message)
        self.ensure_one()
        if self.state != "1_draft":
            return
        self._set_action_log("validate")
        self.state = "0_sent"
        for signer in self.signer_ids:
            signer._portal_ensure_token()
        self._sign_oca_tier_notify_signers(
            message=message,
            exclude_users=self.env.user if sign_now else None,
        )

    def _sign_oca_tier_notify_signers(self, message="", exclude_users=None):
        """Email the reviewers of each tier that became signable.

        Each signer is notified once (``tier_notified``), when at least one
        of its reviewers can sign: every such reviewer gets sign_oca's
        invitation with the signer's link.

        :param str message: text added to the email
        :param exclude_users: ``res.users`` that get no email
        """
        self.ensure_one()
        if self.state != "0_sent":
            return
        exclude_users = exclude_users or self.env["res.users"]
        for signer in self.sudo().signer_ids:
            if signer.signed_on or signer.tier_bypassed or signer.tier_notified:
                continue
            users = signer._sign_oca_tier_signable_users()
            if not users:
                continue
            signer.tier_notified = True
            partners = (users - exclude_users).partner_id
            if not partners:
                continue
            body = self.env["ir.qweb"]._render(
                "sign_oca.sign_oca_template_mail",
                {"record": signer, "body": message, "link": signer.access_url},
                engine="ir.qweb",
                minimal_qcontext=True,
            )
            self.env["mail.thread"].message_notify(
                body=body,
                partner_ids=partners.ids,
                subject=self.env._("New document to sign"),
                subtype_id=self.env.ref("mail.mt_comment").id,
                mail_auto_delete=False,
                email_layout_xmlid="mail.mail_notification_light",
            )


class SignOcaRequestSigner(models.Model):
    _inherit = "sign.oca.request.signer"

    tier_review_id = fields.Many2one(
        comodel_name="tier.review",
        readonly=True,
        copy=False,
        ondelete="set null",
        index="btree_not_null",
    )
    tier_notified = fields.Boolean(
        readonly=True,
        copy=False,
        help="The reviewers of this tier got the email inviting them to sign.",
    )
    tier_bypassed = fields.Boolean(
        string="Approved by Bypass",
        readonly=True,
        copy=False,
        help="The tier was approved along with an earlier tier signed by the "
        "same reviewer (Approve Sequence Bypass of its tier definition): it "
        "needs no signature of its own.",
    )

    def _get_server_fields(self):
        """The tier of a signer is set by ``_sign_oca_create_request``, and
        its bypass by the signature of an earlier tier, only."""
        return super()._get_server_fields() | {
            "tier_review_id",
            "tier_notified",
            "tier_bypassed",
        }

    # ------------------------------------------------------------------
    # Who signs
    # ------------------------------------------------------------------

    def _sign_oca_tier_check_user(self, user):
        """Raise unless ``user`` may sign this signer's tier now.

        The request must be the open request of the record it validates,
        and the review one of the request's. The order is
        base_tier_validation's: the review must be pending, the user one of
        its reviewers, and its tier among the sequences
        ``_get_sequences_to_approve`` gives the user, on a record neither
        validated nor rejected.

        :param user: a ``res.users`` record
        :raise ValidationError: with the reason, shown to the signer
        """
        self.ensure_one()
        review = self.sudo().tier_review_id
        record = review and review._sign_oca_get_record().sudo()
        if not review or not record:
            raise ValidationError(
                self.env._("The tier of this signature no longer exists.")
            )
        sign_request = self.sudo().request_id
        record_ref = sign_request.record_ref
        if (
            not record_ref
            or (record_ref._name, record_ref.id) != (record._name, record.id)
            or review not in sign_request.tier_review_ids
            or sign_request not in record._sign_oca_get_open_requests()
        ):
            raise ValidationError(
                self.env._("This document is not the one sent to sign the validation.")
            )
        # A review moves from waiting to pending while its stored can_review
        # is computed, which a new validation leaves pending until a flush.
        record.review_ids.flush_recordset(["can_review", "status"])
        if record.validation_status in ("validated", "rejected"):
            raise ValidationError(
                self.env._("The validation of this document is already decided.")
            )
        if review.status not in ("waiting", "pending"):
            raise ValidationError(self.env._("This tier is already decided."))
        if not user or user._is_public() or user not in review.reviewer_ids:
            raise ValidationError(self.env._("You are not a reviewer of this tier."))
        if review.status != "pending" or review.sequence not in (
            record._get_sequences_to_approve(user)
        ):
            raise ValidationError(
                self.env._(
                    "This tier cannot be signed yet: an earlier tier has to sign first."
                )
            )

    def _sign_oca_tier_can_sign_user(self, user):
        """Return whether ``_sign_oca_tier_check_user`` accepts ``user``."""
        self.ensure_one()
        try:
            self._sign_oca_tier_check_user(user)
        except UserError:
            return False
        return True

    def _sign_oca_tier_signable_users(self):
        """Return the reviewers of this signer's tier who can sign it now."""
        self.ensure_one()
        return self.sudo().tier_review_id.reviewer_ids.filtered(
            lambda user: self._sign_oca_tier_can_sign_user(user)
        )

    def _is_login_user(self, user):
        """Until a tier is signed or refused, any of its reviewers is its
        signer; afterwards the contact that signed or refused is."""
        self.ensure_one()
        signer = self.sudo()
        if (
            signer.request_id.is_tier_validation
            and not signer.signed_on
            and not signer.refused_on
        ):
            return bool(
                user
                and not user._is_public()
                and user in signer.tier_review_id.reviewer_ids
            )
        return super()._is_login_user(user)

    def _get_login_required_message(self):
        """Any reviewer of the tier may sign it: the message names none."""
        if self.request_id.is_tier_validation:
            return self.env._(
                "This document can only be signed by a reviewer of this tier, "
                "logged in."
            )
        return super()._get_login_required_message()

    def _check_can_sign(self):
        """On a tier request, also apply ``_sign_oca_tier_check_user`` to the
        acting user (``_get_login_user``)."""
        result = super()._check_can_sign()
        if self.request_id.is_tier_validation:
            self._sign_oca_tier_check_user(self._get_login_user())
        return result

    @api.depends(
        "signed_on",
        "partner_id",
        "refused_on",
        "request_id.state",
        "tier_review_id.status",
    )
    @api.depends_context("uid")
    def _compute_is_allow_signature(self):
        result = super()._compute_is_allow_signature()
        user = self.env.user
        for item in self:
            if item.request_id.is_tier_validation:
                item.is_allow_signature = bool(
                    item.request_id.state == "0_sent"
                    and not item.signed_on
                    and not item.refused_on
                    and item._sign_oca_tier_can_sign_user(user)
                )
        return result

    # ------------------------------------------------------------------
    # Values written by the server
    # ------------------------------------------------------------------

    def _sign_oca_tier_get_company(self):
        """Return the company of the validated record, else the request's."""
        self.ensure_one()
        record = self.request_id._sign_oca_tier_get_record()
        return record._get_company() if record else self.request_id.company_id

    def _sign_oca_tier_format_date(self, moment, user):
        """Return ``moment`` (naive UTC) as ``YYYY-MM-DD HH:MM``.

        The time zone is the request's ``tier_tz``, set once for every
        signature of the document; without it, the one of the record's
        company contact, else the user's, else UTC.
        """
        tz_name = (
            self.request_id.sudo().tier_tz
            or self._sign_oca_tier_get_company().partner_id.tz
            or user.tz
            or "UTC"
        )
        if tz_name not in pytz.all_timezones_set:
            tz_name = "UTC"
        local = pytz.utc.localize(moment).astimezone(pytz.timezone(tz_name))
        return local.strftime(DATE_FORMAT)

    def _sign_oca_tier_stamped_name(self, user):
        """Return the name of ``user`` written in the document: the name,
        which the user may change, followed by the login, which tells the
        account apart, as ``Name (login)``."""
        self.ensure_one()
        if not user.name or user.name == user.login:
            return user.login or ""
        return f"{user.name} ({user.login})"

    def _pades_seal_signer_name(self):
        """On a tier request, the seal of a signature names the signer as the
        document does (``_sign_oca_tier_stamped_name``). Hook of
        sign_oca_pades, called only when that module is installed."""
        if self.request_id.is_tier_validation:
            return self._sign_oca_tier_stamped_name(self._get_login_user())
        return super()._pades_seal_signer_name()

    def _sign_oca_tier_item_values(self, user, decision, moment):
        """Return ``{kind: value}`` of the fields the server fills.

        :param user: the ``res.users`` deciding the tier
        :param str decision: ``approved`` or ``rejected``
        :param datetime moment: when the decision is taken, naive UTC
        :return: dict with the ``name``, ``date`` and ``status`` kinds; the
            status is in the language of the record's company contact, else
            the user's
        """
        self.ensure_one()
        lang = self._sign_oca_tier_get_company().partner_id.lang or user.lang
        env = self.with_context(lang=lang).env
        status = env._("Approved") if decision == "approved" else env._("Refused")
        return {
            "name": self._sign_oca_tier_stamped_name(user),
            "date": self._sign_oca_tier_format_date(moment, user),
            "status": status,
        }

    def _sign_oca_tier_take_role(self, user):
        """Make ``user``'s contact the signer: whoever signs a tier holds its
        role, in place of the reviewer set when the request was created."""
        self.ensure_one()
        if self.partner_id != user.partner_id:
            self.sudo().partner_id = user.partner_id

    def _prepare_sign_items(self, items):
        """On a tier request, the server fills the name, date and status
        fields, the comment is stripped, and a tier whose definition asks for
        a comment refuses a signature without one. The signing user's contact
        becomes the signer, before the hashes are computed."""
        role_items = super()._prepare_sign_items(items)
        if not self.request_id.is_tier_validation:
            return role_items
        user = self._get_login_user()
        self._sign_oca_tier_take_role(user)
        values = self._sign_oca_tier_item_values(user, "approved", self.signed_on)
        has_comment_item = False
        comment = False
        for item in role_items.values():
            kind = self.request_id._sign_oca_tier_item_kind(item)
            if kind in values:
                item["value"] = values[kind]
            elif kind == "comment":
                has_comment_item = True
                value = item["value"]
                item["value"] = value.strip() if isinstance(value, str) else False
                comment = comment or item["value"]
        if self.sudo().tier_review_id.has_comment and not comment:
            if has_comment_item:
                raise ValidationError(
                    self.env._("This tier asks for a comment with the signature.")
                )
            raise ValidationError(
                self.env._(
                    "This tier asks for a comment, but the document has no "
                    "comment field for it."
                )
            )
        return role_items

    def _sign_oca_tier_get_comment(self):
        """Return the comment this signer wrote in the document, or False."""
        self.ensure_one()
        sign_request = self.request_id
        for item in sign_request.signatory_data.values():
            if (
                item.get("role_id") == self.role_id.id
                and sign_request._sign_oca_tier_item_kind(item) == "comment"
                and item.get("value")
            ):
                return item["value"]
        return False

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def _sign_oca_tier_decide(self, decision, comment):
        """Approve or reject this signer's review as the acting user.

        It follows base_tier_validation's comment wizard: the comment goes
        to the review, then ``_validate_tier`` or ``_rejected_tier`` and
        ``_update_counter`` run on the record, as the user with superuser
        rights. An approval also approves the reviews that
        ``_sign_oca_tier_bypass_reviews`` gives, as ``validate_tier`` does,
        and ``_sign_oca_tier_apply_bypass`` marks their signers.

        :param str decision: ``approved`` or ``rejected``
        :param str comment: the comment of the review, or False
        :raise UserError: when the review does not end with ``decision``
        """
        self.ensure_one()
        user = self._get_login_user()
        review = self.sudo().tier_review_id
        record = review._sign_oca_get_record().with_user(user).sudo()
        review = review.with_env(record.env)
        if comment:
            review.comment = comment
        bypassed = review.browse()
        if decision == "approved":
            bypassed = self._sign_oca_tier_bypass_reviews(user).with_env(record.env)
            record._validate_tier(review | bypassed)
        else:
            record._rejected_tier(review)
        record._update_counter({"review_deleted": True})
        if review.status != decision:
            raise UserError(
                self.env._(
                    "The tier %(tier)s could not be decided.",
                    tier=review.name or review.sequence,
                )
            )
        bypassed = bypassed.filtered(lambda r: r.status == "approved")
        if bypassed:
            self._sign_oca_tier_apply_bypass(bypassed, user)

    def _sign_oca_tier_bypass_reviews(self, user):
        """Return the reviews that approving this signer's tier approves too,
        as superuser.

        base_tier_validation's ``validate_tier`` approves, with the user's
        tier, every review whose definition has ``approve_sequence_bypass``
        and that the user reviews, whatever its sequence. The same reviews
        are returned here, those still to decide (waiting or pending): a
        decided review keeps its decision.

        :param user: the ``res.users`` approving this signer's tier
        """
        self.ensure_one()
        review = self.sudo().tier_review_id
        record = review._sign_oca_get_record().sudo()
        return record.review_ids.filtered(
            lambda r: r != review
            and r.approve_sequence_bypass
            and r.status in ("waiting", "pending")
            and user in r.reviewer_ids
        )

    def _sign_oca_tier_apply_bypass(self, reviews, user):
        """Mark the signers of ``reviews``, approved along with this
        signer's tier, as done without a signature (``tier_bypassed``).

        ``user``'s contact takes each of their roles, and their name, date
        and status fields are stamped in the document like a decision, the
        status followed by ``_sign_oca_tier_bypass_label``; their signature
        and comment fields stay empty, as nobody signed them.

        :param reviews: ``tier.review`` records approved by bypass
        :param user: the ``res.users`` whose approval bypassed them
        """
        self.ensure_one()
        moment = fields.Datetime.now()
        signers = self.sudo().request_id.signer_ids.filtered(
            lambda s: s.tier_review_id in reviews
            and not s.signed_on
            and not s.refused_on
            and not s.tier_bypassed
        )
        for signer in signers:
            signer._sign_oca_tier_take_role(user)
            signer._sign_oca_tier_stamp_decision(user, "bypassed", moment)
            signer.write({"tier_bypassed": True, "tier_notified": True})

    def _sign_oca_tier_bypass_label(self):
        """Return the note added to the status of a tier approved by bypass,
        in the language of the status (the environment's)."""
        return self.env._("same reviewer")

    def _after_sign(self):
        """On a tier request, approve the signer's review with the comment of
        the document.

        When that approval also approves, by bypass, the tiers that remained
        to sign, it completes the request: ``_on_completed`` runs here, the
        state having been checked by ``action_sign`` before the approval.
        """
        result = super()._after_sign()
        if self.request_id.is_tier_validation:
            sign_request = self.request_id.sudo()
            was_signed = sign_request.state == "2_signed"
            self._sign_oca_tier_decide("approved", self._sign_oca_tier_get_comment())
            if not was_signed and sign_request._check_signed():
                sign_request._on_completed()
        return result

    def _before_refuse(self, reason):
        """On a tier request, the refusing user's contact becomes the signer
        before the refusal is logged and posted."""
        result = super()._before_refuse(reason)
        if self.request_id.is_tier_validation:
            self._sign_oca_tier_take_role(self._get_login_user())
        return result

    def _on_refuse(self, reason):
        """On a tier request, stamp the refusal in the document, reject the
        signer's review with ``reason`` as its comment, then finalize the
        document with ``_pades_finalize`` when sign_oca_pades is installed:
        the refused document gets its certificate and final seal, as a
        completed one does."""
        result = super()._on_refuse(reason)
        if self.request_id.is_tier_validation:
            user = self._get_login_user()
            self._sign_oca_tier_stamp_refusal(user, reason)
            self._sign_oca_tier_decide("rejected", reason)
            sign_request = self.request_id.sudo()
            if hasattr(sign_request, "_pades_finalize"):
                sign_request._pades_finalize()
        return result

    def _sign_oca_tier_stamp_refusal(self, user, reason):
        """Draw the name, date, ``Refused`` status and ``reason`` in the
        fields of this signer's role; the signature field stays empty."""
        self.ensure_one()
        self._sign_oca_tier_stamp_decision(
            user, "rejected", self.refused_on, comment=reason
        )

    def _sign_oca_tier_stamp_decision(self, user, decision, moment, comment=None):
        """Draw the name, date and status of a decision taken without a
        signature in the fields of this signer's role, and ``comment`` in
        its comment field; the signature field stays empty.

        :param user: the ``res.users`` the decision is written for
        :param str decision: ``rejected``, or ``bypassed`` for a tier
            approved along with an earlier tier of ``user``: its status is
            the approval's, followed by ``_sign_oca_tier_bypass_label``
        :param datetime moment: when the decision is taken, naive UTC
        :param str comment: text of the comment field, or None to leave it
            empty

        The stamp records the status and the stamped name as its reason
        (context key ``sign_oca_stamp_reason``), which a sealed stamp keeps.
        """
        self.ensure_one()
        sign_request = self.request_id.sudo()
        lang = self._sign_oca_tier_get_company().partner_id.lang or user.lang
        if decision == "bypassed":
            values = self._sign_oca_tier_item_values(user, "approved", moment)
            label = self.with_context(lang=lang)._sign_oca_tier_bypass_label()
            values["status"] = f"{values['status']} ({label})"
        else:
            values = self._sign_oca_tier_item_values(user, decision, moment)
        if comment is not None:
            values["comment"] = comment
        signatory_data = sign_request.signatory_data
        items = {}
        for key, item in signatory_data.items():
            if item.get("role_id") != self.role_id.id:
                continue
            kind = sign_request._sign_oca_tier_item_kind(item)
            if kind in values:
                items[key] = dict(item, value=values[kind])
        if not items:
            return
        reason = self.with_context(lang=lang).env._(
            "%(status)s: %(name)s as %(role)s",
            status=values["status"],
            name=values["name"],
            role=self.role_id.name,
        )
        pdf = sign_request.with_context(sign_oca_stamp_reason=reason)._stamp_pdf(
            b64decode(sign_request.data), items, signer=self.sudo()
        )
        signatory_data.update(items)
        sign_request.write(
            {
                "signatory_data": signatory_data,
                "data": b64encode(pdf),
                "current_hash": sha256(pdf).hexdigest(),
            }
        )

    # ------------------------------------------------------------------
    # Signing page
    # ------------------------------------------------------------------

    def _sign_oca_tier_item_placeholders(self):
        """Return ``{kind: text}``, the placeholder the signing page shows in
        an empty field of that kind, in the language of the environment.

        Hook. The item names come from the sign fields, which are not
        translated; a field with a placeholder of its own keeps it.
        """
        return {
            "signature": self.env._("Signature"),
            "comment": self.env._("Comment"),
        }

    def get_info(self, access_token=False):
        """On a tier request, the fields the server fills are not given to
        the page as fields of the signer's role (``role_id`` is False in the
        copy sent), the others get the placeholders of
        ``_sign_oca_tier_item_placeholders``, and ``tier_steps_html`` holds
        the steps panel."""
        result = super().get_info(access_token=access_token)
        if not self.request_id.is_tier_validation:
            return result
        items = copy.deepcopy(result["items"])
        placeholders = self._sign_oca_tier_item_placeholders()
        for item in items.values():
            kind = self.request_id._sign_oca_tier_item_kind(item)
            if not item.get("placeholder") and kind in placeholders:
                item["placeholder"] = placeholders[kind]
            if item.get("role_id") == self.role_id.id and kind in SERVER_FILLED_KINDS:
                item["role_id"] = False
        result["items"] = items
        result["tier_steps_html"] = str(self._sign_oca_tier_render_steps())
        return result

    def _sign_oca_tier_status_labels(self):
        """Return ``{status: label}`` of the steps panel, by ``tier.review``
        status, in the language of the environment.

        Hook. The labels tell the tiers still to decide apart: ``waiting``
        for a tier whose turn has not come, ``pending`` for a tier that can
        be signed now; ``rejected`` is worded as the refusal the signing
        page records.
        """
        return {
            "waiting": self.env._("Upcoming"),
            "pending": self.env._("Awaiting signature"),
            "approved": self.env._("Approved"),
            "rejected": self.env._("Refused"),
        }

    def _sign_oca_tier_steps_values(self, user):
        """Return the values of the ``tier_steps_panel`` template for ``user``.

        :return: dict with ``sign_request``, ``record``, ``signer``,
            ``user``, ``steps`` (one dict per review in tier order:
            ``review``, ``sequence``, ``name``, ``who``, ``status``, the
            review status or ``closed`` for a tier left undecided by a
            closed document, ``status_label`` (see
            ``_sign_oca_tier_status_labels``), ``date``, the decision time
            in the zone of the document's dates or False, ``decided``,
            ``comment``, ``current`` for this signer's tier, ``to_sign`` when
            ``user`` can sign it now, ``bypassed`` when it was approved by
            bypass), ``intro``, the line under the panel title, worded for a
            decision to come until this signer's tier is decided or the
            document closed, ``message``, what the user's step expects, or
            False, and ``hints``, the notes on the comment shown under it
            when the user can sign now
        """
        self.ensure_one()
        signer = self.sudo()
        sign_request = signer.request_id
        record = sign_request._sign_oca_tier_get_record()
        labels = self._sign_oca_tier_status_labels()
        can_sign = (
            sign_request.state == "0_sent"
            and not signer.signed_on
            and not signer.refused_on
            and signer._sign_oca_tier_can_sign_user(user)
        )
        refused = sign_request.state == "4_refused" or (
            record and record.validation_status == "rejected"
        )
        cancelled = sign_request.state == "3_cancel"
        bypassed_reviews = sign_request.signer_ids.filtered(
            "tier_bypassed"
        ).tier_review_id
        reviews = sign_request.tier_review_ids.sorted(lambda r: (r.sequence, r.id))
        steps = []
        for review in reviews:
            current = review == signer.tier_review_id
            to_sign = current and can_sign
            bypassed = review in bypassed_reviews and review.status == "approved"
            decided = review.status in ("approved", "rejected")
            status = review.status
            if (refused or cancelled) and not decided:
                status = "closed"
                status_label = self.env._("Not decided")
            else:
                status_label = labels.get(review.status, review.status)
            if to_sign:
                status_label = self.env._("To sign")
            elif bypassed:
                status_label = f"{status_label} ({self._sign_oca_tier_bypass_label()})"
            date = False
            if decided and review.reviewed_date:
                date = self._sign_oca_tier_format_date(review.reviewed_date, user)
            steps.append(
                {
                    "review": review,
                    "sequence": review.sequence,
                    "name": review.name or "",
                    "who": review.done_by.name or review.todo_by or "",
                    "status": status,
                    "status_label": status_label,
                    "date": date,
                    "decided": decided,
                    "comment": review.comment or "",
                    "current": current,
                    "to_sign": to_sign,
                    "bypassed": bypassed,
                }
            )
        closed = (
            signer.signed_on
            or signer.refused_on
            or signer.tier_bypassed
            or sign_request.state not in ("0_sent", "1_draft")
            or (record and record.validation_status in ("validated", "rejected"))
        )
        earlier = reviews.filtered(
            lambda r: r.sequence < signer.tier_review_id.sequence
        )
        if closed:
            intro = self.env._("The decision and comment of each tier.")
        elif earlier:
            intro = self.env._(
                "Read the comments of the previous tiers before signing."
            )
        else:
            intro = self.env._("The tiers sign the document in this order.")
        if record and record.validation_status == "validated":
            message = self.env._("This document is fully approved.")
        elif refused:
            message = self.env._("This document was refused.")
        elif cancelled:
            message = self.env._("This signature request was cancelled.")
        elif signer.signed_on:
            message = self.env._("Your signature is recorded.")
        elif signer.tier_bypassed:
            message = self.env._(
                "This tier is approved along with an earlier tier of the same "
                "reviewer, without a signature of its own."
            )
        elif can_sign:
            message = self.env._(
                "Your turn: fill in your fields in the document, then validate it."
            )
        elif user in signer.tier_review_id.reviewer_ids:
            message = self.env._(
                "Your signature is not expected yet: an earlier tier has to sign first."
            )
        else:
            message = False
        hints = []
        if can_sign:
            hints = [
                self.env._("Your comment is required by this tier.")
                if signer.tier_review_id.has_comment
                else self.env._("Your comment is optional; give one when you refuse."),
                self.env._(
                    "Your comment is printed in the document and shown to the "
                    "next tiers."
                ),
            ]
        return {
            "sign_request": sign_request,
            "record": record,
            "signer": signer,
            "user": user,
            "steps": steps,
            "intro": intro,
            "message": message,
            "hints": hints,
        }

    def _sign_oca_tier_render_steps(self, user=None):
        """Return the HTML of the steps panel for ``user``, by default the
        acting user (``_get_login_user``), in that user's language."""
        self.ensure_one()
        user = user or self._get_login_user()
        signer = self.with_context(lang=user.lang or self.env.lang)
        return signer.env["ir.qweb"]._render(
            "sign_oca_tier_validation.tier_steps_panel",
            signer._sign_oca_tier_steps_values(user),
        )
