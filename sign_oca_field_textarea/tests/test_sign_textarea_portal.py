# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64

from odoo.tests.common import HttpCase, tagged
from odoo.tools import misc


@tagged("post_install", "-at_install")
class TestSignTextareaPortal(HttpCase):
    browser_size = "1366x768"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            data = base64.b64encode(pdf.read())
        role = cls.env.ref("sign_oca.sign_role_customer")
        partner = cls.env["res.partner"].create(
            {"name": "Textarea portal signer", "email": "textarea.signer@example.com"}
        )
        field = cls.env["sign.oca.field"].create(
            {"name": "Remarks", "field_type": "textarea"}
        )
        cls.request = cls.env["sign.oca.request"].create(
            {
                "data": data,
                "name": "Textarea portal test",
                "signer_ids": [(0, 0, {"partner_id": partner.id, "role_id": role.id})],
            }
        )
        cls.item = cls.request.add_item(
            {
                "role_id": role.id,
                "field_id": field.id,
                "page": 1,
                "position_x": 10,
                "position_y": 10,
                "width": 40,
                "height": 10,
                "required": True,
            }
        )
        cls.request.action_send()
        cls.signer = cls.request.signer_ids

    def test_type_two_lines_and_sign(self):
        """Fill a required multi-line field on the signing page and sign.

        The page loads the ``oca_sign.assets_frontend_sign`` bundle, which has
        no tour service, so ``browser_js`` drives it. The field lives in the
        PDF viewer frame. The sign button stays disabled until the field
        holds text; following the action it returns unloads the page, which
        is the success signal.
        """
        code = """
            const frameDocument = () =>
                document.querySelector("iframe.o_sign_oca_iframe").contentDocument;
            const wait = (find) => new Promise((resolve, reject) => {
                const start = Date.now();
                const check = () => {
                    const el = find();
                    if (el) {
                        resolve(el);
                    } else if (Date.now() - start > 20000) {
                        reject(new Error("Element not found"));
                    } else {
                        setTimeout(check, 100);
                    }
                };
                check();
            });
            window.addEventListener("beforeunload", () => {
                console.log("test successful");
            });
            (async () => {
                const textarea = await wait(() => frameDocument().querySelector(
                    "[data-field-type='textarea'] textarea.o_sign_oca_textarea"
                ));
                const button = document.querySelector("#sign_oca_button");
                if (!button.disabled) {
                    throw new Error("Sign enabled with an empty required field");
                }
                // A click on the box, as the navigator does, focuses the field.
                textarea.parentElement.click();
                if (frameDocument().activeElement !== textarea) {
                    throw new Error("The box click does not focus the field");
                }
                textarea.value = "First line";
                textarea.dispatchEvent(new Event("input", {bubbles: true}));
                const enter = new KeyboardEvent("keydown", {
                    key: "Enter", bubbles: true, cancelable: true,
                });
                textarea.dispatchEvent(enter);
                if (enter.defaultPrevented) {
                    throw new Error("Enter does not add a line");
                }
                textarea.value += "\\nSecond line";
                textarea.dispatchEvent(new Event("input", {bubbles: true}));
                (await wait(() => {
                    const el = document.querySelector("#sign_oca_button");
                    return el && !el.disabled && el;
                })).click();
            })().catch((error) => console.error(error.message));
            undefined;
        """
        self.browser_js(
            self.signer.access_url,
            code,
            ready="!!document.querySelector('iframe.o_sign_oca_iframe')",
        )
        self.signer.invalidate_recordset()
        self.request.invalidate_recordset()
        self.assertTrue(self.signer.signed_on)
        stored = self.request.signatory_data[str(self.item["id"])]
        self.assertEqual(stored["value"], "First line\nSecond line")
