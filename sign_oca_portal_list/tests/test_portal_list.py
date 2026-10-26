# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import base64
from datetime import datetime
from unittest.mock import patch

from lxml import etree
from werkzeug.urls import url_encode

from odoo.tests.common import HttpCase, new_test_user, tagged
from odoo.tools import misc

from odoo.addons.portal.controllers.portal import CustomerPortal


@tagged("post_install", "-at_install")
class TestSignPortalList(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with open(misc.file_path("sign_oca/tests/empty.pdf"), "rb") as pdf:
            cls.data = base64.b64encode(pdf.read())
        cls.portal_user = new_test_user(
            cls.env,
            login="sign_list_portal",
            password="sign_list_portal",
            groups="base.group_portal",
            tz="UTC",
        )
        cls.sender_one = new_test_user(
            cls.env, login="sign_list_sender_one", name="Sender One"
        )
        cls.sender_two = new_test_user(
            cls.env, login="sign_list_sender_two", name="Sender Two"
        )
        cls.record_alpha = cls.env["res.partner"].create({"name": "Sample Store"})
        cls.record_beta = cls.env["res.partner"].create({"name": "Sample Workshop"})
        cls.alpha = cls._create_request(
            "Alpha contract",
            cls.sender_one,
            cls.record_alpha,
            datetime(2024, 3, 5, 10, 11, 12),
        )
        cls.beta = cls._create_request(
            "Beta agreement",
            cls.sender_two,
            cls.record_beta,
            datetime(2025, 7, 20, 8, 9, 10),
        )
        cls.gamma = cls._create_request(
            "Gamma order",
            cls.sender_one,
            False,
            datetime(2025, 7, 21, 14, 15, 16),
            state="2_signed",
        )
        cls.gamma.signer_ids.signed_on = datetime(2025, 7, 22, 9, 30, 45)

    @classmethod
    def _create_request(cls, name, sender, record, sent_on, state="0_sent"):
        sign_request = cls.env["sign.oca.request"].create(
            {
                "data": cls.data,
                "name": name,
                "user_id": sender.id,
                "record_ref": record and f"{record._name},{record.id}",
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": cls.portal_user.partner_id.id,
                            "role_id": cls.env.ref("sign_oca.sign_role_customer").id,
                        },
                    )
                ],
            }
        )
        sign_request.state = state
        cls.env.cr.execute(
            "UPDATE sign_oca_request_signer SET create_date = %s WHERE id = %s",
            (sent_on, sign_request.signer_ids.id),
        )
        sign_request.signer_ids.invalidate_recordset(["create_date"])
        return sign_request

    def _list(self, **params):
        self.authenticate("sign_list_portal", "sign_list_portal")
        response = self.url_open("/my/sign_requests?" + url_encode(params))
        self.assertEqual(response.status_code, 200)
        return etree.fromstring(response.content, etree.HTMLParser())

    def _names(self, page):
        return [
            " ".join(" ".join(td.itertext()).split())
            for td in page.xpath("//table//tr/td[1]")
        ]

    def _documents(self, page):
        names = self._names(page)
        return [
            name
            for name in ("Alpha contract", "Beta agreement", "Gamma order")
            if any(name in line for line in names)
        ]

    def _group_labels(self, page):
        return [
            " ".join(th.xpath("./span[1]")[0].itertext())
            for th in page.xpath("//tr[contains(@class, 'o_sign_oca_portal_group')]/th")
        ]

    def test_dates_to_the_second(self):
        text = etree.tostring(self._list(), encoding=str)
        self.assertIn("2024-03-05 10:11:12", text)
        self.assertIn("2025-07-21 14:15:16", text)
        self.assertIn("2025-07-22 09:30:45", text)

    def test_sender_and_related_record_columns(self):
        page = self._list(groupby="none")
        rows = {
            " ".join(" ".join(row.xpath("./td[1]")[0].itertext()).split()): [
                " ".join(td.itertext()).strip() for td in row.xpath("./td")
            ]
            for row in page.xpath("//table//tr[td]")
        }
        alpha = next(cells for name, cells in rows.items() if "Alpha" in name)
        self.assertEqual(alpha[1], "Sample Store")
        self.assertEqual(alpha[2], "Sender One")
        gamma = next(cells for name, cells in rows.items() if "Gamma" in name)
        self.assertEqual(gamma[1], "")

    def test_record_named_like_document(self):
        """A related record named like its request is not repeated."""
        record = self.env["res.partner"].create({"name": "Epsilon order"})
        self._create_request(
            "Epsilon order", self.sender_two, record, datetime(2025, 8, 1, 9, 0, 0)
        )
        page = self._list(groupby="none")
        row = next(
            row
            for row in page.xpath("//table//tr[td]")
            if "Epsilon" in " ".join(row.xpath("./td[1]")[0].itertext())
        )
        self.assertEqual(" ".join(row.xpath("./td[2]")[0].itertext()).strip(), "")
        self.assertEqual(
            " ".join(row.xpath("./td[1]")[0].itertext()).count("Epsilon order"), 1
        )

    def test_narrow_screen_columns(self):
        """Under the md breakpoint only the document and its state show; the
        line under the document gives the sent date and the sender."""
        page = self._list(groupby="none")
        headers = page.xpath("//table/thead/tr/th")
        visible = [
            " ".join(th.itertext()).strip()
            for th in headers
            if "d-none" not in (th.get("class") or "").split()
        ]
        self.assertEqual(visible, ["Document", "State"])
        row = next(
            row
            for row in page.xpath("//table//tr[td]")
            if "Alpha" in " ".join(row.xpath("./td[1]")[0].itertext())
        )
        details = " ".join(
            " ".join(
                row.xpath(".//div[contains(@class, 'o_sign_oca_portal_details')]")[
                    0
                ].itertext()
            ).split()
        )
        self.assertIn("Sample Store", details)
        self.assertIn("2024-03-05 10:11", details)
        self.assertIn("Sender One", details)

    def test_list_in_french(self):
        """The list renders for a user whose language translates the
        templates it extends."""
        self.env["res.lang"]._activate_lang("fr_CA")
        self.env["ir.module.module"]._load_module_terms(
            ["sign_oca", "sign_oca_portal_list"], ["fr_CA"]
        )
        self.portal_user.lang = "fr_CA"
        page = self._list(groupby="none")
        headers = [
            " ".join(th.itertext()).strip() for th in page.xpath("//table/thead/tr/th")
        ]
        self.assertEqual(len(headers), 6)
        self.assertIn("État", headers)

    def test_filter_labels(self):
        page = self._list()
        labels = [
            " ".join(a.itertext()).strip()
            for a in page.xpath("//a[contains(@href, 'filterby=')]")
        ]
        self.assertIn("Sent", labels)
        self.assertNotIn("sent", labels)

    def test_search_document_name(self):
        page = self._list(search_in="name", search="beta")
        self.assertEqual(self._documents(page), ["Beta agreement"])

    def test_search_related_record(self):
        page = self._list(search_in="record", search="workshop")
        self.assertEqual(self._documents(page), ["Beta agreement"])
        page = self._list(search_in="name", search="workshop")
        self.assertEqual(self._documents(page), [])
        page = self._list(search_in="record", search="contract")
        self.assertEqual(self._documents(page), [])

    def test_search_all(self):
        page = self._list(search="store")
        self.assertEqual(self._documents(page), ["Alpha contract"])
        page = self._list(search="gamma")
        self.assertEqual(self._documents(page), ["Gamma order"])

    def test_search_record_of_another_signer(self):
        """Only the related records of the user's own requests are searched."""
        other = self.env["res.partner"].create({"name": "Other signer"})
        self.env["sign.oca.request"].create(
            {
                "data": self.data,
                "name": "Delta lease",
                "state": "0_sent",
                "record_ref": f"res.partner,{self.record_beta.id}",
                "signer_ids": [
                    (
                        0,
                        0,
                        {
                            "partner_id": other.id,
                            "role_id": self.env.ref("sign_oca.sign_role_customer").id,
                        },
                    )
                ],
            }
        )
        page = self._list(search_in="record", search="workshop")
        self.assertNotIn("Delta lease", " ".join(self._names(page)))

    def test_group_by(self):
        expected = {
            "document": ["Alpha contract", "Beta agreement", "Gamma order"],
            "state": ["Sent", "Signed"],
            "sender": ["Sender One", "Sender Two"],
            "year": ["2025", "2024"],
            "month": ["July 2025", "March 2024"],
            "day": ["07/21/2025", "07/20/2025", "03/05/2024"],
        }
        for groupby, labels in expected.items():
            page = self._list(groupby=groupby)
            self.assertEqual(self._group_labels(page), labels, groupby)
            self.assertEqual(len(page.xpath("//table//tr[td]")), 3, groupby)

    def test_group_by_keeps_sorting(self):
        page = self._list(groupby="year", sortby="reverse_date")
        self.assertEqual(self._group_labels(page), ["2024", "2025"])

    def test_group_by_none_and_unknown_values(self):
        for params in (
            {},
            {"groupby": "none"},
            {"groupby": "unknown"},
            {"sortby": "unknown"},
            {"filterby": "unknown"},
            {"search_in": "unknown", "search": "alpha"},
        ):
            page = self._list(**params)
            self.assertFalse(self._group_labels(page), params)
            self.assertTrue(page.xpath("//table//tr[td]"), params)

    def test_filter_still_applies(self):
        page = self._list(filterby="signed", groupby="sender")
        self.assertEqual(self._documents(page), ["Gamma order"])
        self.assertEqual(self._group_labels(page), ["Sender One"])

    def test_groups_do_not_split_across_pages(self):
        with patch.object(CustomerPortal, "_items_per_page", 2):
            page = self._list(groupby="sender", sortby="date")
            self.assertEqual(self._group_labels(page), ["Sender One"])
            self.assertEqual(self._documents(page), ["Alpha contract", "Gamma order"])
            links = page.xpath("//ul[contains(@class, 'pagination')]//a/@href")
            page_two = [link for link in links if "/page/2" in link]
            self.assertTrue(page_two)
            self.assertIn("groupby=sender", page_two[0])
            self.assertIn("sortby=date", page_two[0])
            self.authenticate("sign_list_portal", "sign_list_portal")
            response = self.url_open(page_two[0])
            page = etree.fromstring(response.content, etree.HTMLParser())
            self.assertEqual(self._group_labels(page), ["Sender Two"])

    def test_home_tile_always_shown(self):
        self.env["sign.oca.request"].search([]).write({"state": "3_cancel"})
        self.authenticate("sign_list_portal", "sign_list_portal")
        home = etree.fromstring(self.url_open("/my").content, etree.HTMLParser())
        cards = home.xpath(
            "//div[contains(@class, 'o_portal_index_card')]"
            "[.//a[@href='/my/sign_requests']]"
        )
        self.assertEqual(len(cards), 1)
        self.assertNotIn("d-none", cards[0].get("class").split())
        # In a category row, as the cards of the other modules.
        self.assertTrue(cards[0].xpath("ancestor::div[@id='portal_client_category']"))
        counters = self.make_jsonrpc_request(
            "/my/counters", {"counters": ["sign_oca_count"]}
        )
        self.assertEqual(counters["sign_oca_count"], 0)

    def test_home_counter(self):
        self.authenticate("sign_list_portal", "sign_list_portal")
        counters = self.make_jsonrpc_request(
            "/my/counters", {"counters": ["sign_oca_count"]}
        )
        self.assertEqual(counters["sign_oca_count"], 3)
