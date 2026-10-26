# Copyright 2026 TechnoLibre
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

"""Constants and helpers of the PDF form a sealed document becomes (see
``_pades_prepare_form`` of ``sign.oca.request``)."""

from dataclasses import dataclass

from pyhanko.pdf_utils import generic
from pyhanko.pdf_utils.content import ResourceType
from pyhanko.pdf_utils.generic import pdf_name
from pyhanko.stamp import BaseStamp, BaseStampStyle

# Names of the form fields the preparation creates. An item of
# ``signatory_data`` gets ``ITEM_FIELD_PREFIX`` followed by its key, padded
# to ``ITEM_FIELD_DIGITS`` digits (see ``item_field_name``); the reserved
# certificate pages get ``CERTIFICATE_FIELD``, then ``CERTIFICATE_FIELD``
# suffixed ``_2``, ``_3``... PDF field names hold no period.
ITEM_FIELD_PREFIX = "sign_oca_item_"
ITEM_FIELD_DIGITS = 6
CERTIFICATE_FIELD = "sign_oca_certificate"
# Name of the form XObject an appearance stream paints.
ITEM_XOBJECT = "/SignOcaItem"
# Field flags (ISO 32000-1, tables 221, 226 and 228) and annotation flags
# (table 165).
FIELD_READ_ONLY = 1
FIELD_MULTILINE = 1 << 12
ANNOT_HIDDEN = 1 << 1
ANNOT_PRINT = 1 << 2
# Default appearance of the text fields: Helvetica 10 pt in black, the font
# of the reportlab ``ParagraphStyle`` sign_oca draws its texts with.
TEXT_DEFAULT_APPEARANCE = "/Helv 10 Tf 0 g"
HELVETICA = "/Helv"


@dataclass(frozen=True)
class ItemStampStyle(BaseStampStyle):
    """pyHanko stamp style that paints a page imported as a form XObject.

    ``xobject`` is the reference of the imported page, and ``offset`` the
    point of that page that lands on the lower left corner of the stamp:
    the stamp shows the part of the page under the signature field.
    """

    border_width: int = 0
    xobject: generic.IndirectObject = None
    offset: tuple = (0, 0)

    def create_stamp(self, writer, box, text_params):
        return ItemStamp(writer=writer, style=self, box=box)


class ItemStamp(BaseStamp):
    """Stamp drawn by ``ItemStampStyle``."""

    def _render_inner_content(self):
        self.set_resource(
            category=ResourceType.XOBJECT,
            name=pdf_name(ITEM_XOBJECT),
            value=self.style.xobject,
        )
        x, y = self.style.offset
        return [place_command(ITEM_XOBJECT, 1, -x, -y)]


@dataclass(frozen=True)
class BlankStampStyle(BaseStampStyle):
    """pyHanko stamp style that paints nothing: the appearance of a
    signature field sealed without a signature image."""

    border_width: int = 0

    def create_stamp(self, writer, box, text_params):
        return BlankStamp(writer=writer, style=self, box=box)


class BlankStamp(BaseStamp):
    """Stamp drawn by ``BlankStampStyle``."""

    def _render_inner_content(self):
        return []


def item_field_name(key):
    """Return the name of the form field of the item ``key``.

    Every name has the same length, so none is the start of another:
    pyHanko applies a field lock to every field whose name starts with a
    locked name, as it would to the children of a parent field.
    """
    return f"{ITEM_FIELD_PREFIX}{int(key):0{ITEM_FIELD_DIGITS}d}"


def item_field_names(key):
    """Return the names the form field of the item ``key`` may have: the
    one of ``item_field_name``, then the unpadded name of the documents
    prepared before the padding."""
    return (item_field_name(key), f"{ITEM_FIELD_PREFIX}{key}")


def pdf_number(value):
    """Return ``value`` as a PDF number operand: fixed-point, at most four
    decimals, since PDF content streams have no exponent notation."""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def place_command(name, scale, x, y):
    """Return the content stream operators that paint the form XObject
    ``name`` scaled by ``scale`` with its origin at ``(x, y)``."""
    operands = " ".join(pdf_number(value) for value in (scale, 0, 0, scale, x, y))
    return f"q {operands} cm {name} Do Q".encode()


def number_array(values):
    return generic.ArrayObject([generic.FloatObject(value) for value in values])
