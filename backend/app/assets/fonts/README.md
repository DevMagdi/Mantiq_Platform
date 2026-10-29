DejaVu fonts — bundled for the issued-policy PDF
================================================

`DejaVuSans.ttf` and `DejaVuSans-Bold.ttf` are the DejaVu Sans family (version 2.37,
the copy shipped with the base Debian image). They are the reason an issued policy can be a
real Arabic document instead of a Latin-only placeholder: DejaVu Sans carries the Arabic block,
including the presentation forms that `arabic-reshaper` maps to.

License: DejaVu fonts are distributed under the Bitstream Vera Fonts Copyright plus the
"Arev" public-domain additions, both of which permit redistribution and embedding. The
license permits bundling and embedding as done here; the fonts are unmodified.

Why a copy in the repository instead of a system font: the runtime image is
`python:3.12-slim`, the CI runner is Ubuntu, and a developer laptop is something else. A
document that renders on one of the three and throws `TTFError` on the others is worse than
a 700 KB blob in Git — and the blob is deterministic, which is what a legal document needs.
