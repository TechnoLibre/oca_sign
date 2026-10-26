# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.exceptions import AccessError
from odoo.http import request

from odoo.addons.sign_oca.controllers.main import PortalSign


class SignOcaLoginRequired(AccessError):
    """Raised by ``_sign_oca_check_access`` when the request requires a login
    and the visitor is anonymous; the error response turns it into a
    redirection to the login page."""


class PortalSignLogin(PortalSign):
    def _sign_oca_check_access(self, signer_id, access_token):
        """On a request that requires a login, also require the session user
        to be the signer (see ``sign.oca.request.signer._is_login_user``).

        :raise SignOcaLoginRequired: when the visitor is anonymous
        :raise AccessError: when the session user is not the signer
        """
        signer_sudo = super()._sign_oca_check_access(signer_id, access_token)
        if signer_sudo.request_id.require_login:
            user = request.env.user
            if user._is_public():
                raise SignOcaLoginRequired(
                    request.env._("Log in to open this document.")
                )
            if not signer_sudo._is_login_user(user):
                raise AccessError(
                    request.env._("This document is addressed to another user.")
                )
        return signer_sudo

    def _sign_oca_access_error_response(self, signer_id, access_token, error):
        """Send an anonymous visitor to the login page, which brings them back
        to the signer's document page once logged in."""
        if isinstance(error, SignOcaLoginRequired):
            return request.redirect_query(
                "/web/login",
                {"redirect": f"/sign_oca/document/{signer_id}/{access_token}"},
            )
        return super()._sign_oca_access_error_response(signer_id, access_token, error)
