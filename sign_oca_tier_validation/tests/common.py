# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from io import BytesIO
from unittest import mock

from reportlab.pdfgen import canvas

from odoo import Command
from odoo.tests import new_test_user

# A 1x1 PNG, enough for reportlab to draw a signature.
SIGNATURE_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
TIER_KINDS = ("signature", "name", "date", "status", "comment")


def anchors_pdf():
    """Return a one-page PDF printing the anchors of two tiers."""
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(595, 842))
    top = 780
    for tier in (1, 2):
        for kind in TIER_KINDS:
            pdf.drawString(50, top, f"[[sig:tier{tier}:{kind}]]")
            top -= 40
    pdf.save()
    return stream.getvalue()


def add_tier_items(record, sign_request):
    """``_sign_oca_prepare_items`` of the tests: one field of each kind per
    tier, placed by code with its ``tier_item`` key."""
    position_y = 5
    for signer in sign_request.signer_ids.sorted("id"):
        for kind, field in sign_request._sign_oca_tier_item_fields().items():
            sign_request.add_item(
                {
                    "field_id": field.id,
                    "role_id": signer.role_id.id,
                    "page": 1,
                    "position_x": 10,
                    "position_y": position_y,
                    "width": 30,
                    "height": 3,
                    "tier_item": kind,
                }
            )
            position_y += 4
    sign_request._sign_oca_tier_tag_items()


class SignOcaTierMixin:
    """Users, tier definitions and helpers shared by the tests.

    ``cls.test_model`` and ``cls.tester_model`` are the model and the
    ``ir.model`` of base_tier_validation's ``tier.validation.tester``.
    """

    @classmethod
    def _setup_sign_oca_tier(cls):
        cls.pdf = anchors_pdf()
        cls.requester = cls._new_user("sign_tier_requester", "Requester")
        cls.reviewer_1 = cls._new_user("sign_tier_reviewer_1", "First Reviewer")
        cls.reviewer_2 = cls._new_user("sign_tier_reviewer_2", "Second Reviewer")
        cls.reviewer_3 = cls._new_user("sign_tier_reviewer_3", "Third Reviewer")
        cls.outsider = cls._new_user("sign_tier_outsider", "Outsider")
        cls.second_tier_group = cls.env["res.groups"].create(
            {
                "name": "Second tier reviewers",
                "users": [Command.set([cls.reviewer_2.id, cls.reviewer_3.id])],
            }
        )
        definitions = cls.env["tier.definition"]
        cls.definition_1 = definitions.create(
            {
                "name": "Manager approval",
                "model_id": cls.tester_model.id,
                "review_type": "individual",
                "reviewer_id": cls.reviewer_1.id,
                "definition_domain": "[('test_field', '=', 2.5)]",
                "approve_sequence": True,
                "sequence": 30,
            }
        )
        cls.definition_2 = definitions.create(
            {
                "name": "Director approval",
                "model_id": cls.tester_model.id,
                "review_type": "group",
                "reviewer_group_id": cls.second_tier_group.id,
                "definition_domain": "[('test_field', '=', 2.5)]",
                "approve_sequence": True,
                "has_comment": True,
                "sequence": 20,
            }
        )
        tester_class = type(cls.test_model)
        pdf = cls.pdf
        cls.startClassPatcher(
            mock.patch.object(
                tester_class,
                "_sign_oca_get_document",
                lambda self: (pdf, "approval.pdf"),
                create=True,
            )
        )
        cls.startClassPatcher(
            mock.patch.object(
                tester_class, "_sign_oca_prepare_items", add_tier_items, create=True
            )
        )

    @classmethod
    def _new_user(cls, login, name):
        return new_test_user(
            cls.env,
            login=login,
            password=login,
            name=name,
            email=f"{login}@example.com",
            groups="base.group_user",
            tz="UTC",
        )

    def _bypass_definition(self, reviewer):
        """Add a third tier, after the two others, that ``reviewer`` reviews
        and whose definition has ``approve_sequence_bypass``."""
        return self.env["tier.definition"].create(
            {
                "name": "Controller approval",
                "model_id": self.tester_model.id,
                "review_type": "individual",
                "reviewer_id": reviewer.id,
                "definition_domain": "[('test_field', '=', 2.5)]",
                "approve_sequence": True,
                "approve_sequence_bypass": True,
                "sequence": 10,
            }
        )

    def _start(self):
        """Create a record, request its validation and send it for signature.

        :return: ``(record, sign_request)``
        """
        record = self.test_model.with_user(self.requester).create({"test_field": 2.5})
        record.request_validation()
        record.invalidate_recordset()
        sign_request = record._sign_oca_create_request()
        return record.sudo(), sign_request

    def _signer(self, sign_request, definition):
        return sign_request.signer_ids.filtered(
            lambda s: s.tier_review_id.definition_id == definition
        )

    def _browser_items(self, signer, **values):
        """Return the items a signing page sends: every item of the request,
        with ``values`` (by kind) on the items of ``signer``'s role."""
        sign_request = signer.request_id
        items = {}
        for key, item in sign_request.signatory_data.items():
            item = dict(item)
            kind = sign_request._sign_oca_tier_item_kind(item)
            if item["role_id"] == signer.role_id.id and kind in values:
                item["value"] = values[kind]
            items[key] = item
        return items

    def _sign(self, signer, user, **values):
        values.setdefault("signature", SIGNATURE_PNG)
        return (
            signer.with_user(user)
            .sudo()
            .action_sign(self._browser_items(signer, **values))
        )

    def _role_values(self, signer):
        """Return ``{kind: value}`` of the items stored for ``signer``."""
        sign_request = signer.request_id
        return {
            sign_request._sign_oca_tier_item_kind(item): item["value"]
            for item in sign_request.signatory_data.values()
            if item["role_id"] == signer.role_id.id
        }
