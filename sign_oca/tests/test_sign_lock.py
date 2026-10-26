# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from unittest.mock import patch

from odoo.exceptions import ValidationError
from odoo.tools import misc

from odoo.addons.base.tests.common import BaseCommon


class TestSignLock(BaseCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        role = cls.env.ref("sign_oca.sign_role_customer")
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Lock test",
                "signer_ids": [
                    (
                        0,
                        0,
                        {"partner_id": cls.env.user.partner_id.id, "role_id": role.id},
                    )
                ],
            }
        )
        cls.item = cls.request.add_item(
            {
                "field_id": cls.env.ref("sign_oca.sign_field_name").id,
                "role_id": role.id,
                "page": 1,
                "position_x": 10,
                "position_y": 10,
                "width": 20,
                "height": 5,
            }
        )
        cls.request.action_send()
        cls.signer = cls.request.signer_ids

    def _items(self):
        return {str(self.item["id"]): dict(self.item, value="Signer name")}

    def test_lock_query_runs(self):
        self.request._lock_for_sign()
        self.signer.action_sign(self._items())
        self.assertEqual(self.request.state, "2_signed")

    def test_signature_committed_while_waiting_is_seen(self):
        """A signature stored by another transaction before the lock is
        granted makes the second one fail, although its cache says
        otherwise."""
        self.assertFalse(self.signer.signed_on)
        request_class = type(self.request)
        lock = request_class._lock_for_sign
        cr = self.env.cr

        def lock_after_concurrent_sign(requests):
            cr.execute(
                "UPDATE sign_oca_request_signer SET signed_on = now() WHERE id = %s",
                [self.signer.id],
            )
            return lock(requests)

        with (
            patch.object(
                request_class,
                "_lock_for_sign",
                autospec=True,
                side_effect=lock_after_concurrent_sign,
            ),
            self.assertRaises(ValidationError),
        ):
            self.signer.action_sign(self._items())
