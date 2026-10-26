# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Sign Oca Field Textarea",
    "summary": "Multi-line text fields in sign_oca documents",
    "version": "18.0.1.0.0",
    "license": "AGPL-3",
    "author": "TechnoLibre,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/sign",
    "development_status": "Alpha",
    "depends": ["sign_oca"],
    "assets": {
        "web.assets_backend": [
            "sign_oca_field_textarea/static/src/elements/*.xml",
            "sign_oca_field_textarea/static/src/elements/*.esm.js",
        ],
        "oca_sign.assets_frontend_sign": [
            "sign_oca_field_textarea/static/src/elements/*.xml",
            "sign_oca_field_textarea/static/src/elements/*.esm.js",
        ],
        "sign_oca.sign_assets": [
            "sign_oca_field_textarea/static/src/scss/sign_textarea.scss",
        ],
    },
}
