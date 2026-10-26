# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Sign Oca Refuse",
    "summary": """
        Let a signer refuse a sign request, with a reason""",
    "version": "18.0.1.0.0",
    "license": "AGPL-3",
    "author": "TechnoLibre,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/sign",
    "development_status": "Alpha",
    "depends": ["sign_oca"],
    "data": [
        "views/sign_oca_request.xml",
        "views/sign_portal_oca_templates.xml",
    ],
    "assets": {
        "oca_sign.assets_frontend_sign": [
            "sign_oca_refuse/static/src/components/**/*.xml",
            "sign_oca_refuse/static/src/components/**/*.esm.js",
        ],
    },
}
