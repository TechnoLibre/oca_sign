This module enriches the portal list of sign requests (`/my/sign_requests`):

- the sent date and the signature date are shown down to the second
  (`yyyy-MM-dd HH:mm:ss`), in the time zone of the user;
- each line shows the related record of the request, when its name differs from the
  document's, and who sent it;
- on a narrow screen the table keeps the document and its state; a line under the
  document gives the related record, the sent date and the sender;
- a search field looks in the document name, in the display name of the related record,
  or in both;
- the lines can be grouped by document, state, sender, or sent date by year, month or
  day; a group never splits across a page;
- the pager keeps the sorting, filter, search and grouping in its links.

The "Signature Requests" tile of `/my` is always shown, even when the user has no
request yet.
