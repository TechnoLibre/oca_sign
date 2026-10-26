# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import models

OPEN_REQUEST_STATES = ("1_draft", "0_sent")


class TierReview(models.Model):
    _inherit = "tier.review"

    def _sign_oca_get_record(self):
        """Return the record this review validates, or an empty recordset."""
        self.ensure_one()
        if not self.model or self.model not in self.env:
            return self.env["tier.validation"]
        return self.env[self.model].browse(self.res_id).exists()

    def _sign_oca_get_role(self):
        """Return the ``sign.oca.role`` this review signs as.

        A review takes the role of its tier definition. A review without a
        definition (one added by hand or forwarded) takes a role named after
        its tier number, shared by every such review of that number.

        :return: a ``sign.oca.role`` record, as superuser
        """
        self.ensure_one()
        if self.definition_id:
            return self.definition_id._sign_oca_get_role()
        name = self.env._("Tier %s", self.sequence)
        roles = self.env["sign.oca.role"].sudo()
        return roles.search([("name", "=", name)], limit=1) or roles.create(
            {"name": name}
        )

    def unlink(self):
        """Cancel the open sign requests of the reviews deleted.

        ``restart_validation`` and the cancellation of a record delete their
        reviews; a request whose reviews are gone can no longer be signed.
        """
        requests = (
            self.env["sign.oca.request"]
            .sudo()
            .search(
                [
                    ("tier_review_ids", "in", self.ids),
                    ("state", "in", OPEN_REQUEST_STATES),
                ]
            )
        )
        requests._sign_oca_tier_cancel()
        return super().unlink()
