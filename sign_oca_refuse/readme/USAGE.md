## Refuse a document

- Open the signing page of a request (from the email or from the portal).
- Press **Refuse** in the top right corner.
- Write why you refuse to sign the document, then press **Refuse**.
- The page then shows that the document is refused. Every other signer that opens the
  document sees the same page.

## Follow refused requests

- The responsible user of the request gets the reason in the chatter of the request.
- The reason and the date of the refusal show in the list of signers of the request.
- Use the _Refused_ filter of Sign / Requests, or of the portal list of signature
  requests.

## For developers

- `sign.oca.request.signer._before_refuse(reason)` runs once the refusal is accepted,
  before anything is written: a change made there (such as the signer's partner) is what
  the log, the chatter message and `_on_refuse` see.
- `sign.oca.request.signer._on_refuse(reason)` runs once the refusal is written, in the
  same transaction: override it to react to a refusal.
- `refused_on` and `refuse_reason` are written by `action_refuse` only.
- `sign.oca.request.signer._check_can_refuse()` raises when the signer may not refuse;
  by default a signer may refuse exactly when it may sign (`_check_can_sign`).
- The JSON route `/sign_oca/refuse/<signer_id>/<access_token>` takes a `reason`
  parameter and uses the same access check as the other `/sign_oca/*` routes.
