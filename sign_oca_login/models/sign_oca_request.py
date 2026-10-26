# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.http import request


class SignOcaRequest(models.Model):
    _inherit = "sign.oca.request"

    require_login = fields.Boolean(
        tracking=True,
        help="Each signer must log in with a user linked to the signer's "
        "contact before they can open or sign the document. Taken from the "
        "template when the request is created; it can only change while the "
        "request is a draft.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Take ``require_login`` from the template when ``vals`` omits it."""
        for vals in vals_list:
            if "require_login" not in vals and vals.get("template_id"):
                template = self.env["sign.oca.template"].browse(vals["template_id"])
                vals["require_login"] = template.require_login
        return super().create(vals_list)

    def write(self, vals):
        """Refuse to change ``require_login`` on a request that left draft."""
        if "require_login" in vals:
            for record in self:
                if record.state != "1_draft" and record.require_login != bool(
                    vals["require_login"]
                ):
                    raise UserError(
                        self.env._(
                            "The login requirement of %(name)s can only change "
                            "while the request is a draft.",
                            name=record.name,
                        )
                    )
        return super().write(vals)


class SignOcaRequestSigner(models.Model):
    _inherit = "sign.oca.request.signer"

    def _get_login_user(self):
        """Return the user who acts on this signer.

        During an HTTP request it is the session user (the public user for an
        anonymous visitor): the portal routes work on a superuser copy of the
        signer, so the environment user is not the visitor. Outside HTTP it
        is the environment user.

        :return: a ``res.users`` record
        """
        if request and getattr(request, "env", None) is not None:
            return request.env.user
        return self.env.user

    def _is_login_user(self, user):
        """Tell whether ``user`` may act as this signer on a request that
        requires a login.

        The user must not be public, and the signer's partner must be the
        user's partner or one of its descendants: the ``child_of`` rule that
        sign_oca's portal uses to list a user's requests.

        :param user: a ``res.users`` record
        :return: bool
        """
        self.ensure_one()
        if not user or user._is_public():
            return False
        target = user.partner_id
        partner = self.sudo().partner_id
        while partner:
            if partner == target:
                return True
            partner = partner.parent_id
        return False

    def _check_can_sign(self):
        """Also refuse, on a request that requires a login, any user who is
        not this signer (see ``_is_login_user``)."""
        result = super()._check_can_sign()
        if self.request_id.require_login and not self._is_login_user(
            self._get_login_user()
        ):
            raise ValidationError(self._get_login_required_message())
        return result

    def _get_login_required_message(self):
        """Return the reason given to a user who may not act as this signer
        on a request that requires a login."""
        self.ensure_one()
        return self.env._(
            "This document can only be signed by %(partner)s, logged in.",
            partner=self.partner_id.name,
        )
