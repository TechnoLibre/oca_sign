/* global window */
/* Copyright 2026 TechnoLibre
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl). */

import {SignOcaPdfPortal} from "@sign_oca/components/sign_oca_pdf_portal/sign_oca_pdf_portal.esm";
import {SignOcaRefuseDialog} from "../sign_oca_refuse_dialog/sign_oca_refuse_dialog.esm";
import {patch} from "@web/core/utils/patch";

/**
 * Adds the refusal to the portal signing page. The Refuse button shows when
 * ``info.can_refuse`` (from ``/sign_oca/info``) is true; it opens
 * ``SignOcaRefuseDialog``, whose reason is posted to ``/sign_oca/refuse``.
 * The page then follows the returned ``ir.actions.act_url``, as after a
 * signature.
 */
patch(SignOcaPdfPortal.prototype, {
    _onClickRefuse() {
        this.dialogService.add(SignOcaRefuseDialog, {
            refuse: (reason) => this.refuseOca(reason),
        });
    },
    async refuseOca(reason) {
        const action = await this.rpc(
            "/sign_oca/refuse/" + this.signer_id + "/" + this.access_token,
            {reason: reason}
        );
        if (action && action.type === "ir.actions.act_url") {
            window.location = action.url;
        } else {
            window.location.reload();
        }
    },
});
