/* Copyright 2026 TechnoLibre
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl). */

import {Component, useState} from "@odoo/owl";
import {Dialog} from "@web/core/dialog/dialog";
import {_t} from "@web/core/l10n/translation";

/**
 * Dialog asking the signer why they refuse the document.
 *
 * The confirm button stays disabled while the reason is blank. On confirm it
 * awaits ``props.refuse(reason)`` with the trimmed reason, then closes; the
 * button is disabled while the call runs, and enabled again if it fails.
 */
export class SignOcaRefuseDialog extends Component {
    setup() {
        this.title = _t("Refuse to sign");
        this.state = useState({reason: "", busy: false});
    }
    get canConfirm() {
        return Boolean(this.state.reason.trim()) && !this.state.busy;
    }
    async onConfirm() {
        if (!this.canConfirm) {
            return;
        }
        this.state.busy = true;
        try {
            await this.props.refuse(this.state.reason.trim());
            this.props.close();
        } finally {
            this.state.busy = false;
        }
    }
}
SignOcaRefuseDialog.template = "sign_oca_refuse.SignOcaRefuseDialog";
SignOcaRefuseDialog.components = {Dialog};
SignOcaRefuseDialog.props = {
    close: Function,
    refuse: Function,
};
