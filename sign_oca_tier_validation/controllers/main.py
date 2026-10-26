# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo.osv import expression

from odoo.addons.sign_oca.controllers.main import PortalSign


class PortalSignTier(PortalSign):
    def get_sign_requests_domain(self, request):
        """Also list the tiers the user reviews, but not the tiers approved
        by bypass, which have nothing to sign.

        The signer of a tier is the contact of its first reviewer until
        someone signs; the other reviewers of the tier find it in their list
        too, with the same conditions on the request.
        """
        domain = super().get_sign_requests_domain(request)
        user = request.env.user
        if user._is_public():
            return domain
        reviewer_domain = [
            ("tier_review_id.reviewer_ids", "in", user.ids)
            if expression.is_leaf(leaf) and leaf[0] == "partner_id"
            else leaf
            for leaf in domain
        ]
        if reviewer_domain != domain:
            domain = expression.OR([domain, reviewer_domain])
        return expression.AND([domain, [("tier_bypassed", "=", False)]])
