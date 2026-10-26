# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
import datetime
import hashlib
import os
import tempfile
from io import BytesIO
from unittest.mock import patch

from asn1crypto import keys as asn1_keys
from asn1crypto import x509 as asn1_x509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from freezegun import freeze_time
from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
from pyhanko.pdf_utils.reader import PdfFileReader as HankoPdfFileReader
from pyhanko.sign import signers
from pyhanko.sign.diff_analysis import ModificationLevel
from pyhanko.sign.timestamps import DummyTimeStamper, TimestampRequestError
from pyhanko.sign.timestamps.api import TimeStamper
from pyhanko.sign.validation import SignatureCoverageLevel, validate_pdf_signature
from pyhanko_certvalidator import ValidationContext
from reportlab.pdfgen import canvas

from odoo.exceptions import UserError
from odoo.tests import new_test_user
from odoo.tools import config, misc
from odoo.tools.pdf import PdfFileReader

from odoo.addons.base.tests.common import BaseCommon

from ..models.pades_form import item_field_name

MODEL_PATH = "odoo.addons.sign_oca_pades.models.sign_oca_request"


def make_certificate(common_name, password, timestamping=False):
    """Return ``(pkcs12_bytes, asn1 certificate, asn1 key)`` of a new
    self-signed certificate, valid from yesterday for 30 days."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    if timestamping:
        builder = builder.add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]),
            critical=True,
        )
    cert = builder.sign(key, hashes.SHA256())
    data = pkcs12.serialize_key_and_certificates(
        b"seal",
        key,
        cert,
        None,
        serialization.BestAvailableEncryption(password),
    )
    asn1_cert = asn1_x509.Certificate.load(
        cert.public_bytes(serialization.Encoding.DER)
    )
    asn1_key = asn1_keys.PrivateKeyInfo.load(
        key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return data, asn1_cert, asn1_key


def make_certificate_pdf(pages=1):
    """Return a PDF of ``pages`` pages standing in for the rendered
    certificate; page ``n`` prints "Signature Certificate n/pages".

    Rendering the report with wkhtmltopdf needs the HTTP server to serve
    its assets, which a test transaction does not allow.
    """
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(595, 842))
    for number in range(1, pages + 1):
        pdf.drawString(72, 770, f"Signature Certificate {number}/{pages}")
        pdf.showPage()
    pdf.save()
    return stream.getvalue()


# A 1x1 PNG, enough for reportlab to draw a signature.
SIGNATURE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


class FailingTimeStamper(TimeStamper):
    """Timestamper whose authority never answers."""

    async def async_request_tsa_response(self, req):
        raise TimestampRequestError("No answer")


class TestSignOcaPades(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            cls.pdf = pdf.read()
        cls.page_count = PdfFileReader(BytesIO(cls.pdf)).numPages
        cls.company_p12, cls.company_cert, __ = make_certificate(
            "Company seal", b"company-secret"
        )
        cls.server_p12, cls.server_cert, __ = make_certificate(
            "Server seal", b"server-secret"
        )
        __, cls.tsa_cert, cls.tsa_key = make_certificate(
            "Test TSA", b"tsa", timestamping=True
        )
        cls.role_customer = cls.env.ref("sign_oca.sign_role_customer")
        cls.role_employee = cls.env.ref("sign_oca.sign_role_employee")
        cls.partner_a = cls.env["res.partner"].create({"name": "First signer"})
        cls.partner_b = cls.env["res.partner"].create({"name": "Second signer"})

    def setUp(self):
        super().setUp()
        self.request = self.env["sign.oca.request"].create(
            {
                "data": base64.b64encode(self.pdf),
                "name": "Sealing test",
                "signer_ids": [
                    (0, 0, {"partner_id": partner.id, "role_id": role.id})
                    for partner, role in (
                        (self.partner_a, self.role_customer),
                        (self.partner_b, self.role_employee),
                    )
                ],
            }
        )
        field = self.env.ref("sign_oca.sign_field_name")
        self.items = {}
        for role, height in ((self.role_customer, 10), (self.role_employee, 30)):
            item = self.request.add_item(
                {
                    "field_id": field.id,
                    "role_id": role.id,
                    "page": 1,
                    "position_x": 10,
                    "position_y": height,
                    "width": 40,
                    "height": 5,
                }
            )
            self.items[role] = {str(item["id"]): dict(item, value=role.name)}
        self.request.action_send()

    # Helpers

    def _set_company_certificate(self, password="company-secret", tsa_url=False):
        self.request.company_id.write(
            {
                "sign_oca_pades_pkcs12": base64.b64encode(self.company_p12),
                "sign_oca_pades_pkcs12_password": password,
                "sign_oca_pades_tsa_url": tsa_url,
            }
        )

    def _sign(self, role):
        signer = self.request.signer_ids.filtered(lambda s: s.role_id == role)
        signer.action_sign(self.items[role])
        return signer

    def _document(self):
        return base64.b64decode(self.request.data)

    def _signatures(self, pdf_bytes=None):
        reader = HankoPdfFileReader(BytesIO(pdf_bytes or self._document()))
        return reader.embedded_signatures

    def _validate(self, signature, trust=None):
        context = ValidationContext(
            trust_roots=[trust or signature.signer_cert], allow_fetching=False
        )
        return validate_pdf_signature(
            signature,
            signer_validation_context=context,
            ts_validation_context=ValidationContext(
                trust_roots=[self.tsa_cert], allow_fetching=False
            ),
        )

    def _assert_seals_pass(self, count, trust=None):
        """Assert the document holds ``count`` seals that all pass pyHanko's
        validation, difference analysis included: each seal is intact and
        valid, the revisions after an earlier seal only fill its form
        fields, and the last seal covers the entire file.

        :return: the embedded signatures
        """
        signatures = self._signatures()
        self.assertEqual(len(signatures), count)
        for signature in signatures:
            status = self._validate(signature, trust=trust or self.company_cert)
            self.assertTrue(status.intact, signature.field_name)
            self.assertTrue(status.valid, signature.field_name)
            self.assertTrue(status.docmdp_ok, signature.field_name)
            self.assertLessEqual(
                status.modification_level,
                ModificationLevel.FORM_FILLING,
                signature.field_name,
            )
            self.assertTrue(status.bottom_line, signature.field_name)
        self.assertEqual(
            signatures[-1].evaluate_signature_coverage(),
            SignatureCoverageLevel.ENTIRE_FILE,
        )
        return signatures

    def _fields(self, pdf_bytes=None):
        """Return ``{name: field dictionary}`` of the document's form."""
        reader = HankoPdfFileReader(BytesIO(pdf_bytes or self._document()))
        return {
            str(ref.get_object()["/T"]): ref.get_object()
            for ref in reader.root["/AcroForm"]["/Fields"]
        }

    def _item_field(self, role, pdf_bytes=None):
        """Return the form field of the item of ``role``."""
        key = next(iter(self.items[role]))
        return self._fields(pdf_bytes)[item_field_name(key)]

    def _appearance_data(self, field):
        """Return the decoded content of the normal appearance of ``field``
        and of the form XObjects it paints, concatenated."""
        stream = field["/AP"]["/N"]
        if "/Yes" in stream or "/Off" in stream:
            stream = stream[field["/AS"]]
        chunks = []
        todo = [stream]
        while todo:
            current = todo.pop()
            chunks.append(current.data)
            xobjects = current.get("/Resources", {}).get("/XObject", {})
            todo.extend(xobjects[name] for name in xobjects)
        return b"\n".join(chunks)

    def _paints_image(self, field):
        """Return whether the normal appearance of ``field`` paints an image."""
        todo = [field["/AP"]["/N"]]
        while todo:
            current = todo.pop()
            xobjects = current.get("/Resources", {}).get("/XObject", {})
            for xobject in (xobjects[name] for name in xobjects):
                if xobject.get("/Subtype") == "/Image":
                    return True
                todo.append(xobject)
        return False

    def _page_count(self):
        return PdfFileReader(BytesIO(self._document())).numPages

    def _page_text(self, page_number):
        reader = PdfFileReader(BytesIO(self._document()))
        return reader.getPage(page_number - 1).extractText()

    # Tests

    def test_no_certificate_keeps_sign_oca_document(self):
        with self.assertLogs(MODEL_PATH, level="WARNING") as logs:
            self._sign(self.role_customer)
        self.assertTrue(
            any("no sealing certificate configured" in line for line in logs.output)
        )
        self.assertFalse(self._signatures())
        self.assertEqual(self.request.pades_status, "none")
        self.assertEqual(self.request.pades_seal_count, 0)
        self.assertFalse(self.request.pades_seals_intact)
        with self.assertLogs(MODEL_PATH, level="WARNING"):
            self._sign(self.role_employee)
        self.assertEqual(self.request.state, "2_signed")
        self.assertFalse(self._signatures())
        self.assertIn(self.role_employee.name, self._page_text(1))
        self.assertEqual(
            PdfFileReader(BytesIO(self._document())).numPages, self.page_count
        )
        self.assertTrue(self.request.pades_original_sha256)
        self.assertEqual(self.request.pades_final_sha256, self.request.current_hash)

    def test_each_signature_is_sealed_incrementally(self):
        self._set_company_certificate()
        self._sign(self.role_customer)
        first = self._document()
        self.assertEqual(self.request.pades_seal_count, 1)
        self.assertEqual(self.request.pades_status, "sealed")
        self.assertEqual(self._page_count(), self.page_count + 2)
        with patch.object(type(self.request), "_on_completed", return_value=None):
            second_signer = self._sign(self.role_employee)
        document = self._document()
        self.assertTrue(document.startswith(first), "earlier revision rewritten")
        signatures = self._assert_seals_pass(2)
        self.assertEqual(
            signatures[0].evaluate_signature_coverage(),
            SignatureCoverageLevel.ENTIRE_REVISION,
        )
        self.assertEqual(
            signatures[1].field_name,
            f"sign_oca_signer_{second_signer.id}_"
            + second_signer.role_id.name.lower().replace(" ", "_"),
        )
        self.assertEqual(signatures[1].sig_object["/SubFilter"], "/ETSI.CAdES.detached")
        for role in (self.role_customer, self.role_employee):
            field = self._item_field(role)
            self.assertEqual(field["/V"], role.name)
            self.assertEqual(field["/F"], 4, "field shown, printed")
            self.assertTrue(field["/Ff"] & 1, "field read-only")
            self.assertIn(role.name.encode(), self._appearance_data(field))
        # The second signature left the field of the first one untouched.
        self.assertEqual(
            self._item_field(self.role_customer, first)["/AP"]["/N"].data,
            self._item_field(self.role_customer)["/AP"]["/N"].data,
        )
        self.assertFalse(self._page_text(1).strip(), "nothing drawn on the page")
        self.assertEqual(self.request.pades_status, "sealed")
        self.assertTrue(self.request.pades_seals_intact)
        self.assertNotIn("changed beyond", self.request.pades_check_details)

    def test_completion_fills_certificate_and_final_seal(self):
        self._set_company_certificate()
        self._sign(self.role_customer)
        with patch.object(
            type(self.request),
            "_pades_render_certificate",
            return_value=make_certificate_pdf(),
        ):
            self._sign(self.role_employee)
        self.assertEqual(self.request.state, "2_signed")
        signatures = self._assert_seals_pass(3)
        self.assertEqual(signatures[-1].field_name, "sign_oca_final_seal")
        # The certificate fills the page reserved before the first seal.
        self.assertEqual(self._page_count(), self.page_count + 2)
        form = self._fields()
        certificate = form["sign_oca_certificate"]
        self.assertEqual(certificate["/V"], "Signature certificate")
        self.assertIn(b"Signature Certificate 1/1", self._appearance_data(certificate))
        # The second reserved page is not needed by a one-page certificate.
        blank = form["sign_oca_certificate_2"]
        self.assertEqual(blank["/V"], "This page is intentionally left blank.")
        self.assertIn(b"intentionally left blank", self._appearance_data(blank))
        self.assertEqual(self.request.pades_seal_count, 3)
        self.assertEqual(self.request.pades_status, "sealed")
        digest = self.request.pades_final_sha256
        self.assertTrue(digest)
        self.assertEqual(self.request.current_hash, digest)
        self.assertNotEqual(self.request.pades_original_sha256, digest)

    def test_certificate_failure_still_seals(self):
        self._set_company_certificate()
        self._sign(self.role_customer)
        with (
            patch.object(
                type(self.request),
                "_pades_render_certificate",
                side_effect=RuntimeError("no renderer"),
            ),
            self.assertLogs(MODEL_PATH, level="ERROR"),
        ):
            self._sign(self.role_employee)
        signatures = self._assert_seals_pass(3)
        self.assertEqual(signatures[-1].field_name, "sign_oca_final_seal")
        self.assertEqual(self._page_count(), self.page_count + 2)
        form = self._fields()
        self.assertNotIn("/V", form["sign_oca_certificate"])
        self.assertNotIn("/V", form["sign_oca_certificate_2"])

    def test_server_configuration_takes_precedence(self):
        self._set_company_certificate()
        with tempfile.NamedTemporaryFile(suffix=".p12", delete=False) as handle:
            handle.write(self.server_p12)
        self.addCleanup(os.unlink, handle.name)
        with patch.dict(
            config.options,
            {
                "sign_oca_pades_pkcs12_path": handle.name,
                "sign_oca_pades_pkcs12_password": "server-secret",
            },
        ):
            self.assertEqual(self.request._pades_get_config()["source"], "config")
            settings = self.env["res.config.settings"].create({})
            self.assertTrue(settings.sign_oca_pades_server_certificate)
            self._sign(self.role_customer)
        signature = self._signatures()[0]
        self.assertEqual(signature.signer_cert.dump(), self.server_cert.dump())
        self.assertEqual(self.request._pades_get_config()["source"], "company")

    def test_unreadable_server_certificate_raises(self):
        with (
            patch.dict(
                config.options,
                {"sign_oca_pades_pkcs12_path": "/nonexistent/sign_oca_pades.p12"},
            ),
            self.assertRaises(UserError),
        ):
            self._sign(self.role_customer)

    def test_wrong_password_raises(self):
        self._set_company_certificate(password="wrong")
        with self.assertRaises(UserError):
            self._sign(self.role_customer)

    def test_timestamp_from_tsa(self):
        self._set_company_certificate(tsa_url="https://tsa.invalid/rfc3161")
        tsa = DummyTimeStamper(tsa_cert=self.tsa_cert, tsa_key=self.tsa_key)
        with patch(f"{MODEL_PATH}.HTTPTimeStamper", return_value=tsa) as http:
            self._sign(self.role_customer)
        http.assert_called_with("https://tsa.invalid/rfc3161")
        status = self._validate(self._signatures()[0])
        self.assertIsNotNone(status.timestamp_validity)
        self.assertTrue(status.timestamp_validity.intact)
        self.assertTrue(status.timestamp_validity.valid)
        self.assertIn("timestamped", self.request.pades_check_details)
        with self.assertNoLogs("pyhanko", level="WARNING"):
            self.request.action_pades_verify()
        self.assertTrue(self.request.pades_seals_intact)

    def test_timestamp_failure_seals_without_timestamp(self):
        self._set_company_certificate(tsa_url="https://tsa.invalid/rfc3161")
        with (
            patch(f"{MODEL_PATH}.HTTPTimeStamper", return_value=FailingTimeStamper()),
            self.assertLogs(MODEL_PATH, level="WARNING"),
        ):
            self._sign(self.role_customer)
        status = self._validate(self._signatures()[0])
        self.assertTrue(status.intact and status.valid)
        self.assertIsNone(status.timestamp_validity)
        self.assertIn("did not answer", self.request.message_ids[0].body.striptags())

    def test_existing_seal_kept_when_certificate_removed(self):
        self._set_company_certificate()
        self._sign(self.role_customer)
        first = self._document()
        self.request.company_id.sign_oca_pades_pkcs12 = False
        with self.assertLogs(MODEL_PATH, level="WARNING"):
            self._sign(self.role_employee)
        document = self._document()
        self.assertTrue(document.startswith(first))
        signatures = self._signatures()
        self.assertEqual(len(signatures), 1)
        status = self._validate(signatures[0])
        self.assertTrue(status.intact)
        # The unsealed update only fills the form of the sealed document.
        self.assertTrue(status.docmdp_ok)
        self.assertEqual(status.modification_level, ModificationLevel.FORM_FILLING)
        self.assertEqual(self.request.pades_status, "modified")
        self.assertEqual(
            self._item_field(self.role_employee)["/V"], self.role_employee.name
        )

    def test_verify_detects_alteration(self):
        self._set_company_certificate()
        self._sign(self.role_customer)
        document = bytearray(self._document())
        # Byte 11 lies in the binary comment of the PDF header: changing it
        # keeps the file readable while altering the signed bytes.
        self.assertEqual(document[9:10], b"%")
        document[11] ^= 0x01
        self.request.data = base64.b64encode(bytes(document))
        self.assertEqual(self.request.pades_status, "broken")
        self.assertFalse(self.request.pades_seals_intact)
        self.assertIn("BROKEN", self.request.pades_check_details)

    def test_verify_button_refreshes_check_date(self):
        self._set_company_certificate()
        self._sign(self.role_customer)
        with freeze_time("2030-01-02 03:04:05"):
            self.request.action_pades_verify()
        self.assertEqual(
            self.request.pades_last_check, datetime.datetime(2030, 1, 2, 3, 4, 5)
        )
        self.assertEqual(self.request.pades_status, "sealed")

    def test_render_certificate(self):
        report_model = type(self.env["ir.actions.report"])
        calls = []
        rendered = [(b"%PDF-", "pdf")]

        def render(report, report_ref, res_ids=None, data=None):
            calls.append((report_ref, res_ids, report.env.su))
            return rendered[0]

        with patch.object(report_model, "_render_qweb_pdf", render):
            self.assertEqual(self.request._pades_render_certificate(), b"%PDF-")
            # Odoo renders HTML while tests run: no page to append.
            rendered[0] = (b"<html/>", "html")
            self.assertFalse(self.request._pades_render_certificate())
        report_ref, res_ids, superuser = calls[0]
        self.assertEqual(report_ref, "sign_oca.action_report_signature_certificate")
        self.assertEqual(res_ids, self.request.ids)
        self.assertTrue(superuser)

    def test_no_certificate_appends_unsealed_certificate_page(self):
        with self.assertLogs(MODEL_PATH, level="WARNING"):
            self._sign(self.role_customer)
        signed = self._document()
        with (
            patch.object(
                type(self.request),
                "_pades_render_certificate",
                return_value=make_certificate_pdf(),
            ),
            self.assertLogs(MODEL_PATH, level="WARNING"),
        ):
            self._sign(self.role_employee)
        document = self._document()
        self.assertEqual(self.request.state, "2_signed")
        self.assertFalse(self._signatures())
        self.assertEqual(PdfFileReader(BytesIO(document)).numPages, self.page_count + 1)
        self.assertIn("Signature Certificate", self._page_text(self.page_count + 1))
        self.assertNotEqual(document, signed)
        digest = hashlib.sha256(document).hexdigest()
        self.assertEqual(self.request.pades_final_sha256, digest)
        self.assertEqual(self.request.current_hash, digest)
        self.assertEqual(self.request.pades_status, "none")

    def _complete_sealed(self):
        self._set_company_certificate()
        self._sign(self.role_customer)
        with patch.object(
            type(self.request),
            "_pades_render_certificate",
            return_value=make_certificate_pdf(),
        ):
            self._sign(self.role_employee)
        self.assertEqual(self.request.pades_status, "sealed")

    def test_seal_by_unknown_certificate_reported(self):
        self._complete_sealed()
        self.assertTrue(self.request.pades_certificate_sha256)
        # Another certificate seals one more revision of the document.
        self.request.company_id.write(
            {
                "sign_oca_pades_pkcs12": base64.b64encode(self.server_p12),
                "sign_oca_pades_pkcs12_password": "server-secret",
            }
        )
        other_signer = self.request._pades_get_signer()
        writer = IncrementalPdfFileWriter(BytesIO(self._document()), strict=False)
        output = BytesIO()
        signers.PdfSigner(
            signers.PdfSignatureMetadata(field_name="other_seal"), other_signer
        ).sign_pdf(writer, output=output)
        self.request.sudo().data = base64.b64encode(output.getvalue())
        self.assertEqual(self.request.pades_status, "untrusted")
        self.assertIn("unknown certificate", self.request.pades_check_details)

    def test_document_changed_after_completion_reported(self):
        """Without seals, the SHA-256 kept at completion still tells a
        replaced document."""
        with self.assertLogs(MODEL_PATH, level="WARNING"):
            self._sign(self.role_customer)
            self._sign(self.role_employee)
        self.assertEqual(self.request.pades_status, "none")
        writer = IncrementalPdfFileWriter(BytesIO(self._document()), strict=False)
        writer.add_object(generic.NullObject())
        output = BytesIO()
        writer.write(output)
        self.request.sudo().data = base64.b64encode(output.getvalue())
        self.assertEqual(self.request.pades_status, "modified")

    def test_users_cannot_replace_a_sent_document(self):
        user = new_test_user(
            self.env, login="pades_sign_user", groups="sign_oca.sign_oca_group_manager"
        )
        request = self.request.with_user(user)
        with self.assertRaises(UserError):
            request.write({"data": base64.b64encode(self.pdf)})
        with self.assertRaises(UserError):
            request.write({"pades_final_sha256": "0" * 64})

    def test_certificate_report_shows_user_agent_and_chain(self):
        with self.assertLogs(MODEL_PATH, level="WARNING"):
            self._sign(self.role_customer)
        self.env["sign.oca.request.log"].create(
            {
                "request_id": self.request.id,
                "action": "view",
                "user_agent": "TestBrowser/1.0",
            }
        )
        html, __ = self.env["ir.actions.report"]._render_qweb_html(
            "sign_oca.action_report_signature_certificate", self.request.ids
        )
        html = html.decode()
        self.assertIn("User agent", html)
        self.assertIn("TestBrowser/1.0", html)
        self.assertIn("Hash chain:", html)
        self.assertIn("verified", html)

    def test_settings_store_company_certificate(self):
        settings = self.env["res.config.settings"].create(
            {
                "sign_oca_pades_pkcs12": base64.b64encode(self.company_p12),
                "sign_oca_pades_pkcs12_filename": "seal.p12",
                "sign_oca_pades_pkcs12_password": "company-secret",
                "sign_oca_pades_tsa_url": "https://tsa.invalid/rfc3161",
            }
        )
        settings.execute()
        self.assertFalse(settings.sign_oca_pades_server_certificate)
        conf = self.request._pades_get_config()
        self.assertEqual(conf["source"], "company")
        self.assertEqual(conf["pkcs12"], self.company_p12)
        self.assertEqual(conf["tsa_url"], "https://tsa.invalid/rfc3161")
        arch = self.env["res.config.settings"].get_views([(False, "form")])["views"][
            "form"
        ]["arch"]
        self.assertIn('name="sign_oca_pades_pkcs12"', arch)

    def test_request_form_shows_seals(self):
        arch = self.env["sign.oca.request"].get_views([(False, "form")])["views"][
            "form"
        ]["arch"]
        self.assertIn('name="action_pades_verify"', arch)
        self.assertIn('name="pades_status"', arch)

    # Form fields

    def _new_request(self, layout):
        """Create a draft request on the test PDF.

        :param layout: ``{role: [(field xmlid, value, position_y, height)]}``
        :return: ``(request, {role: {key: item with value}})``
        """
        request = self.env["sign.oca.request"].create(
            {
                "data": base64.b64encode(self.pdf),
                "name": "Form test",
                "signer_ids": [
                    (0, 0, {"partner_id": partner.id, "role_id": role.id})
                    for partner, role in zip(
                        (self.partner_a, self.partner_b), layout, strict=False
                    )
                ],
            }
        )
        items = {}
        for role, fields_spec in layout.items():
            items[role] = {}
            for xmlid, value, position_y, height in fields_spec:
                item = request.add_item(
                    {
                        "field_id": self.env.ref(xmlid).id,
                        "role_id": role.id,
                        "page": 1,
                        "position_x": 10,
                        "position_y": position_y,
                        "width": 40,
                        "height": height,
                    }
                )
                items[role][str(item["id"])] = dict(item, value=value)
        return request, items

    def test_send_prepares_form(self):
        self._set_company_certificate()
        request, items = self._new_request(
            {
                self.role_customer: [
                    ("sign_oca.sign_field_name", False, 10, 5),
                    ("sign_oca.sign_field_check", False, 20, 3),
                    ("sign_oca.sign_field_signature", False, 30, 10),
                ],
            }
        )
        request.action_send()
        document = base64.b64decode(request.data)
        self.assertEqual(
            request.pades_original_sha256, hashlib.sha256(self.pdf).hexdigest()
        )
        self.assertFalse(self._signatures(document))
        self.assertEqual(PdfFileReader(BytesIO(document)).numPages, self.page_count + 2)
        form = self._fields(document)
        signer = request.signer_ids
        name_key, check_key, __ = items[self.role_customer]
        self.assertEqual(form[item_field_name(name_key)]["/FT"], "/Tx")
        self.assertEqual(form[item_field_name(name_key)]["/F"], 4 | 2, "hidden")
        self.assertEqual(form[item_field_name(name_key)]["/Ff"], 1 | 4096)
        self.assertEqual(form[item_field_name(check_key)]["/FT"], "/Btn")
        signature_field = form[request._pades_signer_field_base(signer)]
        self.assertEqual(signature_field["/FT"], "/Sig")
        self.assertNotIn("/V", signature_field)
        self.assertEqual(form["sign_oca_certificate"]["/FT"], "/Tx")
        self.assertEqual(form["sign_oca_certificate_2"]["/FT"], "/Tx")
        self.assertIn(
            b"Reserved for the signature certificate",
            self._appearance_data(form["sign_oca_certificate"]),
        )
        # Sending prepares the document once.
        request.sudo().write({"state": "1_draft"})
        request.action_send()
        self.assertEqual(base64.b64decode(request.data), document)

    def test_every_field_kind_passes_difference_analysis(self):
        self._set_company_certificate()
        request, items = self._new_request(
            {
                self.role_customer: [
                    ("sign_oca.sign_field_name", "Customer name", 10, 4),
                    ("sign_oca.sign_field_check", True, 16, 3),
                    ("sign_oca.sign_field_signature", SIGNATURE_PNG, 22, 8),
                ],
                self.role_employee: [
                    ("sign_oca.sign_field_text", "First line\nSecond line", 40, 8),
                    ("sign_oca.sign_field_check", False, 50, 3),
                    ("sign_oca.sign_field_signature", SIGNATURE_PNG, 56, 8),
                    ("sign_oca.sign_field_signature", SIGNATURE_PNG, 66, 8),
                ],
            }
        )
        request.action_send()
        self.request = request
        customer = request.signer_ids.filtered(
            lambda s: s.role_id == self.role_customer
        )
        employee = request.signer_ids - customer
        customer.action_sign(items[self.role_customer])
        first = self._document()
        with patch.object(
            type(request),
            "_pades_render_certificate",
            return_value=make_certificate_pdf(pages=3),
        ):
            employee.action_sign(items[self.role_employee])
        self.assertEqual(request.state, "2_signed")
        signatures = self._assert_seals_pass(3)
        self.assertEqual(
            [signature.field_name for signature in signatures],
            [
                request._pades_signer_field_base(customer),
                request._pades_signer_field_base(employee),
                "sign_oca_final_seal",
            ],
        )
        form = self._fields()
        # The seals sign the visible fields of the signature items.
        for signer in customer | employee:
            field = form[request._pades_signer_field_base(signer)]
            self.assertIn("/V", field)
            self.assertTrue(float(field["/Rect"][2]) > float(field["/Rect"][0]))
            self.assertTrue(self._paints_image(field), field["/T"])
        keys = {role: list(role_items) for role, role_items in items.items()}
        self.assertEqual(
            form[item_field_name(keys[self.role_customer][0])]["/V"], "Customer name"
        )
        check_on = form[item_field_name(keys[self.role_customer][1])]
        self.assertEqual((check_on["/V"], check_on["/AS"]), ("/Yes", "/Yes"))
        check_off = form[item_field_name(keys[self.role_employee][1])]
        self.assertEqual((check_off["/V"], check_off["/AS"]), ("/Off", "/Off"))
        self.assertEqual(check_off["/F"], 4, "an unchecked box is shown too")
        multiline = form[item_field_name(keys[self.role_employee][0])]
        self.assertEqual(multiline["/V"], "First line\nSecond line")
        self.assertTrue(multiline["/Ff"] & 4096)
        appearance = self._appearance_data(multiline)
        self.assertIn(b"First line", appearance)
        self.assertIn(b"Second line", appearance)
        # A second signature item of a role is a field of its own.
        self.assertIn(item_field_name(keys[self.role_employee][3]), form)
        # The fields of the first signature did not change afterwards.
        first_form = self._fields(first)
        for key in keys[self.role_customer][:2]:
            name = item_field_name(key)
            self.assertEqual(first_form[name]["/V"], form[name]["/V"])
        # The certificate has more pages than the document reserves.
        self.assertEqual(self._page_count(), self.page_count + 2)
        # The first certificate page fills the first reserved page; the two
        # others share the last one.
        first_page = self._appearance_data(form["sign_oca_certificate"])
        self.assertIn(b"Signature Certificate 1/3", first_page)
        self.assertNotIn(b"Signature Certificate 2/3", first_page)
        last_page = self._appearance_data(form["sign_oca_certificate_2"])
        for number in (2, 3):
            self.assertIn(f"Signature Certificate {number}/3".encode(), last_page)
        self.assertEqual(request.pades_status, "sealed")

    def test_finalize_refused_document(self):
        """A process that ends without every signature, as a refusal does,
        is finalized like a completed one."""
        self._set_company_certificate()
        self._sign(self.role_customer)
        with patch.object(
            type(self.request),
            "_pades_render_certificate",
            return_value=make_certificate_pdf(),
        ):
            self.assertTrue(self.request._pades_finalize())
            finalized = self._document()
            self.assertFalse(self.request._pades_finalize(), "finalized once")
        self.assertEqual(self._document(), finalized)
        signatures = self._assert_seals_pass(2)
        self.assertEqual(signatures[-1].field_name, "sign_oca_final_seal")
        self.assertIn("/V", self._fields()["sign_oca_certificate"])
        self.assertNotEqual(self._item_field(self.role_employee)["/F"], 4)
        self.assertEqual(self.request.pades_final_sha256, self.request.current_hash)
        self.assertEqual(self.request.pades_status, "sealed")

    def test_finalize_prepares_unsigned_document(self):
        """A document finalized before any signature is prepared first, so
        the certificate fills its reserved page."""
        self._set_company_certificate()
        with patch.object(
            type(self.request),
            "_pades_render_certificate",
            return_value=make_certificate_pdf(),
        ):
            self.request._pades_finalize()
        self._assert_seals_pass(1)
        self.assertEqual(self._page_count(), self.page_count + 2)
        self.assertIn("/V", self._fields()["sign_oca_certificate"])

    def test_document_sealed_without_form_draws_on_page(self):
        """A document that carries a seal but no form fields cannot be
        prepared any more: the items are drawn on its page, sealed, and the
        status reports the change made after the earlier seal."""
        self._set_company_certificate()
        writer = IncrementalPdfFileWriter(BytesIO(self.pdf), strict=False)
        output = BytesIO()
        signers.PdfSigner(
            signers.PdfSignatureMetadata(field_name="earlier_seal"),
            self.request._pades_get_signer(),
        ).sign_pdf(writer, output=output)
        self.request.sudo().data = base64.b64encode(output.getvalue())
        self._sign(self.role_customer)
        self.assertEqual(len(self._signatures()), 2)
        self.assertIn(self.role_customer.name, self._page_text(1))
        self.assertEqual(self.request.pades_status, "modified")
        self.assertIn("changed beyond form filling", self.request.pades_check_details)

    # Locks and signature fields

    def _complete_with_signatures(self):
        """Return a request whose two signers each signed a name and a
        signature item, completed with its certificate and final seal."""
        self._set_company_certificate()
        request, items = self._new_request(
            {
                self.role_customer: [
                    ("sign_oca.sign_field_name", "Customer name", 10, 4),
                    ("sign_oca.sign_field_signature", SIGNATURE_PNG, 16, 8),
                ],
                self.role_employee: [
                    ("sign_oca.sign_field_name", "Employee name", 40, 4),
                    ("sign_oca.sign_field_signature", SIGNATURE_PNG, 46, 8),
                ],
            }
        )
        request.action_send()
        self.request = request
        with patch.object(
            type(request),
            "_pades_render_certificate",
            return_value=make_certificate_pdf(),
        ):
            for role in (self.role_customer, self.role_employee):
                signer = request.signer_ids.filtered(lambda s, r=role: s.role_id == r)
                signer.action_sign(items[role])
        return request, items

    def _sign_new_revision(self, pdf_bytes, change=None):
        """Return ``pdf_bytes`` with one more sealed revision, in which
        ``change(writer)`` runs first."""
        writer = IncrementalPdfFileWriter(BytesIO(pdf_bytes), strict=False)
        if change:
            change(writer)
        output = BytesIO()
        signers.PdfSigner(
            signers.PdfSignatureMetadata(field_name="later_seal"),
            self.request._pades_get_signer(),
        ).sign_pdf(writer, output=output)
        return output.getvalue()

    def test_signer_field_locks_its_role_fields(self):
        request, items = self._complete_with_signatures()
        customer = request.signer_ids.filtered(
            lambda s: s.role_id == self.role_customer
        )
        name_key = next(iter(items[self.role_customer]))
        field = self._fields()[customer.pades_field_name]
        lock = field["/Lock"]
        self.assertEqual(lock["/Action"], "/Include")
        self.assertEqual(list(lock["/Fields"]), [item_field_name(name_key)])
        self.assertEqual(lock["/P"], 2)

    def test_rewritten_field_breaks_its_seal(self):
        """A field of a signer rewritten in a later sealed revision makes
        that signer's seal report a forbidden change."""
        self._set_company_certificate()
        request, items = self._new_request(
            {
                self.role_customer: [
                    ("sign_oca.sign_field_name", "Customer name", 10, 4),
                    ("sign_oca.sign_field_signature", SIGNATURE_PNG, 16, 8),
                ],
                self.role_employee: [
                    ("sign_oca.sign_field_name", "Employee name", 40, 4),
                ],
            }
        )
        request.action_send()
        self.request = request
        customer = request.signer_ids.filtered(
            lambda s: s.role_id == self.role_customer
        )
        customer.action_sign(items[self.role_customer])
        name_key = next(iter(items[self.role_customer]))

        def change(writer):
            field_ref = request._pades_form_fields(writer)[item_field_name(name_key)]
            field_ref.get_object()["/V"] = generic.pdf_string("Another name")
            writer.mark_update(field_ref)

        tampered = self._sign_new_revision(self._document(), change)
        status = self._validate(self._signatures(tampered)[0])
        self.assertTrue(status.intact)
        self.assertFalse(status.docmdp_ok)

    def test_final_seal_locks_document(self):
        """Nothing may follow the final seal: a signature added afterwards
        is reported by the final seal itself."""
        request, __ = self._complete_with_signatures()
        final_field = self._fields()["sign_oca_final_seal"]
        self.assertEqual(final_field["/Lock"]["/Action"], "/All")
        self.assertEqual(final_field["/Lock"]["/P"], 1)
        later = self._sign_new_revision(self._document())
        status = self._validate(self._signatures(later)[-2])
        self.assertEqual(self._signatures(later)[-2].field_name, "sign_oca_final_seal")
        self.assertFalse(status.docmdp_ok)
        result = request._pades_check_pdf(later)
        self.assertEqual(result["status"], "modified")

    def test_stamp_without_signature_signs_own_field(self):
        """A stamp of a signer that carries no signature image, as a refusal,
        seals the signer's own signature field: none is left empty for a
        later signature."""
        self._set_company_certificate()
        request, items = self._new_request(
            {
                self.role_customer: [
                    ("sign_oca.sign_field_name", "Customer name", 10, 4),
                    ("sign_oca.sign_field_signature", False, 16, 8),
                ],
            }
        )
        request.action_send()
        self.request = request
        customer = request.signer_ids
        name_items = {
            key: item
            for key, item in items[self.role_customer].items()
            if item["field_type"] != "signature"
        }
        pdf = request._stamp_pdf(self._document(), name_items, signer=customer)
        signatures = self._signatures(pdf)
        self.assertEqual(
            [signature.field_name for signature in signatures],
            [customer.pades_field_name],
        )
        self.assertFalse(
            self._paints_image(self._fields(pdf)[customer.pades_field_name])
        )

    def test_role_renamed_after_preparation(self):
        """The signature field of a signer keeps the name it was prepared
        with: renaming its role afterwards changes nothing."""
        self._set_company_certificate()
        role = self.env["sign.oca.role"].create({"name": "Sample approver"})
        self.role_customer = role
        request, items = self._new_request(
            {
                role: [
                    ("sign_oca.sign_field_name", "Approver name", 10, 4),
                    ("sign_oca.sign_field_signature", SIGNATURE_PNG, 16, 8),
                ],
            }
        )
        request.action_send()
        self.request = request
        signer = request.signer_ids
        prepared_name = signer.pades_field_name
        self.assertEqual(prepared_name, request._pades_signer_field_base(signer))
        role.name = "Renamed approver"
        with patch.object(
            type(request),
            "_pades_render_certificate",
            return_value=make_certificate_pdf(),
        ):
            signer.action_sign(items[role])
        signatures = self._assert_seals_pass(2)
        self.assertEqual(signatures[0].field_name, prepared_name)
        self.assertEqual(request.pades_status, "sealed")

    def test_seal_reason_names_signer(self):
        self._set_company_certificate()
        self._sign(self.role_customer)
        reason = str(self._signatures()[0].sig_object["/Reason"])
        self.assertEqual(reason, f"Signed by First signer as {self.role_customer.name}")

    def test_certificate_rendered_to_fit(self):
        """A certificate longer than its reserved pages is rendered again
        at a smaller zoom, which the report reads as the page style."""
        zooms = []

        def render(request):
            zoom = request.env.context.get("sign_oca_pades_certificate_zoom")
            zooms.append(zoom)
            return make_certificate_pdf(pages=2 if zoom and zoom < 0.8 else 4)

        with patch.object(
            type(self.request), "_pades_render_certificate", autospec=True
        ) as mocked:
            mocked.side_effect = render
            certificate = self.request._pades_render_certificate_to_fit(2)
        self.assertEqual(PdfFileReader(BytesIO(certificate)).numPages, 2)
        self.assertIsNone(zooms[0])
        self.assertTrue(all(0.6 <= zoom < 1 for zoom in zooms[1:]))
        certificate_values = self.env["sign.oca.pades.certificate"]
        self.assertFalse(certificate_values._get_page_style())
        self.assertEqual(
            certificate_values.with_context(
                sign_oca_pades_certificate_zoom=0.75
            )._get_page_style(),
            "zoom: 0.750;",
        )
