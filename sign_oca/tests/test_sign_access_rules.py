# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.exceptions import AccessError
from odoo.tests import new_test_user
from odoo.tools import misc

from odoo.addons.base.tests.common import BaseCommon


class TestSignAccessRules(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            cls.data = base64.b64encode(pdf.read())
        cls.role = cls.env.ref("sign_oca.sign_role_customer")
        cls.owner = new_test_user(
            cls.env, login="sign_rules_owner", groups="sign_oca.sign_oca_group_user"
        )
        cls.other_sign_user = new_test_user(
            cls.env, login="sign_rules_other", groups="sign_oca.sign_oca_group_user"
        )
        cls.manager = new_test_user(
            cls.env,
            login="sign_rules_manager",
            groups="sign_oca.sign_oca_group_manager",
        )
        cls.employee = new_test_user(
            cls.env, login="sign_rules_employee", groups="base.group_user"
        )
        cls.signer_employee = new_test_user(
            cls.env, login="sign_rules_signer", groups="base.group_user"
        )
        cls.request = (
            cls.env["sign.oca.request"]
            .with_user(cls.owner)
            .create(
                {
                    "data": cls.data,
                    "name": "Rules test",
                    "signer_ids": [
                        (
                            0,
                            0,
                            {
                                "partner_id": cls.signer_employee.partner_id.id,
                                "role_id": cls.role.id,
                            },
                        )
                    ],
                }
            )
        )
        cls.signer = cls.request.signer_ids

    def test_unrelated_employee_cannot_read(self):
        requests = self.env["sign.oca.request"].with_user(self.employee)
        self.assertFalse(requests.search([("id", "=", self.request.id)]))
        with self.assertRaises(AccessError):
            self.request.with_user(self.employee).check_access("read")

    def test_signer_and_responsible_read(self):
        for user in (self.signer_employee, self.owner):
            self.request.with_user(user).check_access("read")

    def test_follower_reads(self):
        self.request.message_subscribe(partner_ids=self.employee.partner_id.ids)
        self.request.with_user(self.employee).check_access("read")

    def test_other_sign_user_cannot_edit(self):
        signer = self.signer.with_user(self.other_sign_user)
        with self.assertRaises(AccessError):
            signer.write({"partner_id": self.other_sign_user.partner_id.id})
        with self.assertRaises(AccessError):
            signer.unlink()
        with self.assertRaises(AccessError):
            self.request.with_user(self.other_sign_user).write({"name": "Renamed"})

    def test_owner_and_manager_edit(self):
        self.signer.with_user(self.owner).write(
            {"partner_id": self.owner.partner_id.id}
        )
        self.request.with_user(self.manager).write({"name": "Renamed"})
        self.signer.with_user(self.manager).write(
            {"partner_id": self.manager.partner_id.id}
        )
        self.assertEqual(self.signer.partner_id, self.manager.partner_id)
