This module places the fields of a sign request from _anchors_: short markers printed in
the PDF, such as `[[sig:customer:signature]]`. Each anchor gives a role, a field and,
optionally, a size or an offset; the module reads where the marker is printed and adds a
field of that role at that place, as if it had been drawn in the configuration editor.

A report can thus carry its own signature boxes: the anchors are printed in white, in a
tiny font, where the boxes go, and the request built from the report needs no manual
configuration. The fields stay editable in the editor of `sign_oca` afterwards. The
anchor text itself is left in the document.

The anchors are read with [pypdf](https://pypi.org/project/pypdf/).
