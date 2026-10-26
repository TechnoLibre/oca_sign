# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import api, models, modules


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model
    def sign_oca_request_user_count(self):
        """Also count the tiers the user can sign now and whose signer is
        another reviewer's contact, under the model of the record each one
        validates."""
        groups = self._sign_oca_tier_uncount_bypassed(
            super().sign_oca_request_user_count()
        )
        user = self.env.user
        commercial = user.partner_id.commercial_partner_id
        signers = (
            self.env["sign.oca.request.signer"]
            .search(
                [
                    ("request_id.state", "=", "0_sent"),
                    ("signed_on", "=", False),
                    ("tier_review_id.reviewer_ids", "in", user.ids),
                ]
            )
            .filtered(
                lambda signer: signer.partner_id.commercial_partner_id != commercial
                and signer.is_allow_signature
            )
        )
        by_model = {group["model"]: group for group in groups}
        for model in set(signers.mapped("model")):
            if not model or model not in self.env:
                continue
            res_ids = set(
                signers.filtered(lambda s, m=model: s.model == m).mapped("res_id")
            )
            total = (
                self.env[model]
                .with_context(active_test=False)
                .search_count([("id", "in", list(res_ids))])
            )
            if not total:
                continue
            group = by_model.get(model)
            if group:
                group["total_records"] += total
                continue
            record = self.env[model]
            group = {
                "id": self.env["ir.model"].sudo()._get_id(model),
                "name": record._description,
                "model": model,
                "icon": modules.module.get_module_icon(record._original_module),
                "total_records": total,
            }
            by_model[model] = group
            groups.append(group)
        return groups

    @api.model
    def _sign_oca_tier_uncount_bypassed(self, groups):
        """Return ``groups``, the counters of sign_oca, without the records
        counted only for signers of tiers approved by bypass: such a signer
        is not signed, yet has nothing left to sign.

        sign_oca counts, per model, the records having an unsigned signer of
        the user's commercial partner in a sent request; a record whose only
        such signers are bypassed is taken off its model's count, and a
        model left without records is dropped.
        """
        commercial = self.env.user.partner_id.commercial_partner_id
        domain = [
            ("request_id.state", "=", "0_sent"),
            ("partner_id", "child_of", [commercial.id]),
            ("signed_on", "=", False),
        ]
        Signer = self.env["sign.oca.request.signer"]
        bypassed = Signer.search(domain + [("tier_bypassed", "=", True)])
        if not bypassed:
            return groups
        others = Signer.search(domain + [("tier_bypassed", "=", False)])
        by_model = {group["model"]: group for group in groups}
        for model in set(bypassed.mapped("model")):
            group = by_model.get(model)
            if not group:
                continue
            res_ids = set(
                bypassed.filtered(lambda s, m=model: s.model == m).mapped("res_id")
            ) - set(others.filtered(lambda s, m=model: s.model == m).mapped("res_id"))
            if not res_ids:
                continue
            group["total_records"] -= (
                self.env[model]
                .with_context(active_test=False)
                .search_count([("id", "in", list(res_ids))])
            )
        return [group for group in groups if group["total_records"] > 0]
