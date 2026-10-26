- Embed revocation information (PAdES B-LT) and a document timestamp (PAdES B-LTA) for
  long-term validation.
- Allow `pyhanko>=0.26` once `lxml>=5.4` can be installed alongside Odoo.
- Certificate pages are drawn as page images: their links and annotations are not kept.
- Two pages are reserved for the certificate, and the page count of a sealed document
  cannot change: a longer certificate is first rendered at a smaller zoom, then, when
  even 60 % does not fit, its last pages are printed smaller, laid out in a grid on the
  last reserved page. A module can reserve more pages by overriding
  `_pades_certificate_page_count`.
- pyHanko applies a field lock to every field whose name starts with a locked name and
  treats an appearance stream written before a seal as rewritten by every later
  revision; the item fields are therefore named with a fixed number of digits, and a
  seal writes again the appearance streams of the fields it locks.
- A refusal outside a tier validation does not finalize the document: `sign_oca_refuse`
  has no hook that runs after every module's `_on_refuse`, where `_pades_finalize` would
  be called.
