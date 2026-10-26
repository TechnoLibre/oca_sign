/* Copyright 2026 TechnoLibre
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl). */

import {SignOcaPdfPortal} from "@sign_oca/components/sign_oca_pdf_portal/sign_oca_pdf_portal.esm";
import {markup} from "@odoo/owl";
import {patch} from "@web/core/utils/patch";

/**
 * Shows the steps panel of a tier sign request next to the document. The
 * panel is rendered by the server (``tier_steps_html`` of ``/sign_oca/info``);
 * without it, the page keeps sign_oca's layout.
 */
patch(SignOcaPdfPortal.prototype, {
    async willStart() {
        await super.willStart(...arguments);
        this.tierStepsHtml =
            this.info && this.info.tier_steps_html
                ? markup(this.info.tier_steps_html)
                : false;
    },
});
