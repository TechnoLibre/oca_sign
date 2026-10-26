# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import datetime
import sys

import pytz

from odoo import api, fields, models

DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# Log actions of the template editor: they describe how the document is
# prepared, not who saw or decided it, and stay off the certificate.
EDITOR_LOG_ACTIONS = ("add_field", "edit_field", "delete_field", "configure")


class SignOcaPadesCertificate(models.AbstractModel):
    """Values of the signature certificate page of a ``sign.oca.request``.

    The certificate template calls ``_get_values`` for each request it
    prints. Every other method is a hook a module overrides to change one
    part of the page: the zone of the local times, the order of the signers,
    the extra columns of the decisions table, the channel of a decision or
    of a log entry, the log entries listed.
    """

    _name = "sign.oca.pades.certificate"
    _description = "Signature Certificate Values"

    @api.model
    def _get_values(self, request):
        """Return the values the certificate page of ``request`` prints.

        They are read as superuser, like the rest of the page, so every
        signer and log entry is listed whoever renders it.

        :param request: a ``sign.oca.request``
        :return: dict with

            - ``tz``: name of the zone of the local times (``_get_tz``)
            - ``created_on`` and ``printed_on``: times, as returned by
              ``_format_datetime``
            - ``signers``: the signers in signing order (``_get_signers``)
            - ``signer_counts``: dict of the numbers of signers, ``total``,
              ``signed``, ``refused``, ``approved`` (approved without a
              signature of their own, see
              ``_get_approved_without_signature``), ``pending`` and
              ``undecided``: the signers left without decision by a request
              closed before completion (refused or cancelled) are undecided,
              not pending
            - ``signer_status``: ``{signer id: status label}``
            - ``decisions``: one dict per signer that signed or refused, in
              signing order (``_get_decision``)
            - ``signer_columns``: extra columns of the decisions table
              (``_get_signer_columns``)
            - ``logs``: one dict per log entry (``_get_log``)
        """
        request.ensure_one()
        request = request.sudo()
        tz_name = self._get_tz(request)
        signers = self._get_signers(request)
        decided = signers.filtered(lambda signer: self._get_decided_on(signer))
        approved = (signers - decided).filtered(
            lambda signer: self._get_approved_without_signature(signer)
        )
        rest = signers - decided - approved
        closed = self._is_closed_undecided(request)
        return {
            "tz": tz_name,
            "created_on": self._format_datetime(request.create_date, tz_name),
            "printed_on": self._format_datetime(fields.Datetime.now(), tz_name),
            "signers": signers,
            "signer_counts": {
                "total": len(signers),
                "signed": len(signers.filtered("signed_on")),
                "refused": len(decided.filtered(lambda signer: not signer.signed_on)),
                "approved": len(approved),
                "pending": 0 if closed else len(rest),
                "undecided": len(rest) if closed else 0,
            },
            "signer_status": {
                signer.id: self._get_signer_status(signer) for signer in signers
            },
            "decisions": [
                self._get_decision(request, signer, tz_name) for signer in decided
            ],
            "signer_columns": self._get_signer_columns(request),
            "logs": [
                self._get_log(request, log, tz_name) for log in self._get_logs(request)
            ],
        }

    @api.model
    def _get_page_style(self):
        """Return the inline style of the certificate page, or False.

        The context key ``sign_oca_pades_certificate_zoom``, set when the
        certificate is rendered again to fit the pages its document reserves
        for it, becomes a CSS ``zoom``: the text reflows to the page width at
        that scale.
        """
        zoom = self.env.context.get("sign_oca_pades_certificate_zoom")
        if not isinstance(zoom, int | float) or not 0 < zoom < 1:
            return False
        return f"zoom: {zoom:.3f};"

    @api.model
    def _get_tz(self, request):
        """Return the name of the zone of the local times of ``request``.

        Hook. Default: the request's ``tier_tz`` when a module defines it
        (``sign_oca_tier_validation`` stores there the zone of the dates
        written in the document), else the zone of the company contact,
        else UTC. An unknown zone name gives UTC.
        """
        tz_name = (
            "tier_tz" in request._fields and request.tier_tz
        ) or request.company_id.partner_id.tz
        return tz_name if tz_name in pytz.all_timezones_set else "UTC"

    @api.model
    def _format_datetime(self, moment, tz_name):
        """Return ``moment`` as the texts the certificate prints.

        :param moment: naive UTC ``datetime``, or False
        :param str tz_name: zone of the local time
        :return: dict ``{"utc": text, "local": text or False}``, or False
            without ``moment``; ``local`` is False when the zone is UTC
        """
        if not moment:
            return False
        moment = fields.Datetime.to_datetime(moment).replace(microsecond=0)
        utc = pytz.utc.localize(moment)
        local = False
        if tz_name != "UTC":
            local_moment = utc.astimezone(pytz.timezone(tz_name))
            zone = local_moment.tzname() or tz_name
            local = f"{local_moment.strftime(DATETIME_FORMAT)} {zone}"
        return {"utc": f"{utc.strftime(DATETIME_FORMAT)} UTC", "local": local}

    @api.model
    def _get_decided_on(self, signer):
        """Return when ``signer`` signed or refused (naive UTC), or False.

        A refusal is read from ``refused_on`` when ``sign_oca_refuse``
        defines it.
        """
        return signer.signed_on or (
            "refused_on" in signer._fields and signer.refused_on
        )

    @api.model
    def _get_approved_without_signature(self, signer):
        """Return whether ``signer``'s part is approved without a signature
        of their own.

        Hook. Default: the signer's ``tier_bypassed`` when
        ``sign_oca_tier_validation`` defines it, set on a tier approved along
        with an earlier tier of the same reviewer.
        """
        return bool("tier_bypassed" in signer._fields and signer.tier_bypassed)

    @api.model
    def _get_signers(self, request):
        """Return the signers of ``request`` in signing order.

        Hook. Default: the signers that decided, by decision time, then
        the others in creation order. Decisions within the same second keep
        the order of the inalterability chain (``secure_sequence_number``),
        a refusal coming after the signatures of its second.
        ``sign.oca.request.signer`` is ordered by ``signed_on desc``, which
        lists the last signer first.
        """
        never = datetime.datetime.max

        def sort_key(signer):
            return (
                self._get_decided_on(signer) or never,
                signer.secure_sequence_number or sys.maxsize,
                signer.id,
            )

        return request.signer_ids.sorted(sort_key)

    @api.model
    def _get_signer_status(self, signer):
        """Return the label of the decision of ``signer``: a signer without
        decision is pending, or not decided once the request is closed
        before completion (``_is_closed_undecided``)."""
        if signer.signed_on:
            return self.env._("Signed")
        if self._get_decided_on(signer):
            return self.env._("Refused")
        if self._get_approved_without_signature(signer):
            return self.env._("Approved without signature")
        if self._is_closed_undecided(signer.request_id):
            return self.env._("Not decided")
        return self.env._("Pending")

    @api.model
    def _is_closed_undecided(self, request):
        """Return whether ``request`` ended before every signer decided: it
        is refused or cancelled, so its signers without decision will not
        decide."""
        return request.state in ("3_cancel", "4_refused")

    @api.model
    def _get_decision(self, request, signer, tz_name):
        """Return the values of the decision of ``signer`` in the certificate.

        :return: dict with ``signer``, ``status`` (label), ``date`` (see
            ``_format_datetime``), ``reason`` (refusal reason or False),
            ``hash`` (inalterability hash or False) and ``altered`` (the hash
            chain no longer matches)
        """
        reason = "refuse_reason" in signer._fields and signer.refuse_reason
        return {
            "signer": signer,
            "status": self._get_signer_status(signer),
            "date": self._format_datetime(self._get_decided_on(signer), tz_name),
            "reason": reason or False,
            "hash": signer.inalterable_hash or False,
            "altered": bool(signer.inalterable_hash and signer.altered_hash),
        }

    @api.model
    def _get_signer_columns(self, request):
        """Return the extra columns of the decisions table of ``request``.

        Hook: append ``(label, getter)`` to add a column, where
        ``getter(signer)`` returns the text of the cell (False prints
        "Not available"). Default: the channel of the decision
        (``_get_signer_channel``).
        """
        return [
            (
                self.env._("Channel"),
                lambda signer: self._get_signer_channel(request, signer),
            )
        ]

    @api.model
    def _get_signer_channel(self, request, signer):
        """Return the label of the channel ``signer`` decided from, or False.

        Hook. Default: the channel of the last ``sign`` or ``refuse`` log
        entry of the signer (``_get_log_channel``). Override it where the
        decision records its own channel.
        """
        log = (
            self.env["sign.oca.request.log"]
            .sudo()
            .search(
                [
                    ("request_id", "=", request.id),
                    ("signer_id", "=", signer.id),
                    ("action", "in", ("sign", "refuse")),
                ],
                order="date desc, id desc",
                limit=1,
            )
        )
        return log and self._get_log_channel(log)

    @api.model
    def _get_log_channel(self, log):
        """Return the label of the channel of a ``sign.oca.request.log``.

        Hook. The portal routes of sign_oca pass the signer's access token,
        which the entry keeps; the back office calls the model without it;
        an entry made outside an HTTP request has the ``0.0.0.0`` address.
        """
        if log.access_token:
            return self.env._("Portal")
        if not log.ip or log.ip == "0.0.0.0":
            return self.env._("Server")
        return self.env._("Back office")

    @api.model
    def _get_logs(self, request):
        """Return the log entries of ``request`` the certificate lists,
        oldest first.

        Hook. Default: every entry of the request but those of the template
        editor (``EDITOR_LOG_ACTIONS``), so the actions added by other
        modules, like a refusal, are listed too.
        """
        return (
            self.env["sign.oca.request.log"]
            .sudo()
            .search(
                [
                    ("request_id", "=", request.id),
                    ("action", "not in", EDITOR_LOG_ACTIONS),
                ],
                order="date, id",
            )
        )

    @api.model
    def _get_log(self, request, log, tz_name):
        """Return the values of a log entry in the certificate.

        :return: dict with ``action`` (label), ``date`` (see
            ``_format_datetime``), ``partner`` (name), ``channel``, ``ip`` and
            ``user_agent`` (False when unknown)
        """
        actions = dict(log._fields["action"]._description_selection(self.env))
        partner = log.signer_id.partner_id if log.signer_id else log.partner_id
        return {
            "action": actions.get(log.action, log.action),
            "date": self._format_datetime(log.date, tz_name),
            "partner": partner.name,
            "channel": self._get_log_channel(log),
            "ip": log.ip or False,
            "user_agent": log.user_agent or False,
        }
