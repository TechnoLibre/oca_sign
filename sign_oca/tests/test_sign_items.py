# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.exceptions import ValidationError
from odoo.tools import misc

from odoo.addons.base.tests.common import BaseCommon


class TestSignItems(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        cls.role_customer = cls.env.ref("sign_oca.sign_role_customer")
        cls.role_employee = cls.env.ref("sign_oca.sign_role_employee")
        other = cls.env["res.partner"].create({"name": "Other signer"})
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Items test",
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": cls.env.user.partner_id.id,
                            "role_id": cls.role_customer.id,
                        },
                    ),
                    (0, 0, {"partner_id": other.id, "role_id": cls.role_employee.id}),
                ],
            }
        )
        field = cls.env.ref("sign_oca.sign_field_name")
        geometry = {"page": 1, "position_x": 10, "position_y": 10}
        cls.own_item = cls.request.add_item(
            dict(
                geometry,
                field_id=field.id,
                role_id=cls.role_customer.id,
                width=20,
                height=5,
                required=True,
            )
        )
        cls.other_item = cls.request.add_item(
            dict(
                geometry,
                field_id=field.id,
                role_id=cls.role_employee.id,
                width=20,
                height=5,
            )
        )
        cls.request.action_send()
        cls.signer = cls.request.signer_ids.filtered(
            lambda s: s.role_id == cls.role_customer
        )

    def _browser_items(self):
        items = self.signer.get_info()["items"]
        return {key: dict(item) for key, item in items.items()}

    def test_browser_cannot_change_definitions(self):
        items = self._browser_items()
        own = items[str(self.own_item["id"])]
        own.update(value="Signer name", position_x=80, page=1, width=99, role_id=0)
        other = items[str(self.other_item["id"])]
        other["value"] = "Written for someone else"
        self.signer.action_sign(items)
        stored = self.request.signatory_data
        stored_own = stored[str(self.own_item["id"])]
        self.assertEqual(stored_own["value"], "Signer name")
        self.assertEqual(stored_own["position_x"], 10)
        self.assertEqual(stored_own["width"], 20)
        self.assertEqual(stored_own["role_id"], self.role_customer.id)
        self.assertFalse(stored[str(self.other_item["id"])]["value"])

    def test_browser_cannot_drop_required(self):
        items = self._browser_items()
        items[str(self.own_item["id"])].update(value=False, required=False)
        with self.assertRaises(ValidationError):
            self.signer.action_sign(items)

    def test_missing_item_gets_no_value(self):
        prepared = self.signer._prepare_sign_items({})
        self.assertEqual(list(prepared), [str(self.own_item["id"])])
        self.assertFalse(prepared[str(self.own_item["id"])]["value"])

    def test_invalid_value_is_refused(self):
        items = self._browser_items()
        items[str(self.own_item["id"])]["value"] = {"nested": "value"}
        with self.assertRaises(ValidationError):
            self.signer.action_sign(items)
