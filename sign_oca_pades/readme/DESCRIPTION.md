This module seals the documents signed with `sign_oca` with PAdES signatures, so that
any later change to a signed document can be detected with a standard PDF reader.

- Before its first seal, the document becomes a PDF form: one read-only field per item
  (a check box for a check item, a multi-line text field for the others), one signature
  field per signer over the box of its signature item, and pages reserved at the end for
  the signature certificate (two by default). This happens when the request is sent, or
  at the first signature for a request sent before a certificate was configured.
- Each signature is written as an incremental update of the PDF that fills the fields of
  the signer (each one shows the item as `sign_oca` draws it) and is sealed at once with
  a PAdES baseline signature (`ETSI.CAdES.detached`, SHA-256) in the signer's signature
  field, which shows the signature image. Earlier seals stay valid: the bytes they cover
  are never rewritten, and the later updates only fill form fields, which PDF readers
  and pyHanko's difference analysis accept after an approval signature.
- Each signer's signature field locks the fields of the signer's role (FieldMDP) and
  allows later revisions to fill the form and sign only: a verifier reports a value or
  an appearance rewritten after the seal. A stamp of a signer without signature image,
  such as a refusal or a tier approved by bypass, still seals the signer's own field
  with a blank appearance, so that no empty signature field is left for a later
  signature. The field keeps the name it was prepared with, whatever happens to its role
  afterwards.
- When the last signer signs, or when the process ends on a refusal (see
  `sign_oca_tier_validation`), the signature certificate fills the reserved pages, and
  the whole document gets a final seal, which locks every field and allows no later
  change. A certificate longer than the reserved pages is rendered again at a smaller
  zoom, down to 60 %, so that its text reflows to the page width. The certificate lists
  the signers in signing order, each signature or refusal with the channel it was made
  from (portal, back office or server), the hash chain status, and the activity log with
  channel, IP address and user agent. Every time is printed in UTC, followed by the
  local time in parentheses. A signer approved without a signature of their own (a tier
  approved by bypass in `sign_oca_tier_validation`) is listed as such, not as pending.
- The reason of a seal names the signer (`_pades_seal_signer_name` of the signer) and
  their role; a module stamping a decision that is not a signature gives its own reason
  in the context key `sign_oca_stamp_reason`.
- With a timestamp authority (RFC 3161), every seal carries a trusted timestamp. When
  the authority does not answer, the seal is applied without timestamp and a message is
  posted on the request.
- The request keeps the SHA-256 of the original and of the final document, the SHA-256
  of the certificates that sealed it, and a summary of its seals (number, all intact or
  not, last check) that the "Verify Seals" button refreshes. A seal made by another
  certificate, or a document that differs from the final one, is reported.

Without sealing certificate the signatures are not sealed: documents are produced as
`sign_oca` makes them, a warning is logged, and the request shows that the document is
not sealed. The certificate page is still appended when the last signer signs, as an
unsealed incremental update. A document already prepared as a form keeps being filled
field by field, unsealed.
