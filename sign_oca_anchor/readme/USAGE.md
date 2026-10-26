## Anchor grammar

An anchor is printed as plain text in the PDF:

```
[[sig:<role>:<field>]]
[[sig:<role>:<field>:<option>=<number>:<option>=<number>...]]
```

- `<role>`: the signer role of the field. It is matched, in order, as:
  - a number `n`: the role of the n-th signer of the request, in the order the signers
    were created (`[[sig:1:signature]]`);
  - an XML id when it holds a dot (`sign_oca.sign_role_customer`);
  - a role name, without case, accents or punctuation: `customer` matches the role
    _Customer_, `team_lead` matches _Team Lead_. The roles of the request's signers are
    tried before the other roles.
- `<field>`: the sign field (_Sign / Settings / Fields_), matched as an XML id when it
  holds a dot, then as a field name (`signature`, `name`, `email`), then as a field type
  (`signature`, `text`, `check`).
- Options, in % of the page, decimals with a dot:
  - `w`, `h`: width and height of the field. Without them the size depends on the field
    type: signature 20 × 5, text 20 × 1.5, check 2.5 × 1.8;
  - `dx`, `dy`: shift the field right and down (negative values shift it left and up);
  - `req=1`: the field is required;
  - `k`: a key. `_place_items_from_anchors(key=...)` places only the anchors that carry
    that key and logs the others; a document that prints text written by its users gives
    the anchors it prints itself a key drawn when it is rendered, so that an anchor
    typed in that text places no field.

The references accept letters, digits, `_`, `.` and `-`. Blanks around the `:`
separators are tolerated.

Examples:

```
[[sig:customer:signature:w=25:h=6:req=1]]
[[sig:2:name]]
[[sig:employee:check:dx=-3]]
```

The top-left corner of the field is the top-left corner of the anchor text (its baseline
raised by the font size), moved by `dx` and `dy`. The field is kept inside the page.

## Printing anchors in a report

- Print each anchor as its own text run, in white and in a tiny font, at the top-left
  corner of the box the signer fills, for example in a QWeb report:
  `<span style="color: white; font-size: 2px; white-space: nowrap;">[[sig:customer:signature]]</span>`.
- Text printed before the anchor on the same run is measured with the font's width table
  when it has one; with composite fonts (the usual case of HTML to PDF converters) it is
  estimated, so the anchor is best placed at the start of its own element.

## Placing the fields

- A request created in draft with signers, a document and no field gets its fields from
  the anchors automatically. An anchor whose role, field or option is unknown is skipped
  and logged. Pass the context key `sign_oca_anchor_auto=False` to create a request
  without this step.
- On a draft request, the _Place from anchors_ button adds the fields of the anchors not
  placed yet. It refuses, listing them, when an anchor names an unknown role, field or
  option. Fields already placed from an anchor are kept, with any change made in the
  editor; a field deleted in the editor comes back when the button is pressed again.

## From code

```python
request._place_items_from_anchors(
    mapping={
        "roles": {"approver": role_record},
        "fields": {"date": "my_module.sign_field_date"},
    },
)
```

`mapping` values are records, ids or XML ids and win over the default matching. `pages`,
a collection of page numbers counted from 1, limits the anchors read to those pages: a
document that embeds files written by others passes the pages it prints itself, so that
text typed in those files places no field. `_resolve_anchor_role` and
`_resolve_anchor_field` can be overridden to match references another way, and
`_find_pdf_anchors` gives the anchors of any PDF without placing them.
