# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class SignOcaTemplate(models.Model):
    _inherit = "sign.oca.template"

    require_login = fields.Boolean(
        help="Requests created from this template require each signer to log "
        "in with a user linked to the signer's contact before they can open "
        "or sign the document.",
    )
