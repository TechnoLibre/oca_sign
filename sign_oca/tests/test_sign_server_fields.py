# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import new_test_user
from odoo.tools import misc

from odoo.addons.base.tests.common import BaseCommon


class TestSignServerFields(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            cls.data = base64.b64encode(pdf.read())
        cls.role = cls.env.ref("sign_oca.sign_role_customer")
        cls.owner = new_test_user(
            cls.env, login="sign_fields_owner", groups="sign_oca.sign_oca_group_user"
        )
        cls.signer_user = new_test_user(
            cls.env, login="sign_fields_signer", groups="sign_oca.sign_oca_group_user"
        )
        cls.other_user = new_test_user(
            cls.env, login="sign_fields_other", groups="sign_oca.sign_oca_group_user"
        )
        cls.request = (
            cls.env["sign.oca.request"]
            .with_user(cls.owner)
            .create(
                {
                    "data": cls.data,
                    "name": "Server fields test",
                    "signer_ids": [
                        Command.create(
                            {
                                "partner_id": cls.signer_user.partner_id.id,
                                "role_id": cls.role.id,
                            }
                        )
                    ],
                }
            )
        )
        cls.request.add_item(
            {
                "role_id": cls.role.id,
                "field_id": cls.env.ref("sign_oca.sign_field_name").id,
                "page": 1,
                "position_x": 10,
                "position_y": 10,
                "width": 10,
                "height": 10,
            }
        )
        cls.signer = cls.request.signer_ids

    def _items(self, value):
        return {
            key: dict(item, value=value)
            for key, item in self.request.signatory_data.items()
        }

    def test_server_fields_never_written_by_users(self):
        signer = self.signer.with_user(self.owner)
        for vals in (
            {"signed_on": fields.Datetime.now()},
            {"signature_hash": "0" * 64},
            {"inalterable_hash": "0" * 64},
        ):
            with self.assertRaises(UserError):
                signer.write(vals)
        with self.assertRaises(UserError):
            self.request.with_user(self.owner).write({"current_hash": "0" * 64})

    def test_draft_stays_editable(self):
        self.request.with_user(self.owner).write({"name": "Renamed draft"})
        self.signer.with_user(self.owner).write(
            {"partner_id": self.owner.partner_id.id}
        )
        self.assertEqual(self.signer.partner_id, self.owner.partner_id)

    def test_sent_request_locked(self):
        self.request.with_user(self.owner).action_send()
        sign_request = self.request.with_user(self.owner)
        with self.assertRaises(UserError):
            sign_request.write({"data": self.data})
        with self.assertRaises(UserError):
            sign_request.write({"signatory_data": {}})
        with self.assertRaises(UserError):
            self.signer.with_user(self.owner).write(
                {"partner_id": self.owner.partner_id.id}
            )
        with self.assertRaises(UserError):
            self.signer.with_user(self.owner).unlink()
        with self.assertRaises(UserError):
            self.env["sign.oca.request.signer"].with_user(self.owner).create(
                {
                    "request_id": self.request.id,
                    "partner_id": self.owner.partner_id.id,
                    "role_id": self.role.id,
                }
            )
        for state in ("2_signed", "1_draft"):
            with self.assertRaises(UserError):
                sign_request.write({"state": state})
        sign_request.write({"name": "Renamed"})
        sign_request.cancel()
        self.assertEqual(self.request.state, "3_cancel")

    def test_only_the_signer_signs_through_rpc(self):
        self.request.with_user(self.owner).action_send()
        # A follower reads the signer, which does not let them sign it.
        self.request.message_subscribe(partner_ids=self.other_user.partner_id.ids)
        with self.assertRaises(ValidationError):
            self.signer.with_user(self.other_user).action_sign(self._items("Forged"))
        self.assertFalse(self.signer.signed_on)
        self.signer.with_user(self.signer_user).action_sign(self._items("My name"))
        self.assertTrue(self.signer.signed_on)
        self.assertEqual(self.request.state, "2_signed")
        self.assertTrue(self.signer.signature_hash)
