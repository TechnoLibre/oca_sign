# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Sign Oca PAdES",
    "summary": "Seal signed documents with PAdES signatures and a certificate page",
    "version": "18.0.1.0.0",
    "category": "Sign",
    "license": "AGPL-3",
    "author": "TechnoLibre,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/sign",
    "development_status": "Alpha",
    "depends": ["sign_oca"],
    "external_dependencies": {"python": ["pyhanko"]},
    "data": [
        "views/res_config_settings_views.xml",
        "views/sign_oca_request_views.xml",
        "reports/signature_certificate.xml",
    ],
}
