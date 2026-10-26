# Copyright 2023 Dixmit
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import json
import logging
from base64 import b64decode, b64encode
from hashlib import sha256
from io import BytesIO
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from reportlab.graphics.shapes import Drawing, Line, Rect
from reportlab.lib.colors import black, transparent
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import Image, Paragraph

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.http import request
from odoo.tools import float_repr
from odoo.tools.pdf import PdfFileReader, PdfFileWriter

_logger = logging.getLogger(__name__)

# Bounds of the values a signer sends: a text field holds at most
# MAX_TEXT_LENGTH characters; a signature is an image of at most
# MAX_SIGNATURE_BYTES once decoded and MAX_SIGNATURE_PIXELS pixels.
MAX_TEXT_LENGTH = 2000
MAX_SIGNATURE_BYTES = 2 * 1024 * 1024
MAX_SIGNATURE_PIXELS = 4096 * 4096


class SignOcaRequest(models.Model):
    _name = "sign.oca.request"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _description = "Sign Request"
    _order = "state, id desc"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    template_id = fields.Many2one("sign.oca.template")
    data = fields.Binary(required=True)
    filename = fields.Char()
    user_id = fields.Many2one(
        comodel_name="res.users",
        string="Responsible",
        default=lambda self: self.env.user,
        required=True,
    )
    record_ref = fields.Reference(
        lambda self: [
            (m.model, m.name)
            for m in self.env["ir.model"]
            .sudo()
            .search([("transient", "=", False), ("model", "not like", "sign.oca")])
        ],
        string="Object",
    )
    signed = fields.Boolean(copy=False)
    signer_ids = fields.One2many(
        "sign.oca.request.signer",
        inverse_name="request_id",
        auto_join=True,
        copy=True,
        string="Signers",
    )
    signer_id = fields.Many2one(
        comodel_name="sign.oca.request.signer",
        compute="_compute_signer_id",
        help="The signer related to the active user.",
        string="Signer",
    )
    state = fields.Selection(
        [
            ("0_sent", "Sent"),
            ("1_draft", "Draft"),
            ("2_signed", "Signed"),
            ("3_cancel", "Cancelled"),
        ],
        default="1_draft",
        required=True,
        copy=False,
        tracking=True,
    )
    signed_count = fields.Integer(compute="_compute_signed_count")
    signer_count = fields.Integer(compute="_compute_signer_count")
    to_sign = fields.Boolean(compute="_compute_to_sign")
    signatory_data = fields.Serialized(
        default=lambda r: {},
        copy=False,
    )
    current_hash = fields.Char(copy=False)
    company_id = fields.Many2one(
        "res.company",
        default=lambda r: r.env.company.id,
        required=True,
    )
    next_item_id = fields.Integer(compute="_compute_next_item_id")
    ask_location = fields.Boolean()

    @api.depends("signer_ids")
    @api.depends_context("uid")
    def _compute_signer_id(self):
        user = self.env.user
        for record in self:
            user_diff_roles = record.signer_ids.filtered(
                lambda x: x.partner_id == user.partner_id.commercial_partner_id
            )
            record.signer_id = (
                fields.first(user_diff_roles.filtered(lambda x: x.is_allow_signature))
                if user_diff_roles.filtered(lambda x: x.is_allow_signature)
                else fields.first(user_diff_roles)
            )

    @api.depends(
        "signer_ids",
        "signer_ids.is_allow_signature",
    )
    @api.depends_context("uid")
    def _compute_to_sign(self):
        for record in self:
            record.to_sign = (
                record.signer_id.is_allow_signature if record.signer_id else False
            )

    def sign(self):
        self.ensure_one()
        if not self.signer_id:
            return self.get_formview_action()
        return self.signer_id.sign()

    @api.depends("signatory_data")
    def _compute_next_item_id(self):
        for record in self:
            record.next_item_id = (
                record.signatory_data
                and max([int(key) for key in record.signatory_data.keys()])
                or 0
            ) + 1

    def preview(self):
        self.ensure_one()
        self._set_action_log("view")
        return {
            "type": "ir.actions.client",
            "tag": "sign_oca_preview",
            "name": self.name,
            "params": {
                "res_model": self._name,
                "res_id": self.id,
            },
        }

    def get_info(self):
        self.ensure_one()
        return {
            "name": self.name,
            "items": self.signatory_data,
            "roles": [
                {"id": signer.role_id.id, "name": signer.role_id.name}
                for signer in self.signer_ids
            ],
            "fields": [
                {"id": field.id, "name": field.name}
                for field in self.env["sign.oca.field"].search([])
            ],
        }

    def _ensure_draft(self):
        self.ensure_one()
        if not self.signer_ids:
            raise ValidationError(
                self.env._(
                    "There are no signers, please fill them before configuring it"
                )
            )
        if not self.state == "1_draft":
            raise ValidationError(
                self.env._("You can only configure requests in draft state")
            )

    def configure(self):
        self._ensure_draft()
        self._set_action_log("configure")
        return {
            "type": "ir.actions.client",
            "tag": "sign_oca_configure",
            "name": self.name,
            "params": {
                "res_model": self._name,
                "res_id": self.id,
            },
        }

    def delete_item(self, item_id):
        self._ensure_draft()
        data = self.signatory_data
        data.pop(str(item_id))
        self.signatory_data = data
        self._set_action_log("delete_field")

    def set_item_data(self, item_id, vals):
        self._ensure_draft()
        data = self.signatory_data
        data[str(item_id)].update(vals)
        self.signatory_data = data
        self._set_action_log("edit_field")

    def add_item(self, item_vals):
        self._ensure_draft()
        item_id = self.next_item_id
        field_id = self.env["sign.oca.field"].browse(item_vals["field_id"])
        signatory_data = self.signatory_data
        signatory_data[item_id] = {
            "id": item_id,
            "field_id": field_id.id,
            "field_type": field_id.field_type,
            "required": False,
            "name": field_id.name,
            "role_id": self.signer_ids[0].role_id.id,
            "page": 1,
            "position_x": 0,
            "position_y": 0,
            "width": 0,
            "height": 0,
            "value": False,
            "default_value": field_id.default_value,
            "placeholder": "",
        }
        signatory_data[item_id].update(item_vals)
        self.signatory_data = signatory_data
        self._set_action_log("add_field")
        return signatory_data[item_id]

    def cancel(self):
        self.write({"state": "3_cancel"})
        self._set_action_log("cancel")

    @api.depends("signer_ids")
    def _compute_signer_count(self):
        for record in self:
            record.signer_count = len(record.signer_ids)

    @api.depends("signer_ids", "signer_ids.signed_on")
    def _compute_signed_count(self):
        for record in self:
            record.signed_count = len(record.signer_ids.filtered(lambda r: r.signed_on))

    def open_template(self):
        return self.template_id.configure()

    def action_send(self, sign_now=False, message=""):
        self.ensure_one()
        if self.state != "1_draft":
            return
        self._set_action_log("validate")
        self.state = "0_sent"
        for signer in self.signer_ids:
            signer._portal_ensure_token()
            if sign_now and signer.partner_id == self.env.user.partner_id:
                continue
            render_result = self.env["ir.qweb"]._render(
                "sign_oca.sign_oca_template_mail",
                {"record": signer, "body": message, "link": signer.access_url},
                engine="ir.qweb",
                minimal_qcontext=True,
            )
            self.env["mail.thread"].message_notify(
                body=render_result,
                partner_ids=signer.partner_id.ids,
                subject=self.env._("New document to sign"),
                subtype_id=self.env.ref("mail.mt_comment").id,
                mail_auto_delete=False,
                email_layout_xmlid="mail.mail_notification_light",
            )

    def action_send_signed_request(self):
        self.ensure_one()
        if (
            self.state != "2_signed"
            or not self.env.company.sign_oca_send_sign_request_copy
        ):
            return
        for signer in self.signer_ids:
            attachments = (
                self.env["ir.attachment"]
                .sudo()
                .search(
                    [
                        ("res_model", "=", "sign.oca.request"),
                        ("res_id", "=", self.id),
                        ("res_field", "=", "data"),
                    ]
                )
            )
            # The message will not be linked to the record because we do not want
            # it happen.
            self.env["mail.thread"].message_notify(
                body=self.env._(
                    "%(name)s (%(email)s) has sent the signed document.",
                    name=self.create_uid.name,
                    email=self.create_uid.email,
                ),
                partner_ids=signer.partner_id.ids,
                subject=self.env._("Signed document"),
                subtype_id=self.env.ref("mail.mt_comment").id,
                mail_auto_delete=False,
                attachment_ids=attachments.ids,
            )

    def _lock_for_sign(self):
        """Lock the rows of these requests until the transaction ends.

        ``action_sign`` reads ``data`` and ``signatory_data`` and writes them
        back, so two signatures running at once would each drop the items of
        the other. The second transaction to lock fails with
        ``LockNotAvailable`` (or ``SerializationFailure`` when the first one
        has already committed); Odoo retries the HTTP request, which then
        reads the committed document. Cached values of the requests and of
        their signers are dropped, so the checks that follow read the
        database.
        """
        if not self.ids:
            return
        self.flush_recordset()
        self.env.cr.execute(
            "SELECT id FROM sign_oca_request WHERE id IN %s FOR UPDATE NOWAIT",
            [tuple(self.ids)],
        )
        self.invalidate_recordset()
        self.signer_ids.invalidate_recordset()

    def _check_signed(self):
        """Set the request to ``2_signed`` when every signer has signed.

        :return: True when this call changed the state, else False
        """
        self.ensure_one()
        if self.state != "0_sent":
            return False
        if all(self.mapped("signer_ids.signed_on")):
            self.state = "2_signed"
            return True
        return False

    def _on_completed(self):
        """Hook run once, by the ``action_sign`` of the last signer.

        The state is ``2_signed`` and every signer's hashes are written; the
        signed copy is not mailed yet (``action_send_signed_request`` runs
        next), so an override that rewrites ``data`` changes what is sent.
        Default does nothing.
        """

    def _stamp_pdf(self, pdf_bytes, items, signer=None):
        """Draw ``items`` on the document and return the new PDF.

        :param bytes pdf_bytes: the document as it stands
        :param dict items: ``{key: item}`` as in ``signatory_data``, with
            ``value`` filled and ``page`` counted from 1
        :param signer: the ``sign.oca.request.signer`` whose
            ``_get_pdf_page`` draws each item; without one the default
            drawing of the model is used
        :return: bytes of the stamped PDF

        Each item becomes a one-page overlay merged onto its page, and the
        whole file is written again; earlier PDF signatures embedded in
        ``pdf_bytes`` do not survive the rewrite. An override may append an
        incremental update instead.
        """
        self.ensure_one()
        drawer = signer or self.env["sign.oca.request.signer"]
        reader = PdfFileReader(BytesIO(pdf_bytes))
        pages = {}
        for page_number in range(1, reader.numPages + 1):
            pages[page_number] = reader.getPage(page_number - 1)
        for item in items.values():
            page = pages[item["page"]]
            new_page = drawer._get_pdf_page(item, page.mediaBox)
            if new_page:
                page.mergePage(new_page)
        output = PdfFileWriter()
        for page in pages.values():
            output.addPage(page)
        output_stream = BytesIO()
        output.write(output_stream)
        return output_stream.getvalue()

    def _set_action_log_vals(self, action, **kwargs):
        vals = kwargs.copy()
        user = self._get_action_log_user()
        vals.update(
            {
                "action": action,
                "request_id": self.id,
                "uid": user.id,
                "partner_id": user.partner_id.id,
                "ip": self._get_action_log_ip(),
                "user_agent": self._get_action_log_user_agent(),
            }
        )
        return vals

    def _get_action_log_user(self):
        """Return the user who acts: the session user of the HTTP request (the
        public user for an anonymous visitor), else the environment user.

        The portal routes work on a superuser copy of the records, so the
        environment user is not the visitor there.
        """
        if request and getattr(request, "env", None) is not None:
            return request.env.user
        return self.env.user

    def _get_action_log_ip(self):
        """Return the address of the HTTP client, or ``0.0.0.0`` outside HTTP.

        It is the peer address of the connection; behind a reverse proxy,
        Odoo's ``proxy_mode`` replaces it with the client address the proxy
        forwards. A forwarding header sent by the client itself is ignored.
        """
        if not request or not hasattr(request, "httprequest"):
            # This comes from a server call. Set as localhost
            return "0.0.0.0"
        return request.httprequest.remote_addr

    def _get_action_log_user_agent(self):
        """Return the User-Agent header of the HTTP request, cut to 512 chars.

        A call made outside an HTTP request has no browser and gets False.
        """
        if not request or not hasattr(request, "httprequest"):
            return False
        return (request.httprequest.user_agent.string or "")[:512] or False

    def _set_action_log(self, action, **kwargs):
        self.ensure_one()
        return (
            self.env["sign.oca.request.log"]
            .sudo()
            .create(self._set_action_log_vals(action, **kwargs))
        )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                self._check_server_fields({k: v for k, v in vals.items() if v})
        records = super().create(vals_list)
        for record in records:
            record._set_action_log("create")
        return records

    def write(self, vals):
        if not self.env.su:
            self._check_server_fields(vals)
            if any(record.state != "1_draft" for record in self):
                self._check_sent_fields(vals)
            if "state" in vals:
                for record in self:
                    if record.state != vals["state"] and not record._can_set_state(
                        vals["state"]
                    ):
                        raise UserError(
                            self.env._(
                                "The state of %(name)s cannot be set to %(state)s.",
                                name=record.name,
                                state=vals["state"],
                            )
                        )
        return super().write(vals)

    def _get_server_fields(self):
        """Return the names of the fields only the server writes.

        A caller without superuser rights may neither create nor write them:
        they record what the signature process did.
        """
        return {"current_hash"}

    def _get_sent_fields(self):
        """Return the names of the fields fixed once the request leaves draft.

        After sending, only the signature process changes them, as
        superuser.
        """
        return {"data", "signatory_data", "template_id"}

    def _check_server_fields(self, vals):
        """Raise when ``vals`` writes a field of ``_get_server_fields``."""
        names = set(vals) & self._get_server_fields()
        if names:
            raise UserError(
                self.env._(
                    "Only the signature process may write %s.",
                    ", ".join(sorted(names)),
                )
            )

    def _check_sent_fields(self, vals):
        """Raise when ``vals`` writes a field of ``_get_sent_fields``."""
        names = set(vals) & self._get_sent_fields()
        if names:
            raise UserError(
                self.env._(
                    "%s cannot change once the request is sent.",
                    ", ".join(sorted(names)),
                )
            )

    def _can_set_state(self, state):
        """Tell whether a caller without superuser rights may move this
        request to ``state``: send a draft (``0_sent``) or cancel it
        (``3_cancel``). The other states come from the signature process.
        """
        self.ensure_one()
        return state == "3_cancel" or (state == "0_sent" and self.state == "1_draft")


class SignOcaRequestSigner(models.Model):
    _name = "sign.oca.request.signer"
    _inherit = ["portal.mixin", "mail.thread", "mail.activity.mixin"]
    _description = "Sign Request Value"
    _order = "signed_on desc, create_date desc, id desc"

    data = fields.Binary(related="request_id.data")
    request_id = fields.Many2one("sign.oca.request", required=True, ondelete="cascade")
    partner_name = fields.Char(related="partner_id.name")
    partner_id = fields.Many2one("res.partner", required=True, ondelete="restrict")
    role_id = fields.Many2one("sign.oca.role", required=True, ondelete="restrict")
    signed_on = fields.Datetime()
    signature_hash = fields.Char()
    model = fields.Char(compute="_compute_model", store=True)
    res_id = fields.Integer(compute="_compute_res_id", store=True)
    is_allow_signature = fields.Boolean(compute="_compute_is_allow_signature")
    secure_sequence_number = fields.Integer(
        string="Inalteralbility No Gap Sequence #",
        readonly=True,
        copy=False,
        index=True,
    )
    inalterable_hash = fields.Char(
        string="Inalterability Hash", readonly=True, copy=False
    )
    sequence_id = fields.Many2one(
        "ir.sequence", copy=False, default=lambda r: r._get_sequence()
    )
    altered_hash = fields.Boolean(compute="_compute_altered_hash")
    latitude = fields.Float()
    longitude = fields.Float()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            for vals in vals_list:
                self._check_server_fields({k: v for k, v in vals.items() if v})
                sign_request = self.env["sign.oca.request"].browse(
                    vals.get("request_id")
                )
                if sign_request and sign_request.state != "1_draft":
                    raise UserError(
                        self.env._("Signers cannot be added to a sent request.")
                    )
        return super().create(vals_list)

    def write(self, vals):
        if not self.env.su:
            self._check_server_fields(vals)
            if any(signer.request_id.state != "1_draft" for signer in self):
                self._check_sent_fields(vals)
        return super().write(vals)

    def unlink(self):
        if not self.env.su and any(
            signer.request_id.state != "1_draft" for signer in self
        ):
            raise UserError(
                self.env._("Signers cannot be removed from a sent request.")
            )
        return super().unlink()

    def _get_server_fields(self):
        """Return the names of the fields only the server writes.

        A caller without superuser rights may neither create nor write them:
        they record the signature and its inalterability chain.
        """
        return {
            "signed_on",
            "signature_hash",
            "inalterable_hash",
            "secure_sequence_number",
            "sequence_id",
            "latitude",
            "longitude",
        }

    def _get_sent_fields(self):
        """Return the names of the fields fixed once the request leaves draft."""
        return {"partner_id", "role_id", "request_id"}

    def _check_server_fields(self, vals):
        """Raise when ``vals`` writes a field of ``_get_server_fields``."""
        names = set(vals) & self._get_server_fields()
        if names:
            raise UserError(
                self.env._(
                    "Only the signature process may write %s.",
                    ", ".join(sorted(names)),
                )
            )

    def _check_sent_fields(self, vals):
        """Raise when ``vals`` writes a field of ``_get_sent_fields``."""
        names = set(vals) & self._get_sent_fields()
        if names:
            raise UserError(
                self.env._(
                    "%s cannot change once the request is sent.",
                    ", ".join(sorted(names)),
                )
            )

    @api.depends("request_id.record_ref")
    def _compute_model(self):
        for item in self.filtered(lambda x: x.request_id.record_ref):
            item.model = item.request_id.record_ref._name

    @api.depends("request_id.record_ref")
    def _compute_res_id(self):
        for item in self.filtered(lambda x: x.request_id.record_ref):
            item.res_id = item.request_id.record_ref.id

    @api.depends("signed_on", "partner_id")
    @api.depends_context("uid")
    def _compute_is_allow_signature(self):
        user = self.env.user
        for item in self:
            item.is_allow_signature = bool(
                not item.signed_on and item.partner_id == user.partner_id
            )

    @api.depends("access_token")
    def _compute_access_url(self):
        result = super()._compute_access_url()
        for record in self:
            record.access_url = f"/sign_oca/document/{record.id}/{record.access_token}"
        return result

    @api.onchange("role_id")
    def _onchange_role_id(self):
        for item in self:
            item.partner_id = item.role_id._get_partner_from_record(
                item.request_id.record_ref
            )

    def get_info(self, access_token=False):
        self.ensure_one()
        self._set_action_log("view", access_token=access_token)
        return {
            "role_id": self.role_id.id if self._can_sign() else False,
            "name": self.request_id.template_id.name,
            "items": self.request_id.signatory_data,
            "to_sign": self.request_id.to_sign,
            "ask_location": self.request_id.ask_location,
            "partner": {
                "id": self.partner_id.id,
                "name": self.partner_id.name,
                "email": self.partner_id.email,
                "phone": self.partner_id.phone,
            },
        }

    def sign(self):
        self.ensure_one()
        if not self.is_allow_signature:
            raise ValidationError(
                self.env._("You are not allowed to sign this document.")
            )
        return {
            "target": "new",
            "type": "ir.actions.act_url",
            "url": self.access_url,
        }

    def _check_can_sign(self):
        """Raise when this signer may not sign now.

        By default the signer must not have signed yet, the request must be
        sent (``0_sent``), and a caller without superuser rights must be one
        ``is_allow_signature`` lets sign. The portal routes call it as
        superuser, once their token check passed. Overrides call ``super()``
        and add their own conditions.

        :raise ValidationError: with the reason, shown to the signer
        """
        self.ensure_one()
        if self.signed_on:
            raise ValidationError(
                self.env._("Users %s has already signed the document")
                % self.partner_id.name
            )
        if self.request_id.state != "0_sent":
            raise ValidationError(self.env._("Request cannot be signed"))
        if not self.env.su and not self.is_allow_signature:
            raise ValidationError(
                self.env._("You are not allowed to sign this document.")
            )

    def _can_sign(self):
        """Return whether ``_check_can_sign`` lets this signer sign now.

        ``get_info`` gives the signer's role to the signing page only when
        it does, and the page shows its fields and sign button to that role.
        """
        self.ensure_one()
        try:
            self._check_can_sign()
        except UserError:
            return False
        return True

    def action_sign(self, items, access_token=False, latitude=False, longitude=False):
        self.ensure_one()
        self.request_id._lock_for_sign()
        self._check_can_sign()
        # The checks run as the caller; the signature then writes fields that
        # only the server may write (see ``_get_server_fields``).
        return self.sudo()._action_sign(
            items, access_token=access_token, latitude=latitude, longitude=longitude
        )

    def _action_sign(self, items, access_token=False, latitude=False, longitude=False):
        self.signed_on = fields.Datetime.now()
        # current_hash = self.request_id.current_hash
        signatory_data = self.request_id.signatory_data
        role_items = self._prepare_sign_items(items)
        signatory_data.update(role_items)

        for item in role_items.values():
            self._check_signable(item)
        signed_pdf = self.request_id._stamp_pdf(
            b64decode(self.request_id.data), role_items, signer=self
        )
        final_hash = sha256(signed_pdf).hexdigest()
        # TODO: Review that the hash has not been changed...
        self.request_id.write(
            {
                "signatory_data": signatory_data,
                "data": b64encode(signed_pdf),
                "current_hash": final_hash,
            }
        )
        self.signature_hash = final_hash
        self.latitude = latitude
        self.longitude = longitude
        completed = self.request_id._check_signed()
        self._set_action_log("sign", access_token=access_token)
        if self.sequence_id:
            self.flush_recordset()
            new_number = self.sequence_id.next_by_id()
            self.write(
                {
                    "secure_sequence_number": new_number,
                    "inalterable_hash": self._get_new_hash(new_number),
                }
            )
        self._after_sign()
        if completed:
            self.request_id._on_completed()
        self.request_id.action_send_signed_request()
        return {
            "type": "ir.actions.act_url",
            "url": self.access_url,
        }

    def _after_sign(self):
        """Hook run by ``action_sign`` once this signer has signed.

        The document, ``signatory_data``, ``signature_hash``, the ``sign`` log
        entry and the inalterability hash are written; the request state is
        already ``2_signed`` when this signer was the last one, and
        ``request._on_completed`` runs right after. Default does nothing.
        """

    def _prepare_sign_items(self, items):
        """Return the items this signer fills, ready to store and draw.

        Each item of the signer's role keeps the definition stored on the
        request (position, page, size, field type, required flag, role,
        name); only its ``value`` comes from ``items``. Items of other roles
        in ``items`` are ignored, and an item missing from ``items`` gets no
        value.

        :param dict items: the items sent by the browser, keyed by item id
        :return: dict ``{key: item}`` keyed like ``request.signatory_data``
        :raise ValidationError: when a value is not a string, number or bool

        Override to set a value computed by the server, after ``super()``.
        ``action_sign`` calls it once ``signed_on`` is set.
        """
        self.ensure_one()
        role_items = {}
        for key, definition in self.request_id.signatory_data.items():
            if definition["role_id"] != self.role_id.id:
                continue
            sent = items.get(str(key)) or items.get(key) or {}
            value = sent.get("value", False) if isinstance(sent, dict) else False
            if value is not None and not isinstance(value, str | int | float | bool):
                raise ValidationError(
                    self.env._("Field %s has an invalid value") % definition["name"]
                )
            role_items[key] = dict(definition, value=value)
        return role_items

    def _check_signable(self, item):
        self._check_item_value(item)
        if not item["required"]:
            return
        if not item["value"]:
            raise ValidationError(self.env._("Field %s is not filled") % item["name"])

    def _check_item_value(self, item):
        """Raise when the value of ``item`` cannot be stored and drawn.

        A text is at most ``MAX_TEXT_LENGTH`` characters long. A signature
        is a base64 image, bare or as a data URI, that PIL reads, within
        ``MAX_SIGNATURE_BYTES`` and ``MAX_SIGNATURE_PIXELS``; only its header
        is read to get its size, so an oversized image is refused before it
        is decoded.

        :raise ValidationError: naming the field
        """
        value = item.get("value")
        if not value or isinstance(value, bool):
            return
        if item.get("field_type") == "signature":
            if not isinstance(value, str) or not self._is_signature_image(value):
                raise ValidationError(
                    self.env._("Field %s does not hold a signature image.")
                    % item["name"]
                )
        elif isinstance(value, str) and len(value) > MAX_TEXT_LENGTH:
            raise ValidationError(
                self.env._(
                    "Field %(name)s is longer than %(max)s characters.",
                    name=item["name"],
                    max=MAX_TEXT_LENGTH,
                )
            )

    def _is_signature_image(self, value):
        """Tell whether ``value``, a base64 image, is one ``_check_item_value``
        accepts."""
        base64_str = value.split(",", 1)[1] if "," in value else value
        if len(base64_str) > MAX_SIGNATURE_BYTES * 4 // 3 + 4:
            return False
        try:
            image_data = b64decode(base64_str + "=" * (-len(base64_str) % 4))
            with PILImage.open(BytesIO(image_data)) as image:
                if image.width * image.height > MAX_SIGNATURE_PIXELS:
                    return False
                image.verify()
        except Exception:  # noqa: BLE001 - any unreadable data is refused
            return False
        return True

    def _get_pdf_page_text(self, item, box):
        packet = BytesIO()
        can = canvas.Canvas(packet, pagesize=(box.getWidth(), box.getHeight()))
        if not item["value"]:
            return False
        par = Paragraph(
            self._get_pdf_text_markup(item["value"]), style=self._getParagraphStyle()
        )
        width = item["width"] / 100 * float(box.getWidth())
        height = item["height"] / 100 * float(box.getHeight())
        x = item["position_x"] / 100 * float(box.getWidth())
        y = (100 - item["position_y"] - item["height"]) / 100 * float(box.getHeight())
        __, text_height = par.wrap(width, height)
        # The text sits at the bottom of its box; a text taller than the box
        # starts at its top instead, and what passes its bottom is cut, so
        # nothing is drawn outside the box.
        can.saveState()
        clip = can.beginPath()
        clip.rect(x, y, width, height)
        can.clipPath(clip, stroke=0, fill=0)
        par.drawOn(can, x, y + min(0, height - text_height))
        can.restoreState()
        can.save()
        packet.seek(0)
        new_pdf = PdfFileReader(packet)
        return new_pdf.getPage(0)

    def _get_pdf_text_markup(self, value):
        """Return ``value`` as reportlab Paragraph markup that shows it verbatim.

        Paragraph reads its text as markup: tags such as ``<img src=...>``
        load files. The value is XML-escaped, and its line breaks become
        ``<br/>`` so a multi-line text keeps its lines.
        """
        text = str(value).replace("\r\n", "\n").replace("\r", "\n")
        return escape(text).replace("\n", "<br/>")

    def _getParagraphStyle(self):
        return ParagraphStyle(name="Oca Sign Style")

    def _get_pdf_page_check(self, item, box):
        packet = BytesIO()
        can = canvas.Canvas(packet, pagesize=(box.getWidth(), box.getHeight()))
        width = item["width"] / 100 * float(box.getWidth())
        height = item["height"] / 100 * float(box.getHeight())
        drawing = Drawing(width=width, height=height)
        drawing.add(
            Rect(
                0,
                0,
                width,
                height,
                strokeWidth=3,
                strokeColor=black,
                fillColor=transparent,
            )
        )
        if item["value"]:
            drawing.add(Line(0, 0, width, height, strokeColor=black, strokeWidth=3))
            drawing.add(Line(0, height, width, 0, strokeColor=black, strokeWidth=3))
        drawing.drawOn(
            can,
            item["position_x"] / 100 * float(box.getWidth()),
            (100 - item["position_y"] - item["height"]) / 100 * float(box.getHeight()),
        )
        can.save()
        packet.seek(0)
        new_pdf = PdfFileReader(packet)
        return new_pdf.getPage(0)

    def _get_pdf_page_signature(self, item, box):
        packet = BytesIO()
        can = canvas.Canvas(packet, pagesize=(box.getWidth(), box.getHeight()))
        if not item["value"]:
            return False
        try:
            base64_str = item["value"]
            if len(base64_str) % 4:
                base64_str += "=" * (4 - len(base64_str) % 4)
            if "," in base64_str:
                base64_str = item["value"].split(",")[1]
            image_data = b64decode(base64_str)
            par = Image(
                BytesIO(image_data),
                width=item["width"] / 100 * float(box.getWidth()),
                height=item["height"] / 100 * float(box.getHeight()),
            )
            par.drawOn(
                can,
                item["position_x"] / 100 * float(box.getWidth()),
                (100 - item["position_y"] - item["height"])
                / 100
                * float(box.getHeight()),
            )
        except Exception as e:
            _logger.info(f"Error decoding Base64 string: {e}")
            return False
        can.save()
        packet.seek(0)
        new_pdf = PdfFileReader(packet)
        return new_pdf.getPage(0)

    def _get_pdf_page(self, item, box):
        return getattr(self, f"_get_pdf_page_{ item['field_type'] }")(item, box)

    def _set_action_log(self, action, **kwargs):
        self.ensure_one()
        return self.request_id._set_action_log(action, signer_id=self.id, **kwargs)

    def _compute_display_name(self):
        for signer in self:
            signer.display_name = signer.partner_id.display_name

    def _get_sequence(self):
        return self.env.ref(
            "sign_oca.sign_inalterability_sequence", raise_if_not_found=False
        )

    @api.depends(
        lambda r: ["request_id.data", "inalterable_hash", "secure_sequence_number"]
        + r._get_integrity_hash_fields()
    )
    def _compute_altered_hash(self):
        for record in self:
            record.altered_hash = (
                record.inalterable_hash
                and record.inalterable_hash
                != record._get_new_hash(record.secure_sequence_number)
            )

    def _get_new_hash(self, secure_seq_number):
        prev_sign = self.sudo().search(
            [
                ("sequence_id", "=", self.sequence_id.id),
                ("secure_sequence_number", "!=", 0),
                ("secure_sequence_number", "=", int(secure_seq_number) - 1),
            ]
        )
        if prev_sign and len(prev_sign) != 1:
            raise UserError(
                self.env._(
                    "An error occurred when computing the inalterability. "
                    "Impossible to get the unique previous signer information."
                )
            )
        return self._compute_hash(prev_sign.inalterable_hash if prev_sign else "")

    def _compute_hash(self, previous_hash):
        """Computes the hash of the browse_record given as self, based on the hash
        of the previous record in the company's securisation sequence given as
        parameter
        """
        self.ensure_one()
        hash_string = sha256((previous_hash + self._string_to_hash()).encode("utf-8"))
        return hash_string.hexdigest()

    def _string_to_hash(self):
        def _getattrstring(obj, field_str):
            field_value = obj[field_str]
            if obj._fields[field_str].type == "many2one":
                field_value = field_value.id
            if obj._fields[field_str].type == "monetary":
                return float_repr(field_value, obj.currency_id.decimal_places)
            return str(field_value)

        values = {"items": {}}
        for field in self._get_integrity_hash_fields():
            values[field] = _getattrstring(self, field)
        for key, signatory_value in self.request_id.signatory_data.items():
            if signatory_value["role_id"] == self.role_id.id:
                values[key] = signatory_value
        return json.dumps(
            values,
            sort_keys=True,
            ensure_ascii=True,
            indent=None,
            separators=(",", ":"),
        )

    def _get_integrity_hash_fields(self):
        return ["partner_id", "role_id", "signed_on", "signature_hash"]


class SignRequestLog(models.Model):
    _name = "sign.oca.request.log"
    _description = "Sign Request Log"
    _log_access = False
    _description = "Log access and edition on requests"

    uid = fields.Many2one(
        "res.users",
        required=True,
        ondelete="cascade",
        default=lambda r: r.env.user.id,
    )
    date = fields.Datetime(required=True, default=lambda r: fields.Datetime.now())
    partner_id = fields.Many2one(
        "res.partner", required=True, default=lambda r: r.env.user.partner_id.id
    )
    request_id = fields.Many2one("sign.oca.request", required=True, ondelete="cascade")
    signer_id = fields.Many2one("sign.oca.request.signer")
    action = fields.Selection(
        [
            ("create", "Create"),
            ("validate", "Validate"),
            ("view", "View Document"),
            ("sign", "Sign"),
            ("add_field", "Add field"),
            ("edit_field", "Edit field"),
            ("delete_field", "Delete field"),
            ("cancel", "Cancel"),
            ("configure", "Configure"),
        ],
        required=True,
    )
    access_token = fields.Char()
    ip = fields.Char()
    user_agent = fields.Char()
