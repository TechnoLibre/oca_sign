# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from io import BytesIO
from unittest import SkipTest, mock

from odoo.tests import tagged

from odoo.addons.base_tier_validation.tests.common import CommonTierValidation

from .common import SignOcaTierMixin


@tagged("post_install", "-at_install")
class TestSignOcaTierPades(SignOcaTierMixin, CommonTierValidation):
    """Seals of a tier document when sign_oca_pades is installed: every seal
    passes pyHanko's difference analysis, and a refused document is
    finalized like a completed one."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "pades_final_sha256" not in cls.env["sign.oca.request"]._fields:
            raise SkipTest("sign_oca_pades is not installed")
        cls._setup_sign_oca_tier()
        from odoo.addons.sign_oca_pades.tests.test_sign_oca_pades import (
            make_certificate,
            make_certificate_pdf,
        )

        cls.seal_p12, cls.seal_cert, __ = make_certificate("Tier seal", b"seal")
        cls.env.company.write(
            {
                "sign_oca_pades_pkcs12": base64.b64encode(cls.seal_p12),
                "sign_oca_pades_pkcs12_password": "seal",
                "sign_oca_pades_tsa_url": False,
            }
        )
        cls.startClassPatcher(
            mock.patch.object(
                type(cls.env["sign.oca.request"]),
                "_pades_render_certificate",
                return_value=make_certificate_pdf(pages=2),
            )
        )

    def _assert_seals_pass(self, sign_request, count):
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.diff_analysis import ModificationLevel
        from pyhanko.sign.validation import (
            SignatureCoverageLevel,
            validate_pdf_signature,
        )
        from pyhanko_certvalidator import ValidationContext

        reader = PdfFileReader(BytesIO(base64.b64decode(sign_request.data)))
        signatures = reader.embedded_signatures
        self.assertEqual(len(signatures), count)
        context = ValidationContext(trust_roots=[self.seal_cert], allow_fetching=False)
        for signature in signatures:
            status = validate_pdf_signature(
                signature, signer_validation_context=context
            )
            self.assertTrue(status.bottom_line, signature.field_name)
            self.assertTrue(status.docmdp_ok, signature.field_name)
            self.assertLessEqual(
                status.modification_level,
                ModificationLevel.FORM_FILLING,
                signature.field_name,
            )
        self.assertEqual(
            signatures[-1].evaluate_signature_coverage(),
            SignatureCoverageLevel.ENTIRE_FILE,
        )
        self.assertEqual(signatures[-1].field_name, "sign_oca_final_seal")
        return signatures

    def test_two_tiers_seals_pass_difference_analysis(self):
        record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        self._sign(signer_1, self.reviewer_1, comment="Prices checked\nOn budget")
        self._sign(signer_2, self.reviewer_2, comment="Approved")
        self.assertEqual(record.validation_status, "validated")
        self.assertEqual(sign_request.state, "2_signed")
        self._assert_seals_pass(sign_request, 3)
        self.assertEqual(sign_request.pades_status, "sealed")

    def test_refused_document_is_finalized(self):
        record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        self._sign(signer_1, self.reviewer_1)
        signer_2.with_user(self.reviewer_2).sudo().action_refuse("Over budget")
        self.assertEqual(record.validation_status, "rejected")
        self.assertEqual(sign_request.state, "4_refused")
        signatures = self._assert_seals_pass(sign_request, 3)
        self.assertEqual(
            signatures[1].field_name,
            signer_2.pades_field_name,
            "the refusal seals the signature field of the refused tier, which "
            "no later signature can fill",
        )
        self.assertTrue(
            str(signatures[1].sig_object["/Reason"]).startswith(
                "Refused: Second Reviewer (sign_tier_reviewer_2)"
            )
        )
        self.assertTrue(sign_request.pades_final_sha256)
        self.assertEqual(sign_request.pades_final_sha256, sign_request.current_hash)
        self.assertEqual(sign_request.pades_status, "sealed")

    def test_bypassed_tier_seal_and_certificate(self):
        """A tier approved by bypass is sealed with its status and stamped
        name as reason, and the certificate lists it as approved without a
        signature, not as pending."""
        definition_3 = self._bypass_definition(self.reviewer_1)
        record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        signer_2 = self._signer(sign_request, self.definition_2)
        signer_3 = self._signer(sign_request, definition_3)
        self._sign(signer_1, self.reviewer_1, comment="Fine")
        self.assertTrue(signer_3.tier_bypassed)
        self._sign(signer_2, self.reviewer_2, comment="Agreed")
        self.assertEqual(record.validation_status, "validated")
        self.assertEqual(sign_request.state, "2_signed")
        signatures = self._assert_seals_pass(sign_request, 4)
        reasons = [str(s.sig_object.get("/Reason")) for s in signatures]
        self.assertTrue(
            reasons[0].startswith("Signed by First Reviewer (sign_tier_reviewer_1)"),
            reasons[0],
        )
        # The bypassed tier's own signature field holds its seal.
        self.assertEqual(signatures[1].field_name, signer_3.pades_field_name)
        self.assertTrue(
            reasons[1].startswith(
                "Approved (same reviewer): First Reviewer (sign_tier_reviewer_1)"
            ),
            reasons[1],
        )
        values = self.env["sign.oca.pades.certificate"]._get_values(sign_request)
        self.assertEqual(
            values["signer_status"][signer_3.id], "Approved without signature"
        )
        self.assertEqual(values["signer_counts"]["approved"], 1)
        self.assertEqual(values["signer_counts"]["pending"], 0)
        self.assertEqual(values["signer_counts"]["signed"], 2)

    def test_signed_fields_locked(self):
        """A seal locks the fields of its tier: rewriting the comment of the
        first tier in a later sealed revision is reported."""
        from pyhanko.pdf_utils import generic
        from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign import signers
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko_certvalidator import ValidationContext

        _record, sign_request = self._start()
        signer_1 = self._signer(sign_request, self.definition_1)
        self._sign(signer_1, self.reviewer_1, comment="Prices checked")
        pdf_bytes = base64.b64decode(sign_request.data)
        writer = IncrementalPdfFileWriter(BytesIO(pdf_bytes), strict=False)
        fields = sign_request._pades_form_fields(writer)
        comment_key = next(
            key
            for key, item in sign_request.signatory_data.items()
            if item["role_id"] == signer_1.role_id.id
            and sign_request._sign_oca_tier_item_kind(item) == "comment"
        )
        from odoo.addons.sign_oca_pades.models.pades_form import item_field_name

        field_ref = fields[item_field_name(comment_key)]
        field_ref.get_object()["/V"] = generic.pdf_string("Prices not checked")
        writer.mark_update(field_ref)
        output = BytesIO()
        signers.PdfSigner(
            signers.PdfSignatureMetadata(field_name="tamper"),
            sign_request._pades_get_signer(),
        ).sign_pdf(writer, output=output)
        reader = PdfFileReader(BytesIO(output.getvalue()))
        context = ValidationContext(trust_roots=[self.seal_cert], allow_fetching=False)
        status = validate_pdf_signature(
            reader.embedded_signatures[0], signer_validation_context=context
        )
        self.assertTrue(status.intact)
        self.assertFalse(status.docmdp_ok)
