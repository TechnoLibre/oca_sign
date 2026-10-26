# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from hashlib import sha256
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tools import misc

from odoo.addons.base.tests.common import BaseCommon


class TestSignHooks(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            cls.pdf = pdf.read()
        cls.role_customer = cls.env.ref("sign_oca.sign_role_customer")
        cls.role_employee = cls.env.ref("sign_oca.sign_role_employee")
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": base64.b64encode(cls.pdf),
                "name": "Hooks test",
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": cls.env.user.partner_id.id,
                            "role_id": role.id,
                        },
                    )
                    for role in (cls.role_customer, cls.role_employee)
                ],
            }
        )
        field = cls.env.ref("sign_oca.sign_field_name")
        cls.items = {}
        for role in (cls.role_customer, cls.role_employee):
            item = cls.request.add_item(
                {
                    "field_id": field.id,
                    "role_id": role.id,
                    "page": 1,
                    "position_x": 10,
                    "position_y": 10,
                    "width": 20,
                    "height": 5,
                }
            )
            cls.items[role] = {str(item["id"]): dict(item, value="Signer name")}
        cls.signer_model = type(cls.env["sign.oca.request.signer"])
        cls.request_model = type(cls.request)

    def _signer(self, role):
        return self.request.signer_ids.filtered(lambda s: s.role_id == role)

    def test_can_sign_follows_request_state(self):
        signer = self._signer(self.role_customer)
        self.assertFalse(signer._can_sign())
        self.assertFalse(signer.get_info()["role_id"])
        self.request.action_send()
        self.assertTrue(signer._can_sign())
        self.assertEqual(signer.get_info()["role_id"], self.role_customer.id)

    def test_check_can_sign_override_blocks_action_sign(self):
        self.request.action_send()
        signer = self._signer(self.role_customer)
        with patch.object(
            self.signer_model,
            "_check_can_sign",
            autospec=True,
            side_effect=ValidationError("Not your turn"),
        ):
            self.assertFalse(signer._can_sign())
            with self.assertRaises(ValidationError):
                signer.action_sign(self.items[self.role_customer])
        self.assertFalse(signer.signed_on)

    def test_stamp_pdf_result_is_stored(self):
        self.request.action_send()
        signer = self._signer(self.role_customer)
        stamped = self.request._stamp_pdf(
            self.pdf, self.items[self.role_customer], signer=signer
        )
        self.assertTrue(stamped.startswith(b"%PDF"))
        with patch.object(
            self.request_model, "_stamp_pdf", autospec=True, return_value=stamped
        ) as stamp:
            signer.action_sign(self.items[self.role_customer])
        stamp.assert_called_once()
        _request, pdf_bytes, items = stamp.call_args.args
        self.assertEqual(pdf_bytes, self.pdf)
        self.assertEqual(list(items), list(self.items[self.role_customer]))
        self.assertEqual(base64.b64decode(self.request.data), stamped)
        self.assertEqual(signer.signature_hash, sha256(stamped).hexdigest())

    def test_after_sign_and_on_completed_order(self):
        self.request.action_send()
        calls = []
        after_sign = self.signer_model._after_sign
        on_completed = self.request_model._on_completed
        send = self.request_model.action_send_signed_request

        def record(name, method):
            def wrapper(records, *args, **kwargs):
                hashes = (
                    records.mapped("inalterable_hash") if name == "after_sign" else []
                )
                calls.append((name, records.ids, hashes))
                return method(records, *args, **kwargs)

            return wrapper

        with (
            patch.object(
                self.signer_model,
                "_after_sign",
                autospec=True,
                side_effect=record("after_sign", after_sign),
            ),
            patch.object(
                self.request_model,
                "_on_completed",
                autospec=True,
                side_effect=record("on_completed", on_completed),
            ),
            patch.object(
                self.request_model,
                "action_send_signed_request",
                autospec=True,
                side_effect=record("send", send),
            ),
        ):
            first = self._signer(self.role_customer)
            first.action_sign(self.items[self.role_customer])
            self.assertEqual([call[0] for call in calls], ["after_sign", "send"])
            self.assertTrue(calls[0][2][0], "after_sign runs after the hash chain")
            calls.clear()
            last = self._signer(self.role_employee)
            last.action_sign(self.items[self.role_employee])
        self.assertEqual(
            [(call[0], call[1]) for call in calls],
            [
                ("after_sign", last.ids),
                ("on_completed", self.request.ids),
                ("send", self.request.ids),
            ],
        )
        self.assertEqual(self.request.state, "2_signed")
