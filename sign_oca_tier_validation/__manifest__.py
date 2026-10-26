# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Sign Oca Tier Validation",
    "summary": "Sign the tiers of a validation in a sign_oca document",
    "version": "18.0.1.0.0",
    "license": "AGPL-3",
    "author": "TechnoLibre,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/sign",
    "development_status": "Alpha",
    "depends": [
        "base_tier_validation",
        "sign_oca",
        "sign_oca_field_textarea",
        "sign_oca_login",
        "sign_oca_refuse",
    ],
    "data": [
        "security/security.xml",
        "data/sign_oca_field_data.xml",
        "templates/tier_validation_templates.xml",
        "views/portal_templates.xml",
        "views/sign_oca_request_views.xml",
        "views/tier_definition_views.xml",
    ],
    "assets": {
        "oca_sign.assets_frontend_sign": [
            "sign_oca_tier_validation/static/src/scss/tier_steps.scss",
            "sign_oca_tier_validation/static/src/components/**/*.xml",
            "sign_oca_tier_validation/static/src/components/**/*.esm.js",
        ],
    },
}
