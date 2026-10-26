# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Sign Oca Login",
    "summary": "Require signers to log in before they open or sign a document",
    "version": "18.0.1.0.0",
    "license": "AGPL-3",
    "author": "TechnoLibre,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/sign",
    "development_status": "Alpha",
    "depends": ["sign_oca"],
    "data": [
        "views/sign_oca_template.xml",
        "views/sign_oca_request.xml",
    ],
}
