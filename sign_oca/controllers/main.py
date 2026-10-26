from collections import OrderedDict

from odoo import _, http
from odoo.exceptions import AccessError, MissingError
from odoo.http import request
from odoo.osv import expression
from odoo.tools import consteq

from odoo.addons.base.models.assetsbundle import AssetsBundle
from odoo.addons.portal.controllers.portal import CustomerPortal
from odoo.addons.portal.controllers.portal import pager as portal_pager


class SignController(http.Controller):
    @http.route("/sign_oca/get_assets.<any(css,js):ext>", type="http", auth="public")
    def get_sign_resources(self, ext):
        bundle = "sign_oca.sign_assets"
        files, _ = request.env["ir.qweb"]._get_asset_content(bundle)
        asset = AssetsBundle(bundle, files)
        mock_attachment = getattr(asset, ext)()
        if isinstance(
            mock_attachment, list
        ):  # suppose that CSS asset will not required to be split in pages
            mock_attachment = mock_attachment[0]
        stream = request.env["ir.binary"]._get_stream_from(mock_attachment)
        response = stream.get_response()
        return response


class PortalSign(CustomerPortal):
    def _sign_oca_check_access(self, signer_id, access_token):
        """Return the signer that ``signer_id`` and ``access_token`` open.

        Every ``/sign_oca/*`` route of a signer starts with this check.

        The token must match even for a user whose access rights let them
        read the signer: the link opens one signer's page, whoever follows
        it.

        :param int signer_id: id of a ``sign.oca.request.signer``
        :param str access_token: the signer's portal access token
        :return: the signer, as superuser
        :raise MissingError: when the signer does not exist
        :raise AccessError: when the token does not match. Overrides may add
            conditions, and raise a subclass of AccessError that
            ``_sign_oca_access_error_response`` turns into its own response.
        """
        signer_sudo = self._document_check_access(
            "sign.oca.request.signer", signer_id, access_token
        )
        if not (
            access_token
            and signer_sudo.access_token
            and consteq(signer_sudo.access_token, access_token)
        ):
            raise AccessError(request.env._("This signature link is not valid."))
        return signer_sudo

    def _sign_oca_access_error_response(self, signer_id, access_token, error):
        """Return the response of a ``/sign_oca/*`` route refused by
        ``_sign_oca_check_access`` with ``error``: a redirection to ``/my``.
        """
        return request.redirect("/my")

    @http.route(
        ["/sign_oca/document/<int:signer_id>/<string:access_token>"],
        type="http",
        auth="public",
        website=True,
    )
    def get_sign_oca_access(self, signer_id, access_token, **kwargs):
        try:
            signer_sudo = self._sign_oca_check_access(signer_id, access_token)
        except (AccessError, MissingError) as error:
            return self._sign_oca_access_error_response(signer_id, access_token, error)
        if signer_sudo.signed_on:
            return request.render(
                "sign_oca.portal_sign_document_signed",
                {
                    "signer": signer_sudo,
                    "company": signer_sudo.request_id.company_id,
                },
            )
        return request.render(
            "sign_oca.portal_sign_document",
            {
                "doc": signer_sudo.request_id,
                "partner": signer_sudo.partner_id,
                "signer": signer_sudo,
                "can_sign": signer_sudo._can_sign(),
                "access_token": access_token,
                "sign_oca_backend_info": {
                    "access_token": access_token,
                    "signer_id": signer_sudo.id,
                    "lang": signer_sudo.partner_id.lang,
                },
            },
        )

    @http.route(
        ["/sign_oca/content/<int:signer_id>/<string:access_token>"],
        type="http",
        auth="public",
        website=True,
    )
    def get_sign_oca_content_access(self, signer_id, access_token):
        try:
            signer_sudo = self._sign_oca_check_access(signer_id, access_token)
        except (AccessError, MissingError) as error:
            return self._sign_oca_access_error_response(signer_id, access_token, error)
        return http.Stream.from_binary_field(
            signer_sudo.request_id, "data"
        ).get_response(mimetype="application/pdf")

    @http.route(
        ["/sign_oca/info/<int:signer_id>/<string:access_token>"],
        type="json",
        auth="public",
        website=True,
    )
    def get_sign_oca_info_access(self, signer_id, access_token):
        try:
            signer_sudo = self._sign_oca_check_access(signer_id, access_token)
        except (AccessError, MissingError) as error:
            return self._sign_oca_access_error_response(signer_id, access_token, error)
        return signer_sudo.get_info(access_token=access_token)

    @http.route(
        ["/sign_oca/sign/<int:signer_id>/<string:access_token>"],
        type="json",
        auth="public",
        website=True,
    )
    def get_sign_oca_sign_access(
        self, signer_id, access_token, items, latitude=False, longitude=False
    ):
        try:
            signer_sudo = self._sign_oca_check_access(signer_id, access_token)
        except (AccessError, MissingError) as error:
            return self._sign_oca_access_error_response(signer_id, access_token, error)
        return signer_sudo.action_sign(
            items, access_token=access_token, latitude=latitude, longitude=longitude
        )

    def get_sign_requests_domain(self, request):
        domain = [
            ("request_id.state", "in", ("0_sent", "2_signed")),
            ("partner_id", "child_of", [request.env.user.partner_id.id]),
        ]
        return domain

    def _get_my_sign_requests_searchbar_filters(self):
        searchbar_filters = {
            "all": {"label": _("All"), "domain": []},
            "sent": {
                "label": _("sent"),
                "domain": [("request_id.state", "=", "0_sent")],
            },
            "signed": {
                "label": _("Signed"),
                "domain": [("request_id.state", "=", "2_signed")],
            },
        }
        return searchbar_filters

    def _prepare_sign_portal_rendering_values(self, page=1, sign_page=False, **kwargs):
        # Sorting feature
        searchbar_sortings = {
            "state": {"label": _("Sent to Signed"), "order": "request_id"},
            "reverse_state": {"label": _("Signed to Sent"), "order": "request_id desc"},
            "date": {"label": _("Newest"), "order": "create_date desc"},
            "reverse_date": {"label": _("Oldest"), "order": "create_date"},
        }
        sortby = kwargs.get("sortby", "state")
        order = searchbar_sortings[sortby]["order"]
        # Filtering feature
        searchbar_filters = self._get_my_sign_requests_searchbar_filters()
        filterby = kwargs.get("filterby") or "all"
        domain = searchbar_filters.get(filterby, searchbar_filters["all"])["domain"]
        domain = expression.AND([domain, self.get_sign_requests_domain(request)])
        SignRequests = request.env["sign.oca.request.signer"].sudo()
        pager_values = portal_pager(
            url="/my/sign_requests",
            total=SignRequests.search_count(domain),
            page=page,
            step=self._items_per_page,
            url_args={},
        )
        sign_requests = SignRequests.search(
            domain,
            order=order,
            limit=self._items_per_page,
            offset=pager_values["offset"],
        )
        values = self._prepare_portal_layout_values()
        values.update(
            {
                "sign_requests": sign_requests.sudo() if sign_page else SignRequests,
                "page_name": "My Sign Requests",
                "pager": pager_values,
                "default_url": "/my/sign_requests",
                "searchbar_sortings": searchbar_sortings,
                "searchbar_filters": OrderedDict(sorted(searchbar_filters.items())),
                "sortby": sortby,
                "filterby": filterby,
            }
        )
        return values

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        if "sign_oca_count" in counters:
            domain = self.get_sign_requests_domain(request)
            SignRequests = request.env["sign.oca.request.signer"].sudo()
            values["sign_oca_count"] = SignRequests.search_count(domain)
        return values

    @http.route(
        ["/my/sign_requests", "/my/sign_requests/page/<int:page>"],
        type="http",
        auth="public",
        website=True,
    )
    def sign_requests(self, **kwargs):
        values = self._prepare_sign_portal_rendering_values(sign_page=True, **kwargs)
        return request.render(
            "sign_oca.sign_requests",
            values,
        )

    def _sign_oca_can_download(self, sign_request):
        """Tell whether the current user may download ``sign_request``.

        A signer of the request may, with the partner and state conditions of
        ``get_sign_requests_domain``; so may a user whose access rights let
        them read the request.

        :param sign_request: a ``sign.oca.request`` record that exists
        :return: bool
        """
        domain = expression.AND(
            [
                [("request_id", "=", sign_request.id)],
                self.get_sign_requests_domain(request),
            ]
        )
        if request.env["sign.oca.request.signer"].sudo().search_count(domain):
            return True
        try:
            sign_request.with_env(request.env).check_access("read")
        except AccessError:
            return False
        return True

    @http.route(
        ["/my/sign/<int:request_id>/download"], type="http", auth="user", website=True
    )
    def portal_download_signed(self, request_id, **kw):
        """Send the current document of the request as a PDF download.

        The file is streamed by this route, which checks the user on every
        call; no lasting link to the attachment is handed out.
        """
        sign_request = request.env["sign.oca.request"].sudo().browse(request_id)
        if not sign_request.exists() or not self._sign_oca_can_download(sign_request):
            return request.not_found()
        if not sign_request.data:
            return request.not_found()
        filename = sign_request.name or "document.pdf"
        # Ensure .pdf extension
        if not filename.lower().endswith(".pdf"):
            filename = f"{filename}.pdf"
        stream = http.Stream.from_binary_field(sign_request, "data")
        stream.mimetype = "application/pdf"
        stream.download_name = filename
        stream.public = False
        return stream.get_response(as_attachment=True)
