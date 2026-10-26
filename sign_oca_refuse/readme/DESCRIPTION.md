This module lets a signer refuse a sign request instead of signing it.

The signer gives a mandatory reason, at most 2000 characters long, as a text signed in
the document. The request moves to the new state _Refused_ and nobody can sign it any
more. The refusal is written in the activity log of the request, and posted in its
chatter, which notifies the responsible user of the request.
