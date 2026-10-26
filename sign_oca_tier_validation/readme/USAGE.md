## Sign a validation

- Request the validation of the record as usual.
- Send it for signature: the model calls `record._sign_oca_create_request()` or offers
  the `action_sign_oca_create_request` button. The _Signatures_ smart button of the
  record opens its sign requests.
- The reviewers of the first tier get an email with the link of the document. They log
  in, fill in their signature (and their comment), and validate; or press **Refuse** and
  give a reason.
- The reviewers of the next tier get their email once the previous tier is approved.
- Restarting the validation cancels the open request. While the request is open, the
  _Validate_ and _Reject_ buttons of the record refuse to decide: the tiers are decided
  in the document.
- A tier whose definition has _Approve Sequence Bypass_ is approved along with an
  earlier tier signed by one of its reviewers, as `base_tier_validation` does from the
  record. It needs no signature: the document gets the reviewer's name, the date and the
  status _Approved (same reviewer)_, its signature and comment stay empty, and the
  request completes without it.
- Once signed, the page of the signer links to the document, to download or view.

## Fields of a tier

Each field of a tier's role has a kind, read from the `tier_item` key of the item in
`signatory_data` or, without it, from its sign field:

| Kind        | Sign field  | Value                                                                                                                                   |
| ----------- | ----------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `signature` | _Signature_ | drawn by the reviewer; always required                                                                                                  |
| `name`      | _Name_      | `Name (login)` of the user who decides, written by the server                                                                           |
| `date`      | _Date_      | date and time of the decision, `YYYY-MM-DD HH:MM`, written by the server                                                                |
| `status`    | _Status_    | _Approved_ or _Refused_, written by the server                                                                                          |
| `comment`   | _Comment_   | written by the reviewer, on several lines (_Multi-line Text_ field); required when the tier asks for a comment; the reason on a refusal |

The date and status are _Multi-line Text_ fields too, so that their text starts at the
top of their box, as the comment does. The date is in the time zone kept on the request
when it is created: the one of the record's company, else of the user who sends it, else
UTC (`tier.validation._sign_oca_tier_get_tz()`). The status is in the language of the
record's company, else of the user. The values sent by the browser for the name, date
and status are ignored, and the signing page does not show these fields to fill.

With `sign_oca_anchor` installed, the fields are placed from the anchors of the
document. A tier's role is named `tier<n>`, `n` being the tier number of the review (its
_Tier_, 1 for the first tier to decide), and the field by its kind:

```
[[sig:tier1:signature]] [[sig:tier1:name]] [[sig:tier1:date]]
[[sig:tier1:status]] [[sig:tier1:comment]]
```

## For developers

- `tier.validation._sign_oca_get_document()` returns `(pdf_bytes, filename)`, the
  document to sign. Each model implements it.
- `tier.validation._sign_oca_prepare_items(sign_request)` places the fields of the draft
  request; override it to place them by code, with a `tier_item` key on each item. A
  document that embeds files written by others calls
  `sign_request._sign_oca_tier_place_items(pages=...)` with the pages it prints itself,
  so that text typed in those files places no field.
- `tier.validation._sign_oca_tier_get_tz()` returns the time zone of the dates of the
  document.
- `tier.validation._sign_oca_tier_decided_in_document()` tells whether the tiers of a
  record are decided in a document only (default: while a tier request is open);
  `_sign_oca_tier_check_backoffice_decision()` refuses then any decision taken as the
  user, which `validate_tier`, `reject_tier`, `_validate_tier` and `_rejected_tier`
  call. The signing page decides as superuser.
- `sign_oca_anchor`'s anchors take a `k` option: `_sign_oca_tier_place_items(key=...)`
  places only the anchors carrying that key, so that a document printing text written by
  its users draws a key when it is rendered and ignores the anchors typed in it.
- `tier.validation._sign_oca_create_request(message="")` creates and sends the request
  of the open reviews, or returns the open one.
- `sign.oca.request.signer._sign_oca_tier_item_values(user, decision, moment)` returns
  the values the server writes; `_sign_oca_tier_stamped_name(user)` the name it writes.
- `sign.oca.request._sign_oca_tier_done_signers()` returns the signers whose tier is
  done: signed, or approved by bypass (`tier_bypassed`).
  `sign.oca.request.signer._sign_oca_tier_bypass_reviews(user)` returns the reviews an
  approval by `user` approves too.
- A decision stamped without a signature (a refusal, a tier approved by bypass) passes
  its status and stamped name in the context key `sign_oca_stamp_reason`; with
  `sign_oca_pades`, the seal of that stamp records it as its reason.
- The steps panel is the QWeb template `sign_oca_tier_validation.tier_steps_panel`. The
  empty templates `tier_steps_intro` (above the steps) and `tier_steps_start` (first
  item of the list of steps, for a step 0) are hooks for other modules.
  `sign.oca.request.signer._sign_oca_tier_status_labels()` gives the labels of the
  steps, and `_sign_oca_tier_item_placeholders()` the placeholders of the empty fields
  of the signing page, in the language of the page.
- The link between a request and a validation (`is_tier_validation`, `tier_review_ids`,
  the signers' `tier_review_id`) is written as superuser only, and a signer signs only
  the open request of the record its review validates.
- Every reviewer of a tier reads its request and signer, and finds it in the portal list
  of their sign requests and in the systray counter.
- A model sets `_sign_oca_tier_smart_button = False` to keep its form view without the
  _Signatures_ smart button.
