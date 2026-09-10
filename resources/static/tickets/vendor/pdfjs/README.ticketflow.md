# PDF.js 6.3.289

Vendored from the official `pdfjs-dist@6.3.289` npm package, without executing
package scripts. Apache-2.0 license is included in LICENSE; the font directory
also contains its applicable licenses. The application loads these assets on
demand from its own static origin, never sends uploaded documents to a CDN,
and renders only pages to canvas (no document scripting or interactive forms).

Included: build/pdf.mjs renamed to pdf.js, build/pdf.worker.mjs renamed to
pdf.worker.js, standard_fonts/, cmaps/, wasm/. Only the two filenames changed;
their upstream bytes are untouched. The .js extension provides a JavaScript
MIME type on Windows static servers. Both assets remain ES modules, and the
application explicitly sets GlobalWorkerOptions.workerSrc to pdf.worker.js.

SHA-256 (identical before and after renaming):

- pdf.js: 495588717f62303a839e91a5343deebf1b41f52e2f9f6361e73dee6ea6a4355e
- pdf.worker.js: f2870db902eaff8397442c912b69459980ac91f6f4b5ed827167b12cf7057930

Upstream: https://github.com/mozilla/pdf.js
API examples: https://mozilla.github.io/pdf.js/examples/

Update both the main module and worker together, keep licenses, and rerun the
PDF preview and browser regression tests before deploying.
