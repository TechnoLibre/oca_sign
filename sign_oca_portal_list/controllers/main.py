# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields
from odoo.http import request
from odoo.osv import expression
from odoo.tools import SQL, format_date
from odoo.tools import groupby as groupbyelem

from odoo.addons.portal.controllers.portal import pager as portal_pager
from odoo.addons.sign_oca.controllers.main import PortalSign


class PortalSignList(PortalSign):
    def _get_my_sign_requests_searchbar_inputs(self):
        """Return the search fields of ``/my/sign_requests``.

        Each key is a ``search_in`` value; ``_get_my_sign_requests_search_domain``
        turns it into a domain.
        """
        return {
            "all": {"input": "all", "label": _("Search in All")},
            "name": {"input": "name", "label": _("Search in Document")},
            "record": {"input": "record", "label": _("Search in Related Record")},
        }

    def _get_my_sign_requests_searchbar_groupby(self):
        """Return the groupings of ``/my/sign_requests``.

        ``request_order`` is an order on ``sign.oca.request`` and ``order`` an
        order on ``sign.oca.request.signer``; either one puts the lines of a
        group next to each other, so a group never splits across the page.
        ``_get_my_sign_requests_group_label`` gives the label of a group.
        """
        return {
            "none": {"label": _("None")},
            "document": {"label": _("Document"), "request_order": "name, id"},
            "state": {"label": _("State"), "request_order": "state"},
            "sender": {"label": _("Sent by"), "request_order": "user_id"},
            "year": {"label": _("Sent on: year"), "order": "create_date desc"},
            "month": {"label": _("Sent on: month"), "order": "create_date desc"},
            "day": {"label": _("Sent on: day"), "order": "create_date desc"},
        }

    def _get_my_sign_requests_search_domain(self, domain, search_in, search):
        """Return the domain on signers that ``search`` matches in ``search_in``.

        :param list domain: the signers the search is restricted to
        :param str search_in: a key of ``_get_my_sign_requests_searchbar_inputs``
        :param str search: the searched text
        """
        if not search:
            return []
        domains = []
        if search_in in ("all", "name"):
            domains.append([("request_id.name", "ilike", search)])
        if search_in in ("all", "record"):
            domains.append(self._get_my_sign_requests_record_domain(domain, search))
        return expression.OR(domains) if domains else []

    def _get_my_sign_requests_record_domain(self, domain, search):
        """Return the domain on signers whose related record has a display name
        matching ``search``.

        Only the records of the signers of ``domain`` are searched, as
        superuser: a signer finds their request without read access on the
        related record. A model that is no longer installed is skipped.
        """
        Signer = request.env["sign.oca.request.signer"].sudo()
        groups = Signer._read_group(
            expression.AND([domain, [("model", "!=", False), ("res_id", "!=", 0)]]),
            ["model"],
            ["res_id:array_agg"],
        )
        domains = []
        for model, res_ids in groups:
            if model not in request.env:
                continue
            records = (
                request.env[model]
                .sudo()
                .search(
                    [
                        ("id", "in", list(set(res_ids))),
                        ("display_name", "ilike", search),
                    ]
                )
            )
            if records:
                domains.append([("model", "=", model), ("res_id", "in", records.ids)])
        return expression.OR(domains) if domains else expression.FALSE_DOMAIN

    def _get_my_sign_requests_order(self, groupby_option, sort_order):
        """Return the signer order of a page: the grouping order, then
        ``sort_order``, then ``id desc``.

        The grouping order is dropped when ``sort_order`` starts on the same
        field, so "Oldest" still reverses a grouping by date.
        """
        orders = []
        group_order = groupby_option.get("order")
        if group_order and group_order.split()[0] != sort_order.split()[0]:
            orders.append(group_order)
        orders += [sort_order, "id desc"]
        return ", ".join(orders)

    def _search_my_sign_requests(self, domain, groupby_option, order, limit, offset):
        """Return a page of signers of ``domain`` as superuser.

        A ``request_order`` of ``groupby_option`` is joined on the signer's
        request and sorts before ``order``.
        """
        Signer = request.env["sign.oca.request.signer"].sudo()
        request_order = groupby_option.get("request_order")
        if not request_order:
            return Signer.search(domain, order=order, limit=limit, offset=offset)
        query = Signer._search(domain, order=order, limit=limit, offset=offset)
        Request = Signer.env["sign.oca.request"]
        alias = query.left_join(
            Signer._table, "request_id", Request._table, "id", "portal_list_group"
        )
        query.order = SQL(
            "%s, %s", Request._order_to_sql(request_order, query, alias), query.order
        )
        return Signer.browse(query.get_result_ids())

    def _get_my_sign_requests_group_label(self, signer, groupby):
        """Return the label of the group of ``signer`` under ``groupby``.

        The sent date is the signer's creation date, read in the user's time
        zone.
        """
        env = request.env
        if groupby == "document":
            return signer.request_id.name
        if groupby == "state":
            sign_request = signer.request_id
            selection = sign_request._fields["state"]._description_selection(env)
            return dict(selection)[sign_request.state]
        if groupby == "sender":
            return signer.request_id.user_id.name or ""
        sent = fields.Datetime.context_timestamp(signer, signer.create_date)
        if groupby == "year":
            return str(sent.year)
        if groupby == "month":
            return format_date(env, sent, date_format="MMMM yyyy")
        return format_date(env, sent)

    def _get_my_sign_requests_record_names(self, signers):
        """Return ``{signer id: display name of its request's related record}``
        for the signers whose related record still exists."""
        names = {}
        for model, model_signers in groupbyelem(
            signers.filtered(lambda s: s.model and s.res_id), lambda s: s.model
        ):
            if model not in request.env:
                continue
            records = (
                request.env[model]
                .sudo()
                .browse({s.res_id for s in model_signers})
                .exists()
            )
            record_names = {record.id: record.display_name for record in records}
            for signer in model_signers:
                if signer.res_id in record_names:
                    names[signer.id] = record_names[signer.res_id]
        return names

    def _prepare_sign_portal_rendering_values(self, page=1, sign_page=False, **kwargs):
        """Add search and grouping to the values of ``/my/sign_requests``.

        The sortings and filters stay those of ``sign_oca``; an unknown
        ``sortby``, ``filterby``, ``groupby`` or ``search_in`` falls back to
        the default. The pager keeps every one of them in its links.
        """
        sortby = kwargs.pop("sortby", None)
        values = super()._prepare_sign_portal_rendering_values(
            page=page, sign_page=sign_page, **kwargs
        )
        if not sign_page:
            return values
        searchbar_sortings = values["searchbar_sortings"]
        if sortby not in searchbar_sortings:
            sortby = values["sortby"]
        searchbar_filters = values["searchbar_filters"]
        filterby = values["filterby"]
        if filterby not in searchbar_filters:
            filterby = "all"
        searchbar_inputs = self._get_my_sign_requests_searchbar_inputs()
        search_in = kwargs.get("search_in")
        if search_in not in searchbar_inputs:
            search_in = "all"
        search = kwargs.get("search") or ""
        searchbar_groupby = self._get_my_sign_requests_searchbar_groupby()
        groupby = kwargs.get("groupby")
        if groupby not in searchbar_groupby:
            groupby = "none"

        domain = expression.AND(
            [
                searchbar_filters[filterby]["domain"],
                self.get_sign_requests_domain(request),
            ]
        )
        domain = expression.AND(
            [
                domain,
                self._get_my_sign_requests_search_domain(domain, search_in, search),
            ]
        )
        url_args = {
            "sortby": sortby,
            "filterby": filterby,
            "search_in": search_in,
            "search": search,
            "groupby": groupby,
        }
        pager_values = portal_pager(
            url=values["default_url"],
            total=request.env["sign.oca.request.signer"].sudo().search_count(domain),
            page=page,
            step=self._items_per_page,
            url_args={key: value for key, value in url_args.items() if value},
        )
        groupby_option = searchbar_groupby[groupby]
        sign_requests = self._search_my_sign_requests(
            domain,
            groupby_option,
            self._get_my_sign_requests_order(
                groupby_option, searchbar_sortings[sortby]["order"]
            ),
            self._items_per_page,
            pager_values["offset"],
        )
        if groupby == "none":
            grouped = [(False, sign_requests)] if sign_requests else []
        else:
            grouped = [
                (label, sign_requests.browse().concat(*group))
                for label, group in groupbyelem(
                    sign_requests,
                    lambda s: self._get_my_sign_requests_group_label(s, groupby),
                )
            ]
        values.update(
            {
                "sign_requests": sign_requests,
                "grouped_sign_requests": grouped,
                "sign_request_record_names": self._get_my_sign_requests_record_names(
                    sign_requests
                ),
                "pager": pager_values,
                "sortby": sortby,
                "filterby": filterby,
                "searchbar_inputs": searchbar_inputs,
                "search_in": search_in,
                "search": search,
                "searchbar_groupby": searchbar_groupby,
                "groupby": groupby,
            }
        )
        return values
