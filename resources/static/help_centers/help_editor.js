(() => {
    const shell = document.querySelector('[data-help-editor]');
    if (!shell) return;

    const form = document.getElementById('help-article-form');
    const editor = document.getElementById('help-rich-editor');
    const source = document.getElementById('id_body_html');
    const sourceWrap = shell.querySelector('.hce-source-wrap');
    const sourceToggle = shell.querySelector('[data-source-toggle]');
    let sourceMode = false;
    let savedRange = null;

    const syncToSource = () => {
        if (editor && source && !sourceMode) source.value = editor.innerHTML;
    };

    const rememberSelection = () => {
        const selection = window.getSelection();
        if (!selection || !selection.rangeCount || !editor.contains(selection.anchorNode)) return;
        savedRange = selection.getRangeAt(0).cloneRange();
    };

    const restoreSelection = () => {
        editor.focus();
        const selection = window.getSelection();
        selection.removeAllRanges();
        if (!savedRange || !editor.contains(savedRange.commonAncestorContainer)) {
            savedRange = document.createRange();
            savedRange.selectNodeContents(editor);
            savedRange.collapse(false);
        }
        selection.addRange(savedRange);
    };

    if (editor) {
        editor.addEventListener('keyup', rememberSelection);
        editor.addEventListener('mouseup', rememberSelection);
        editor.addEventListener('input', syncToSource);
    }

    shell.querySelectorAll('[data-command]').forEach((button) => {
        button.addEventListener('mousedown', (event) => event.preventDefault());
        button.addEventListener('click', () => {
            restoreSelection();
            const command = button.dataset.command;
            let value = button.dataset.value || null;
            if (command === 'createLink') {
                value = window.prompt('Dirección del enlace (https://…)');
                if (!value) return;
            }
            document.execCommand(command, false, value);
            rememberSelection();
            syncToSource();
        });
    });

    if (sourceToggle) {
        sourceToggle.addEventListener('click', () => {
            sourceMode = !sourceMode;
            sourceToggle.setAttribute('aria-pressed', String(sourceMode));
            if (sourceMode) {
                syncToSource();
                editor.hidden = true;
                sourceWrap.hidden = false;
                source.focus();
            } else {
                editor.innerHTML = source.value;
                sourceWrap.hidden = true;
                editor.hidden = false;
                editor.focus();
            }
        });
    }

    if (form) form.addEventListener('submit', syncToSource);

    const insertAsset = (url, contentType, name) => {
        if (sourceMode) {
            sourceMode = false;
            sourceToggle?.setAttribute('aria-pressed', 'false');
            editor.innerHTML = source.value;
            sourceWrap.hidden = true;
            editor.hidden = false;
        }
        restoreSelection();
        const escapedUrl = url.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
        const escapedName = name.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
        const html = contentType.startsWith('image/')
            ? `<figure><img src="${escapedUrl}" alt="${escapedName}"><figcaption>${escapedName}</figcaption></figure><p><br></p>`
            : `<p><a href="${escapedUrl}">${escapedName}</a></p>`;
        const selection = window.getSelection();
        const range = selection.getRangeAt(0);
        range.deleteContents();
        const fragment = range.createContextualFragment(html);
        const lastNode = fragment.lastChild;
        range.insertNode(fragment);
        if (lastNode) {
            range.setStartAfter(lastNode);
            range.collapse(true);
            selection.removeAllRanges();
            selection.addRange(range);
        }
        rememberSelection();
        syncToSource();
    };

    const bindAssetButton = (button) => {
        button.addEventListener('click', () => insertAsset(
            button.dataset.url,
            button.dataset.type || 'application/octet-stream',
            button.dataset.name || 'Recurso'
        ));
    };
    shell.querySelectorAll('[data-insert-asset]').forEach(bindAssetButton);

    const assetInput = shell.querySelector('[data-asset-input]');
    const uploadStatus = shell.querySelector('[data-upload-status]');
    const assetList = shell.querySelector('[data-asset-list]');
    if (assetInput && shell.dataset.uploadUrl) {
        assetInput.addEventListener('change', async () => {
            const file = assetInput.files?.[0];
            if (!file) return;
            uploadStatus.textContent = `Subiendo ${file.name}…`;
            const payload = new FormData();
            payload.append('asset', file);
            try {
                const response = await fetch(shell.dataset.uploadUrl, {
                    method: 'POST',
                    headers: {'X-CSRFToken': form.querySelector('[name=csrfmiddlewaretoken]').value},
                    body: payload,
                    credentials: 'same-origin'
                });
                const result = await response.json();
                if (!response.ok || !result.ok) throw new Error(result.error || 'No se pudo subir el archivo.');
                assetList.querySelector('.hce-assets__empty')?.remove();
                const item = document.createElement('li');
                const label = document.createElement('span');
                label.textContent = result.name;
                label.title = result.name;
                const button = document.createElement('button');
                button.type = 'button';
                button.textContent = 'Insertar';
                button.dataset.insertAsset = '';
                button.dataset.url = result.url;
                button.dataset.type = result.content_type;
                button.dataset.name = result.name;
                bindAssetButton(button);
                item.append(label, button);
                assetList.prepend(item);
                uploadStatus.textContent = 'Recurso subido. Pulsa «Insertar» para añadirlo al contenido.';
            } catch (error) {
                uploadStatus.textContent = error.message;
            } finally {
                assetInput.value = '';
            }
        });
    }
})();
