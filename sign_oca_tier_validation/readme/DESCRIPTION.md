This module lets the reviewers of a tier validation (`base_tier_validation`) sign their
tiers in a `sign_oca` document. It works with any model that inherits `tier.validation`.

Once a validation is requested, the record sends one sign request whose document is
provided by the model:

- each tier review is a signer, with the sign role of its tier definition (created on
  the fly the first time);
- any reviewer of the tier may sign it; the first one who signs holds the role, and the
  document shows their name;
- the tiers are signed in the order that `base_tier_validation` enforces (_Approve by
  sequence_): a reviewer gets the email inviting them to sign when their tier's turn
  comes;
- signing approves the tier, refusing (`sign_oca_refuse`) rejects it, exactly as the
  _Validate_ and _Reject_ buttons of the record do; the comment written in the document
  becomes the comment of the review; with `sign_oca_pades`, a refused document gets its
  certificate and final seal as a completed one does, and the seal of a signature names
  the reviewer as the document does;
- while the document is out for signature, no tier is decided outside it: the buttons of
  the record, the comment wizard and any call made as the user are refused; the request
  is cancelled with its validation only, not by a user, its responsible included;
- the request always requires a login (`sign_oca_login`): the reviewer signs as their
  own user;
- the name, date and status fields of a tier are written by the server, so the signed
  document states who decided, when, and what;
- the signing page shows the steps of the validation next to the document, with each
  tier's status, the time of its decision and its comment.
