/* Admin Console: data loading and actions for the dashboard sections (moved out of templates/admin/dashboard.html). */
// Global state
let currentPage = {
    users: 1,
    lessons: 1,
    documents: 1
};

// Load dashboard stats
function loadDashboardStats() {
    // Stats are passed from server-side rendering via data attribute
    const statsElement = document.getElementById('dashboard-stats-data');
    if (statsElement) {
        try {
            const stats = JSON.parse(statsElement.textContent || '{}');
            document.getElementById('stat-total-users').textContent = stats.total_users || 0;
            document.getElementById('stat-total-teachers').textContent = stats.total_teachers || 0;
            document.getElementById('stat-total-students').textContent = stats.total_students || 0;
            document.getElementById('stat-total-lessons').textContent = stats.total_lessons || 0;
            document.getElementById('stat-total-documents').textContent = stats.total_documents || 0;
            document.getElementById('stat-total-coupons').textContent = stats.total_coupons || 0;
            const total = stats.total_users || 0, t = stats.total_teachers || 0, st = stats.total_students || 0;
            const other = Math.max(0, total - t - st);
            const pct = n => total ? Math.round(n * 100 / total) : 0;
            [['Teachers', t], ['Students', st], ['Admins', other]].forEach(([k, n]) => {
                const bar = document.getElementById('adMix' + k), lab = document.getElementById('adMix' + k + 'Pct');
                if (bar) bar.style.width = (total ? n * 100 / total : 0) + '%';
                if (lab) lab.textContent = pct(n) + '% · ' + n;
            });
        } catch (e) {
            console.error('Error parsing dashboard stats:', e);
        }
    }
}

// Sidebar / overview navigation
function adGo(sectionName) {
    showSection(sectionName);
    adToggleSidebar(false);
    window.scrollTo({ top: 0 });
}

function adOpenUsers(role) {
    const f = document.getElementById('user-role-filter');
    if (f) f.value = role || 'all';
    adSyncUserRoleTabs();
    adGo('users');
}

// Role sub-tabs mirror #user-role-filter (the value loadUsers() reads)
function adSyncUserRoleTabs() {
    const f = document.getElementById('user-role-filter');
    const role = f ? f.value : 'all';
    document.querySelectorAll('#adUserRoleTabs .ad-stab').forEach(b => {
        const on = b.dataset.role === role;
        b.classList.toggle('active', on);
        b.setAttribute('aria-selected', String(on));
    });
}

function adSetUserRole(role) {
    const f = document.getElementById('user-role-filter');
    if (f) f.value = role;
    adSyncUserRoleTabs();
    loadUsers();
}

// Section navigation
function showSection(sectionName) {
    const target = document.getElementById(sectionName + '-section');
    if (!target) return;

    // Hide all sections, show the selected one
    document.querySelectorAll('.section').forEach(s => s.classList.add('hidden'));
    target.classList.remove('hidden');
    adSetActiveNav(sectionName);
    if (location.hash !== '#' + sectionName) history.replaceState(null, '', '#' + sectionName);

    // Load settings when settings section is shown
    if (sectionName === 'settings') {
        loadLLMSettings();
    }

    // Load section data
    if (sectionName === 'dashboard') {
        loadDashboardStats();
    } else if (sectionName === 'users') {
        loadUsers();
    } else if (sectionName === 'prompts') {
        loadRagSystemPrompt();
    } else if (sectionName === 'coupons') {
        loadCoupons();
    } else if (sectionName === 'lessons') {
        loadLessons();
    } else if (sectionName === 'documents') {
        loadDocuments();
    } else if (sectionName === 'diagnostic') {
        loadAdminDiagnostics();
    } else if (sectionName === 'theme') {
        loadAdminThemeSettings();
    } else if (sectionName === 'settings') {
        loadLLMProvider();
    }
}

// User Management
async function loadUsers(page = 1) {
    const role = document.getElementById('user-role-filter').value;
    const search = document.getElementById('user-search').value;
    // search fires per keystroke: ignore responses that arrive after a newer request
    const seq = loadUsers._seq = (loadUsers._seq || 0) + 1;

    try {
        const params = new URLSearchParams({
            role: role,
            search: search,
            page: page,
            per_page: 50
        });

        const response = await fetch(`/admin/users?${params}`);
        const data = await response.json();
        if (seq !== loadUsers._seq) return;

        if (data.success) {
            renderUsersTable(data.users);
            renderPagination('users-pagination', data, loadUsers);
        } else {
            showNotification('Error loading users: ' + data.error, 'error');
        }
    } catch (error) {
        console.error('Error loading users:', error);
        showNotification('Error loading users', 'error');
    }
}

function adEsc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const AD_ROLE_TONE = { admin: 'navy', teacher: 'green', student: 'purple' };

const AD_ICON_BASE = '/static/admin/icons/';

// Empty state in the teacher/student tone: icon, one line, optional call to action
function adEmptyRow(cols, title, sub = '', opts = {}) {
    const ic = opts.img
        ? `<img class="ad-empty-img" src="${AD_ICON_BASE}${opts.img}" alt="">`
        : `<span class="ad-empty-ic"><i class="fas ${opts.icon || 'fa-inbox'}"></i></span>`;
    const cta = opts.cta ? `<button type="button" class="ad-btn ad-btn-primary ad-btn-sm" onclick="${opts.cta.onclick}"><i class="fas fa-plus"></i>${opts.cta.label}</button>` : '';
    return `<tr><td colspan="${cols}" class="ad-empty-cell"><div class="ad-empty">${ic}<h3>${title}</h3>${sub ? `<p>${sub}</p>` : ''}${cta}</div></td></tr>`;
}

function renderUsersTable(users) {
    const tbody = document.getElementById('users-tbody');
    if (users.length === 0) {
        tbody.innerHTML = adEmptyRow(6, 'No users found', 'Try another search or role.', { icon: 'fa-user-slash' });
        return;
    }

    tbody.innerHTML = users.map(user => {
        const tone = AD_ROLE_TONE[user.role] || 'grey';
        const tier = String(user.subscription_tier || 'free').replace('_', ' ');
        return `
        <tr>
            <td class="id">${user.id}</td>
            <td><div class="ad-user-cell"><span class="ad-initial ${tone}">${adEsc((user.full_name || user.username || '?').charAt(0).toUpperCase())}</span><span><b>${adEsc(user.full_name || user.username)}</b><br><small class="muted">${adEsc(user.username)}</small></span></div></td>
            <td class="muted">${adEsc(user.useremail)}</td>
            <td><span class="ad-pill ${tone}">${adEsc(user.role)}</span></td>
            <td><span class="ad-pill ${tier === 'free' ? 'grey' : ''}">${adEsc(tier)}</span></td>
            <td class="actions">
                <button type="button" onclick="editUser(${user.id})" class="ad-icon-btn" title="Edit user" aria-label="Edit ${adEsc(user.username)}"><i class="fas fa-pen"></i></button>
                <button type="button" onclick="changeUserPassword(${user.id})" class="ad-icon-btn warn" title="Change password" aria-label="Change password for ${adEsc(user.username)}"><i class="fas fa-key"></i></button>
                <button type="button" onclick="deleteUser(${user.id})" class="ad-icon-btn danger" title="Delete user" aria-label="Delete ${adEsc(user.username)}"><i class="fas fa-trash"></i></button>
            </td>
        </tr>`;
    }).join('');
}

// Global Prompt Management
function estimateTokensRough(text) {
    if (!text) return 0;
    return Math.max(0, Math.floor(text.length / 4));
}

function renderRagPromptLimits(limits) {
    const box = document.getElementById('rag-prompt-limits-box');
    const doc = document.getElementById('rag-prompt-limits-doc');
    const list = document.getElementById('rag-prompt-limits-list');
    if (!limits || !box || !list) return;
    box.classList.remove('hidden');
    if (doc) doc.textContent = limits.doc_note || '';
    const max = limits.max_system_body_tokens_estimated;
    document.querySelectorAll('.rag-max-tok').forEach(el => { el.textContent = max != null ? String(max) : '—'; });
    list.innerHTML = `
        <li>Model context window: ${limits.context_window_tokens?.toLocaleString()} tokens (Groq model docs)</li>
        <li>Reserved for completion: ${limits.reserved_output_tokens?.toLocaleString()} tokens</li>
        <li>Reserved for chat history + tools: ${limits.reserved_history_tokens?.toLocaleString()} tokens</li>
        <li>Reserved for user custom RAG prompt: ${limits.reserved_user_custom_rag_prompt_tokens?.toLocaleString()} tokens</li>
        <li>Reserved misc (overhead): ${limits.reserved_misc_tokens?.toLocaleString()} tokens</li>
        <li><strong>Max per admin field (estimated): ${max?.toLocaleString()} tokens</strong> (~4 characters per token)</li>
    `;
}

function updateRagAdminTokenEstimates() {
    const w = document.getElementById('rag-prompt-with-pdf');
    const n = document.getElementById('rag-prompt-no-pdf');
    const ew = document.getElementById('rag-est-with-pdf');
    const en = document.getElementById('rag-est-no-pdf');
    const maxEl = document.querySelector('.rag-max-tok');
    const max = maxEl && maxEl.textContent !== '—' ? parseInt(maxEl.textContent.replace(/,/g, ''), 10) : null;
    const tw = w ? estimateTokensRough(w.value) : 0;
    const tn = n ? estimateTokensRough(n.value) : 0;
    if (ew) {
        ew.textContent = tw.toLocaleString();
        ew.classList.toggle('text-red-600', max != null && !isNaN(max) && tw > max);
    }
    if (en) {
        en.textContent = tn.toLocaleString();
        en.classList.toggle('text-red-600', max != null && !isNaN(max) && tn > max);
    }
}

async function loadRagSystemPrompt() {
    const statusEl = document.getElementById('rag-prompt-status');
    try {
        const response = await fetch('/admin/prompt/rag');
        const data = await response.json();

        if (data.success) {
            document.getElementById('rag-prompt-with-pdf').value = data.prompt_with_pdf || '';
            document.getElementById('rag-prompt-no-pdf').value = data.prompt_no_pdf || '';
            if (data.limits) renderRagPromptLimits(data.limits);
            updateRagAdminTokenEstimates();
            if (statusEl) {
                const parts = [];
                parts.push(data.using_defaults_with_pdf ? 'with PDF: built-in default' : 'with PDF: custom saved');
                parts.push(data.using_defaults_no_pdf ? 'no PDF: built-in default' : 'no PDF: custom saved');
                if (data.updated_at) {
                    parts.push('last updated: ' + new Date(data.updated_at).toLocaleString());
                }
                statusEl.textContent = parts.join(' · ');
            }
        } else {
            showNotification('Error loading RAG prompt: ' + data.error, 'error');
        }
    } catch (error) {
        console.error('Error loading RAG prompt:', error);
        showNotification('Error loading RAG prompt', 'error');
    }
}

async function saveRagSystemPrompt() {
    const body = {
        prompt_with_pdf: document.getElementById('rag-prompt-with-pdf').value,
        prompt_no_pdf: document.getElementById('rag-prompt-no-pdf').value
    };

    try {
        const response = await fetch('/admin/prompt/rag', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        const data = await response.json();

        if (data.success) {
            showNotification('RAG system prompts saved.', 'success');
            loadRagSystemPrompt();
        } else {
            let msg = data.error || 'Save failed';
            if (data.code === 'RAG_PROMPT_TOO_LONG' && data.limits) {
                renderRagPromptLimits(data.limits);
                updateRagAdminTokenEstimates();
            }
            showNotification(msg, 'error');
        }
    } catch (error) {
        console.error('Error saving RAG prompt:', error);
        showNotification('Error saving RAG prompt', 'error');
    }
}

async function resetRagSystemPrompt() {
    if (!confirm('Reset RAG system prompts to built-in defaults?')) return;

    try {
        const response = await fetch('/admin/prompt/rag', { method: 'DELETE' });
        const data = await response.json();

        if (data.success) {
            showNotification('RAG prompts reset to defaults.', 'success');
            loadRagSystemPrompt();
        } else {
            showNotification('Error: ' + data.error, 'error');
        }
    } catch (error) {
        console.error('Error resetting RAG prompt:', error);
        showNotification('Error resetting RAG prompt', 'error');
    }
}

// Coupon Management
async function loadCoupons() {
    try {
        const response = await fetch('/admin/coupons');
        const data = await response.json();

        if (data.success) {
            renderCouponsTable(data.coupons);
        } else {
            showNotification('Error loading coupons: ' + data.error, 'error');
        }
    } catch (error) {
        console.error('Error loading coupons:', error);
        showNotification('Error loading coupons', 'error');
    }
}

function renderCouponsTable(coupons) {
    const tbody = document.getElementById('coupons-tbody');
    if (coupons.length === 0) {
        tbody.innerHTML = adEmptyRow(6, 'No coupons yet', 'Create a coupon to grant Pro or Pro Plus access.', { icon: 'fa-ticket-alt', cta: { label: 'Create Coupon', onclick: 'showCreateCouponModal()' } });
        return;
    }

    tbody.innerHTML = coupons.map(coupon => `
        <tr>
            <td class="strong"><span class="ad-code">${adEsc(coupon.code)}</span></td>
            <td><span class="ad-pill">${adEsc(String(coupon.subscription_tier || '').replace('_', ' '))}</span></td>
            <td>${coupon.used_count}</td>
            <td class="muted">${coupon.max_uses || 'Unlimited'}</td>
            <td><span class="ad-pill ${coupon.is_active ? 'green' : 'red'}">${coupon.is_active ? 'Active' : 'Inactive'}</span></td>
            <td class="actions">
                <button type="button" onclick="deleteCoupon(${coupon.id})" class="ad-icon-btn danger" title="Delete coupon" aria-label="Delete coupon ${adEsc(coupon.code)}"><i class="fas fa-trash"></i></button>
            </td>
        </tr>
    `).join('');
}

// Lesson Management
async function loadLessons() {
    try {
        const response = await fetch('/admin/lessons');
        const data = await response.json();

        if (data.success) {
            renderLessonsTable(data.lessons);
        } else {
            showNotification('Error loading lessons: ' + data.error, 'error');
        }
    } catch (error) {
        console.error('Error loading lessons:', error);
        showNotification('Error loading lessons', 'error');
    }
}

function renderLessonsTable(lessons) {
    const tbody = document.getElementById('lessons-tbody');
    if (lessons.length === 0) {
        tbody.innerHTML = adEmptyRow(5, 'No lessons yet', 'Lessons appear here as soon as teachers create them.', { img: 'my-lessons.png' });
        return;
    }

    tbody.innerHTML = lessons.map(lesson => `
        <tr>
            <td class="id">${lesson.id}</td>
            <td class="strong">${adEsc(lesson.title)}</td>
            <td class="muted">${adEsc(lesson.teacher_name)}</td>
            <td class="muted">${new Date(lesson.created_at).toLocaleDateString()}</td>
            <td class="actions">
                <button type="button" onclick="deleteLesson(${lesson.id})" class="ad-icon-btn danger" title="Delete lesson" aria-label="Delete lesson ${adEsc(lesson.title)}"><i class="fas fa-trash"></i></button>
            </td>
        </tr>
    `).join('');
}

// Document Management
async function loadDocuments() {
    try {
        const response = await fetch('/admin/documents');
        const data = await response.json();

        if (data.success) {
            renderDocumentsTable(data.documents);
        } else {
            showNotification('Error loading documents: ' + data.error, 'error');
        }
    } catch (error) {
        console.error('Error loading documents:', error);
        showNotification('Error loading documents', 'error');
    }
}

function renderDocumentsTable(documents) {
    const tbody = document.getElementById('documents-tbody');
    if (documents.length === 0) {
        tbody.innerHTML = adEmptyRow(6, 'No documents yet', 'PDFs uploaded for chat and lessons will be listed here.', { icon: 'fa-file-pdf' });
        return;
    }

    tbody.innerHTML = documents.map(doc => `
        <tr>
            <td class="id">${doc.id}</td>
            <td class="strong"><i class="fas fa-file-pdf" style="color:#dc2626;margin-right:8px;"></i>${adEsc(doc.file_name)}</td>
            <td class="muted">${adEsc(doc.username)}</td>
            <td class="muted">${formatFileSize(doc.file_size)}</td>
            <td class="muted">${new Date(doc.uploaded_at).toLocaleDateString()}</td>
            <td class="actions">
                <button type="button" onclick="deleteDocument(${doc.id})" class="ad-icon-btn danger" title="Delete document" aria-label="Delete ${adEsc(doc.file_name)}"><i class="fas fa-trash"></i></button>
            </td>
        </tr>
    `).join('');
}

// Utility functions
function formatFileSize(bytes) {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
}

function showNotification(message, type = 'info') {
    const toast = document.getElementById('notification-toast');
    const messageEl = document.getElementById('toast-message');
    const iconEl = document.getElementById('toast-icon');

    messageEl.textContent = message;

    const icons = { success: 'fa-check', error: 'fa-exclamation', warning: 'fa-exclamation-triangle', info: 'fa-info' };
    const kind = icons[type] ? type : 'info';
    toast.className = `ad-toast ${kind}`;
    iconEl.innerHTML = `<i class="fas ${icons[kind]}"></i>`;

    clearTimeout(showNotification._t);
    showNotification._t = setTimeout(() => {
        hideNotification();
    }, 5000);
}

function hideNotification() {
    document.getElementById('notification-toast').classList.add('hidden');
}

function closeModal(modalId) {
    document.getElementById(modalId).classList.add('hidden');
}

function openModal(modalId) {
    document.getElementById(modalId).classList.remove('hidden');
}

function renderPagination(containerId, data, loadFunction) {
    const container = document.getElementById(containerId);
    if (!container || data.total_pages <= 1) {
        if (container) container.innerHTML = '';
        return;
    }

    let html = '<div class="ad-pagination">';
    html += `<span>Showing ${(data.page - 1) * data.per_page + 1} to ${Math.min(data.page * data.per_page, data.total)} of ${data.total} results</span>`;
    html += '<div style="display:flex;gap:8px;">';
    html += `<button type="button" class="ad-btn ad-btn-outline ad-btn-sm" ${data.page > 1 ? `onclick="${loadFunction.name}(${data.page - 1})"` : 'disabled'}>Previous</button>`;
    html += `<button type="button" class="ad-btn ad-btn-outline ad-btn-sm" ${data.page < data.total_pages ? `onclick="${loadFunction.name}(${data.page + 1})"` : 'disabled'}>Next</button>`;
    html += '</div></div>';
    container.innerHTML = html;
}

// Create User
function showCreateUserModal() {
    // Reset form
    document.getElementById('create-user-form').reset();
    openModal('create-user-modal');
}

async function createUser(event) {
    event.preventDefault();

    const data = {
        username: document.getElementById('create-username').value.trim(),
        full_name: document.getElementById('create-full-name').value.trim(),
        useremail: document.getElementById('create-email').value.trim(),
        password: document.getElementById('create-password').value,
        role: document.getElementById('create-role').value,
        class_standard: document.getElementById('create-class-standard').value.trim() || 'N/A',
        medium: document.getElementById('create-medium').value.trim() || 'N/A'
    };
    if (data.role === 'student' && data.class_standard === 'N/A') {
        // a student's grade decides which diagnostic they get
        showNotification('Class Standard (grade 1-12) is required for students', 'error');
        return;
    }

    try {
        const response = await fetch('/admin/users', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        const result = await response.json();

        if (result.success) {
            showNotification('User created successfully!', 'success');
            closeModal('create-user-modal');
            loadUsers(); // Refresh user list
        } else {
            showNotification('Error: ' + (result.error || 'Failed to create user'), 'error');
        }
    } catch (error) {
        console.error('Error creating user:', error);
        showNotification('Error creating user: ' + error.message, 'error');
    }
}

// Edit User
async function editUser(userId) {
    try {
        // Load user data directly by ID
        const response = await fetch(`/admin/users/${userId}`);
        const data = await response.json();

        if (data.success && data.user) {
            const user = data.user;
            document.getElementById('edit-user-id').value = user.id;
            document.getElementById('edit-username').value = user.username;
            document.getElementById('edit-full-name').value = user.full_name || user.username;
            document.getElementById('edit-email').value = user.useremail;
            document.getElementById('edit-role').value = user.role;
            document.getElementById('edit-class-standard').value = user.class_standard || '';
            document.getElementById('edit-medium').value = user.medium || '';
            document.getElementById('edit-subscription-tier').value = user.subscription_tier || 'free';

            openModal('edit-user-modal');
        } else {
            showNotification('Error: ' + (data.error || 'User not found'), 'error');
        }
    } catch (error) {
        console.error('Error loading user:', error);
        showNotification('Error loading user: ' + error.message, 'error');
    }
}

async function updateUser(event) {
    event.preventDefault();

    const userId = document.getElementById('edit-user-id').value;
    const data = {
        username: document.getElementById('edit-username').value.trim(),
        full_name: document.getElementById('edit-full-name').value.trim(),
        useremail: document.getElementById('edit-email').value.trim(),
        role: document.getElementById('edit-role').value,
        class_standard: document.getElementById('edit-class-standard').value.trim() || 'N/A',
        medium: document.getElementById('edit-medium').value.trim() || 'N/A',
        subscription_tier: document.getElementById('edit-subscription-tier').value
    };

    try {
        const response = await fetch(`/admin/users/${userId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        const result = await response.json();

        if (result.success) {
            showNotification('User updated successfully!', 'success');
            closeModal('edit-user-modal');
            loadUsers(); // Refresh user list
        } else {
            showNotification('Error: ' + (result.error || 'Failed to update user'), 'error');
        }
    } catch (error) {
        console.error('Error updating user:', error);
        showNotification('Error updating user: ' + error.message, 'error');
    }
}

// Create Coupon
function showCreateCouponModal() {
    // Reset form
    document.getElementById('create-coupon-form').reset();
    openModal('create-coupon-modal');
}

async function createCoupon(event) {
    event.preventDefault();

    const code = document.getElementById('create-coupon-code').value.trim().toUpperCase();
    const data = {
        code: code,
        subscription_tier: document.getElementById('create-coupon-tier').value,
        description: document.getElementById('create-coupon-description').value.trim(),
        max_uses: document.getElementById('create-coupon-max-uses').value || null,
        expires_at: document.getElementById('create-coupon-expires').value || null
    };

    // Convert expires_at to ISO format if provided
    if (data.expires_at) {
        const date = new Date(data.expires_at);
        data.expires_at = date.toISOString();
    }

    // Convert max_uses to integer if provided
    if (data.max_uses) {
        data.max_uses = parseInt(data.max_uses);
    }

    try {
        const response = await fetch('/admin/coupons', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        const result = await response.json();

        if (result.success) {
            showNotification('Coupon created successfully!', 'success');
            closeModal('create-coupon-modal');
            loadCoupons(); // Refresh coupon list
        } else {
            showNotification('Error: ' + (result.error || 'Failed to create coupon'), 'error');
        }
    } catch (error) {
        console.error('Error creating coupon:', error);
        showNotification('Error creating coupon: ' + error.message, 'error');
    }
}

function changeUserPassword(userId) {
    const newPassword = prompt('Enter new password:');
    if (newPassword) {
        fetch(`/admin/users/${userId}/change-password`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: newPassword })
        })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    showNotification('Password changed successfully!', 'success');
                } else {
                    showNotification('Error: ' + data.error, 'error');
                }
            });
    }
}

function deleteUser(userId) {
    if (!confirm('Are you sure you want to delete this user?')) return;

    fetch(`/admin/users/${userId}`, { method: 'DELETE' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showNotification('User deleted successfully!', 'success');
                loadUsers();
            } else {
                showNotification('Error: ' + data.error, 'error');
            }
        });
}

function deleteCoupon(couponId) {
    if (!confirm('Are you sure you want to delete this coupon?')) return;

    fetch(`/admin/coupons/${couponId}`, { method: 'DELETE' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showNotification('Coupon deleted successfully!', 'success');
                loadCoupons();
            } else {
                showNotification('Error: ' + data.error, 'error');
            }
        });
}

function deleteLesson(lessonId) {
    if (!confirm('Are you sure you want to delete this lesson?')) return;

    fetch(`/admin/lessons/${lessonId}`, { method: 'DELETE' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showNotification('Lesson deleted successfully!', 'success');
                loadLessons();
            } else {
                showNotification('Error: ' + data.error, 'error');
            }
        });
}

function deleteDocument(docId) {
    if (!confirm('Are you sure you want to delete this document?')) return;

    fetch(`/admin/documents/${docId}`, { method: 'DELETE' })
        .then(r => r.json())
        .then(data => {
            if (data.success) {
                showNotification('Document deleted successfully!', 'success');
                loadDocuments();
            } else {
                showNotification('Error: ' + data.error, 'error');
            }
        });
}

// LLM Provider Management
async function loadLLMProvider() {
    try {
        const response = await fetch('/admin/settings/llm-provider');
        const data = await response.json();

        if (data.success) {
            const provider = data.provider || 'openai';
            document.getElementById('provider-openai').checked = provider === 'openai';
            document.getElementById('provider-groq').checked = provider === 'groq';
        } else {
            showNotification('Error loading LLM provider: ' + data.error, 'error');
        }
    } catch (error) {
        console.error('Error loading LLM provider:', error);
        showNotification('Error loading LLM provider', 'error');
    }
}

// Load LLM settings
async function loadLLMSettings() {
    try {
        const response = await fetch('/admin/settings/llm');
        const data = await response.json();

        if (data.success) {
            const settings = data.settings;

            // Set active provider
            document.querySelector(`input[name="active-provider"][value="${settings.active_provider}"]`).checked = true;

            // OpenAI settings
            if (settings.openai_api_key_set) {
                document.getElementById('openai-key-status').textContent = '✓ Key is set';
                document.getElementById('openai-key-status').classList.add('text-green-600');
            } else {
                document.getElementById('openai-key-status').textContent = '⚠ Not set';
                document.getElementById('openai-key-status').classList.add('text-yellow-600');
            }
            document.getElementById('openai-default-model').value = settings.openai_default_model;
            document.getElementById('openai-allow-user-selection').checked = settings.openai_allow_user_model_selection;

            // Groq settings
            if (settings.groq_api_key_set) {
                document.getElementById('groq-key-status').textContent = '✓ Key is set';
                document.getElementById('groq-key-status').classList.add('text-green-600');
            } else {
                document.getElementById('groq-key-status').textContent = '⚠ Not set';
                document.getElementById('groq-key-status').classList.add('text-yellow-600');
            }
            document.getElementById('groq-default-model').value = settings.groq_default_model;
            document.getElementById('groq-allow-user-selection').checked = settings.groq_allow_user_model_selection;
            document.getElementById('rag-answer-quality-gate').checked = settings.rag_answer_quality_gate_enabled !== false;
        }
    } catch (error) {
        console.error('Error loading LLM settings:', error);
    }
}

// Save LLM settings
async function saveLLMSettings() {
    const activeProvider = document.querySelector('input[name="active-provider"]:checked');

    if (!activeProvider) {
        showNotification('Please select an active provider', 'warning');
        return;
    }

    const settings = {
        active_provider: activeProvider.value,
        openai_api_key: document.getElementById('openai-api-key').value.trim(),
        openai_default_model: document.getElementById('openai-default-model').value,
        openai_allow_user_model_selection: document.getElementById('openai-allow-user-selection').checked,
        groq_api_key: document.getElementById('groq-api-key').value.trim(),
        groq_default_model: document.getElementById('groq-default-model').value,
        groq_allow_user_model_selection: document.getElementById('groq-allow-user-selection').checked,
        rag_answer_quality_gate_enabled: document.getElementById('rag-answer-quality-gate').checked
    };

    // Only include API keys if they were provided (non-empty)
    if (!settings.openai_api_key) {
        delete settings.openai_api_key;
    }
    if (!settings.groq_api_key) {
        delete settings.groq_api_key;
    }

    try {
        const response = await fetch('/admin/settings/llm', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(settings)
        });

        const data = await response.json();

        if (data.success) {
            showNotification('LLM settings saved successfully!', 'success');
            // Clear password fields
            document.getElementById('openai-api-key').value = '';
            document.getElementById('groq-api-key').value = '';
            // Reload settings to update status
            loadLLMSettings();
        } else {
            showNotification('Error saving settings: ' + data.error, 'error');
        }
    } catch (error) {
        console.error('Error saving LLM settings:', error);
        showNotification('Error saving LLM settings', 'error');
    }
}

// Legacy function for backward compatibility
async function saveLLMProvider() {
    const selectedProvider = document.querySelector('input[name="llm-provider"]:checked');

    if (!selectedProvider) {
        showNotification('Please select a provider', 'warning');
        return;
    }

    try {
        const response = await fetch('/admin/settings/llm-provider', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ provider: selectedProvider.value })
        });

        const data = await response.json();

        if (data.success) {
            showNotification(`LLM provider set to ${data.provider.toUpperCase()} successfully!`, 'success');
        } else {
            showNotification('Error saving provider: ' + data.error, 'error');
        }
    } catch (error) {
        console.error('Error saving LLM provider:', error);
        showNotification('Error saving LLM provider', 'error');
    }
}

// Initialize dashboard on load
document.addEventListener('DOMContentLoaded', function () {
    loadDashboardStats();
    const today = document.getElementById('adTodayLabel');
    if (today) today.textContent = new Date().toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
    // Deep links (/admin/#users) from the sidebar on Load Testing / LLM Telemetry, and reloads
    const initial = location.hash.slice(1);
    if (initial && initial !== 'dashboard' && document.getElementById(initial + '-section')) showSection(initial);
});
window.addEventListener('hashchange', function () {
    const h = location.hash.slice(1);
    if (h && document.getElementById(h + '-section')) showSection(h);
});
