# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging
import re
import unicodedata
from base64 import b64decode
from bisect import bisect_right
from collections import defaultdict
from io import BytesIO

from pypdf import PdfReader
from reportlab.pdfbase import pdfmetrics

from odoo import api, models
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

# [[sig:<role>:<field>(:<option>=<number>)*]]; blanks between the tokens are
# accepted because text extraction may insert them between glyph runs.
ANCHOR_PATTERN = (
    r"\[\[\s*sig\s*:\s*(?P<role>[\w.\-]+)\s*:\s*(?P<field>[\w.\-]+)"
    r"(?P<options>(?:\s*:\s*[a-z]+\s*=\s*-?\d+(?:\.\d+)?)*)\s*\]\]"
)
ANCHOR_OPTION_PATTERN = r"([a-z]+)\s*=\s*(-?\d+(?:\.\d+)?)"

# Width and height, in % of the page, of an item whose anchor gives no size.
ANCHOR_DEFAULT_SIZES = {
    "signature": (20.0, 5.0),
    "text": (20.0, 1.5),
    "check": (2.5, 1.8),
}


def _multiply(first, second):
    """Return the product of two PDF affine matrices ``[a, b, c, d, e, f]``.

    A point goes through ``first`` then ``second``, as the text matrix then
    the current transformation matrix of a content stream.
    """
    a, b, c, d, e, f = (float(value) for value in first)
    g, h, i, j, k, m = (float(value) for value in second)
    return [
        a * g + b * i,
        a * h + b * j,
        c * g + d * i,
        c * h + d * j,
        e * g + f * i + k,
        e * h + f * j + m,
    ]


def _normalize_ref(value):
    """Return ``value`` lowercased, without accents, words joined by ``_``."""
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(char for char in value if not unicodedata.combining(char))
    return re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")


class SignOcaRequest(models.Model):
    _inherit = "sign.oca.request"

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if self.env.context.get("sign_oca_anchor_auto", True):
            for record in records:
                if (
                    record.state == "1_draft"
                    and record.data
                    and record.signer_ids
                    and not record.signatory_data
                ):
                    record._place_items_from_anchors_on_create()
        return records

    def _place_items_from_anchors_on_create(self):
        """Place the anchors of a new request without items, never raising.

        An unreadable PDF or an anchor that names an unknown role or field is
        logged and leaves the request as it was created, so a document with
        broken anchors can still be configured by hand.
        """
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                self._place_items_from_anchors(raise_if_unresolved=False)
        except Exception:
            _logger.warning(
                "Sign request %s: the fields cannot be placed from anchors",
                self.id,
                exc_info=True,
            )

    def action_place_from_anchors(self):
        """Button of the draft request: place the anchors not placed yet."""
        self.ensure_one()
        items = self._place_items_from_anchors()
        if items:
            message = self.env._(
                "%(count)s field(s) placed from the anchors of the document.",
                count=len(items),
            )
        else:
            message = self.env._("No new anchor found in the document.")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "type": "success" if items else "warning",
                "message": message,
                "sticky": False,
            },
        }

    def _place_items_from_anchors(
        self, mapping=None, raise_if_unresolved=True, pages=None, key=None
    ):
        """Add an item to ``signatory_data`` for each anchor of the document.

        :param dict mapping: optional ``{"roles": {ref: role}, "fields":
            {ref: field}}``; each value is a record, an id or an XML id, and
            wins over the default resolution of ``_resolve_anchor_role`` and
            ``_resolve_anchor_field``
        :param bool raise_if_unresolved: raise a ``UserError`` listing the
            anchors whose role, field or option is unknown, before any item
            is added; when False those anchors are logged and skipped
        :param pages: optional collection of page numbers, counted from 1;
            anchors printed on other pages are ignored. A document that
            embeds files written by others limits the anchors to the pages
            it prints itself, so text in those files places no field.
        :param int key: optional number every anchor must carry as its
            ``k`` option; an anchor without it, or with another number, is
            logged and ignored. A document that prints text written by its
            users (a name, a description) gives the anchors it prints itself
            a key drawn when it is rendered, so that a user who types an
            anchor in that text places no field.
        :return: list of the item dicts added, as ``add_item`` returns them

        Each item keeps its anchor in the ``anchor`` key (page, compacted
        anchor text, occurrence on the page): an anchor already placed is
        skipped, so calling this again only adds the new anchors and keeps
        the items moved or resized in the editor. The anchor text stays in
        the document. Items are added in reading order (page, top, left)
        with a ``tabindex`` that follows the existing ones.
        """
        self.ensure_one()
        self._ensure_draft()
        anchors = self._find_pdf_anchors(b64decode(self.data))
        placed = {item.get("anchor") for item in self.signatory_data.values()}
        to_place = []
        problems = []
        foreign = []
        for anchor in anchors:
            if anchor["key"] in placed:
                continue
            if pages is not None and anchor["page"] not in pages:
                continue
            if key is not None and anchor["options"].get("k") != key:
                foreign.append(anchor["text"])
                continue
            role = self._resolve_anchor_role(anchor["role_ref"], mapping)
            field = self._resolve_anchor_field(anchor["field_ref"], mapping)
            reasons = []
            if not role:
                reasons.append(self.env._("unknown role"))
            if not field:
                reasons.append(self.env._("unknown field"))
            for option in anchor["unknown_options"]:
                reasons.append(self.env._("unknown option %s", option))
            if reasons:
                problems.append((anchor, reasons))
            else:
                to_place.append(self._prepare_anchor_item(anchor, role, field))
        if problems:
            lines = [
                self.env._(
                    "Page %(page)s: %(anchor)s (%(reasons)s)",
                    page=anchor["page"],
                    anchor=anchor["text"],
                    reasons=", ".join(reasons),
                )
                for anchor, reasons in problems
            ]
            if raise_if_unresolved:
                raise UserError(
                    self.env._(
                        "Some anchors of the document cannot be placed:\n%s",
                        "\n".join(lines),
                    )
                )
            _logger.warning(
                "Sign request %s: anchors skipped: %s", self.id, "; ".join(lines)
            )
        if foreign:
            _logger.warning(
                "Sign request %s: anchors without the document's key ignored: %s",
                self.id,
                "; ".join(foreign),
            )
        tabindex = max(
            [item.get("tabindex") or 0 for item in self.signatory_data.values()],
            default=0,
        )
        items = []
        for vals in sorted(
            to_place,
            key=lambda vals: (vals["page"], vals["position_y"], vals["position_x"]),
        ):
            tabindex += 1
            vals["tabindex"] = tabindex
            items.append(self.add_item(vals))
        return items

    @api.model
    def _find_pdf_anchors(self, pdf_bytes):
        """Return the anchors printed in ``pdf_bytes``, in reading order.

        :param bytes pdf_bytes: the PDF document
        :return: list of dicts with ``text`` (the anchor without blanks),
            ``key`` (page, text and occurrence on the page, unique in the
            document), ``page`` (counted from 1), ``role_ref``,
            ``field_ref``, ``options`` (``{name: float}`` of the known
            options), ``unknown_options`` (list of names), ``position_x``
            and ``position_y`` (top-left corner of the anchor text, in % of
            the page's media box, origin at the top-left corner, as
            sign_oca stores item positions) and ``line_height`` (font size
            in % of the page height)

        The text runs of a page are joined before matching, so an anchor
        split over several runs, or wrapped at a hyphen, is still found;
        its position is the one of the run where it starts.
        """
        reader = PdfReader(BytesIO(pdf_bytes))
        regex = re.compile(self._get_anchor_pattern())
        known_options = self._get_anchor_options()
        anchors = []
        for page_number, page in enumerate(reader.pages, start=1):
            runs = self._get_pdf_page_text_runs(page)
            text = "".join(run["text"] for run in runs)
            if "[[" not in text:
                continue
            starts = []
            length = 0
            for run in runs:
                starts.append(length)
                length += len(run["text"])
            box = page.mediabox
            left, top = float(box.left), float(box.top)
            width, height = float(box.width), float(box.height)
            occurrences = defaultdict(int)
            for match in regex.finditer(text):
                index = bisect_right(starts, match.start()) - 1
                run = runs[index]
                prefix = run["text"][: match.start() - starts[index]]
                point_x, point_y, line_height = self._get_anchor_point(run, prefix)
                options = {}
                unknown_options = []
                for name, value in re.findall(
                    ANCHOR_OPTION_PATTERN, match.group("options")
                ):
                    if name in known_options:
                        options[name] = float(value)
                    else:
                        unknown_options.append(name)
                compact = re.sub(r"\s+", "", match.group(0))
                occurrences[compact] += 1
                anchors.append(
                    {
                        "text": compact,
                        "key": f"{page_number}:{compact}:{occurrences[compact]}",
                        "page": page_number,
                        "role_ref": match.group("role"),
                        "field_ref": match.group("field"),
                        "options": options,
                        "unknown_options": unknown_options,
                        "position_x": (point_x - left) / width * 100,
                        "position_y": (top - point_y) / height * 100,
                        "line_height": line_height / height * 100,
                    }
                )
        return anchors

    @api.model
    def _get_anchor_pattern(self):
        """Return the regular expression of an anchor.

        It must define the named groups ``role``, ``field`` and ``options``;
        ``options`` holds ``:name=number`` pairs.
        """
        return ANCHOR_PATTERN

    @api.model
    def _get_anchor_options(self):
        """Return the option names an anchor accepts.

        ``w`` and ``h`` set the width and height, ``dx`` and ``dy`` shift
        the item right and down, all in % of the page; ``req`` makes the
        item required when not 0; ``k`` is the key of
        ``_place_items_from_anchors``.
        """
        return {"w", "h", "dx", "dy", "req", "k"}

    @api.model
    def _get_pdf_page_text_runs(self, page):
        """Return the text runs of a pypdf page, in content stream order.

        :return: list of dicts with ``text``, ``matrix`` (text matrix times
            the current transformation matrix: text space to user space),
            ``font_size`` and ``font_dict``
        """
        runs = []

        def visitor(text, cm, tm, font_dict, font_size):
            if text:
                runs.append(
                    {
                        "text": text,
                        "matrix": _multiply(tm, cm),
                        "font_size": float(font_size or 0.0),
                        "font_dict": font_dict,
                    }
                )

        page.extract_text(visitor_text=visitor)
        return runs

    @api.model
    def _get_anchor_point(self, run, prefix):
        """Return the top-left corner of an anchor that starts inside ``run``.

        :param dict run: a text run of ``_get_pdf_page_text_runs``
        :param str prefix: the text of the run before the anchor
        :return: tuple ``(x, y, line_height)`` in PDF user space units

        The top of the text is its baseline raised by the font size; the
        left is the start of the run moved by the width of ``prefix``.
        """
        a, b, c, d, e, f = run["matrix"]
        font_size = run["font_size"]
        advance = self._get_anchor_text_width(prefix, run["font_dict"], font_size)
        point_x = a * advance + c * font_size + e
        point_y = b * advance + d * font_size + f
        line_height = abs(font_size * (c * c + d * d) ** 0.5)
        return point_x, point_y, line_height

    @api.model
    def _get_anchor_text_width(self, text, font_dict, font_size):
        """Return the advance of ``text`` in text space, font size applied.

        A simple font is measured with its ``/Widths`` table and a standard
        14 font with the metrics of reportlab; any other font, such as the
        composite fonts that HTML to PDF converters embed, counts half an em
        per character. Character and word spacing are ignored.
        """
        if not text:
            return 0.0
        font = font_dict or {}
        widths = font.get("/Widths")
        if widths:
            widths = [float(width.get_object()) for width in widths]
            first_char = int(font.get("/FirstChar", 0))
            average = sum(widths) / len(widths)
            total = 0.0
            for char in text:
                index = ord(char) - first_char
                total += widths[index] if 0 <= index < len(widths) else average
            return total / 1000 * font_size
        base_font = str(font.get("/BaseFont", "")).lstrip("/").split("+")[-1]
        if base_font in pdfmetrics.standardFonts:
            try:
                return pdfmetrics.stringWidth(text, base_font, font_size)
            except (KeyError, UnicodeError):
                _logger.debug("No reportlab width for %r in %s", text, base_font)
        return len(text) * font_size / 2

    def _resolve_anchor_role(self, role_ref, mapping=None):
        """Return the ``sign.oca.role`` an anchor names, or an empty record.

        ``mapping["roles"][role_ref]`` wins. Otherwise a number ``n`` is the
        role of the n-th signer of the request (in creation order), a
        reference with a dot is an XML id, and any other text is compared to
        the role names, without case, accents or punctuation: the roles of
        the request's signers first, then every role.
        """
        self.ensure_one()
        roles = self.env["sign.oca.role"]
        mapped = ((mapping or {}).get("roles") or {}).get(role_ref)
        if mapped:
            return self._anchor_browse(roles, mapped)
        if role_ref.isascii() and role_ref.isdigit():
            signers = self.signer_ids.sorted("id")
            number = int(role_ref)
            if 0 < number <= len(signers):
                return signers[number - 1].role_id
            return roles
        if "." in role_ref:
            role = self._anchor_browse(roles, role_ref)
            if role:
                return role
        key = _normalize_ref(role_ref)
        if not key:
            return roles
        for candidates in (self.signer_ids.role_id, roles.search([])):
            for role in candidates.sorted("id"):
                if _normalize_ref(role.name) == key:
                    return role
        return roles

    def _resolve_anchor_field(self, field_ref, mapping=None):
        """Return the ``sign.oca.field`` an anchor names, or an empty record.

        ``mapping["fields"][field_ref]`` wins. Otherwise a reference with a
        dot is an XML id, then the text is compared to the field names
        without case, accents or punctuation, then to the field types
        (``signature``, ``text``, ``check``), the oldest field winning.
        """
        self.ensure_one()
        sign_fields = self.env["sign.oca.field"]
        mapped = ((mapping or {}).get("fields") or {}).get(field_ref)
        if mapped:
            return self._anchor_browse(sign_fields, mapped)
        if "." in field_ref:
            field = self._anchor_browse(sign_fields, field_ref)
            if field:
                return field
        key = _normalize_ref(field_ref)
        if not key:
            return sign_fields
        candidates = sign_fields.search([], order="id")
        for field in candidates:
            if _normalize_ref(field.name) == key:
                return field
        for field in candidates:
            if field.field_type == key:
                return field
        return sign_fields

    @api.model
    def _anchor_browse(self, model, value):
        """Return ``value`` (record, id or XML id) as a record of ``model``.

        A value that does not name an existing record of ``model`` gives an
        empty recordset.
        """
        if isinstance(value, models.BaseModel):
            return value if value._name == model._name else model
        if isinstance(value, int):
            return model.browse(value).exists()
        record = self.env.ref(value, raise_if_not_found=False)
        if record and record._name == model._name:
            return record
        return model

    def _get_anchor_default_size(self, field):
        """Return ``(width, height)`` in % of the page for ``field``'s items."""
        return ANCHOR_DEFAULT_SIZES.get(field.field_type, (20.0, 1.5))

    def _prepare_anchor_item(self, anchor, role, field):
        """Return the ``add_item`` values that place ``field`` at ``anchor``.

        The item's top-left corner is the anchor's, shifted by the ``dx`` and
        ``dy`` options; ``w`` and ``h`` override the default size. The item
        is kept inside the page.
        """
        options = anchor["options"]
        default_width, default_height = self._get_anchor_default_size(field)
        width = min(max(options.get("w", default_width), 0.1), 100.0)
        height = min(max(options.get("h", default_height), 0.1), 100.0)
        position_x = anchor["position_x"] + options.get("dx", 0.0)
        position_y = anchor["position_y"] + options.get("dy", 0.0)
        return {
            "field_id": field.id,
            "role_id": role.id,
            "page": anchor["page"],
            "position_x": round(min(max(position_x, 0.0), 100.0 - width), 4),
            "position_y": round(min(max(position_y, 0.0), 100.0 - height), 4),
            "width": width,
            "height": height,
            "required": bool(options.get("req", 0.0)),
            "anchor": anchor["key"],
        }
