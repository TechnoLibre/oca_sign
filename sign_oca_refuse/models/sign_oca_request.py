# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from markupsafe import Markup

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import plaintext2html

from odoo.addons.sign_oca.models.sign_oca_request import MAX_TEXT_LENGTH


class SignOcaRequest(models.Model):
    _inherit = "sign.oca.request"

    state = fields.Selection(
        selection_add=[("4_refused", "Refused")],
        ondelete={"4_refused": "set default"},
    )

    def _notify_refused(self, signer, reason):
        """Post the refusal of ``signer`` in the chatter of the request.

        The message is authored by the signer's partner and notifies the
        partner of the responsible user (``user_id``) besides the followers.

        :param signer: the ``sign.oca.request.signer`` that refused
        :param str reason: the reason given by the signer, as plain text
        :return: the posted ``mail.message``
        """
        self.ensure_one()
        body = Markup("<p>%s</p>%s") % (
            self.env._(
                "%(partner)s refused to sign the document.",
                partner=signer.partner_id.display_name,
            ),
            plaintext2html(reason),
        )
        return self.message_post(
            body=body,
            author_id=signer.partner_id.id,
            partner_ids=self.user_id.partner_id.ids,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )


class SignOcaRequestSigner(models.Model):
    _inherit = "sign.oca.request.signer"

    refused_on = fields.Datetime(readonly=True, copy=False)
    refuse_reason = fields.Text(readonly=True, copy=False)

    def _get_server_fields(self):
        """The refusal is written by ``action_refuse`` only."""
        return super()._get_server_fields() | {"refused_on", "refuse_reason"}

    @api.depends("signed_on", "partner_id", "refused_on", "request_id.state")
    @api.depends_context("uid")
    def _compute_is_allow_signature(self):
        result = super()._compute_is_allow_signature()
        for item in self:
            if item.refused_on or item.request_id.state == "4_refused":
                item.is_allow_signature = False
        return result

    def _check_can_sign(self):
        """Refuse to sign a request that one of its signers has refused."""
        self.ensure_one()
        if self.refused_on or self.request_id.state == "4_refused":
            raise ValidationError(
                self.env._("This document has been refused and cannot be signed.")
            )
        return super()._check_can_sign()

    def _check_can_refuse(self):
        """Raise when this signer may not refuse now.

        A signer may refuse exactly when it may sign: by default this calls
        ``_check_can_sign``, so its conditions (and those added by other
        modules) apply to the refusal too.

        :raise ValidationError: with the reason, shown to the signer
        """
        self.ensure_one()
        self._check_can_sign()

    def _can_refuse(self):
        """Return whether ``_check_can_refuse`` lets this signer refuse now."""
        self.ensure_one()
        try:
            self._check_can_refuse()
        except UserError:
            return False
        return True

    def get_info(self, access_token=False):
        result = super().get_info(access_token=access_token)
        result["can_refuse"] = self._can_refuse()
        return result

    def action_refuse(self, reason, access_token=False):
        """Refuse to sign, for ``reason``, and close the request.

        The request rows are locked as in ``action_sign`` and the checks run
        as the caller. Then, as superuser, ``_before_refuse`` runs, the
        signer gets ``refused_on`` and ``refuse_reason``, the request moves
        to ``4_refused``, a ``refuse`` log entry is written, the refusal is
        posted in the chatter of the request for its responsible user, and
        ``_on_refuse`` runs last.

        :param str reason: why the signer refuses; blank values, and values
            longer than ``MAX_TEXT_LENGTH`` characters as a signed text, are
            rejected
        :param str access_token: token of the portal access, kept in the log
        :return: an ``ir.actions.act_url`` action to the signer's page
        :raise ValidationError: when the reason is blank or the signer may
            not refuse (see ``_check_can_refuse``)
        """
        self.ensure_one()
        self.request_id._lock_for_sign()
        self._check_can_refuse()
        reason = (reason or "").strip() if isinstance(reason, str) else ""
        if not reason:
            raise ValidationError(self.env._("A reason is required to refuse."))
        if len(reason) > MAX_TEXT_LENGTH:
            raise ValidationError(
                self.env._(
                    "The reason is longer than %(max)s characters.",
                    max=MAX_TEXT_LENGTH,
                )
            )
        return self.sudo()._action_refuse(reason, access_token=access_token)

    def _action_refuse(self, reason, access_token=False):
        self._before_refuse(reason)
        self.write({"refused_on": fields.Datetime.now(), "refuse_reason": reason})
        self.request_id.state = "4_refused"
        self._set_action_log("refuse", access_token=access_token)
        self.request_id._notify_refused(self, reason)
        self._on_refuse(reason)
        return {
            "type": "ir.actions.act_url",
            "url": self.access_url,
        }

    def _before_refuse(self, reason):
        """Hook run by ``action_refuse`` once the refusal is accepted.

        The checks and the reason passed; nothing is written yet, so the
        signer's partner set here is the one the log, the chatter message
        and ``_on_refuse`` see. Default does nothing.

        :param str reason: the stripped reason given by the signer
        """

    def _on_refuse(self, reason):
        """Hook run by ``action_refuse`` once this signer has refused.

        ``refused_on``, ``refuse_reason``, the ``4_refused`` state of the
        request, the ``refuse`` log entry and the chatter message are
        written. It runs in the same transaction: raising cancels the
        refusal. Default does nothing.

        :param str reason: the stripped reason given by the signer
        """


class SignOcaRequestLog(models.Model):
    _inherit = "sign.oca.request.log"

    action = fields.Selection(
        selection_add=[("refuse", "Refuse")],
        ondelete={"refuse": "cascade"},
    )
