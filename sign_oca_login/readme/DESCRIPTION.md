This module lets a sign request require its signers to log in.

A template, and each request, gets a _Require login_ option. A request created from a
template takes the template's value; the value can change on the request while it is a
draft, and is read-only once the request is sent.

When the option is set, every `/sign_oca/*` page of a signer requires, on top of the
signer's access token, a session whose user is linked to the signer's contact (the
contact itself or one of its parents, as in the portal list of sign requests). An
anonymous visitor goes to the login page and comes back to the document afterwards;
another user is sent to their portal home. The same check applies when signing from
code: `action_sign` refuses any user who is not the signer.

Without the option, a request keeps the token-only access of `sign_oca`.
