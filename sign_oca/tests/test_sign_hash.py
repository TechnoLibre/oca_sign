# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from hashlib import sha256

from odoo.tools import misc

from odoo.addons.base.tests.common import BaseCommon


class TestSignHash(BaseCommon):
    def test_signature_hash_is_sha256(self):
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        role = self.env.ref("sign_oca.sign_role_customer")
        request = self.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Hash test",
                "signer_ids": [
                    (
                        0,
                        0,
                        {"partner_id": self.env.user.partner_id.id, "role_id": role.id},
                    )
                ],
            }
        )
        item = request.add_item(
            {
                "field_id": self.env.ref("sign_oca.sign_field_name").id,
                "role_id": role.id,
                "page": 1,
                "position_x": 10,
                "position_y": 10,
                "width": 20,
                "height": 5,
            }
        )
        request.action_send()
        signer = request.signer_ids
        signer.action_sign({str(item["id"]): dict(item, value="Signer name")})
        expected = sha256(base64.b64decode(request.data)).hexdigest()
        self.assertEqual(signer.signature_hash, expected)
        self.assertEqual(request.current_hash, expected)
