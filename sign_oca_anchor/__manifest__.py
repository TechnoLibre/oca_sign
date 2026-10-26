# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "Sign Oca Anchor",
    "summary": "Place the fields of a sign request from anchor texts in the PDF",
    "version": "18.0.1.0.0",
    "license": "AGPL-3",
    "author": "TechnoLibre,Odoo Community Association (OCA)",
    "website": "https://github.com/OCA/sign",
    "development_status": "Alpha",
    "depends": ["sign_oca"],
    "external_dependencies": {"python": ["pypdf"]},
    "data": [
        "views/sign_oca_request.xml",
    ],
}
