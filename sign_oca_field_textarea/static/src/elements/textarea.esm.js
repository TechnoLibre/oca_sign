/* Copyright 2026 TechnoLibre
 * License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl). */
/* global Event, window */

import {registry} from "@web/core/registry";
import {renderToString} from "@web/core/utils/render";

/**
 * Signing element of the ``textarea`` field type: a <textarea> that fills
 * its box and scrolls when the text is taller. Enter adds a line, Tab moves
 * to the next field of the signer's role as sign_oca's text element does.
 *
 * The value is stored on every keystroke, so the sign button follows a
 * required item as it is typed. A click anywhere in the box focuses the
 * textarea: sign_oca's navigator clicks the box of a field that holds no
 * <input>.
 *
 * While it has the focus, a textarea whose box is smaller than
 * EXPANDED_MIN_WIDTH x EXPANDED_MIN_HEIGHT pixels, as on a phone where the
 * page is scaled down, grows over the page to that size, kept inside the
 * page, with a larger font (class o_sign_oca_textarea_expanded); it goes
 * back to its box on blur.
 */
const EXPANDED_MIN_WIDTH = 260;
const EXPANDED_MIN_HEIGHT = 112;

const textareaSignOca = {
    change(value, parent, item) {
        item.value = value;
        parent.checkFilledAll();
    },
    expand(box, textarea) {
        if (
            box.offsetWidth >= EXPANDED_MIN_WIDTH &&
            box.offsetHeight >= EXPANDED_MIN_HEIGHT
        ) {
            return;
        }
        const page = box.offsetParent;
        const pageWidth = page ? page.clientWidth : window.innerWidth;
        const width = Math.min(
            Math.max(box.offsetWidth, EXPANDED_MIN_WIDTH),
            pageWidth
        );
        // A box near the right edge of the page grows to its left.
        const left = Math.min(0, pageWidth - box.offsetLeft - width);
        textarea.style.width = `${width}px`;
        textarea.style.height = `${Math.max(box.offsetHeight, EXPANDED_MIN_HEIGHT)}px`;
        textarea.style.left = `${left}px`;
        box.classList.add("o_sign_oca_textarea_expanded");
    },
    collapse(box, textarea) {
        box.classList.remove("o_sign_oca_textarea_expanded");
        textarea.style.width = "";
        textarea.style.height = "";
        textarea.style.left = "";
    },
    focusNext(parent, item, textarea) {
        const nextItems = Object.values(parent.info.items)
            .filter(
                (other) =>
                    other.tabindex > item.tabindex &&
                    other.role_id === parent.info.role_id
            )
            .sort((a, b) => a.tabindex - b.tabindex);
        if (!nextItems.length) {
            return;
        }
        textarea.blur();
        const nextBox = parent.items && parent.items[nextItems[0].id];
        if (nextBox) {
            nextBox.dispatchEvent(new Event("focus_signature"));
        }
    },
    generate(parent, item, signatureItem) {
        const box = signatureItem[0];
        const textarea = $(
            renderToString("sign_oca_field_textarea.sign_iframe_field_textarea", {
                item: item,
            })
        )[0];
        textarea.value = item.value || "";
        box.addEventListener("focus_signature", () => {
            textarea.focus();
        });
        box.addEventListener("click", (ev) => {
            if (ev.target !== textarea) {
                textarea.focus();
            }
        });
        textarea.addEventListener("blur", () => {
            this.collapse(box, textarea);
        });
        textarea.addEventListener("focus", () => {
            this.expand(box, textarea);
            const partnerValue =
                item.default_value && parent.info.partner[item.default_value];
            if (partnerValue && !item.value) {
                textarea.value = partnerValue;
                this.change(partnerValue, parent, item);
            }
        });
        textarea.addEventListener("input", () => {
            this.change(textarea.value, parent, item);
        });
        textarea.addEventListener("keydown", (ev) => {
            if (ev.key !== "Tab") {
                return;
            }
            ev.preventDefault();
            this.focusNext(parent, item, textarea);
        });
        return textarea;
    },
    check(item) {
        return Boolean(item.value && String(item.value).trim());
    },
};
registry.category("sign_oca").add("textarea", textareaSignOca);
export default textareaSignOca;
