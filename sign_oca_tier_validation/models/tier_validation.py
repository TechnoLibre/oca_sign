# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from base64 import b64encode

from lxml import etree

from odoo import Command, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import frozendict

from odoo.addons.base_tier_validation.models.tier_validation import (
    _merge_view_fields,
)

from .tier_review import OPEN_REQUEST_STATES


class TierValidation(models.AbstractModel):
    _inherit = "tier.validation"

    # Add the "Signatures" smart button to the form views of the model.
    _sign_oca_tier_smart_button = True

    sign_oca_request_ids = fields.Many2many(
        comodel_name="sign.oca.request",
        string="Sign Requests",
        compute="_compute_sign_oca_request_ids",
    )
    sign_oca_request_count = fields.Integer(
        string="Sign Request Count",
        compute="_compute_sign_oca_request_ids",
    )

    def _sign_oca_record_ref(self):
        """Return the ``record_ref`` value of a sign request for this record."""
        self.ensure_one()
        return f"{self._name},{self.id}"

    def _compute_sign_oca_request_ids(self):
        requests = self.env["sign.oca.request"]
        records = self.filtered("id")
        by_record = {}
        if records and requests.has_access("read"):
            for sign_request in requests.search(
                [("record_ref", "in", [r._sign_oca_record_ref() for r in records])]
            ):
                key = (sign_request.record_ref._name, sign_request.record_ref.id)
                by_record[key] = by_record.get(key, requests) | sign_request
        for record in self:
            found = by_record.get((record._name, record.id), requests)
            record.sign_oca_request_ids = found
            record.sign_oca_request_count = len(found)

    def _sign_oca_get_open_requests(self):
        """Return the tier sign requests of this record that are not closed.

        :return: ``sign.oca.request`` records, as superuser
        """
        self.ensure_one()
        return (
            self.env["sign.oca.request"]
            .sudo()
            .search(
                [
                    ("record_ref", "=", self._sign_oca_record_ref()),
                    ("is_tier_validation", "=", True),
                    ("state", "in", OPEN_REQUEST_STATES),
                ]
            )
        )

    def _sign_oca_get_document(self):
        """Return the document the reviewers sign: ``(pdf_bytes, filename)``.

        Models that sign their tiers in a document implement it; the PDF is
        read once, when the request is created, and signed as it is.

        :raise UserError: by default, as the model gives no document
        """
        raise UserError(
            self.env._(
                "%(model)s does not provide a document to sign its validation.",
                model=self._description,
            )
        )

    def _sign_oca_prepare_request_vals(self, reviews, pdf_bytes, filename):
        """Return the ``sign.oca.request`` values that sign ``reviews``.

        One signer per review, in tier order, with the role of the review's
        tier definition. The signer's contact is the first reviewer's until
        a reviewer signs: the contact of whoever signs replaces it. The
        request requires a login.

        :raise UserError: when a review has no reviewer, or two reviews share
            a role
        """
        self.ensure_one()
        signers = []
        roles = self.env["sign.oca.role"]
        for review in reviews.sorted(lambda r: (r.sequence, r.id)):
            role = review._sign_oca_get_role()
            if role in roles:
                raise UserError(
                    self.env._(
                        "Two tiers of %(record)s sign as the role %(role)s.",
                        record=self.display_name,
                        role=role.name,
                    )
                )
            roles |= role
            partner = review.reviewer_ids[:1].partner_id
            if not partner:
                raise UserError(
                    self.env._(
                        "The tier %(tier)s of %(record)s has no reviewer.",
                        tier=review.name or review.sequence,
                        record=self.display_name,
                    )
                )
            signers.append(
                Command.create(
                    {
                        "partner_id": partner.id,
                        "role_id": role.id,
                        "tier_review_id": review.id,
                    }
                )
            )
        return {
            "name": self.display_name,
            "data": b64encode(pdf_bytes),
            "filename": filename,
            "record_ref": self._sign_oca_record_ref(),
            "company_id": self._get_company().id,
            "require_login": True,
            "is_tier_validation": True,
            "tier_review_ids": [Command.set(reviews.ids)],
            "tier_tz": self._sign_oca_tier_get_tz(),
            "signer_ids": signers,
        }

    def _sign_oca_tier_get_tz(self):
        """Return the time zone of the dates the signatures write in the
        document: the one of the record's company contact, else the current
        user's, else UTC. It is read once, when the request is created, so
        every date of the document is in the same zone.
        """
        self.ensure_one()
        return self._get_company().sudo().partner_id.tz or self.env.user.tz or "UTC"

    def _sign_oca_prepare_items(self, sign_request):
        """Place the fields of ``sign_request``, still a draft.

        Default: ``sign_request._sign_oca_tier_place_items()``, which reads
        the anchors of the document when ``sign_oca_anchor`` is installed.
        Override to add the fields by code (``add_item``, with a
        ``tier_item`` key).
        """
        sign_request._sign_oca_tier_place_items()

    def _sign_oca_create_request(self, message=""):
        """Create and send the sign request of the open reviews of the record.

        The record must be under validation. An open request of the record
        is returned as it is: a record has one request at a time. The new
        request is created as superuser, its fields are placed by
        ``_sign_oca_prepare_items``, then it is sent: only the reviewers who
        can sign now get an email; the others get theirs when their tier's
        turn comes.

        :param str message: text added to the invitation emails
        :return: the ``sign.oca.request``, as superuser
        :raise UserError: when the record is not under validation
        """
        self.ensure_one()
        self.check_access("write")
        sign_request = self._sign_oca_get_open_requests()[:1]
        if sign_request:
            return sign_request
        reviews = self.review_ids.filtered(lambda r: r.status in ("waiting", "pending"))
        if self.validation_status not in ("waiting", "pending") or not reviews:
            raise UserError(
                self.env._(
                    "Request a validation of %(record)s before sending it for "
                    "signature.",
                    record=self.display_name,
                )
            )
        pdf_bytes, filename = self._sign_oca_get_document()
        sign_request = (
            self.env["sign.oca.request"]
            .sudo()
            .with_context(sign_oca_anchor_auto=False)
            .create(self._sign_oca_prepare_request_vals(reviews, pdf_bytes, filename))
        )
        self._sign_oca_prepare_items(sign_request)
        sign_request.action_send(message=message)
        return sign_request

    def action_sign_oca_create_request(self):
        """Button: send the validation for signature and show the request."""
        self.ensure_one()
        self._sign_oca_create_request()
        return self.action_view_sign_oca_requests()

    def action_view_sign_oca_requests(self):
        """Smart button: open the sign requests of the record."""
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(
            "sign_oca.sign_oca_request_act_window"
        )
        requests = self.sign_oca_request_ids
        action["context"] = {}
        if len(requests) == 1:
            action.update(res_id=requests.id, view_mode="form", views=[(False, "form")])
        else:
            action["domain"] = [("id", "in", requests.ids)]
        return action

    def _sign_oca_tier_decided_in_document(self):
        """Return whether the tiers of this record are decided in a signed
        document only. Hook; default: while a tier request of the record is
        open (``_sign_oca_get_open_requests``)."""
        self.ensure_one()
        return bool(self._sign_oca_get_open_requests())

    def _sign_oca_tier_check_backoffice_decision(self):
        """Raise when a tier of this record is decided outside its document.

        While ``_sign_oca_tier_decided_in_document`` holds, a tier is
        approved by signing it and rejected by refusing it on the signing
        page, which decide as superuser (``_sign_oca_tier_decide``). A
        decision taken as the user (the buttons of the form, the comment
        wizard, a stage change that validates the record, a remote call) is
        refused; code running as superuser decides as before.

        :raise UserError: naming the record
        """
        for record in self:
            if not self.env.su and record._sign_oca_tier_decided_in_document():
                raise UserError(
                    self.env._(
                        "The tiers of %(record)s are decided in its document: "
                        "sign or refuse it on the signing page.",
                        record=record.display_name,
                    )
                )

    def validate_tier(self):
        self._sign_oca_tier_check_backoffice_decision()
        return super().validate_tier()

    def reject_tier(self):
        self._sign_oca_tier_check_backoffice_decision()
        return super().reject_tier()

    def _validate_tier(self, tiers=False):
        self._sign_oca_tier_check_backoffice_decision()
        return super()._validate_tier(tiers=tiers)

    def _rejected_tier(self, tiers=False):
        """Cancel the open sign requests once the record is rejected."""
        self._sign_oca_tier_check_backoffice_decision()
        result = super()._rejected_tier(tiers=tiers)
        if self.validation_status == "rejected":
            self._sign_oca_get_open_requests()._sign_oca_tier_cancel()
        return result

    @api.model
    def _update_counter(self, review_counter):
        """Email the reviewers whose tier became signable.

        base_tier_validation calls this after every decision, once the next
        tiers are pending.
        """
        result = super()._update_counter(review_counter)
        for record in self.filtered("id"):
            for sign_request in record._sign_oca_get_open_requests():
                if sign_request.state == "0_sent":
                    sign_request._sign_oca_tier_notify_signers()
        return result

    @api.model
    def get_view(self, view_id=None, view_type="form", **options):
        """Add the "Signatures" smart button to the form view.

        The button goes in the ``button_box`` of the form, created at the
        top of the sheet when the form has none. A model sets
        ``_sign_oca_tier_smart_button = False`` to keep its form unchanged.
        """
        res = super().get_view(view_id=view_id, view_type=view_type, **options)
        if (
            view_type != "form"
            or not self._sign_oca_tier_smart_button
            or not self.env["sign.oca.request"].has_access("read")
        ):
            return res
        doc = etree.XML(res["arch"])
        boxes = doc.xpath("//div[@name='button_box']")
        if boxes:
            box = boxes[0]
        else:
            sheets = doc.xpath("/form/sheet")
            if not sheets:
                return res
            box = etree.Element("div", {"class": "oe_button_box", "name": "button_box"})
            sheets[0].insert(0, box)
        button = etree.fromstring(
            self.env["ir.qweb"]._render(
                "sign_oca_tier_validation.tier_validation_sign_oca_button", {}
            )
        )
        new_arch, new_models = self.env["ir.ui.view"].postprocess_and_fields(
            button, self._name
        )
        box.append(etree.fromstring(new_arch))
        all_models = dict(res["models"])
        _merge_view_fields(all_models, new_models)
        res["arch"] = etree.tostring(doc)
        res["models"] = frozendict(all_models)
        return res
