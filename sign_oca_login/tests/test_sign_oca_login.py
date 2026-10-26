# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.exceptions import UserError, ValidationError
from odoo.tests import new_test_user
from odoo.tools import misc

from odoo.addons.base.tests.common import BaseCommon


class TestSignOcaLogin(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            cls.data = base64.b64encode(pdf.read())
        cls.role = cls.env.ref("sign_oca.sign_role_customer")
        cls.template = cls.env["sign.oca.template"].create(
            {
                "data": cls.data,
                "name": "Login template",
                "filename": "empty.pdf",
                "require_login": True,
            }
        )
        cls.env["sign.oca.template.item"].create(
            {
                "template_id": cls.template.id,
                "role_id": cls.role.id,
                "field_id": cls.env.ref("sign_oca.sign_field_name").id,
                "page": 1,
                "position_x": 10,
                "position_y": 10,
                "width": 20,
                "height": 5,
            }
        )
        cls.signer_user = new_test_user(
            cls.env, login="sign_login_signer", groups="base.group_portal"
        )
        cls.other_user = new_test_user(
            cls.env, login="sign_login_other", groups="base.group_portal"
        )
        cls.company_partner = cls.env["res.partner"].create(
            {"name": "Login company", "is_company": True}
        )
        cls.company_user = new_test_user(
            cls.env,
            login="sign_login_company",
            groups="base.group_portal",
            partner_id=cls.company_partner.id,
        )

    def _create_request(self, partner, **vals):
        values = {
            "data": self.data,
            "name": "Login request",
            "signer_ids": [(0, 0, {"partner_id": partner.id, "role_id": self.role.id})],
        }
        values.update(vals)
        return self.env["sign.oca.request"].create(values)

    def _sign_as(self, signer, user):
        """Sign as ``user`` the way the portal does: superuser rights, with
        ``user`` as the environment user."""
        return signer.with_user(user).sudo().action_sign({})

    def test_create_copies_template_flag(self):
        request = self._create_request(
            self.signer_user.partner_id, template_id=self.template.id
        )
        self.assertTrue(request.require_login)
        self.template.require_login = False
        request = self._create_request(
            self.signer_user.partner_id, template_id=self.template.id
        )
        self.assertFalse(request.require_login)

    def test_explicit_value_wins_over_template(self):
        request = self._create_request(
            self.signer_user.partner_id,
            template_id=self.template.id,
            require_login=False,
        )
        self.assertFalse(request.require_login)

    def test_template_change_keeps_existing_requests(self):
        request = self._create_request(
            self.signer_user.partner_id, template_id=self.template.id
        )
        self.template.require_login = False
        self.assertTrue(request.require_login)

    def test_generate_wizard_copies_template_flag(self):
        wizard = self.env["sign.oca.template.generate"].create(
            {
                "template_id": self.template.id,
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "role_id": self.role.id,
                            "partner_id": self.signer_user.partner_id.id,
                        },
                    )
                ],
            }
        )
        request = wizard._generate()
        self.assertTrue(request.require_login)

    def test_generate_from_record_copies_template_flag(self):
        vals = self.template._prepare_sign_oca_request_vals_from_record(
            self.signer_user.partner_id
        )
        request = self.env["sign.oca.request"].create(vals)
        self.assertTrue(request.require_login)

    def test_flag_editable_in_draft_only(self):
        request = self._create_request(self.signer_user.partner_id)
        self.assertFalse(request.require_login)
        request.require_login = True
        request.action_send()
        request.require_login = True
        with self.assertRaises(UserError):
            request.require_login = False
        self.assertTrue(request.require_login)

    def test_signer_user_signs(self):
        request = self._create_request(self.signer_user.partner_id, require_login=True)
        request.action_send()
        self.assertTrue(
            request.signer_ids.with_user(self.signer_user).sudo()._can_sign()
        )
        self._sign_as(request.signer_ids, self.signer_user)
        self.assertTrue(request.signer_ids.signed_on)
        self.assertEqual(request.state, "2_signed")

    def test_other_user_refused(self):
        request = self._create_request(self.signer_user.partner_id, require_login=True)
        request.action_send()
        signer = request.signer_ids
        as_other = signer.with_user(self.other_user).sudo()
        self.assertFalse(as_other._can_sign())
        self.assertFalse(as_other.get_info()["role_id"])
        with self.assertRaises(ValidationError):
            self._sign_as(signer, self.other_user)
        self.assertFalse(signer.signed_on)

    def test_public_user_refused(self):
        request = self._create_request(self.signer_user.partner_id, require_login=True)
        request.action_send()
        with self.assertRaises(ValidationError):
            self._sign_as(request.signer_ids, self.env.ref("base.public_user"))

    def test_parent_partner_user_signs_for_contact(self):
        contact = self.env["res.partner"].create(
            {"name": "Login contact", "parent_id": self.company_partner.id}
        )
        request = self._create_request(contact, require_login=True)
        request.action_send()
        self._sign_as(request.signer_ids, self.company_user)
        self.assertTrue(request.signer_ids.signed_on)

    def test_contact_user_refused_for_parent(self):
        contact_user = new_test_user(
            self.env,
            login="sign_login_contact",
            groups="base.group_portal",
            parent_id=self.company_partner.id,
        )
        request = self._create_request(self.company_partner, require_login=True)
        request.action_send()
        with self.assertRaises(ValidationError):
            self._sign_as(request.signer_ids, contact_user)

    def test_flag_off_any_user_signs(self):
        request = self._create_request(self.signer_user.partner_id)
        request.action_send()
        self._sign_as(request.signer_ids, self.other_user)
        self.assertTrue(request.signer_ids.signed_on)
