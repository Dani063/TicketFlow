/* Quill composer for #new-message (Sprint 2)
 *
 * Visual: replaces the textarea with a Quill editor. The textarea stays in
 * the DOM hidden so existing code (and native form submission) can keep
 * reading/writing `value`.
 *
 * Sync strategy (revised):
 *   - On every Quill text-change → write Quill's plain text into the
 *     textarea via the *native* value setter (bypasses any instance
 *     accessors). This guarantees `textarea.value` and native form
 *     submission both return the plain-text version of the editor.
 *   - Intercept *writes* to textarea.value (e.g. macros doing `ta.value = ...`)
 *     and route them into Quill via setText.
 *   - Expose window.QuillComposer.getHtml() for the AJAX path that wants
 *     the formatted HTML (sent as `html_body`).
 */
(function () {
    'use strict';

    function init() {
        const textarea = document.getElementById('new-message');
        const editorEl = document.getElementById('quill-editor');
        const toolbar  = document.getElementById('quill-toolbar');
        if (!textarea || !editorEl || !toolbar || typeof Quill === 'undefined') return;

        const quill = new Quill(editorEl, {
            theme: 'snow',
            placeholder: 'Escribe un mensaje... (puedes pegar o arrastrar archivos)',
            modules: { toolbar: toolbar },
        });

        // Native textarea setter — used to write plain text into the textarea
        // bypassing any instance-level setter we install below.
        const nativeValueDesc = Object.getOwnPropertyDescriptor(
            HTMLTextAreaElement.prototype, 'value'
        );
        const nativeSetValue = nativeValueDesc && nativeValueDesc.set
            ? (el, v) => nativeValueDesc.set.call(el, v)
            : (el, v) => { el.setAttribute('value', v); };

        const _getText = () => quill.getText().replace(/\n+$/, '');
        const _isEmpty = () => _getText().trim().length === 0;
        // We use root.innerHTML rather than getSemanticHTML() because Quill
        // 2.0.x's semantic serializer HTML-escapes the entire output in some
        // edge cases. We then clean Quill-specific attributes/elements before
        // sending to the server.
        const _getHtml = () => {
            if (_isEmpty()) return '';

            // Clone the editor content to avoid modifying the actual DOM
            const temp = document.createElement('div');
            temp.innerHTML = quill.root.innerHTML;

            // Remove Quill-specific helper elements (cursor, ui markers)
            temp.querySelectorAll('.ql-ui, .ql-cursor').forEach(el => el.remove());

            // Remove Quill-specific attributes from all elements
            temp.querySelectorAll('*').forEach(el => {
                el.removeAttribute('data-list');
                el.removeAttribute('contenteditable');
            });

            return temp.innerHTML;
        };

        function syncPlainTextToTextarea() {
            nativeSetValue(textarea, _isEmpty() ? '' : _getText());
        }

        // Initial sync (in case Quill loads with prefilled content)
        syncPlainTextToTextarea();
        quill.on('text-change', syncPlainTextToTextarea);

        // Override only the SETTER on the textarea instance: when external
        // code does `ta.value = "..."`, route the text into Quill (which then
        // triggers text-change and syncs back natively).
        // The GETTER falls through to the native one — readers see the
        // up-to-date plain text we synced above.
        let _suppressSync = false;
        Object.defineProperty(textarea, 'value', {
            configurable: true,
            get() {
                return nativeValueDesc.get.call(this);
            },
            set(v) {
                const str = v == null ? '' : String(v);
                _suppressSync = true;
                try {
                    quill.setText(str);
                    // setText fires text-change → syncPlainTextToTextarea
                    // will write str back. Also write directly in case the
                    // event fires async.
                    nativeSetValue(this, str);
                } finally {
                    _suppressSync = false;
                }
            },
        });

        // Public API
        window.QuillComposer = {
            quill,
            getText: _getText,
            getHtml: _getHtml,
            setText(t) { quill.setText(t == null ? '' : String(t)); },
            setHtml(html) {
                if (html == null) { quill.setText(''); return; }
                const delta = quill.clipboard.convert({ html: String(html) });
                quill.setContents(delta, 'silent');
            },
            clear() {
                quill.setText('');
                nativeSetValue(textarea, '');
            },
            focus() { quill.focus(); },
            isEmpty: _isEmpty,
        };
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
