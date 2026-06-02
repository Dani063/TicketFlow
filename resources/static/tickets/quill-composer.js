/* Quill composer for #new-message (Sprint 2 + SPA panes)
 *
 * Visual: replaces the textarea with a Quill editor. The textarea stays in
 * the DOM hidden so existing code (and native form submission) can keep
 * reading/writing `value`.
 *
 * Pane-scoped init (Sprint 4):
 *   `window.QuillComposer.initFor(rootEl)` initializes Quill on the
 *   `#quill-toolbar` / `#quill-editor` / `#new-message` triplet inside rootEl.
 *   - Idempotent: if the editor was already wired (e.g. a cached pane is being
 *     re-attached), it just rebinds `window.QuillComposer` to that pane's
 *     instance and returns.
 *   - Called from `initTicketPane` so every pane mounted by tabs.js (SPA
 *     fragment fetch OR cached re-attach) ends up with a working composer.
 *   - Also runs once on DOMContentLoaded scoped to `document` for the
 *     deep-link page-load path.
 */
(function () {
    'use strict';

    function initFor(rootEl) {
        rootEl = rootEl || document;
        const textarea = rootEl.querySelector('#new-message');
        const editorEl = rootEl.querySelector('#quill-editor');
        const toolbar  = rootEl.querySelector('#quill-toolbar');
        if (!textarea || !editorEl || !toolbar || typeof Quill === 'undefined') return null;

        if (editorEl.__quillComposer) {
            window.QuillComposer = editorEl.__quillComposer;
            return editorEl.__quillComposer;
        }

        const quill = new Quill(editorEl, {
            theme: 'snow',
            placeholder: 'Escribe un mensaje... (puedes pegar o arrastrar archivos)',
            modules: { toolbar: toolbar },
        });

        const nativeValueDesc = Object.getOwnPropertyDescriptor(
            HTMLTextAreaElement.prototype, 'value'
        );
        const nativeSetValue = nativeValueDesc && nativeValueDesc.set
            ? (el, v) => nativeValueDesc.set.call(el, v)
            : (el, v) => { el.setAttribute('value', v); };

        const _getText = () => quill.getText().replace(/\n+$/, '');
        const _isEmpty = () => _getText().trim().length === 0;
        const _getHtml = () => {
            if (_isEmpty()) return '';
            const temp = document.createElement('div');
            temp.innerHTML = quill.root.innerHTML;
            temp.querySelectorAll('.ql-ui, .ql-cursor').forEach(el => el.remove());
            temp.querySelectorAll('*').forEach(el => {
                el.removeAttribute('data-list');
                el.removeAttribute('contenteditable');
            });
            return temp.innerHTML;
        };

        function syncPlainTextToTextarea() {
            nativeSetValue(textarea, _isEmpty() ? '' : _getText());
        }

        syncPlainTextToTextarea();
        quill.on('text-change', syncPlainTextToTextarea);

        Object.defineProperty(textarea, 'value', {
            configurable: true,
            get() { return nativeValueDesc.get.call(this); },
            set(v) {
                const str = v == null ? '' : String(v);
                quill.setText(str);
                nativeSetValue(this, str);
            },
        });

        const api = {
            quill,
            getText: _getText,
            getHtml: _getHtml,
            setText(t) { quill.setText(t == null ? '' : String(t)); },
            setHtml(html) {
                if (html == null) { quill.setText(''); return; }
                const delta = quill.clipboard.convert({ html: String(html) });
                quill.setContents(delta, 'silent');
            },
            clear() { quill.setText(''); nativeSetValue(textarea, ''); },
            focus() { quill.focus(); },
            isEmpty: _isEmpty,
            initFor,
        };
        editorEl.__quillComposer = api;
        window.QuillComposer = api;
        return api;
    }

    window.QuillComposer = { initFor };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => initFor(document));
    } else {
        initFor(document);
    }
})();
