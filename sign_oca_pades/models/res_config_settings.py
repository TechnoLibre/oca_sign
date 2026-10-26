# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models
from odoo.tools import config


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    sign_oca_pades_pkcs12 = fields.Binary(
        related="company_id.sign_oca_pades_pkcs12", readonly=False
    )
    sign_oca_pades_pkcs12_filename = fields.Char(
        related="company_id.sign_oca_pades_pkcs12_filename", readonly=False
    )
    sign_oca_pades_pkcs12_password = fields.Char(
        related="company_id.sign_oca_pades_pkcs12_password", readonly=False
    )
    sign_oca_pades_tsa_url = fields.Char(
        related="company_id.sign_oca_pades_tsa_url", readonly=False
    )
    sign_oca_pades_server_certificate = fields.Boolean(
        string="Server Sealing Certificate",
        compute="_compute_sign_oca_pades_server_certificate",
        help="The server configuration file names a sealing certificate, "
        "which replaces the one of the company.",
    )

    def _compute_sign_oca_pades_server_certificate(self):
        server_certificate = bool(config.get("sign_oca_pades_pkcs12_path"))
        for record in self:
            record.sign_oca_pades_server_certificate = server_certificate
