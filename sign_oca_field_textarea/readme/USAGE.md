## Add a multi-line field

- Go to Sign / Settings / Fields and create a field, for instance _Remarks_, with the
  type _Multi-line Text_.
- In the editor of a template or of a draft request, right-click the page and choose
  that field. Its box shows a paragraph icon; give it the height of the lines it must
  hold.
- The signer types the text in the box on the signing page; it is printed in the
  document when it is signed. A box smaller than about six lines, as on a phone where
  the page is scaled down, grows over the page while it is being typed in, with a larger
  font, and goes back to its size afterwards.

## For developers

- `sign.oca.request.signer._get_pdf_page_textarea(item, box)` draws the item; it reuses
  sign_oca's `_get_pdf_text_markup`, so the value is escaped and keeps its line breaks.
- `sign.oca.request.signer._get_pdf_textarea_style()` returns the reportlab paragraph
  style of the text; by default the one of sign_oca's text fields
  (`_getParagraphStyle`).
- The item boxes of the document frame carry a `data-field-type` attribute, for
  stylesheets that target one field type.
- Uninstalling the module turns the multi-line fields into one-line text fields.
