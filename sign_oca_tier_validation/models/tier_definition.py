# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class TierDefinition(models.Model):
    _inherit = "tier.definition"

    sign_oca_role_id = fields.Many2one(
        comodel_name="sign.oca.role",
        string="Sign Role",
        copy=False,
        ondelete="set null",
        help="Role of this tier in the documents signed for a validation. "
        "Created with the tier's name the first time a document is sent for "
        "signature; the fields of the document are placed for this role.",
    )

    def _sign_oca_get_role(self):
        """Return the ``sign.oca.role`` of this tier, creating it when unset.

        :return: a ``sign.oca.role`` record, as superuser
        """
        self.ensure_one()
        definition = self.sudo()
        if not definition.sign_oca_role_id:
            definition.sign_oca_role_id = (
                self.env["sign.oca.role"].sudo().create({"name": definition.name})
            )
        return definition.sign_oca_role_id
