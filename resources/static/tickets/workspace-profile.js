/* Preferencias personales y perfil; compatible con los panes SPA. */
(function () {
    'use strict';
    if (window.WorkspaceSettings) return;
    function readJSON(id) { try { return JSON.parse(document.getElementById(id)?.textContent || '{}'); } catch (_) { return {}; } }
    let preferences = readJSON('workspace-shell-preferences');
    const csrf = () => document.querySelector('[name=csrfmiddlewaretoken]')?.value
        || decodeURIComponent(document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)?.[1] || '');
    function apply(prefs) {
        preferences = prefs;
        document.documentElement.dataset.workspaceDensity = prefs.density || 'comfortable';
        document.documentElement.dataset.workspaceText = prefs.text_size || 'normal';
        if (prefs.theme) window.TfTheme?.set(prefs.theme);
        if (prefs.notification_animation === false) document.querySelector('.notifications')?.classList.remove('has-news');
    }
    let audio;
    window.WorkspaceSettings = {
        get: () => preferences,
        notify() {
            if (!preferences.notification_sound) return;
            try {
                const Audio = window.AudioContext || window.webkitAudioContext;
                audio ||= new Audio();
                if (audio.state !== 'running') return;
                const tone = audio.createOscillator(), gain = audio.createGain();
                tone.frequency.value = 680;
                gain.gain.setValueAtTime(.04, audio.currentTime);
                gain.gain.exponentialRampToValueAtTime(.001, audio.currentTime + .18);
                tone.connect(gain); gain.connect(audio.destination);
                tone.start(); tone.stop(audio.currentTime + .2);
            } catch (_) { /* El navegador puede bloquear el sonido sin interacción. */ }
        },
    };
    document.addEventListener('pointerdown', () => {
        if (!preferences.notification_sound) return;
        try { const Audio = window.AudioContext || window.webkitAudioContext; audio ||= new Audio(); void audio.resume(); } catch (_) {}
    });
    function initProfile(root) {
        if (root.dataset.initialized) return;
        root.dataset.initialized = '1';
        const form = root.querySelector('form'), status = root.querySelector('#workspace-save-status');
        form.noValidate = true;
        let photoPreview;
        const photo = form.elements.photo;
        const preview = root.querySelector('[data-wp-photo-preview]');
        const initial = root.querySelector('[data-wp-photo-initial]');
        const photoStatus = root.querySelector('[data-wp-photo-status]');
        const cancelPhoto = root.querySelector('[data-wp-cancel-photo]');
        function updatePhoto() {
            if (photoPreview) { URL.revokeObjectURL(photoPreview); photoPreview = null; }
            const image = photo.files[0];
            const invalid = image && (image.size > 5 * 1024 * 1024 || !['image/jpeg','image/png','image/webp'].includes(image.type));
            if (invalid) photo.value = '';
            const selected = photo.files[0];
            const source = selected ? (photoPreview = URL.createObjectURL(selected)) : preview.dataset.currentPhoto;
            if (source) preview.src = source; else preview.removeAttribute('src');
            preview.hidden = !source; initial.hidden = !!source;
            cancelPhoto.hidden = !selected;
            photoStatus.textContent = invalid ? 'Selecciona un JPG, PNG o WebP de hasta 5 MB.'
                : selected ? 'Nueva foto lista. Guarda los cambios para aplicarla.' : 'Se actualizará al guardar los cambios.';
            photoStatus.dataset.error = invalid ? 'true' : 'false';
        }
        preview.addEventListener('error', () => { preview.hidden = true; initial.hidden = false; });
        root.querySelector('[data-wp-change-photo]').addEventListener('click', () => photo.click());
        cancelPhoto.addEventListener('click', () => { photo.value = ''; updatePhoto(); });
        photo.addEventListener('change', updatePhoto);
        const prefs = root.querySelector('#workspace-page-preferences')
            ? JSON.parse(root.querySelector('#workspace-page-preferences').textContent || '{}') : preferences;
        for (const key of ['density', 'text_size', 'signature']) if (form.elements[key]) form.elements[key].value = prefs[key] || ({density:'comfortable', text_size:'normal'}[key] || '');
        form.querySelector(`[name=theme][value="${['light','dark','system'].includes(prefs.theme) ? prefs.theme : (window.TfTheme?.get() || 'system')}"]`).checked = true;
        form.elements.notification_sound.checked = !!prefs.notification_sound;
        form.elements.notification_animation.checked = prefs.notification_animation !== false;
        const selectSection = id => {
            root.querySelectorAll('.wp-section').forEach(section => { section.hidden = section.id !== id; });
            root.querySelectorAll('[data-wp-section]').forEach(button => {
                const selected = button.dataset.wpSection === id;
                button.classList.toggle('is-active', selected);
                if (selected) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current');
            });
            root.querySelector('.wp-savebar').hidden = ['activity-section','tickets-section'].includes(id);
        };
        root.querySelectorAll('[data-wp-section]').forEach(button => button.addEventListener('click', () => selectSection(button.dataset.wpSection)));
        const updatePreview = () => { root.querySelector('[data-wp-signature-preview]').textContent = form.elements.signature.value || 'Tu firma aparecerá aquí.'; };
        form.elements.signature.addEventListener('input', updatePreview); updatePreview();
        form.addEventListener('input', () => { status.textContent = 'Tienes cambios sin guardar.'; delete status.dataset.error; });
        root.querySelector('[data-wp-read-all]').addEventListener('click', async event => {
            const button = event.currentTarget; button.disabled = true;
            try {
                const response = await fetch('/api/notifications/read-all/', {method:'POST', headers:{'X-CSRFToken':csrf()}});
                if (!response.ok) throw new Error();
                const result = await response.json();
                const badge = document.querySelector('.notifications .badge');
                if (badge) { badge.textContent = result.unread > 99 ? '99+' : result.unread; badge.style.display = result.unread ? 'inline-block' : 'none'; }
                button.textContent = 'Notificaciones al día';
            } catch (_) { status.textContent = 'No se pudieron actualizar las notificaciones. Inténtalo de nuevo.'; status.dataset.error = 'true'; }
            finally { button.disabled = false; }
        });
        form.addEventListener('submit', async event => {
            event.preventDefault();
            const invalid = Array.from(form.elements).find(field => typeof field.checkValidity === 'function' && !field.checkValidity());
            if (invalid) { selectSection(invalid.closest('.wp-section').id); invalid.reportValidity(); return; }
            const prefs = {
                theme: form.elements.theme.value, density: form.elements.density.value,
                text_size: form.elements.text_size.value, signature: form.elements.signature.value,
                notification_sound: form.elements.notification_sound.checked,
                notification_animation: form.elements.notification_animation.checked,
            };
            const button = form.querySelector('[type=submit]'); button.disabled = true; status.textContent = 'Guardando…';
            try {
                const body = new FormData();
                body.append('payload', JSON.stringify({name:form.elements.name.value, phone:form.elements.phone.value, preferences:prefs}));
                if (form.elements.photo.files[0]) body.append('photo', form.elements.photo.files[0]);
                const response = await fetch('/api/profile/preferences/', {method:'POST', headers:{'X-CSRFToken':csrf()}, body});
                const result = await response.json();
                if (!response.ok) throw new Error(result.error?.message || 'No se han podido guardar los cambios.');
                apply(result.preferences);
                root.querySelector('[data-wp-display-name]').textContent = result.name;
                const oldAvatar = root.querySelector('.wp-avatar');
                const avatar = document.createElement(result.photo_url ? 'img' : 'span');
                avatar.className = 'wp-avatar' + (result.photo_url ? '' : ' wp-initial');
                if (result.photo_url) { avatar.src = result.photo_url; avatar.alt = ''; }
                else avatar.textContent = result.name.charAt(0).toUpperCase();
                oldAvatar.replaceWith(avatar);
                document.querySelectorAll('.navbar .profile-picture, .user-popup .up-avatar').forEach(img => {
                    img.src = result.photo_url || img.dataset.defaultAvatar;
                });
                form.elements.photo.value = '';
                preview.dataset.currentPhoto = result.photo_url || '';
                initial.textContent = result.name.charAt(0).toUpperCase();
                updatePhoto();
                document.querySelectorAll('.navbar .username').forEach(name => { name.textContent = result.name; });
                document.querySelectorAll('.user-popup .up-name').forEach(name => { name.textContent = result.name; });
                const stored = root.querySelector('#workspace-page-preferences');
                if (stored) stored.textContent = JSON.stringify(result.preferences);
                const shell = document.getElementById('workspace-shell-preferences');
                if (shell) shell.textContent = JSON.stringify(result.preferences);
                document.querySelectorAll('[data-workspace-tools]').forEach(tools => initTools(tools, true));
                status.textContent = 'Cambios guardados.'; delete status.dataset.error;
            } catch (error) { status.textContent = error.message.startsWith('Cannot ') ? 'No se pudo actualizar la vista. Recarga el perfil para comprobar los cambios guardados.' : error.message; status.dataset.error = 'true'; }
            finally { button.disabled = false; }
        });
    }
    function insertText(pane, text) {
        if (!text || pane.querySelector('#ticket-form')?.dataset.locked === '1') return;
        const composer = pane.querySelector('#quill-editor')?.__quillComposer;
        if (composer) {
            const quill = composer.quill;
            const position = quill.getSelection()?.index ?? Math.max(0,quill.getLength()-1);
            quill.insertText(position, (position > 0 ? '\n' : '') + text + '\n', 'user');
            quill.setSelection(position + text.length + 1, 0); quill.focus();
        } else {
            const textarea = pane.querySelector('#new-message');
            if (textarea) { textarea.value += (textarea.value ? '\n' : '') + text; textarea.dispatchEvent(new Event('input',{bubbles:true})); textarea.focus(); }
        }
    }
    function initTools(tools, refresh = false) {
        if (tools.dataset.initialized && !refresh) return;
        const first = !tools.dataset.initialized; tools.dataset.initialized = '1';
        const button = tools.querySelector('[data-workspace-signature]');
        button.disabled = !preferences.signature;
        button.title = preferences.signature ? 'Insertar mi firma' : 'Configura tu firma en Mi perfil';
        if (first) {
            button.addEventListener('click', () => insertText(tools.closest('.ticket-pane'), preferences.signature));
        }
    }
    function boot() {
        document.querySelectorAll('[data-workspace-profile]').forEach(initProfile);
        document.querySelectorAll('[data-workspace-tools]').forEach(tools => initTools(tools));
    }
    apply(preferences);
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded',boot); else boot();
    new MutationObserver(boot).observe(document.body,{subtree:true,childList:true});
})();
