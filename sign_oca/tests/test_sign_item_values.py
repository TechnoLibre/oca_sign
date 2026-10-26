# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from io import BytesIO

from PIL import Image

from odoo.exceptions import ValidationError
from odoo.tools import misc

from odoo.addons.base.tests.common import BaseCommon


def png_base64(width, height):
    """Return a blank PNG of ``width`` x ``height`` pixels, in base64."""
    stream = BytesIO()
    Image.new("L", (width, height), 255).save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


class TestSignItemValues(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        cls.role = cls.env.ref("sign_oca.sign_role_customer")
        partner = cls.env["res.partner"].create({"name": "Values signer"})
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Values test",
                "signer_ids": [
                    (0, 0, {"partner_id": partner.id, "role_id": cls.role.id})
                ],
            }
        )
        cls.signature = cls.request.add_item(
            {
                "role_id": cls.role.id,
                "field_id": cls.env.ref("sign_oca.sign_field_signature").id,
                "page": 1,
                "position_x": 10,
                "position_y": 10,
                "width": 20,
                "height": 5,
            }
        )
        cls.name_item = cls.request.add_item(
            {
                "role_id": cls.role.id,
                "field_id": cls.env.ref("sign_oca.sign_field_name").id,
                "page": 1,
                "position_x": 10,
                "position_y": 30,
                "width": 20,
                "height": 5,
            }
        )
        cls.request.action_send()
        cls.signer = cls.request.signer_ids

    def _sign(self, signature, name="A name"):
        items = {
            str(self.signature["id"]): dict(self.signature, value=signature),
            str(self.name_item["id"]): dict(self.name_item, value=name),
        }
        return self.signer.action_sign(items)

    def test_signature_not_an_image_refused(self):
        for value in ("not an image", "data:image/png;base64,bm90IGFuIGltYWdl"):
            with self.assertRaises(ValidationError):
                self._sign(value)
        self.assertFalse(self.signer.signed_on)

    def test_signature_too_many_pixels_refused(self):
        with self.assertRaises(ValidationError):
            self._sign(png_base64(4097, 4097))
        self.assertFalse(self.signer.signed_on)

    def test_text_too_long_refused(self):
        with self.assertRaises(ValidationError):
            self._sign(png_base64(2, 2), name="x" * 2001)
        self.assertFalse(self.signer.signed_on)

    def test_image_signature_accepted(self):
        self._sign("data:image/png;base64," + png_base64(40, 10))
        self.assertTrue(self.signer.signed_on)
        self.assertEqual(self.request.state, "2_signed")

    def test_empty_optional_signature_accepted(self):
        self._sign(False)
        self.assertTrue(self.signer.signed_on)

    def test_true_is_no_signature(self):
        """``true`` sent for a signature is no value: a required signature
        stays to fill, an optional one is stored empty."""
        data = self.request.signatory_data
        data[str(self.signature["id"])]["required"] = True
        self.request.sudo().signatory_data = data
        with self.assertRaises(ValidationError):
            self._sign(True)
        self.assertFalse(self.signer.signed_on)
        data[str(self.signature["id"])]["required"] = False
        self.request.sudo().signatory_data = data
        self._sign(True)
        self.assertTrue(self.signer.signed_on)
        stored = self.request.signatory_data[str(self.signature["id"])]
        self.assertIs(stored["value"], False)

    def test_blank_required_text_not_filled(self):
        data = self.request.signatory_data
        data[str(self.name_item["id"])]["required"] = True
        self.request.sudo().signatory_data = data
        with self.assertRaises(ValidationError):
            self._sign(False, name="  \n\t ")
        self.assertFalse(self.signer.signed_on)
