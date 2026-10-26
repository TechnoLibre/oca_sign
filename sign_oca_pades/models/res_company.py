# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    sign_oca_pades_pkcs12 = fields.Binary(
        string="Sealing Certificate (PKCS#12)",
        attachment=True,
        groups="base.group_system",
        help="PKCS#12 file (.p12 or .pfx) holding the key and certificate "
        "used to seal signed documents. The odoo.conf key "
        "sign_oca_pades_pkcs12_path takes precedence over it.",
    )
    sign_oca_pades_pkcs12_filename = fields.Char(
        string="Sealing Certificate File Name",
        groups="base.group_system",
    )
    sign_oca_pades_pkcs12_password = fields.Char(
        string="Sealing Certificate Password",
        groups="base.group_system",
    )
    sign_oca_pades_tsa_url = fields.Char(
        string="Timestamp Authority URL",
        groups="base.group_system",
        help="RFC 3161 timestamp authority that timestamps each seal. "
        "Leave empty to seal without timestamp.",
    )
