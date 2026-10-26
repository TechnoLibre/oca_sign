# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
import math
import os
import re
from base64 import b64decode, b64encode
from collections import defaultdict
from hashlib import sha256
from io import BytesIO

from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.generic import pdf_name, pdf_string
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader as HankoPdfFileReader
from pyhanko.sign import fields as sig_fields
from pyhanko.sign import signers
from pyhanko.sign.diff_analysis import ModificationLevel
from pyhanko.sign.general import (
    MultivaluedAttributeError,
    NonexistentAttributeError,
    find_unique_cms_attribute,
)
from pyhanko.sign.timestamps import HTTPTimeStamper, TimestampRequestError
from pyhanko.sign.validation import SignatureCoverageLevel, validate_pdf_signature
from pyhanko_certvalidator import ValidationContext
from pyhanko_certvalidator.registry import SimpleCertificateStore
from reportlab.pdfgen import canvas

from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools import config
from odoo.tools.pdf import PdfFileReader, PdfFileWriter

from .pades_form import (
    ANNOT_HIDDEN,
    ANNOT_PRINT,
    CERTIFICATE_FIELD,
    FIELD_MULTILINE,
    FIELD_READ_ONLY,
    HELVETICA,
    ITEM_FIELD_PREFIX,
    ITEM_XOBJECT,
    TEXT_DEFAULT_APPEARANCE,
    BlankStampStyle,
    ItemStampStyle,
    item_field_name,
    item_field_names,
    number_array,
    place_command,
)

_logger = logging.getLogger(__name__)

FINAL_SEAL_FIELD = "sign_oca_final_seal"
# Smallest zoom of a certificate rendered again to fit its reserved pages,
# and the number of renderings tried.
CERTIFICATE_MIN_ZOOM = 0.6
CERTIFICATE_FIT_ATTEMPTS = 4


class SignOcaRequest(models.Model):
    _inherit = "sign.oca.request"

    pades_original_sha256 = fields.Char(
        string="Original SHA-256",
        readonly=True,
        copy=False,
        help="SHA-256 of the document before the first signature.",
    )
    pades_final_sha256 = fields.Char(
        string="Final SHA-256",
        readonly=True,
        copy=False,
        help="SHA-256 of the document once every signer has signed, "
        "certificate page and final seal included.",
    )
    pades_certificate_sha256 = fields.Char(
        string="Sealing Certificates",
        readonly=True,
        copy=False,
        help="SHA-256 of each certificate that sealed this document, comma "
        "separated; a seal made by any other certificate is reported.",
    )
    pades_status = fields.Selection(
        [
            ("none", "Not sealed"),
            ("sealed", "Sealed"),
            ("modified", "Modified after the last seal"),
            ("broken", "Seal broken"),
            ("untrusted", "Sealed by an unknown certificate"),
        ],
        string="Seal Status",
        compute="_compute_pades_verification",
        store=True,
        readonly=True,
        copy=False,
    )
    pades_seal_count = fields.Integer(
        string="Seals",
        compute="_compute_pades_verification",
        store=True,
        readonly=True,
        copy=False,
    )
    pades_seals_intact = fields.Boolean(
        string="All Seals Intact",
        compute="_compute_pades_verification",
        store=True,
        readonly=True,
        copy=False,
    )
    pades_last_check = fields.Datetime(
        string="Seals Last Checked",
        compute="_compute_pades_verification",
        store=True,
        readonly=True,
        copy=False,
    )
    pades_check_details = fields.Text(
        string="Seal Details",
        compute="_compute_pades_verification",
        store=True,
        readonly=True,
        copy=False,
    )

    def _get_server_fields(self):
        """The fingerprints of the document are written by the signature
        process only."""
        return super()._get_server_fields() | {
            "pades_original_sha256",
            "pades_final_sha256",
            "pades_certificate_sha256",
            "pades_status",
            "pades_seal_count",
            "pades_seals_intact",
            "pades_last_check",
            "pades_check_details",
        }

    def write(self, vals):
        """Prepare the document as a form when the request is sent (see
        ``_pades_prepare_on_send``)."""
        sent = (
            self.filtered(lambda record: record.state == "1_draft")
            if vals.get("state") == "0_sent"
            else self.browse()
        )
        result = super().write(vals)
        for record in sent:
            record._pades_prepare_on_send()
        return result

    def _pades_prepare_on_send(self):
        """Prepare the document of this sent request with
        ``_pades_prepare_form`` when a sealing certificate is configured, so
        every signer sees the fields and the reserved certificate pages. A
        document already prepared, or already sealed, is left as it is; a
        request sent without certificate is prepared by its first sealed
        signature.

        :raise UserError: when the configured certificate cannot be loaded
        """
        self.ensure_one()
        sign_request = self.sudo()
        data = sign_request.with_context(bin_size=False).data
        if not data or not self._pades_get_signer():
            return
        pdf_bytes = b64decode(data)
        if self._pades_form_prepared(pdf_bytes) or self._pades_has_signatures(
            pdf_bytes
        ):
            return
        if not sign_request.pades_original_sha256:
            sign_request.pades_original_sha256 = sha256(pdf_bytes).hexdigest()
        sign_request.data = b64encode(self._pades_prepare_form(pdf_bytes))

    # Configuration

    def _pades_get_config(self):
        """Return the sealing configuration of this request, or None.

        The odoo.conf keys ``sign_oca_pades_pkcs12_path``,
        ``sign_oca_pades_pkcs12_password`` and ``sign_oca_pades_tsa_url``
        take precedence; without a path, the PKCS#12 file, password and TSA
        URL of the request's company are used.

        :return: dict with ``pkcs12`` (bytes), ``password`` (bytes or None),
            ``tsa_url`` (str or False) and ``source`` (``"config"`` or
            ``"company"``), or None when no certificate is configured
        :raise UserError: when the configured file cannot be read
        """
        self.ensure_one()
        company = self.company_id.sudo()
        tsa_url = config.get("sign_oca_pades_tsa_url") or company.sign_oca_pades_tsa_url
        path = config.get("sign_oca_pades_pkcs12_path")
        if path:
            try:
                with open(os.path.expanduser(path), "rb") as pkcs12_file:
                    data = pkcs12_file.read()
            except OSError as error:
                raise UserError(
                    self.env._(
                        "The sealing certificate named in the server "
                        "configuration cannot be read: %s",
                        error,
                    )
                ) from error
            password = config.get("sign_oca_pades_pkcs12_password")
            source = "config"
        elif company.sign_oca_pades_pkcs12:
            data = b64decode(company.sign_oca_pades_pkcs12)
            password = company.sign_oca_pades_pkcs12_password
            source = "company"
        else:
            return None
        return {
            "pkcs12": data,
            "password": password.encode() if password else None,
            "tsa_url": tsa_url or False,
            "source": source,
        }

    def _pades_get_signer(self):
        """Return the pyHanko signer that seals this request, or None.

        :return: ``pyhanko.sign.signers.SimpleSigner`` built from the
            PKCS#12 of ``_pades_get_config``, or None without certificate
        :raise UserError: when the PKCS#12 cannot be decrypted or holds no
            key and certificate
        """
        conf = self._pades_get_config()
        if not conf:
            return None
        try:
            key, cert, other_certs = pkcs12.load_key_and_certificates(
                conf["pkcs12"], conf["password"]
            )
        except (ValueError, TypeError) as error:
            raise UserError(
                self.env._(
                    "The sealing certificate cannot be loaded; check the "
                    "PKCS#12 file and its password."
                )
            ) from error
        if not key or not cert:
            raise UserError(
                self.env._(
                    "The sealing certificate file holds no private key and certificate."
                )
            )
        signing_key = asn1_keys.PrivateKeyInfo.load(
            key.private_bytes(
                serialization.Encoding.DER,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        registry = SimpleCertificateStore.from_certs(
            [
                asn1_x509.Certificate.load(
                    other.public_bytes(serialization.Encoding.DER)
                )
                for other in other_certs or []
            ]
        )
        return signers.SimpleSigner(
            signing_cert=asn1_x509.Certificate.load(
                cert.public_bytes(serialization.Encoding.DER)
            ),
            signing_key=signing_key,
            cert_registry=registry,
        )

    def _pades_get_timestamper(self):
        """Return the RFC 3161 timestamper of the configured TSA, or None."""
        conf = self._pades_get_config()
        if not conf or not conf["tsa_url"]:
            return None
        return HTTPTimeStamper(conf["tsa_url"])

    # Incremental writing

    def _pades_has_signatures(self, pdf_bytes):
        """Return whether ``pdf_bytes`` embeds at least one PDF signature."""
        try:
            reader = HankoPdfFileReader(BytesIO(pdf_bytes), strict=False)
            return bool(reader.embedded_signatures)
        except Exception:  # noqa: BLE001 - an unreadable file has no seal
            return False

    def _pades_free_xobject_name(self, writer, page_ix, taken):
        """Return an XObject resource name unused on page ``page_ix``.

        ``taken`` holds the names already chosen for this page in the
        current update; the returned name is added to it.
        """
        __, resources = writer.find_page_for_modification(page_ix)
        resources = resources.get_object() if resources is not None else {}
        existing = resources.get("/XObject", generic.DictionaryObject())
        existing = existing.get_object()
        number = 1
        while f"/SignOca{number}" in existing or f"/SignOca{number}" in taken:
            number += 1
        name = f"/SignOca{number}"
        taken.add(name)
        return name

    def _pades_add_overlays(self, writer, pdf_bytes, items, signer=None):
        """Draw ``items`` on the pages of ``writer`` without rewriting them.

        Each item is drawn by ``signer._get_pdf_page`` as in ``_stamp_pdf``
        of sign_oca. Every resulting page becomes a form XObject; the page
        content is wrapped in ``q``/``Q`` and followed by a stream that
        paints those XObjects, so the existing content and earlier
        signatures stay byte for byte as they are.

        :param writer: ``IncrementalPdfFileWriter`` opened on ``pdf_bytes``
        :param bytes pdf_bytes: the document as it stands
        :param dict items: ``{key: item}`` with ``value`` filled
        :param signer: the ``sign.oca.request.signer`` that draws the items
        """
        drawer = signer or self.env["sign.oca.request.signer"]
        reader = PdfFileReader(BytesIO(pdf_bytes))
        overlay_writer = PdfFileWriter()
        overlays_by_page = defaultdict(list)
        overlay_count = 0
        for item in items.values():
            box = reader.getPage(item["page"] - 1).mediaBox
            overlay = drawer._get_pdf_page(item, box)
            if not overlay:
                continue
            overlay_writer.addPage(overlay)
            overlays_by_page[item["page"] - 1].append(overlay_count)
            overlay_count += 1
        if not overlay_count:
            return
        overlay_stream = BytesIO()
        overlay_writer.write(overlay_stream)
        overlay_reader = HankoPdfFileReader(overlay_stream, strict=False)
        for page_ix, overlay_indexes in overlays_by_page.items():
            taken = set()
            xobjects = generic.DictionaryObject()
            paint = [b"Q"]
            for overlay_ix in overlay_indexes:
                name = self._pades_free_xobject_name(writer, page_ix, taken)
                xobjects[generic.pdf_name(name)] = writer.import_page_as_xobject(
                    overlay_reader, page_ix=overlay_ix
                )
                paint.append(f"q {name} Do Q".encode())
            writer.add_stream_to_page(
                page_ix,
                writer.add_object(generic.StreamObject(stream_data=b"q\n")),
                prepend=True,
            )
            writer.add_stream_to_page(
                page_ix,
                writer.add_object(
                    generic.StreamObject(stream_data=b"\n".join(paint) + b"\n")
                ),
                resources=generic.DictionaryObject(
                    {generic.pdf_name("/XObject"): xobjects}
                ),
            )

    def _pades_append_pdf(self, writer, pdf_bytes):
        """Append every page of ``pdf_bytes`` at the end of ``writer``.

        Each page is imported as a form XObject painted on a new page of the
        same media box; links and annotations of the appended pages are
        not kept.
        """
        appended = HankoPdfFileReader(BytesIO(pdf_bytes), strict=False)
        for page_ix in range(int(appended.root["/Pages"]["/Count"])):
            page_ref, __ = appended.find_page_for_modification(page_ix)
            media_box = page_ref.get_object().get("/MediaBox")
            if media_box is None:
                media_box = appended.root["/Pages"]["/MediaBox"]
            xobject = writer.import_page_as_xobject(appended, page_ix=page_ix)
            content = writer.add_object(
                generic.StreamObject(stream_data=b"q /SignOcaPage Do Q\n")
            )
            writer.insert_page(
                generic.DictionaryObject(
                    {
                        generic.pdf_name("/Type"): generic.pdf_name("/Page"),
                        generic.pdf_name("/MediaBox"): writer.import_object(media_box),
                        generic.pdf_name("/Resources"): generic.DictionaryObject(
                            {
                                generic.pdf_name("/XObject"): (
                                    generic.DictionaryObject(
                                        {generic.pdf_name("/SignOcaPage"): xobject}
                                    )
                                )
                            }
                        ),
                        generic.pdf_name("/Contents"): content,
                    }
                )
            )

    def _pades_field_name(self, writer, base):
        """Return ``base``, suffixed when ``writer`` already has that field."""
        existing = {name for name, __, __ in sig_fields.enumerate_sig_fields(writer)}
        name = base
        number = 2
        while name in existing:
            name = f"{base}_{number}"
            number += 1
        return name

    def _pades_signer_field_base(self, signer):
        """Return the signature field name of ``signer``'s seal.

        PDF field names may not hold a period; the name only uses letters,
        digits and underscores.
        """
        role = re.sub(r"\W+", "_", signer.role_id.name or "").strip("_").lower()
        return f"sign_oca_signer_{signer.id}" + (f"_{role}" if role else "")

    def _pades_signer_field_name(self, signer):
        """Return the name of ``signer``'s signature field: the name recorded
        when ``_pades_prepare_form`` created it (``pades_field_name``), so
        that a role renamed afterwards changes nothing, else
        ``_pades_signer_field_base``."""
        return signer.sudo().pades_field_name or self._pades_signer_field_base(signer)

    def _pades_signature_metadata(self, field_name, reason, docmdp=None):
        """Return the ``PdfSignatureMetadata`` of a PAdES seal.

        The seal is a PAdES baseline signature (``ETSI.CAdES.detached``)
        hashed with SHA-256, in the signature field named ``field_name``,
        created invisible when the document has no field of that name.
        ``docmdp``, a pyHanko ``MDPPerm``, is the modification policy the
        seal requests, by default pyHanko's (form filling).
        """
        values = {}
        if docmdp is not None:
            values["docmdp_permissions"] = docmdp
        return signers.PdfSignatureMetadata(
            field_name=field_name,
            md_algorithm="sha256",
            subfilter=sig_fields.SigSeedSubFilter.PADES,
            reason=reason,
            **values,
        )

    def _pades_final_seal_field_spec(self, field_name):
        """Return the pyHanko ``SigFieldSpec`` of the final seal's field.

        Hook. Default: an invisible field whose lock covers every field of
        the form and allows no change after the seal (``/P 1``), so that a
        verifier reports any revision added to the completed document, an
        empty signature field signed afterwards included.
        """
        return sig_fields.SigFieldSpec(
            sig_field_name=field_name,
            field_mdp_spec=sig_fields.FieldMDPSpec(
                action=sig_fields.FieldMDPAction.ALL
            ),
            doc_mdp_update_value=sig_fields.MDPPerm.NO_CHANGES,
        )

    def _pades_seal(
        self, pdf_bytes, prepare, field_base, reason, seal_signer, final=False
    ):
        """Write one incremental update of ``pdf_bytes`` and seal it.

        :param bytes pdf_bytes: the document as it stands
        :param prepare: callable taking the ``IncrementalPdfFileWriter``,
            which adds the content of the update (field values, overlays,
            pages); it returns None to seal a new invisible field, or a
            dict with the ``field_name`` of an existing empty signature
            field to sign and the pyHanko ``stamp_style`` of its appearance
        :param str field_base: name of the new signature field, suffixed
            when the document already has a field of that name
        :param str reason: reason recorded in the signature
        :param seal_signer: pyHanko signer from ``_pades_get_signer``
        :param bool final: the seal closes the document: its new field
            follows ``_pades_final_seal_field_spec``
        :return: bytes of the sealed document

        The update and the signature form one revision, so the seal covers
        the whole file. With a TSA, the seal carries a signature timestamp;
        when the TSA does not answer, the update is written again and
        sealed without timestamp, and a message on the request says so.
        """
        timestamper = self._pades_get_timestamper()
        try:
            return self._pades_write_seal(
                pdf_bytes, prepare, field_base, reason, seal_signer, timestamper, final
            )
        except TimestampRequestError as error:
            if not timestamper:
                raise
            _logger.warning(
                "sign.oca.request %s: timestamp authority unavailable, "
                "seal %s applied without timestamp: %s",
                self.id,
                field_base,
                error,
            )
            self.message_post(
                body=self.env._(
                    "The timestamp authority did not answer: the seal %s "
                    "was applied without timestamp.",
                    field_base,
                )
            )
            return self._pades_write_seal(
                pdf_bytes, prepare, field_base, reason, seal_signer, None, final
            )

    def _pades_write_seal(
        self, pdf_bytes, prepare, field_base, reason, seal_signer, timestamper, final
    ):
        writer = IncrementalPdfFileWriter(BytesIO(pdf_bytes), strict=False)
        target = prepare(writer) or {}
        field_name = target.get("field_name") or self._pades_field_name(
            writer, field_base
        )
        field_spec = None
        docmdp = None
        if final and not target.get("field_name"):
            field_spec = self._pades_final_seal_field_spec(field_name)
            docmdp = field_spec.doc_mdp_update_value
        output = BytesIO()
        signers.PdfSigner(
            self._pades_signature_metadata(field_name, reason, docmdp=docmdp),
            seal_signer,
            timestamper=timestamper,
            stamp_style=target.get("stamp_style"),
            new_field_spec=field_spec,
        ).sign_pdf(writer, output=output)
        self._pades_record_certificate(seal_signer.signing_cert)
        return output.getvalue()

    def _pades_certificate_fingerprints(self):
        """Return the set of ``pades_certificate_sha256`` fingerprints."""
        self.ensure_one()
        return {
            fingerprint
            for fingerprint in (self.sudo().pades_certificate_sha256 or "").split(",")
            if fingerprint
        }

    def _pades_record_certificate(self, certificate):
        """Add the SHA-256 of ``certificate``, an asn1crypto certificate that
        sealed this document, to ``pades_certificate_sha256``."""
        self.ensure_one()
        fingerprints = self._pades_certificate_fingerprints()
        fingerprint = sha256(certificate.dump()).hexdigest()
        if fingerprint not in fingerprints:
            self.sudo().pades_certificate_sha256 = ",".join(
                sorted(fingerprints | {fingerprint})
            )

    def _stamp_pdf(self, pdf_bytes, items, signer=None):
        """Draw ``items`` as an incremental update, sealed when configured.

        With a sealing certificate, a document without seal is first
        prepared as a form (``_pades_prepare_form``); a signature on a
        prepared document fills the fields of ``items`` and signs the
        signer's signature field (``_pades_fill_fields``), so that the
        earlier seals see a form filling only. Without certificate, a
        prepared document is filled the same way, unsealed. A document
        sealed without form fields gets its items drawn on its pages, and
        sealed. Without certificate and without earlier seal, sign_oca's
        full rewrite is kept. A document that already carries a seal is
        never rewritten; an unsealed update after the last seal makes
        ``pades_status`` report it as modified.
        """
        self.ensure_one()
        if not self.pades_original_sha256:
            self.sudo().pades_original_sha256 = sha256(pdf_bytes).hexdigest()
        seal_signer = self._pades_get_signer()
        if (
            seal_signer
            and not self._pades_form_prepared(pdf_bytes)
            and not self._pades_has_signatures(pdf_bytes)
        ):
            pdf_bytes = self._pades_prepare_form(pdf_bytes)
        prepared = self._pades_form_prepared(pdf_bytes)
        if prepared:

            def prepare(writer):
                return self._pades_fill_fields(writer, pdf_bytes, items, signer=signer)

        else:

            def prepare(writer):
                self._pades_add_overlays(writer, pdf_bytes, items, signer=signer)

        if not seal_signer:
            _logger.warning(
                "sign.oca.request %s: no sealing certificate configured, "
                "the signature is not sealed",
                self.id,
            )
            if not prepared and not self._pades_has_signatures(pdf_bytes):
                return super()._stamp_pdf(pdf_bytes, items, signer=signer)
            writer = IncrementalPdfFileWriter(BytesIO(pdf_bytes), strict=False)
            prepare(writer)
            output = BytesIO()
            writer.write(output)
            return output.getvalue()
        field_base = (
            self._pades_signer_field_base(signer) if signer else "sign_oca_seal"
        )
        reason = self._pades_seal_reason(signer)
        return self._pades_seal(pdf_bytes, prepare, field_base, reason, seal_signer)

    def _pades_seal_reason(self, signer=None):
        """Return the reason recorded in the seal of a stamp of ``signer``.

        Hook. The context key ``sign_oca_stamp_reason`` gives it for a stamp
        that is not a signature, such as a decision a module draws for the
        signer; otherwise a stamp of ``signer`` is its signature, and a stamp
        without signer is the server's.
        """
        reason = self.env.context.get("sign_oca_stamp_reason")
        if reason:
            return reason
        if signer:
            return self.env._(
                "Signed by %(name)s as %(role)s",
                name=signer._pades_seal_signer_name(),
                role=signer.role_id.name,
            )
        return self.env._("Document stamped")

    # Completion

    def _pades_render_certificate(self):
        """Return the signature certificate of this request as PDF bytes, or
        False when the report is not rendered as a PDF.

        The report of sign_oca is rendered as superuser, so the page lists
        every signer and log entry whoever signs last. Odoo renders reports
        as HTML while tests run, which gives False here.
        """
        self.ensure_one()
        pdf, report_type = (
            self.env["ir.actions.report"]
            .sudo()
            ._render_qweb_pdf(
                "sign_oca.action_report_signature_certificate", res_ids=self.ids
            )
        )
        return pdf if report_type == "pdf" else False

    def _pades_render_certificate_to_fit(self, page_count):
        """Return the certificate of ``_pades_render_certificate`` on at most
        ``page_count`` pages when a smaller zoom achieves it, or False.

        A longer certificate is rendered again with the context key
        ``sign_oca_pades_certificate_zoom`` (read by the report through
        ``sign.oca.pades.certificate._get_page_style``): its text reflows
        to the width of the page, which stays readable where the reserved
        pages would otherwise print several pages side by side. The zoom
        goes down to ``CERTIFICATE_MIN_ZOOM``, in at most
        ``CERTIFICATE_FIT_ATTEMPTS`` renderings; the last rendering is
        returned when none fits.

        :param int page_count: the pages reserved for the certificate, or
            0 to render it once, as it is
        """
        self.ensure_one()
        certificate = self._pades_render_certificate()
        zoom = 1.0
        for __ in range(CERTIFICATE_FIT_ATTEMPTS):
            pages = self._pades_pdf_page_count(certificate)
            if not page_count or not pages or pages <= page_count:
                break
            if zoom <= CERTIFICATE_MIN_ZOOM:
                break
            # The height of reflowed text shrinks about as the square of the
            # zoom; a later attempt shrinks it by a further tenth.
            estimate = zoom * math.sqrt(page_count / pages)
            zoom = round(max(CERTIFICATE_MIN_ZOOM, min(estimate, zoom * 0.9)), 3)
            certificate = (
                self.with_context(
                    sign_oca_pades_certificate_zoom=zoom
                )._pades_render_certificate()
                or certificate
            )
        return certificate

    def _pades_pdf_page_count(self, pdf_bytes):
        """Return the number of pages of ``pdf_bytes``, 0 when unreadable."""
        if not pdf_bytes:
            return 0
        try:
            return PdfFileReader(BytesIO(pdf_bytes)).numPages
        except Exception:  # noqa: BLE001 - an unreadable file has no page
            return 0

    def _on_completed(self):
        """Finalize the document once every signer has signed (see
        ``_pades_finalize``)."""
        result = super()._on_completed()
        self._pades_finalize()
        return result

    def _pades_finalize(self):
        """Add the certificate and the final seal, then keep the hashes.

        It runs once per request, when the signature process ends: at
        completion, and on a refusal through the module that handles it.
        On a document prepared as a form, the certificate fills the
        reserved certificate pages (``_pades_fill_certificate``); a
        document with a sealing certificate and no seal yet is prepared
        first. On any other document, the certificate pages are appended.
        The update is sealed by ``sign_oca_final_seal`` when a sealing
        certificate is configured and left unsealed otherwise. A
        certificate that cannot be rendered is logged, and the final seal
        is applied alone. ``pades_final_sha256`` and ``current_hash`` hold
        the hash of the resulting document in every case.

        :return: False when the request was already finalized, else True
        """
        self.ensure_one()
        sign_request = self.sudo()
        if sign_request.pades_final_sha256:
            return False
        seal_signer = self._pades_get_signer()
        pdf_bytes = b64decode(sign_request.with_context(bin_size=False).data)
        if (
            seal_signer
            and not self._pades_form_prepared(pdf_bytes)
            and not self._pades_has_signatures(pdf_bytes)
        ):
            pdf_bytes = self._pades_prepare_form(pdf_bytes)
        prepared = self._pades_form_prepared(pdf_bytes)
        try:
            certificate = self._pades_render_certificate_to_fit(
                self._pades_reserved_page_count(pdf_bytes) if prepared else 0
            )
        except Exception:
            _logger.exception(
                "sign.oca.request %s: signature certificate not rendered",
                self.id,
            )
            certificate = False

        def prepare(writer):
            if not certificate:
                return
            if prepared:
                self._pades_fill_certificate(writer, certificate)
            else:
                self._pades_append_pdf(writer, certificate)

        if seal_signer:
            final = self._pades_seal(
                pdf_bytes,
                prepare,
                FINAL_SEAL_FIELD,
                self.env._("Signature certificate and final seal"),
                seal_signer,
                final=True,
            )
        elif certificate:
            writer = IncrementalPdfFileWriter(BytesIO(pdf_bytes), strict=False)
            prepare(writer)
            output = BytesIO()
            writer.write(output)
            final = output.getvalue()
        else:
            final = pdf_bytes
        final_hash = sha256(final).hexdigest()
        sign_request.write(
            {
                "data": b64encode(final),
                "current_hash": final_hash,
                "pades_final_sha256": final_hash,
            }
        )
        return True

    # Verification

    def _pades_check_pdf(self, pdf_bytes):
        """Validate every seal embedded in ``pdf_bytes``.

        A seal is intact when the hash of the bytes it covers matches and
        its cryptographic signature is valid. Each seal is checked against
        its own certificate, without any network access; trust comes from
        ``pades_certificate_sha256``, the certificates that sealed this
        request: a seal by another one makes the status ``untrusted``.
        pyHanko's difference analysis judges the revisions written after
        each seal: anything beyond filling form fields and adding
        signatures makes the status ``modified``. Once the request is
        completed, a document whose SHA-256 is not ``pades_final_sha256``
        is ``modified`` too.

        :return: dict with ``count``, ``intact`` (every seal intact),
            ``status`` (a ``pades_status`` value) and ``details`` (one line
            per seal)
        """
        # The check runs on every write of ``data``, including the one of a
        # signature: any failure is reported in the summary, never raised.
        try:
            reader = HankoPdfFileReader(BytesIO(pdf_bytes), strict=False)
            embedded = reader.embedded_signatures
        except Exception as error:  # noqa: BLE001
            return {
                "count": 0,
                "intact": False,
                "status": "none",
                "details": self.env._("The document cannot be read: %s", error),
            }
        details = []
        intact = True
        known = self._pades_certificate_fingerprints()
        foreign = False
        altered = False
        for signature in embedded:
            try:
                context = ValidationContext(
                    trust_roots=self._pades_embedded_certificates(signature),
                    allow_fetching=False,
                )
                status = validate_pdf_signature(
                    signature,
                    signer_validation_context=context,
                    ts_validation_context=context,
                )
                seal_intact = bool(status.intact and status.valid)
                timestamped = status.timestamp_validity is not None
                level = status.modification_level
                seal_altered = status.docmdp_ok is False or (
                    level is not None and level > ModificationLevel.FORM_FILLING
                )
            except Exception as error:  # noqa: BLE001
                _logger.info("Seal %s not validated: %s", signature.field_name, error)
                seal_intact = timestamped = seal_altered = False
            intact = intact and seal_intact
            altered = altered or seal_altered
            unknown = bool(known) and (
                sha256(signature.signer_cert.dump()).hexdigest() not in known
            )
            foreign = foreign or unknown
            details.append(
                self.env._(
                    "%(field)s: %(state)s%(timestamp)s%(unknown)s%(altered)s",
                    field=signature.field_name,
                    state=self.env._("intact") if seal_intact else self.env._("BROKEN"),
                    timestamp=self.env._(", timestamped") if timestamped else "",
                    unknown=self.env._(", unknown certificate") if unknown else "",
                    altered=(
                        self.env._(", changed beyond form filling afterwards")
                        if seal_altered
                        else ""
                    ),
                )
            )
        final_hash = self.sudo().pades_final_sha256
        changed = bool(final_hash) and sha256(pdf_bytes).hexdigest() != final_hash
        if changed:
            details.append(
                self.env._(
                    "The document differs from the one completed by the last "
                    "signature."
                )
            )
        if not embedded:
            status = "modified" if changed else "none"
        elif not intact:
            status = "broken"
        elif foreign:
            status = "untrusted"
        elif (
            changed
            or altered
            or embedded[-1].coverage != SignatureCoverageLevel.ENTIRE_FILE
        ):
            status = "modified"
        else:
            status = "sealed"
        return {
            "count": len(embedded),
            "intact": bool(embedded) and intact,
            "status": status,
            "details": "\n".join(details),
        }

    def _pades_embedded_certificates(self, signature):
        """Return the certificates embedded in ``signature``.

        They are the signer's chain of the CMS and the chain of its
        signature timestamp token, if any; ``_pades_check_pdf`` takes them
        as trust roots so that the check judges integrity only.
        """
        certificates = [signature.signer_cert]
        signed_datas = [signature.signed_data]
        try:
            token = find_unique_cms_attribute(
                signature.signer_info["unsigned_attrs"], "signature_time_stamp_token"
            )
        except (NonexistentAttributeError, MultivaluedAttributeError):
            token = None
        if token is not None:
            signed_datas.append(token["content"])
        for signed_data in signed_datas:
            for choice in signed_data["certificates"] or []:
                if choice.name == "certificate":
                    certificates.append(choice.chosen)
        return certificates

    def _pades_verification_values(self):
        """Return the values of the ``pades_*`` summary fields for this request."""
        self.ensure_one()
        data = self.with_context(bin_size=False).data
        result = (
            self._pades_check_pdf(b64decode(data))
            if data
            else {"count": 0, "intact": False, "status": "none", "details": False}
        )
        return {
            "pades_seal_count": result["count"],
            "pades_seals_intact": result["intact"],
            "pades_status": result["status"],
            "pades_check_details": result["details"],
            "pades_last_check": fields.Datetime.now(),
        }

    @api.depends("data", "pades_final_sha256", "pades_certificate_sha256")
    def _compute_pades_verification(self):
        for record in self:
            record.update(record._pades_verification_values())

    def action_pades_verify(self):
        """Validate the seals of the document again and store the summary."""
        for record in self:
            record.check_access("read")
            record.sudo().write(record._pades_verification_values())
        return True

    # Form fields of a sealed document
    #
    # A PAdES seal accepts later revisions that only fill form fields, sign
    # signature fields or add new invisible signature fields; drawing on a
    # page or adding one is reported as a modification of the sealed
    # document. So, before its first seal, the document gets one read-only
    # form field per item (``_pades_prepare_form``): a check box for a
    # ``check`` item, a signature field for the first signature item of each
    # signer's role, a multi-line text field for any other item, plus the
    # pages reserved for the signature certificate, each covered by a text
    # field. A signature then fills the fields of its items
    # (``_pades_fill_fields``) and signs the role's signature field; the
    # certificate fills the reserved pages (``_pades_fill_certificate``).

    # Reading the form

    def _pades_form_fields(self, handler):
        """Return ``{name: reference}`` of the top-level form fields of
        ``handler``, a pyHanko reader or writer."""
        result = {}
        try:
            form = handler.root["/AcroForm"]
            field_refs = form["/Fields"]
        except KeyError:
            return result
        for field_ref in field_refs:
            if not isinstance(field_ref, generic.IndirectObject):
                continue
            name = field_ref.get_object().get("/T")
            if name is not None:
                result[str(name)] = field_ref
        return result

    def _pades_reserved_page_count(self, pdf_bytes):
        """Return how many pages ``pdf_bytes`` reserves for the certificate:
        its certificate fields, one per reserved page."""
        try:
            reader = HankoPdfFileReader(BytesIO(pdf_bytes), strict=False)
            names = self._pades_form_fields(reader)
        except Exception:  # noqa: BLE001 - an unreadable file reserves none
            return 0
        return sum(1 for name in names if name.startswith(CERTIFICATE_FIELD))

    def _pades_form_prepared(self, pdf_bytes):
        """Return whether ``_pades_prepare_form`` already ran on ``pdf_bytes``.

        The document is prepared when it holds an item field or a
        certificate field.
        """
        try:
            reader = HankoPdfFileReader(BytesIO(pdf_bytes), strict=False)
            names = self._pades_form_fields(reader)
        except Exception:  # noqa: BLE001 - an unreadable file has no form
            return False
        return any(
            name.startswith(ITEM_FIELD_PREFIX) or name.startswith(CERTIFICATE_FIELD)
            for name in names
        )

    # Geometry

    def _pades_item_rect(self, item, box):
        """Return ``(x, y, width, height)`` of ``item`` on a page of media
        box ``box``, in points from the lower left corner, as sign_oca
        computes it to draw the item."""
        page_width = float(box.getWidth())
        page_height = float(box.getHeight())
        return (
            item["position_x"] / 100 * page_width,
            (100 - item["position_y"] - item["height"]) / 100 * page_height,
            item["width"] / 100 * page_width,
            item["height"] / 100 * page_height,
        )

    def _pades_media_box(self, page_obj):
        """Return the ``/MediaBox`` of ``page_obj``, inherited if needed."""
        node = page_obj
        while node is not None:
            if "/MediaBox" in node:
                return [float(value) for value in node["/MediaBox"]]
            node = node.get("/Parent")
        return [0.0, 0.0, 612.0, 792.0]

    # Appearance streams

    def _pades_overlay_reader(self, pages):
        """Return a pyHanko reader on a PDF made of the pypdf ``pages``."""
        overlay_writer = PdfFileWriter()
        for page in pages:
            overlay_writer.addPage(page)
        stream = BytesIO()
        overlay_writer.write(stream)
        return HankoPdfFileReader(stream, strict=False)

    def _pades_appearance(self, writer, xobject, rect):
        """Return a new appearance stream showing ``xobject`` inside ``rect``.

        :param writer: the ``IncrementalPdfFileWriter`` of the update
        :param xobject: reference of a page imported as a form XObject,
            whose drawing is in page coordinates
        :param rect: ``(x, y, width, height)`` of the widget on that page
        :return: indirect reference of the appearance form XObject; it
            shows the part of the page under the widget, cut to its box
        """
        x, y, width, height = rect
        return writer.add_object(
            generic.StreamObject(
                {
                    pdf_name("/Type"): pdf_name("/XObject"),
                    pdf_name("/Subtype"): pdf_name("/Form"),
                    pdf_name("/BBox"): number_array((0, 0, width, height)),
                    pdf_name("/Resources"): generic.DictionaryObject(
                        {
                            pdf_name("/XObject"): generic.DictionaryObject(
                                {pdf_name(ITEM_XOBJECT): xobject}
                            )
                        }
                    ),
                },
                stream_data=place_command(ITEM_XOBJECT, 1, -x, -y),
            )
        )

    def _pades_empty_appearance(self, writer, width, height):
        """Return a new empty appearance stream of ``width`` x ``height``."""
        return writer.add_object(
            generic.StreamObject(
                {
                    pdf_name("/Type"): pdf_name("/XObject"),
                    pdf_name("/Subtype"): pdf_name("/Form"),
                    pdf_name("/BBox"): number_array((0, 0, width, height)),
                },
                stream_data=b"",
            )
        )

    # Preparation

    def _pades_certificate_page_count(self):
        """Return how many pages the document reserves for the certificate.

        Hook. Default 2: the first page of the certificate (the document,
        its signers and their decisions) is printed full size, and the
        activity log that follows usually fits on the second. The pages
        can no longer change once the document is sealed: the pages of a
        longer certificate share the last reserved page at a reduced
        scale, and a reserved page the certificate does not need says it
        is left blank (see ``_pades_fill_certificate``). 0 reserves none,
        and the certificate is then left out of a prepared document.
        """
        return 2

    def _pades_certificate_placeholder(self):
        """Return the text the reserved certificate pages show until the
        certificate fills them."""
        return self.env._("Reserved for the signature certificate.")

    def _pades_certificate_blank_note(self):
        """Return the text of a reserved certificate page the certificate
        does not need."""
        return self.env._("This page is intentionally left blank.")

    def _pades_note_reader(self, width, height, text):
        """Return a pyHanko reader on a one-page PDF of ``width`` x
        ``height`` that prints ``text`` in grey, centred near the top."""
        packet = BytesIO()
        note = canvas.Canvas(packet, pagesize=(width, height))
        note.setFillGray(0.5)
        note.setFont("Helvetica", 9)
        note.drawCentredString(width / 2, height - 72, text)
        note.save()
        return HankoPdfFileReader(packet, strict=False)

    def _pades_text_field_flags(self, item):
        """Return the field flags of the text field of ``item``.

        Hook. Default: read-only and multi-line, as sign_oca wraps the text
        of every item in its box.
        """
        return FIELD_READ_ONLY | FIELD_MULTILINE

    def _pades_signature_fields(self):
        """Return ``{signer: item key}``: the signature item of each signer
        that becomes the PDF signature field the signer's seal signs.

        It is the first signature item, by key, of the signer's role; a role
        held by several signers gives it to the first signer only.
        """
        self.ensure_one()
        result = {}
        roles = set()
        signers = self.signer_ids.sorted("id")
        for key, item in sorted(
            self.signatory_data.items(), key=lambda pair: int(pair[0])
        ):
            if item.get("field_type") != "signature" or item["role_id"] in roles:
                continue
            role_id = item["role_id"]
            signer = signers.filtered(
                lambda s, role_id=role_id: s.role_id.id == role_id
            )
            if signer:
                roles.add(role_id)
                result[signer[0]] = str(key)
        return result

    def _pades_signer_field_lock(self, signer, field_names):
        """Return the ``/Lock`` dictionary of ``signer``'s signature field.

        Hook. Default: once the field is signed, the fields ``field_names``
        of the signer's role can no longer change (FieldMDP ``Include``),
        and the later revisions may only fill the form and sign
        (``/P 2``). A verifier then reports a value or an appearance of
        those fields rewritten after the seal.

        :param signer: the ``sign.oca.request.signer`` owning the field
        :param list field_names: names of the item fields of its role
        """
        lock = sig_fields.FieldMDPSpec(
            action=sig_fields.FieldMDPAction.INCLUDE, fields=field_names
        ).as_sig_field_lock()
        lock[pdf_name("/P")] = generic.NumberObject(sig_fields.MDPPerm.FILL_FORMS.value)
        return lock

    def _pades_ensure_acroform(self, writer):
        """Return the ``/AcroForm`` dictionary of ``writer``, created if
        needed, with the Helvetica font of the text fields, ``/SigFlags`` 3
        and without ``/NeedAppearances``, so that viewers show the
        appearance streams of the fields rather than draw their own."""
        root = writer.root
        if "/AcroForm" not in root:
            root[pdf_name("/AcroForm")] = writer.add_object(
                generic.DictionaryObject({pdf_name("/Fields"): generic.ArrayObject()})
            )
            writer.update_root()
        form = root["/AcroForm"]
        if "/Fields" not in form:
            form[pdf_name("/Fields")] = generic.ArrayObject()
        resources = form.get("/DR")
        if resources is None:
            resources = form[pdf_name("/DR")] = generic.DictionaryObject()
        fonts = resources.get("/Font")
        if fonts is None:
            fonts = resources[pdf_name("/Font")] = generic.DictionaryObject()
        if HELVETICA not in fonts:
            fonts[pdf_name(HELVETICA)] = generic.DictionaryObject(
                {
                    pdf_name("/Type"): pdf_name("/Font"),
                    pdf_name("/Subtype"): pdf_name("/Type1"),
                    pdf_name("/BaseFont"): pdf_name("/Helvetica"),
                    pdf_name("/Encoding"): pdf_name("/WinAnsiEncoding"),
                }
            )
            writer.update_container(fonts)
        writer.update_container(resources)
        if "/DA" not in form:
            form[pdf_name("/DA")] = pdf_string(TEXT_DEFAULT_APPEARANCE)
        form[pdf_name("/NeedAppearances")] = generic.BooleanObject(False)
        form[pdf_name("/SigFlags")] = generic.NumberObject(3)
        writer.update_container(form)
        return form

    def _pades_add_widget(self, writer, form, page_ref, field):
        """Add ``field``, a merged field and widget dictionary, to the form
        and to the annotations of the page ``page_ref``."""
        field[pdf_name("/Type")] = pdf_name("/Annot")
        field[pdf_name("/Subtype")] = pdf_name("/Widget")
        field[pdf_name("/P")] = page_ref
        field_ref = writer.add_object(field)
        writer.register_annotation(page_ref, field_ref)
        field_refs = form["/Fields"]
        field_refs.append(field_ref)
        writer.update_container(field_refs)
        return field_ref

    def _pades_add_item_field(self, writer, form, page_ref, name, item, rect):
        """Add the hidden, empty field of ``item`` at ``rect`` on ``page_ref``.

        A ``check`` item gets a check box (``/Btn``) set to ``/Off``; any
        other item a text field (``/Tx``) with the flags of
        ``_pades_text_field_flags``. ``_pades_fill_fields`` shows it.
        """
        x, y, width, height = rect
        field = generic.DictionaryObject(
            {
                pdf_name("/T"): pdf_string(name),
                pdf_name("/Rect"): number_array((x, y, x + width, y + height)),
                pdf_name("/F"): generic.NumberObject(ANNOT_PRINT | ANNOT_HIDDEN),
            }
        )
        empty = self._pades_empty_appearance(writer, width, height)
        if item.get("field_type") == "check":
            field[pdf_name("/FT")] = pdf_name("/Btn")
            field[pdf_name("/Ff")] = generic.NumberObject(FIELD_READ_ONLY)
            field[pdf_name("/V")] = pdf_name("/Off")
            field[pdf_name("/AS")] = pdf_name("/Off")
            # The state dictionary is an indirect object: pyHanko's
            # difference analysis reads ``/N`` as a reference.
            field[pdf_name("/AP")] = generic.DictionaryObject(
                {
                    pdf_name("/N"): writer.add_object(
                        generic.DictionaryObject({pdf_name("/Off"): empty})
                    )
                }
            )
        else:
            field[pdf_name("/FT")] = pdf_name("/Tx")
            field[pdf_name("/Ff")] = generic.NumberObject(
                self._pades_text_field_flags(item)
            )
            field[pdf_name("/DA")] = pdf_string(TEXT_DEFAULT_APPEARANCE)
            field[pdf_name("/AP")] = generic.DictionaryObject({pdf_name("/N"): empty})
        return self._pades_add_widget(writer, form, page_ref, field)

    def _pades_reserve_certificate_pages(self, writer, form):
        """Append the pages of ``_pades_certificate_page_count``, each covered
        by a read-only text field showing
        ``_pades_certificate_placeholder``, with the media box of the last
        page."""
        count = self._pades_certificate_page_count()
        if count <= 0:
            return
        last_ref, __ = writer.find_page_for_modification(-1)
        x0, y0, x1, y1 = self._pades_media_box(last_ref.get_object())
        width, height = x1 - x0, y1 - y0
        placeholder_reader = self._pades_note_reader(
            width, height, self._pades_certificate_placeholder()
        )
        for number in range(1, count + 1):
            page_ref = writer.insert_page(
                generic.DictionaryObject(
                    {
                        pdf_name("/Type"): pdf_name("/Page"),
                        pdf_name("/MediaBox"): number_array((x0, y0, x1, y1)),
                        pdf_name("/Resources"): generic.DictionaryObject(),
                        pdf_name("/Contents"): writer.add_object(
                            generic.StreamObject(stream_data=b"")
                        ),
                    }
                )
            )
            name = CERTIFICATE_FIELD if number == 1 else f"{CERTIFICATE_FIELD}_{number}"
            appearance = self._pades_appearance(
                writer,
                writer.import_page_as_xobject(placeholder_reader, page_ix=0),
                (0, 0, width, height),
            )
            self._pades_add_widget(
                writer,
                form,
                page_ref,
                generic.DictionaryObject(
                    {
                        pdf_name("/T"): pdf_string(name),
                        pdf_name("/FT"): pdf_name("/Tx"),
                        pdf_name("/Ff"): generic.NumberObject(
                            FIELD_READ_ONLY | FIELD_MULTILINE
                        ),
                        pdf_name("/DA"): pdf_string(TEXT_DEFAULT_APPEARANCE),
                        pdf_name("/Rect"): number_array((x0, y0, x1, y1)),
                        pdf_name("/F"): generic.NumberObject(ANNOT_PRINT),
                        pdf_name("/AP"): generic.DictionaryObject(
                            {pdf_name("/N"): appearance}
                        ),
                    }
                ),
            )

    def _pades_prepare_form(self, pdf_bytes):
        """Return ``pdf_bytes`` with the form fields of every item, the
        signature field of every signer and the reserved certificate pages.

        The fields are added as one unsealed incremental update, to a
        document that carries no seal yet. Every item field is read-only
        and hidden until a signature fills it. A signer's signature field
        (named ``_pades_signer_field_base``, recorded on the signer as
        ``pades_field_name``) covers the box of its item of
        ``_pades_signature_fields``; that item has no other field. Its lock
        (``_pades_signer_field_lock``) covers the item fields of the
        signer's role.

        :param bytes pdf_bytes: the document as it stands
        :return: bytes of the prepared document
        """
        self.ensure_one()
        writer = IncrementalPdfFileWriter(BytesIO(pdf_bytes), strict=False)
        form = self._pades_ensure_acroform(writer)
        reader = PdfFileReader(BytesIO(pdf_bytes))
        page_count = reader.numPages
        existing = self._pades_form_fields(writer)
        signature_items = {
            key: signer for signer, key in self._pades_signature_fields().items()
        }
        role_fields = defaultdict(list)
        for key, item in self.signatory_data.items():
            if str(key) not in signature_items:
                role_fields[item.get("role_id")].append(item_field_name(key))
        for key, item in sorted(
            self.signatory_data.items(), key=lambda pair: int(pair[0])
        ):
            page_number = item.get("page") or 0
            if not 1 <= page_number <= page_count:
                continue
            page_ref, __ = writer.find_page_for_modification(page_number - 1)
            rect = self._pades_item_rect(item, reader.getPage(page_number - 1).mediaBox)
            signer = signature_items.get(str(key))
            name = (
                self._pades_signer_field_name(signer)
                if signer
                else item_field_name(key)
            )
            if name in existing:
                continue
            if signer:
                x, y, width, height = rect
                field = sig_fields.SignatureFormField(
                    name, box=(x, y, x + width, y + height), include_on_page=page_ref
                )
                empty = self._pades_empty_appearance(writer, width, height)
                field[pdf_name("/AP")] = generic.DictionaryObject(
                    {pdf_name("/N"): empty}
                )
                field[pdf_name("/Lock")] = self._pades_signer_field_lock(
                    signer, sorted(role_fields[signer.role_id.id])
                )
                if signer.sudo().pades_field_name != name:
                    signer.sudo().pades_field_name = name
                field_ref = writer.add_object(field)
                field.register_widget_annotation(writer, field_ref)
                form["/Fields"].append(field_ref)
                writer.update_container(form["/Fields"])
            else:
                self._pades_add_item_field(writer, form, page_ref, name, item, rect)
        if not any(name.startswith(CERTIFICATE_FIELD) for name in existing):
            self._pades_reserve_certificate_pages(writer, form)
        output = BytesIO()
        writer.write(output)
        return output.getvalue()

    # Filling

    def _pades_fill_fields(self, writer, pdf_bytes, items, signer=None):
        """Fill the fields of ``items`` in ``writer`` and return the seal target.

        Each item is drawn by ``signer._get_pdf_page`` as in sign_oca's
        ``_stamp_pdf``; the drawing under the item's box becomes the
        appearance of its field, which is shown and gets the item's value
        (``/V``). A field already shown keeps its value and appearance. A
        signature item of ``_pades_signature_fields`` gives the appearance
        of its signature field, which the seal of the update signs; an
        empty one leaves the field unsigned. An item without field is drawn
        on its page as by ``_pades_add_overlays``.

        :param writer: ``IncrementalPdfFileWriter`` opened on ``pdf_bytes``
        :param bytes pdf_bytes: the document as it stands, prepared by
            ``_pades_prepare_form``
        :param dict items: ``{key: item}`` with ``value`` filled
        :param signer: the ``sign.oca.request.signer`` that draws the items
        :return: dict with ``field_name`` and ``stamp_style`` of the
            signature field to sign, or None to seal a new invisible field

        A stamp of ``signer`` that carries no signature image, such as a
        refusal, still signs the signer's own signature field while it is
        empty, with a blank appearance: no field of the document is left
        for a later signature to fill.
        """
        drawer = signer or self.env["sign.oca.request.signer"]
        reader = PdfFileReader(BytesIO(pdf_bytes))
        form_fields = self._pades_form_fields(writer)
        owners = self._pades_signature_fields()
        signature_fields = {
            key: self._pades_signer_field_name(owner) for owner, key in owners.items()
        }
        target_item = None
        jobs = []
        missing = {}
        for key, item in items.items():
            box = reader.getPage(item["page"] - 1).mediaBox
            rect = self._pades_item_rect(item, box)
            field_ref = next(
                (
                    form_fields[name]
                    for name in item_field_names(key)
                    if name in form_fields
                ),
                None,
            )
            signature_name = signature_fields.get(str(key))
            signature_ref = form_fields.get(signature_name)
            if field_ref is None and signature_ref is not None:
                if not item.get("value"):
                    continue
                if "/V" in signature_ref.get_object():
                    _logger.warning(
                        "sign.oca.request %s: signature field %s already "
                        "signed, item %s not drawn",
                        self.id,
                        signature_name,
                        key,
                    )
                    continue
                if target_item is None:
                    target_item = (
                        signature_name,
                        rect,
                        drawer._get_pdf_page(item, box),
                    )
                    continue
            if field_ref is None:
                missing[key] = item
                continue
            field = field_ref.get_object()
            if not int(field.get("/F", 0)) & ANNOT_HIDDEN:
                _logger.warning(
                    "sign.oca.request %s: field of item %s already filled, kept",
                    self.id,
                    key,
                )
                continue
            if item.get("field_type") == "check":
                pages = [
                    drawer._get_pdf_page(dict(item, value=True), box),
                    drawer._get_pdf_page(dict(item, value=False), box),
                ]
            else:
                pages = [drawer._get_pdf_page(item, box)]
            if all(pages):
                jobs.append((field_ref, item, rect, pages))
        overlay_pages = [page for job in jobs for page in job[3]]
        if target_item and target_item[2]:
            overlay_pages.append(target_item[2])
        if overlay_pages:
            overlay_reader = self._pades_overlay_reader(overlay_pages)
            page_ix = 0
            for field_ref, item, rect, pages in jobs:
                appearances = []
                for __ in pages:
                    appearances.append(
                        self._pades_appearance(
                            writer,
                            writer.import_page_as_xobject(
                                overlay_reader, page_ix=page_ix
                            ),
                            rect,
                        )
                    )
                    page_ix += 1
                self._pades_show_field(writer, field_ref, item, appearances)
        target = None
        if target_item and target_item[2]:
            signature_name, rect, __ = target_item
            target = {
                "field_name": signature_name,
                "stamp_style": ItemStampStyle(
                    xobject=writer.import_page_as_xobject(
                        overlay_reader, page_ix=len(overlay_pages) - 1
                    ),
                    offset=(rect[0], rect[1]),
                ),
            }
        target = target or self._pades_blank_signature_target(
            form_fields, signer, owners
        )
        self._pades_refresh_locked_appearances(writer, form_fields, target)
        if missing:
            _logger.warning(
                "sign.oca.request %s: items %s have no form field, drawn on "
                "their page",
                self.id,
                ", ".join(str(key) for key in missing),
            )
            self._pades_add_overlays(writer, pdf_bytes, missing, signer=signer)
        return target

    def _pades_refresh_locked_appearances(self, writer, form_fields, target):
        """Write again, in the revision ``target`` seals, the appearance
        streams of the fields its signature field locks.

        pyHanko's difference analysis treats the appearance stream of a
        field last written before the sealed revision as rewritten in place
        by every later revision, which a lock forbids: a field left empty by
        its signer would then break the signer's seal. Written again in the
        sealed revision, the stream counts as part of it.

        :param dict form_fields: ``{name: reference}`` of the form fields
        :param dict target: the seal target of ``_pades_fill_fields``, or
            None
        """
        if not target:
            return
        field_ref = form_fields.get(target["field_name"])
        lock = field_ref.get_object().get("/Lock") if field_ref is not None else None
        if not lock:
            return
        for name in lock.get("/Fields") or []:
            locked_ref = form_fields.get(str(name))
            if locked_ref is None:
                continue
            appearance = locked_ref.get_object().get("/AP")
            if appearance is None:
                continue
            for key in ("/N", "/R", "/D"):
                raw = appearance.raw_get(key) if key in appearance else None
                if isinstance(raw, generic.IndirectObject):
                    writer.mark_update(raw)

    def _pades_blank_signature_target(self, form_fields, signer, owners):
        """Return the seal target that signs ``signer``'s own signature field
        with a blank appearance, or None when ``signer`` owns no field of
        ``owners`` (``_pades_signature_fields``), or its field is missing or
        already signed.

        :param dict form_fields: ``{name: reference}`` of the form fields
        :param signer: a ``sign.oca.request.signer``, or None
        """
        if not signer or signer not in owners:
            return None
        name = self._pades_signer_field_name(signer)
        field_ref = form_fields.get(name)
        if field_ref is None or "/V" in field_ref.get_object():
            return None
        return {"field_name": name, "stamp_style": BlankStampStyle()}

    def _pades_show_field(self, writer, field_ref, item, appearances):
        """Give the field ``field_ref`` the value of ``item`` and its
        ``appearances``, and show it.

        A check box gets ``[checked, unchecked]`` appearances and the state
        of the value; a text field one appearance and, unless it draws a
        signature image, the text of the value.
        """
        field = field_ref.get_object()
        if item.get("field_type") == "check":
            state = pdf_name("/Yes") if item.get("value") else pdf_name("/Off")
            field[pdf_name("/AP")] = generic.DictionaryObject(
                {
                    pdf_name("/N"): writer.add_object(
                        generic.DictionaryObject(
                            {
                                pdf_name("/Yes"): appearances[0],
                                pdf_name("/Off"): appearances[1],
                            }
                        )
                    )
                }
            )
            field[pdf_name("/V")] = state
            field[pdf_name("/AS")] = state
        else:
            field[pdf_name("/AP")] = generic.DictionaryObject(
                {pdf_name("/N"): appearances[0]}
            )
            value = item.get("value")
            if item.get("field_type") != "signature" and value not in (None, False):
                field[pdf_name("/V")] = pdf_string(str(value))
        field[pdf_name("/F")] = generic.NumberObject(ANNOT_PRINT)
        writer.mark_update(field_ref)

    def _pades_fill_certificate(self, writer, certificate):
        """Draw the pages of ``certificate`` on the reserved certificate pages.

        Each reserved page but the last gets one page of the certificate,
        full size; the last one gets the remaining pages, laid out in a
        grid scaled to fit, so a certificate longer than the reserved pages
        is printed smaller from its last reserved page on. A reserved page
        left without certificate page shows
        ``_pades_certificate_blank_note``. Nothing changes when a
        certificate field is already filled.

        :param writer: ``IncrementalPdfFileWriter`` of the update
        :param bytes certificate: the rendered certificate, as PDF
        :return: whether the certificate was drawn
        """
        form_fields = self._pades_form_fields(writer)
        names = sorted(
            (name for name in form_fields if name.startswith(CERTIFICATE_FIELD)),
            key=lambda name: (len(name), name),
        )
        field_refs = [form_fields[name] for name in names]
        if not field_refs or any("/V" in ref.get_object() for ref in field_refs):
            return False
        cert_reader = HankoPdfFileReader(BytesIO(certificate), strict=False)
        page_count = int(cert_reader.root["/Pages"]["/Count"])
        if not page_count:
            return False
        last = len(field_refs) - 1
        for position, field_ref in enumerate(field_refs):
            if position < last:
                chunk = [position] if position < page_count else []
            else:
                chunk = list(range(last, page_count))
            if chunk:
                self._pades_draw_pages(writer, field_ref, cert_reader, chunk)
            else:
                self._pades_draw_blank_note(writer, field_ref)
        return True

    def _pades_draw_blank_note(self, writer, field_ref):
        """Make ``_pades_certificate_blank_note`` the value and appearance of
        the certificate field ``field_ref``, and show the field."""
        field = field_ref.get_object()
        x0, y0, x1, y1 = (float(value) for value in field["/Rect"])
        width, height = x1 - x0, y1 - y0
        note = self._pades_certificate_blank_note()
        appearance = self._pades_appearance(
            writer,
            writer.import_page_as_xobject(
                self._pades_note_reader(width, height, note), page_ix=0
            ),
            (0, 0, width, height),
        )
        field[pdf_name("/AP")] = generic.DictionaryObject({pdf_name("/N"): appearance})
        field[pdf_name("/V")] = pdf_string(note)
        field[pdf_name("/F")] = generic.NumberObject(ANNOT_PRINT)
        writer.mark_update(field_ref)

    def _pades_draw_pages(self, writer, field_ref, source, page_indexes):
        """Make the pages ``page_indexes`` of the pyHanko reader ``source``
        the appearance of the text field ``field_ref``, in a grid of
        ``ceil(sqrt(n))`` columns scaled to fit, and show the field."""
        field = field_ref.get_object()
        x0, y0, x1, y1 = (float(value) for value in field["/Rect"])
        width, height = x1 - x0, y1 - y0
        count = len(page_indexes)
        columns = math.ceil(math.sqrt(count))
        rows = math.ceil(count / columns)
        xobjects = generic.DictionaryObject()
        commands = []
        for position, source_ix in enumerate(page_indexes):
            page_ref, __ = source.find_page_for_modification(source_ix)
            px0, py0, px1, py1 = self._pades_media_box(page_ref.get_object())
            page_width, page_height = px1 - px0, py1 - py0
            scale = min(width / (columns * page_width), height / (rows * page_height))
            left = (width - columns * page_width * scale) / 2
            bottom = (height - rows * page_height * scale) / 2
            column, row = position % columns, position // columns
            name = f"/SignOcaPage{position + 1}"
            xobjects[pdf_name(name)] = writer.import_page_as_xobject(
                source, page_ix=source_ix
            )
            commands.append(
                place_command(
                    name,
                    scale,
                    left + column * page_width * scale - px0 * scale,
                    bottom + (rows - 1 - row) * page_height * scale - py0 * scale,
                )
            )
        appearance = writer.add_object(
            generic.StreamObject(
                {
                    pdf_name("/Type"): pdf_name("/XObject"),
                    pdf_name("/Subtype"): pdf_name("/Form"),
                    pdf_name("/BBox"): number_array((0, 0, width, height)),
                    pdf_name("/Resources"): generic.DictionaryObject(
                        {pdf_name("/XObject"): xobjects}
                    ),
                },
                stream_data=b"\n".join(commands),
            )
        )
        field[pdf_name("/AP")] = generic.DictionaryObject({pdf_name("/N"): appearance})
        field[pdf_name("/V")] = pdf_string(self.env._("Signature certificate"))
        field[pdf_name("/F")] = generic.NumberObject(ANNOT_PRINT)
        writer.mark_update(field_ref)


class SignOcaRequestSigner(models.Model):
    _inherit = "sign.oca.request.signer"

    pades_field_name = fields.Char(
        string="Signature Field",
        readonly=True,
        copy=False,
        help="Name of the PDF signature field this signer's seal signs, set "
        "when the document is prepared as a form.",
    )

    def _get_server_fields(self):
        """The signature field of a signer is named by the server only."""
        return super()._get_server_fields() | {"pades_field_name"}

    def _pades_seal_signer_name(self):
        """Return the name the reason of this signer's seal gives.

        Hook. Default: the signer's contact name.
        """
        self.ensure_one()
        return self.partner_id.name
