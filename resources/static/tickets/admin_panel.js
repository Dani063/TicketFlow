/* ============================================================
   Admin panel logic — users/roles/groups CRUD + bulk actions.
   Loaded from admin_panel.html via {% block extra_js %}.
   Reads runtime data from window.__adminPanelData (set by template).
   ============================================================ */
(function () {
    const data = window.__adminPanelData || {};
    const CUR_UID = data.currentUserId;

    function csrf() {
        for (const c of document.cookie.split(';')) {
            const [k, v] = c.trim().split('=');
            if (k === 'csrftoken') return decodeURIComponent(v || '');
        }
        return '';
    }
    function hdr() { return { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }; }
    function esc(s) {
        return String(s == null ? '' : s)
            .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
            .replace(/"/g,'&quot;').replace(/'/g,'&#39;');
    }
    function showErr(id, msg) { const e=document.getElementById(id); e.textContent=msg; e.style.display='block'; }
    function hideErr(id) { const e=document.getElementById(id); e.textContent=''; e.style.display='none'; }

    /* ---- modal helpers (use dialog.css transitions) ---- */
    function openModal(id) {
        const el = document.getElementById(id);
        if (!el) return;
        // force reflow so transition triggers from hidden state
        el.offsetHeight;
        el.classList.add('open');
    }
    function closeModal(id) {
        const el = document.getElementById(id);
        if (!el) return;
        el.classList.remove('open');
        el.classList.add('closing');
        const finish = () => { el.classList.remove('closing'); };
        el.addEventListener('transitionend', finish, { once: true });
        setTimeout(finish, 300);  // fallback
    }
    document.querySelectorAll('[data-close]').forEach(btn => btn.addEventListener('click', () => closeModal(btn.dataset.close)));
    document.querySelectorAll('.dialog-backdrop').forEach(o => o.addEventListener('click', e => { if(e.target===o) closeModal(o.id); }));

    /* ---- tabs ---- */
    document.querySelectorAll('.tf-tab[data-tab]').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tf-tab[data-tab]').forEach(b => b.classList.remove('is-active'));
            document.querySelectorAll('.tf-admin-panel').forEach(p => p.classList.remove('is-active'));
            btn.classList.add('is-active');
            document.getElementById('tab-' + btn.dataset.tab).classList.add('is-active');
            if (btn.dataset.tab === 'roles')  loadRoles();
            if (btn.dataset.tab === 'groups') loadGroups();
            if (btn.dataset.tab === 'templates') loadTemplates();
        });
    });

    /* ================================================================
       USERS
       ================================================================ */
    let _uPage = 1, _uPageSize = 50, _uTotal = 0, _uTotalPages = 1;
    const _sel = new Set();
    const _uMap = new Map();

    function getPageRange(cur, tot) {
        if (tot <= 7) return Array.from({length: tot}, (_, i) => i + 1);
        if (cur <= 4) return [1,2,3,4,5,'…',tot];
        if (cur >= tot-3) return [1,'…',tot-4,tot-3,tot-2,tot-1,tot];
        return [1,'…',cur-1,cur,cur+1,'…',tot];
    }

    function renderPageNums(cur, tot) {
        const container = document.getElementById('usersPageNums');
        container.innerHTML = '';

        const prev = document.createElement('button');
        prev.className = 'page-num-btn page-arrow';
        prev.innerHTML = '&#8249;';
        prev.disabled = cur <= 1;
        prev.onclick = () => usersGo(cur - 1);
        container.appendChild(prev);

        getPageRange(cur, tot).forEach(p => {
            if (p === '…') {
                const s = document.createElement('span');
                s.className = 'page-ellipsis'; s.textContent = '…';
                container.appendChild(s);
            } else {
                const b = document.createElement('button');
                b.className = 'page-num-btn' + (p === cur ? ' active' : '');
                b.textContent = p;
                b.onclick = () => usersGo(p);
                container.appendChild(b);
            }
        });

        const next = document.createElement('button');
        next.className = 'page-num-btn page-arrow';
        next.innerHTML = '&#8250;';
        next.disabled = cur >= tot;
        next.onclick = () => usersGo(cur + 1);
        container.appendChild(next);
    }

    function updateBulkBar() {
        document.getElementById('usersBulkCount').textContent = _sel.size;
        document.getElementById('usersBulkBar').classList.toggle('is-visible', _sel.size > 0);
    }

    function clearSel() {
        _sel.clear();
        document.querySelectorAll('#usersTbody .row-cb').forEach(cb => cb.checked = false);
        document.querySelectorAll('#usersTbody tr.row-selected').forEach(r => r.classList.remove('row-selected'));
        const sa = document.getElementById('usersSelectAll');
        if (sa) { sa.checked = false; sa.indeterminate = false; }
        updateBulkBar();
    }

    function syncSelectAll() {
        const all = document.querySelectorAll('#usersTbody .row-cb');
        const chk = document.querySelectorAll('#usersTbody .row-cb:checked');
        const sa  = document.getElementById('usersSelectAll');
        if (!sa) return;
        if (!all.length || !chk.length) { sa.checked=false; sa.indeterminate=false; }
        else if (all.length===chk.length) { sa.checked=true; sa.indeterminate=false; }
        else { sa.checked=false; sa.indeterminate=true; }
    }

    function usersGo(p) {
        if (p < 1 || p > _uTotalPages) return;
        _uPage = p;
        loadUsers(false);
    }

    function emptyRow(colspan, text) {
        return `<tr><td colspan="${colspan}"><div class="tf-empty tf-empty--compact"><span class="tf-empty-message">${text}</span></div></td></tr>`;
    }

    async function loadUsers(reset) {
        if (reset !== false) { _uPage = 1; clearSel(); }
        _uPageSize = parseInt(document.getElementById('usersPageSize').value, 10) || 50;

        const params = new URLSearchParams({
            page: _uPage, page_size: _uPageSize,
            q:        document.getElementById('usersSearch').value.trim(),
            role_id:  document.getElementById('usersRoleFilter').value,
            group_id: document.getElementById('usersGroupFilter').value,
        });

        const tbody = document.getElementById('usersTbody');
        tbody.innerHTML = emptyRow(8, 'Cargando…');

        try {
            const res  = await fetch('/api/admin/users/?' + params);
            const json = await res.json();
            const pag  = json.pagination || {};
            _uTotal      = pag.total || 0;
            _uTotalPages = pag.total_pages || 1;
            _uPage       = pag.page || 1;
            _uMap.clear();

            if (!json.users || !json.users.length) {
                tbody.innerHTML = emptyRow(8, 'Sin resultados');
            } else {
                const frag = document.createDocumentFragment();
                json.users.forEach(u => {
                    _uMap.set(u.id, u);
                    const tr = document.createElement('tr');
                    if (_sel.has(u.id)) tr.classList.add('row-selected');
                    tr.innerHTML =
                        `<td class="col-cb" style="text-align:center;">
                            <input type="checkbox" class="row-cb" data-id="${u.id}"${_sel.has(u.id)?' checked':''}>
                        </td>
                        <td class="col-id">${u.id}</td>
                        <td class="col-name" title="${esc(u.name)}">${esc(u.name)}</td>
                        <td class="col-email" title="${esc(u.email)}">${esc(u.email)}</td>
                        <td class="col-role">${esc(u.role_name||'—')}</td>
                        <td class="col-group">${esc(u.group_name||'—')}</td>
                        <td class="col-status">
                            <span class="tf-pill ${u.is_active?'tf-pill--success':'tf-pill--danger'}">${u.is_active?'Activo':'Inactivo'}</span>
                        </td>
                        <td class="col-actions">
                            <div class="tf-admin-table-actions">
                                <button class="tf-btn tf-btn--secondary tf-btn--sm" data-action="edit" data-id="${u.id}" title="Editar"><i class="fas fa-pen"></i></button>
                                <button class="tf-btn tf-btn--secondary tf-btn--sm" data-action="pwd"  data-id="${u.id}" title="Contraseña"><i class="fas fa-key"></i></button>
                                <button class="tf-btn tf-btn--danger tf-btn--sm"    data-action="del"  data-id="${u.id}" title="Eliminar"><i class="fas fa-trash"></i></button>
                            </div>
                        </td>`;
                    frag.appendChild(tr);
                });
                tbody.innerHTML = '';
                tbody.appendChild(frag);
            }

            const bar = document.getElementById('usersPagBar');
            bar.style.display = '';
            renderPageNums(_uPage, _uTotalPages);
            const from = _uTotal ? (_uPage-1)*_uPageSize+1 : 0;
            const to   = Math.min(_uPage*_uPageSize, _uTotal);
            document.getElementById('usersRegInfo').textContent =
                `registros (mostrando ${from}–${to} de ${_uTotal.toLocaleString()})`;
            document.getElementById('usersPageSize').value = _uPageSize;

        } catch(e) {
            tbody.innerHTML = `<tr><td colspan="8"><div class="tf-empty tf-empty--compact"><span class="tf-empty-message" style="color: var(--color-danger);">Error al cargar usuarios</span></div></td></tr>`;
        }
    }

    document.getElementById('usersTbody').addEventListener('click', e => {
        const btn = e.target.closest('button[data-action]');
        if (!btn) return;
        const id  = parseInt(btn.dataset.id, 10);
        const act = btn.dataset.action;
        if (act === 'edit') openUserModal(_uMap.get(id));
        if (act === 'pwd')  resetPassword(id);
        if (act === 'del')  deleteUser(id);
    });

    document.getElementById('usersTbody').addEventListener('change', e => {
        const cb = e.target.closest('.row-cb');
        if (!cb) return;
        const id = parseInt(cb.dataset.id, 10);
        const tr = cb.closest('tr');
        if (cb.checked) { _sel.add(id); tr.classList.add('row-selected'); }
        else            { _sel.delete(id); tr.classList.remove('row-selected'); }
        updateBulkBar(); syncSelectAll();
    });

    document.getElementById('usersSelectAll').addEventListener('change', function() {
        document.querySelectorAll('#usersTbody .row-cb').forEach(cb => {
            cb.checked = this.checked;
            const id = parseInt(cb.dataset.id, 10);
            const tr = cb.closest('tr');
            if (this.checked) { _sel.add(id); tr.classList.add('row-selected'); }
            else              { _sel.delete(id); tr.classList.remove('row-selected'); }
        });
        updateBulkBar();
    });

    document.getElementById('usersPageSize').addEventListener('change', function() {
        _uPageSize = parseInt(this.value, 10); usersGo(1);
    });

    let _st = null;
    document.getElementById('usersSearch').addEventListener('input', () => {
        clearTimeout(_st); _st = setTimeout(loadUsers, 260);
    });
    document.getElementById('usersRoleFilter').addEventListener('change', loadUsers);
    document.getElementById('usersGroupFilter').addEventListener('change', loadUsers);

    document.getElementById('btnNewUser').addEventListener('click', () => openUserModal(null));

    document.getElementById('btnBulkDeactivate').addEventListener('click', async () => {
        if (!_sel.size) return;
        const ok = await window.dialog.confirm({
            title:'Desactivar usuarios', message:`¿Desactivar ${_sel.size} usuario(s)?`,
            confirmText:'Desactivar', cancelText:'Cancelar', variant:'danger',
        });
        if (!ok) return;
        let n = 0;
        for (const id of _sel) {
            try {
                const r = await fetch('/api/admin/users/', { method:'PUT', headers:hdr(), body:JSON.stringify({id, is_active:false}) });
                if ((await r.json()).ok) n++;
            } catch(_) {}
        }
        window.toast.success(`${n} usuario(s) desactivado(s)`);
        clearSel(); loadUsers(false);
    });

    document.getElementById('btnBulkDelete').addEventListener('click', async () => {
        if (!_sel.size) return;
        const ok = await window.dialog.confirm({
            title:'Eliminar usuarios',
            message:`¿Eliminar ${_sel.size} usuario(s)? Los que tengan tickets se desactivarán.`,
            confirmText:'Eliminar', cancelText:'Cancelar', variant:'danger',
        });
        if (!ok) return;
        let del=0, deact=0;
        for (const id of _sel) {
            if (id === CUR_UID) continue;
            try {
                const r = await fetch('/api/admin/users/?id='+id, { method:'DELETE', headers:hdr() });
                const d = await r.json();
                if (d.ok) { d.deactivated ? deact++ : del++; }
            } catch(_) {}
        }
        window.toast.success([del&&`${del} eliminado(s)`,deact&&`${deact} desactivado(s)`].filter(Boolean).join(', ')||'Hecho');
        clearSel(); loadUsers(false);
    });

    function openUserModal(u) {
        hideErr('userModalError');
        document.getElementById('userModalId').value    = u ? u.id : '';
        document.getElementById('userModalTitle').textContent = u ? 'Editar usuario' : 'Nuevo usuario';
        document.getElementById('userModalName').value  = u ? u.name : '';
        document.getElementById('userModalEmail').value = u ? u.email : '';
        document.getElementById('userModalRole').value  = u && u.role_id  ? u.role_id  : '';
        document.getElementById('userModalGroup').value = u && u.group_id ? u.group_id : '';
        document.getElementById('userModalActive').value = u ? String(u.is_active) : 'true';
        document.getElementById('userModalActiveField').style.display = u ? 'flex' : 'none';
        document.getElementById('userModalPassword').value = '';
        openModal('userModal');
    }

    document.getElementById('btnSaveUser').addEventListener('click', async () => {
        hideErr('userModalError');
        const id      = document.getElementById('userModalId').value;
        const payload = {
            name:     document.getElementById('userModalName').value.trim(),
            email:    document.getElementById('userModalEmail').value.trim(),
            role_id:  document.getElementById('userModalRole').value  || null,
            group_id: document.getElementById('userModalGroup').value || null,
        };
        if (id) {
            payload.id = parseInt(id, 10);
            payload.is_active = document.getElementById('userModalActive').value === 'true';
        } else {
            const pwd = document.getElementById('userModalPassword').value;
            if (pwd) payload.password = pwd;
        }
        try {
            const res  = await fetch('/api/admin/users/', { method:id?'PUT':'POST', headers:hdr(), body:JSON.stringify(payload) });
            const json = await res.json();
            if (!json.ok) { showErr('userModalError', json.error||'Error al guardar'); return; }
            closeModal('userModal');
            window.toast.success(id ? 'Usuario actualizado' : 'Usuario creado');
            loadUsers(false);
        } catch(e) { showErr('userModalError', 'Error de red'); }
    });

    async function deleteUser(id) {
        if (id === CUR_UID) { window.toast.warning('No puedes eliminar tu propia cuenta'); return; }
        const ok = await window.dialog.confirm({
            title:'Eliminar usuario', message:'Si tiene tickets, se desactivará en lugar de borrarse.',
            confirmText:'Eliminar', cancelText:'Cancelar', variant:'danger',
        });
        if (!ok) return;
        try {
            const res  = await fetch('/api/admin/users/?id='+id, { method:'DELETE', headers:hdr() });
            const json = await res.json();
            if (!json.ok) { window.toast.error(json.error||'Error'); return; }
            window.toast.success(json.deactivated ? 'Usuario desactivado' : 'Usuario eliminado');
            clearSel(); loadUsers(false);
        } catch(e) { window.toast.error('Error de red'); }
    }

    async function resetPassword(id) {
        const pwd = window.prompt('Nueva contraseña (mín. 8 caracteres). Vacío para invalidarla:', '');
        if (pwd === null) return;
        try {
            const res  = await fetch(`/api/admin/users/${id}/password/`, { method:'POST', headers:hdr(), body:JSON.stringify({password:pwd}) });
            const json = await res.json();
            if (!json.ok) { window.toast.error(json.error||'Error'); return; }
            window.toast.success('Contraseña actualizada');
        } catch(e) { window.toast.error('Error de red'); }
    }

    /* ================================================================
       ROLES
       ================================================================ */
    const _rMap = new Map();

    async function loadRoles() {
        const tbody = document.getElementById('rolesTbody');
        tbody.innerHTML = emptyRow(4, 'Cargando…');
        try {
            const res  = await fetch('/api/admin/roles/');
            const json = await res.json();
            _rMap.clear();
            if (!json.roles || !json.roles.length) {
                tbody.innerHTML = emptyRow(4, 'Sin roles');
                return;
            }
            const frag = document.createDocumentFragment();
            json.roles.forEach(r => {
                _rMap.set(r.id, r);
                const tr = document.createElement('tr');
                tr.innerHTML =
                    `<td class="col-id">${r.id}</td>
                    <td>${esc(r.role_name)} ${r.protected?'<span class="tf-pill tf-pill--warning">Sistema</span>':''}</td>
                    <td>${r.user_count.toLocaleString()}</td>
                    <td class="col-actions"><div class="tf-admin-table-actions">
                        <button class="tf-btn tf-btn--secondary tf-btn--sm" data-action="edit" data-id="${r.id}" ${r.protected?'disabled':''} title="Editar"><i class="fas fa-pen"></i></button>
                        <button class="tf-btn tf-btn--danger tf-btn--sm" data-action="del" data-id="${r.id}" ${r.protected||r.user_count>0?'disabled':''} title="Eliminar"><i class="fas fa-trash"></i></button>
                    </div></td>`;
                frag.appendChild(tr);
            });
            tbody.innerHTML = '';
            tbody.appendChild(frag);
        } catch(e) {
            tbody.innerHTML = `<tr><td colspan="4"><div class="tf-empty tf-empty--compact"><span class="tf-empty-message" style="color: var(--color-danger);">Error al cargar</span></div></td></tr>`;
        }
    }

    document.getElementById('rolesTbody').addEventListener('click', e => {
        const btn = e.target.closest('button[data-action]');
        if (!btn || btn.disabled) return;
        const id = parseInt(btn.dataset.id, 10);
        if (btn.dataset.action === 'edit') openRoleModal(_rMap.get(id));
        if (btn.dataset.action === 'del')  deleteRole(id);
    });

    document.getElementById('btnNewRole').addEventListener('click', () => openRoleModal(null));

    function openRoleModal(r) {
        hideErr('roleModalError');
        document.getElementById('roleModalId').value   = r ? r.id : '';
        document.getElementById('roleModalTitle').textContent = r ? 'Editar rol' : 'Nuevo rol';
        document.getElementById('roleModalName').value = r ? r.role_name : '';
        document.getElementById('roleModalDesc').value = r ? r.description : '';
        openModal('roleModal');
    }

    document.getElementById('btnSaveRole').addEventListener('click', async () => {
        hideErr('roleModalError');
        const id = document.getElementById('roleModalId').value;
        const payload = { role_name: document.getElementById('roleModalName').value.trim(), description: document.getElementById('roleModalDesc').value.trim() };
        if (id) payload.id = parseInt(id, 10);
        try {
            const res  = await fetch('/api/admin/roles/', { method:id?'PUT':'POST', headers:hdr(), body:JSON.stringify(payload) });
            const json = await res.json();
            if (!json.ok) { showErr('roleModalError', json.error||'Error'); return; }
            closeModal('roleModal'); window.toast.success(id?'Rol actualizado':'Rol creado'); loadRoles();
        } catch(e) { showErr('roleModalError', 'Error de red'); }
    });

    async function deleteRole(id) {
        const ok = await window.dialog.confirm({ title:'Eliminar rol', message:'¿Eliminar este rol?', confirmText:'Eliminar', cancelText:'Cancelar', variant:'danger' });
        if (!ok) return;
        try {
            const res  = await fetch('/api/admin/roles/?id='+id, { method:'DELETE', headers:hdr() });
            const json = await res.json();
            if (!json.ok) { window.toast.error(json.error||'Error'); return; }
            window.toast.success('Rol eliminado'); loadRoles();
        } catch(e) { window.toast.error('Error de red'); }
    }

    /* ================================================================
       GROUPS
       ================================================================ */
    const _gMap = new Map();

    async function loadGroups() {
        const tbody = document.getElementById('groupsTbody');
        tbody.innerHTML = emptyRow(4, 'Cargando…');
        try {
            const res  = await fetch('/api/admin/groups/');
            const json = await res.json();
            _gMap.clear();
            if (!json.groups || !json.groups.length) {
                tbody.innerHTML = emptyRow(4, 'Sin grupos');
                return;
            }
            const frag = document.createDocumentFragment();
            json.groups.forEach(g => {
                _gMap.set(g.id, g);
                const tr = document.createElement('tr');
                tr.innerHTML =
                    `<td class="col-id">${g.id}</td>
                    <td>${esc(g.group_name)}</td>
                    <td>${g.user_count.toLocaleString()}</td>
                    <td class="col-actions"><div class="tf-admin-table-actions">
                        <button class="tf-btn tf-btn--secondary tf-btn--sm" data-action="edit" data-id="${g.id}" title="Editar"><i class="fas fa-pen"></i></button>
                        <button class="tf-btn tf-btn--danger tf-btn--sm" data-action="del" data-id="${g.id}" title="Eliminar"><i class="fas fa-trash"></i></button>
                    </div></td>`;
                frag.appendChild(tr);
            });
            tbody.innerHTML = '';
            tbody.appendChild(frag);
        } catch(e) {
            tbody.innerHTML = `<tr><td colspan="4"><div class="tf-empty tf-empty--compact"><span class="tf-empty-message" style="color: var(--color-danger);">Error al cargar</span></div></td></tr>`;
        }
    }

    document.getElementById('groupsTbody').addEventListener('click', e => {
        const btn = e.target.closest('button[data-action]');
        if (!btn || btn.disabled) return;
        const id = parseInt(btn.dataset.id, 10);
        if (btn.dataset.action === 'edit') openGroupModal(_gMap.get(id));
        if (btn.dataset.action === 'del')  deleteGroup(id);
    });

    document.getElementById('btnNewGroup').addEventListener('click', () => openGroupModal(null));

    function openGroupModal(g) {
        hideErr('groupModalError');
        document.getElementById('groupModalId').value   = g ? g.id : '';
        document.getElementById('groupModalTitle').textContent = g ? 'Editar grupo' : 'Nuevo grupo';
        document.getElementById('groupModalName').value = g ? g.group_name : '';
        document.getElementById('groupModalDesc').value = g ? g.description : '';
        openModal('groupModal');
    }

    document.getElementById('btnSaveGroup').addEventListener('click', async () => {
        hideErr('groupModalError');
        const id = document.getElementById('groupModalId').value;
        const payload = { group_name: document.getElementById('groupModalName').value.trim(), description: document.getElementById('groupModalDesc').value.trim() };
        if (id) payload.id = parseInt(id, 10);
        try {
            const res  = await fetch('/api/admin/groups/', { method:id?'PUT':'POST', headers:hdr(), body:JSON.stringify(payload) });
            const json = await res.json();
            if (!json.ok) { showErr('groupModalError', json.error||'Error'); return; }
            closeModal('groupModal'); window.toast.success(id?'Grupo actualizado':'Grupo creado'); loadGroups();
        } catch(e) { showErr('groupModalError', 'Error de red'); }
    });

    async function deleteGroup(id) {
        const g   = _gMap.get(id);
        const cnt = g ? g.user_count : 0;
        const ok  = await window.dialog.confirm({
            title:'Eliminar grupo',
            message: cnt > 0 ? `Hay ${cnt} usuario(s) que quedarán sin grupo. ¿Continuar?` : '¿Eliminar este grupo?',
            confirmText:'Eliminar', cancelText:'Cancelar', variant:'danger',
        });
        if (!ok) return;
        try {
            const res  = await fetch('/api/admin/groups/?id='+id, { method:'DELETE', headers:hdr() });
            const json = await res.json();
            if (!json.ok) { window.toast.error(json.error||'Error'); return; }
            window.toast.success('Grupo eliminado'); loadGroups();
        } catch(e) { window.toast.error('Error de red'); }
    }

    /* ================================================================
       RESPONSE TEMPLATES (plantillas de respuesta al cliente)
       ================================================================ */
    const _tMap = new Map();
    let _tQuill = null;

    function ensureTemplateQuill() {
        if (_tQuill) return _tQuill;
        if (!window.Quill) return null;
        _tQuill = new Quill('#templateModalBodyEditor', {
            theme: 'snow',
            modules: {
                toolbar: [
                    ['bold', 'italic', 'underline'],
                    [{ list: 'ordered' }, { list: 'bullet' }],
                    ['link', 'blockquote'],
                    ['clean'],
                ],
            },
        });
        return _tQuill;
    }

    async function loadTemplates() {
        const tbody = document.getElementById('templatesTbody');
        tbody.innerHTML = emptyRow(7, 'Cargando…');
        try {
            const res  = await fetch('/api/admin/response-templates/');
            const json = await res.json();
            _tMap.clear();
            if (!json.templates || !json.templates.length) {
                tbody.innerHTML = emptyRow(7, 'Sin plantillas');
                return;
            }
            const frag = document.createDocumentFragment();
            json.templates.forEach(t => {
                _tMap.set(t.id, t);
                const tr = document.createElement('tr');
                tr.innerHTML =
                    `<td class="col-id">${t.id}</td>
                    <td><code>${esc(t.key)}</code></td>
                    <td>${esc(t.brand_name || 'Global')}</td>
                    <td>${esc((t.language || '').toUpperCase())}</td>
                    <td title="${esc(t.subject)}">${esc(t.subject)}</td>
                    <td class="col-status">
                        <span class="tf-pill ${t.active?'tf-pill--success':'tf-pill--danger'}">${t.active?'Activa':'Inactiva'}</span>
                    </td>
                    <td class="col-actions"><div class="tf-admin-table-actions">
                        <button class="tf-btn tf-btn--secondary tf-btn--sm" data-action="edit" data-id="${t.id}" title="Editar"><i class="fas fa-pen"></i></button>
                        <button class="tf-btn tf-btn--danger tf-btn--sm" data-action="del" data-id="${t.id}" ${t.active?'':'disabled'} title="Desactivar"><i class="fas fa-ban"></i></button>
                    </div></td>`;
                frag.appendChild(tr);
            });
            tbody.innerHTML = '';
            tbody.appendChild(frag);
        } catch(e) {
            tbody.innerHTML = `<tr><td colspan="7"><div class="tf-empty tf-empty--compact"><span class="tf-empty-message" style="color: var(--color-danger);">Error al cargar</span></div></td></tr>`;
        }
    }

    document.getElementById('templatesTbody').addEventListener('click', e => {
        const btn = e.target.closest('button[data-action]');
        if (!btn || btn.disabled) return;
        const id = parseInt(btn.dataset.id, 10);
        if (btn.dataset.action === 'edit') openTemplateModal(_tMap.get(id));
        if (btn.dataset.action === 'del')  deactivateTemplate(id);
    });

    document.getElementById('btnNewTemplate').addEventListener('click', () => openTemplateModal(null));

    function openTemplateModal(t) {
        hideErr('templateModalError');
        document.getElementById('templateModalId').value      = t ? t.id : '';
        document.getElementById('templateModalTitle').textContent = t ? 'Editar plantilla' : 'Nueva plantilla';
        document.getElementById('templateModalKey').value     = t ? t.key : '';
        document.getElementById('templateModalBrand').value   = t && t.brand_id ? t.brand_id : '';
        document.getElementById('templateModalLanguage').value = t ? t.language : 'es';
        document.getElementById('templateModalSubject').value = t ? t.subject : '';
        document.getElementById('templateModalBodyText').value = t ? (t.body_text || '') : '';
        document.getElementById('templateModalActive').value  = t ? String(t.active) : 'true';
        document.getElementById('templatePreviewArea').style.display = 'none';
        openModal('templateModal');
        const quill = ensureTemplateQuill();
        if (quill) {
            quill.clipboard.dangerouslyPasteHTML(t ? (t.body_html || '') : '');
        }
    }

    function templatePayload() {
        const quill = ensureTemplateQuill();
        return {
            key:       document.getElementById('templateModalKey').value.trim(),
            brand_id:  document.getElementById('templateModalBrand').value || null,
            language:  document.getElementById('templateModalLanguage').value,
            subject:   document.getElementById('templateModalSubject').value.trim(),
            body_html: quill ? quill.root.innerHTML : '',
            body_text: document.getElementById('templateModalBodyText').value,
            active:    document.getElementById('templateModalActive').value === 'true',
        };
    }

    document.getElementById('btnPreviewTemplate').addEventListener('click', async () => {
        hideErr('templateModalError');
        try {
            const res  = await fetch('/api/admin/response-templates/?action=preview', {
                method: 'POST', headers: hdr(), body: JSON.stringify(templatePayload()),
            });
            const json = await res.json();
            if (!json.ok) { showErr('templateModalError', json.error || 'Error en la vista previa'); return; }
            document.getElementById('templatePreviewSubject').textContent = json.preview.subject;
            document.getElementById('templatePreviewBody').innerHTML = json.preview.body_html;
            document.getElementById('templatePreviewArea').style.display = '';
        } catch(e) { showErr('templateModalError', 'Error de red'); }
    });

    document.getElementById('btnSaveTemplate').addEventListener('click', async () => {
        hideErr('templateModalError');
        const id = document.getElementById('templateModalId').value;
        const payload = templatePayload();
        if (id) payload.id = parseInt(id, 10);
        try {
            const res  = await fetch('/api/admin/response-templates/', {
                method: id ? 'PUT' : 'POST', headers: hdr(), body: JSON.stringify(payload),
            });
            const json = await res.json();
            if (!json.ok) { showErr('templateModalError', json.error || 'Error al guardar'); return; }
            closeModal('templateModal');
            window.toast.success(id ? 'Plantilla actualizada' : 'Plantilla creada');
            loadTemplates();
        } catch(e) { showErr('templateModalError', 'Error de red'); }
    });

    async function deactivateTemplate(id) {
        const ok = await window.dialog.confirm({
            title: 'Desactivar plantilla',
            message: 'La plantilla dejará de usarse (no se borra). ¿Continuar?',
            confirmText: 'Desactivar', cancelText: 'Cancelar', variant: 'danger',
        });
        if (!ok) return;
        try {
            const res  = await fetch('/api/admin/response-templates/?id=' + id, { method: 'DELETE', headers: hdr() });
            const json = await res.json();
            if (!json.ok) { window.toast.error(json.error || 'Error'); return; }
            window.toast.success('Plantilla desactivada'); loadTemplates();
        } catch(e) { window.toast.error('Error de red'); }
    }

    /* initial load */
    loadUsers();
})();
