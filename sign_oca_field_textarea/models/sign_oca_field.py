# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class SignOcaField(models.Model):
    _inherit = "sign.oca.field"

    # Listed right after "text"; removing the module turns these fields back
    # into one-line texts.
    field_type = fields.Selection(
        selection_add=[("text",), ("textarea", "Multi-line Text"), ("signature",)],
        ondelete={"textarea": "set text"},
    )
