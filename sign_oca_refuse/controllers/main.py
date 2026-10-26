# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request

from odoo.addons.sign_oca.controllers.main import PortalSign


class PortalSignRefuse(PortalSign):
    @http.route()
    def get_sign_oca_access(self, signer_id, access_token, **kwargs):
        """Show the refusal page instead of the document once the request is
        refused, to every signer that has not signed it.

        A refused access is left to ``super()``, which answers it.
        """
        try:
            signer_sudo = self._sign_oca_check_access(signer_id, access_token)
        except (AccessError, MissingError):
            return super().get_sign_oca_access(signer_id, access_token, **kwargs)
        if not signer_sudo.signed_on and signer_sudo.request_id.state == "4_refused":
            return request.render(
                "sign_oca_refuse.portal_sign_document_refused",
                {
                    "signer": signer_sudo,
                    "company": signer_sudo.request_id.company_id,
                },
            )
        return super().get_sign_oca_access(signer_id, access_token, **kwargs)

    @http.route(
        ["/sign_oca/refuse/<int:signer_id>/<string:access_token>"],
        type="json",
        auth="public",
        website=True,
    )
    def get_sign_oca_refuse_access(self, signer_id, access_token, reason=""):
        """Refuse the request for the signer that the token opens.

        :param str reason: why the signer refuses, required
        :return: the action of ``signer.action_refuse``
        """
        try:
            signer_sudo = self._sign_oca_check_access(signer_id, access_token)
        except (AccessError, MissingError) as error:
            return self._sign_oca_access_error_response(signer_id, access_token, error)
        return signer_sudo.action_refuse(reason, access_token=access_token)

    def get_sign_requests_domain(self, request):
        """Keep the requests refused in the portal list of their signers."""
        domain = super().get_sign_requests_domain(request)
        result = []
        for leaf in domain:
            if (
                isinstance(leaf, list | tuple)
                and len(leaf) == 3
                and tuple(leaf[:2]) == ("request_id.state", "in")
            ):
                leaf = (leaf[0], leaf[1], tuple(leaf[2]) + ("4_refused",))
            result.append(leaf)
        return result

    def _get_my_sign_requests_searchbar_filters(self):
        searchbar_filters = super()._get_my_sign_requests_searchbar_filters()
        searchbar_filters["refused"] = {
            "label": _("Refused"),
            "domain": [("request_id.state", "=", "4_refused")],
        }
        return searchbar_filters
