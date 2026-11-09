/* Copyright 2025 Kencove - Mohamed Alkobrosli
   License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl). */
import {registry} from "@web/core/registry";

registry.category("web_tour.tours").add("test_sign_tour", {
    url: "/my",
    steps: () => [
        {
            trigger: "a:contains('Your Documents to be Signed')",
        },
    ],
});

registry.category("web_tour.tours").add("test_sign_doc_tour", {
    url: "/my/sign_requests",
    steps: () => [
        {
            trigger: "td a",
        },
    ],
});
