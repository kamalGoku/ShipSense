/**
 * ShipSense — Shipments page logic.
 * Depends on sse.js (escapeHtml, apiHeaders, runCommandStream) loaded first.
 */

/* --------------------------------------------------------------------------
   Inline SVG icons (Lucide, 24x24, stroke 1.5, currentColor)
   -------------------------------------------------------------------------- */
const ICONS = {
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path><polyline points="22 4 12 14.01 9 11.01"></polyline></svg>',
    alert: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"></path><path d="M12 9v4"></path><path d="M12 17h.01"></path></svg>',
    inbox: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="22 12 16 12 14 15 10 15 8 12 2 12"></polyline><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"></path></svg>',
    chevronUp: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="18 15 12 9 6 15"></polyline></svg>',
    chevronDown: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="6 9 12 15 18 9"></polyline></svg>'
};

/* --------------------------------------------------------------------------
   Tab switching (Pending Orders / All Orders)
   -------------------------------------------------------------------------- */
function switchTab(tab) {
    document.querySelectorAll('.filter-btn[data-tab]').forEach(btn => {
        const active = btn.dataset.tab === tab;
        btn.classList.toggle('active', active);
        btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });

    document.getElementById('panel-pending').classList.toggle('hidden', tab !== 'pending');
    document.getElementById('panel-orders').classList.toggle('hidden', tab !== 'orders');

    if (tab === 'orders') fetchState();
    if (tab === 'pending') fetchPendingOrders();
}

/* --------------------------------------------------------------------------
   Toast notifications (bottom-right, slide-in, auto-dismiss 4s)
   -------------------------------------------------------------------------- */
let toastTimeoutId = null;

function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    const icon = toast.querySelector('.toast-icon');
    const msg = toast.querySelector('.toast-message');

    toast.classList.remove('toast-success', 'toast-error', 'toast-warning', 'toast-info');
    toast.classList.add(type === 'error' ? 'toast-error' : 'toast-success');
    icon.innerHTML = type === 'error' ? ICONS.alert : ICONS.check;
    msg.textContent = message;

    toast.classList.add('visible');
    if (toastTimeoutId) clearTimeout(toastTimeoutId);
    toastTimeoutId = setTimeout(() => {
        toast.classList.remove('visible');
        toastTimeoutId = null;
    }, 4000);
}

/* --------------------------------------------------------------------------
   Sync log (collapsible terminal output)
   -------------------------------------------------------------------------- */
const syncLogSection = document.getElementById('sync-log-section');
const syncLogOutput = document.getElementById('sync-log-output');
let syncLogOpen = false;
let commandRunning = false;

function setSyncLogOpen(open) {
    syncLogOpen = open;
    syncLogSection.style.maxHeight = open ? '320px' : '0';
    const btn = document.getElementById('btn-collapse-log');
    if (btn) {
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
        btn.innerHTML = (open ? ICONS.chevronUp : ICONS.chevronDown) + (open ? ' Collapse' : ' Expand');
    }
}

function toggleSyncLog() {
    setSyncLogOpen(!syncLogOpen);
}

function appendToTerminal(text, type = '') {
    const line = document.createElement('div');
    line.className = type ? `log-${type}` : '';
    line.textContent = text;

    // Auto-color lines based on content
    if (!type) {
        const lower = text.toLowerCase();
        if (lower.includes('error') || lower.includes('failed')) {
            line.classList.add('log-error');
        } else if (lower.includes('success')) {
            line.classList.add('log-success');
        }
    }

    syncLogOutput.appendChild(line);
    syncLogOutput.scrollTop = syncLogOutput.scrollHeight;
}

function clearTerminal() {
    syncLogOutput.innerHTML = '';
}

/* --------------------------------------------------------------------------
   Command execution via shared SSE helper (sse.js)
   -------------------------------------------------------------------------- */
function runCommand(cmdName, orders = null, onDone = null) {
    if (commandRunning) {
        showToast('A command is already running.', 'error');
        return;
    }
    commandRunning = true;

    // Slide the sync log open
    setSyncLogOpen(true);
    appendToTerminal(`> Running: ${cmdName}${orders ? ' IDs: ' + orders : ''}`, 'time');

    runCommandStream(cmdName, orders, {
        onLog: (text) => appendToTerminal(text),
        onComplete: (code) => {
            commandRunning = false;
            appendToTerminal(`> Process finished with code ${code}`, 'time');
            if (onDone) {
                onDone(code);
            } else {
                fetchState(); // Refresh dashboard stats
                showToast(code === 0 ? 'Task completed' : 'Task finished with errors', code === 0 ? 'success' : 'error');
            }
        }
    });
}

function runManualPrint() {
    const input = document.getElementById('manual-print-id');
    const id = input.value.trim();
    if (!id) {
        showToast('Please enter an ID', 'error');
        return;
    }
    if (!/^[A-Za-z0-9_-]+$/.test(id)) {
        showToast('Invalid ID: only letters, numbers, dashes and underscores allowed', 'error');
        return;
    }
    runCommand(`print-order ${id}`);
    input.value = '';
}

/* --------------------------------------------------------------------------
   Loading / empty state helpers
   -------------------------------------------------------------------------- */
function renderSkeletonRows(tbody, cols) {
    tbody.innerHTML = '';
    for (let i = 0; i < 3; i++) {
        const tr = document.createElement('tr');
        for (let c = 0; c < cols; c++) {
            const td = document.createElement('td');
            const bar = document.createElement('div');
            bar.className = 'skeleton';
            bar.style.width = (c === 0 ? '60%' : '80%');
            td.appendChild(bar);
            tr.appendChild(td);
        }
        tbody.appendChild(tr);
    }
}

function renderEmptyState(tbody, cols, title, text) {
    tbody.innerHTML = `
        <tr>
            <td colspan="${cols}" style="height: auto;">
                <div class="empty-state">
                    ${ICONS.inbox}
                    <div class="empty-state-title">${escapeHtml(title)}</div>
                    <div class="empty-state-text">${escapeHtml(text)}</div>
                </div>
            </td>
        </tr>
    `;
}

/* --------------------------------------------------------------------------
   Stat value tween (0 -> value over 300ms)
   -------------------------------------------------------------------------- */
function animateStat(id, target) {
    const el = document.getElementById(id);
    if (!el) return;
    const end = Number(target) || 0;
    const duration = 300;
    const start = performance.now();

    function frame(now) {
        const t = Math.min((now - start) / duration, 1);
        el.textContent = Math.round(end * t);
        if (t < 1) requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
}

/* --------------------------------------------------------------------------
   State fetching (stats + All Orders table)
   -------------------------------------------------------------------------- */
async function fetchState() {
    const tbody = document.getElementById('orders-table-body');
    try {
        renderSkeletonRows(tbody, 7);

        const res = await fetch('/api/state', { headers: apiHeaders() });
        const data = await res.json();

        if (data.error) {
            console.error(data.error);
            renderEmptyState(tbody, 7, 'Could not load orders', String(data.error));
            return;
        }

        const orders = data.orders || [];

        // Update stat cards (animated)
        animateStat('stat-total', orders.length);
        animateStat('stat-synced', orders.filter(o => o.synced_to_amazon).length);
        animateStat('stat-printed', orders.filter(o => o.label_printed).length);
        animateStat('stat-errors', orders.filter(o => o.error && o.error !== 'null').length);

        // Populate table
        tbody.innerHTML = '';

        if (orders.length === 0) {
            renderEmptyState(tbody, 7, 'No orders yet', 'Run a sync to populate order state.');
            return;
        }

        // Reverse to show newest first
        [...orders].reverse().forEach(order => {
            const tr = document.createElement('tr');

            const syncBadge = order.synced_to_amazon
                ? '<span class="badge badge-success">Yes</span>'
                : '<span class="badge badge-warning">No</span>';

            const printBadge = order.label_printed
                ? '<span class="badge badge-success">Yes</span>'
                : '<span class="badge badge-warning">No</span>';

            const channel = order.amazon_order_id && order.amazon_order_id.startsWith('LIRIYA')
                ? 'WooCommerce'
                : 'Amazon';

            tr.innerHTML = `
                <td class="font-mono">${escapeHtml(order.amazon_order_id || '-')}</td>
                <td>${escapeHtml(channel)}</td>
                <td class="font-mono">${escapeHtml(order.awb_number || '-')}</td>
                <td>${escapeHtml(order.courier_name || '-')}</td>
                <td>${syncBadge}</td>
                <td>${printBadge}</td>
            `;
            tbody.appendChild(tr);
        });

    } catch (e) {
        console.error('Failed to fetch state', e);
        renderEmptyState(tbody, 7, 'Error loading orders', 'Check the server connection and try again.');
    }
}

/* --------------------------------------------------------------------------
   Pending orders
   -------------------------------------------------------------------------- */
function statusBadgeClass(status) {
    const s = String(status || '').toLowerCase();
    if (s.includes('ship')) return 'badge-success';
    if (s.includes('cancel')) return 'badge-danger';
    if (s.includes('pend') || s.includes('process') || s.includes('unship')) return 'badge-warning';
    return 'badge-info'; // New / unknown
}

async function fetchPendingOrders() {
    const tbody = document.getElementById('pending-orders-body');
    if (!tbody) return;

    try {
        renderSkeletonRows(tbody, 7);

        const res = await fetch('/api/pending-orders', { headers: apiHeaders() });
        const data = await res.json();
        const orders = data.pending_orders || [];

        if (data.errors && data.errors.length) {
            data.errors.forEach(err => appendToTerminal(`Pending orders: ${err}`, 'error'));
        }

        tbody.innerHTML = '';
        if (orders.length === 0) {
            renderEmptyState(tbody, 7, 'No pending orders', 'All orders are shipped. Refresh to check again.');
            const allCb = document.getElementById('selectAllPending');
            if (allCb) allCb.checked = false;
            updateSyncButtonState();
            return;
        }

        orders.forEach(order => {
            const tr = document.createElement('tr');

            const sourceBadge = order.source.toLowerCase().includes('amazon')
                ? `<span class="badge badge-primary">${escapeHtml(order.source)}</span>`
                : `<span class="badge badge-warning">${escapeHtml(order.source)}</span>`;

            const dateStr = order.date ? new Date(order.date).toLocaleString() : '-';

            tr.innerHTML = `
                <td>
                    <input type="checkbox" class="pending-order-cb" data-source="${escapeHtml(order.source)}" onclick="updateSyncButtonState()" aria-label="Select order">
                </td>
                <td>${sourceBadge}</td>
                <td class="font-mono">${escapeHtml(order.order_id || '-')}</td>
                <td>${escapeHtml(dateStr)}</td>
                <td><span class="badge ${statusBadgeClass(order.status)}">${escapeHtml(order.status || '-')}</span></td>
                <td><span class="badge badge-info">${escapeHtml(order.gateway || '-')}</span></td>
                <td>${escapeHtml(order.items != null ? order.items : '-')}</td>
            `;
            // Set the checkbox value via the property so untrusted IDs can't
            // break out of the attribute.
            tr.querySelector('.pending-order-cb').value = order.order_id != null ? String(order.order_id) : '';
            tbody.appendChild(tr);
        });

        // Reset selection state
        const allCb = document.getElementById('selectAllPending');
        if (allCb) allCb.checked = false;
        updateSyncButtonState();

    } catch (e) {
        console.error('Failed to fetch pending orders', e);
        renderEmptyState(tbody, 7, 'Error loading pending orders', 'Check the server connection and try again.');
    }
}

/* --------------------------------------------------------------------------
   Selection logic
   -------------------------------------------------------------------------- */
function toggleAllPending(sourceCheckbox) {
    const checkboxes = document.querySelectorAll('.pending-order-cb');
    checkboxes.forEach(cb => cb.checked = sourceCheckbox.checked);
    updateSyncButtonState();
}

function updateSyncButtonState() {
    const checkboxes = document.querySelectorAll('.pending-order-cb');
    const checkedBoxes = document.querySelectorAll('.pending-order-cb:checked');
    const anyChecked = checkedBoxes.length > 0;
    const allChecked = checkboxes.length > 0 && checkboxes.length === checkedBoxes.length;

    const btn = document.getElementById('btn-sync-selected');
    if (btn) btn.disabled = !anyChecked;

    // Auto-check or uncheck the select-all box
    const allCb = document.getElementById('selectAllPending');
    if (allCb) allCb.checked = allChecked;
}

function runSelectedSync() {
    const checked = Array.from(document.querySelectorAll('.pending-order-cb:checked'));
    if (checked.length === 0) return;

    const amazonIds = checked.filter(cb => cb.dataset.source.toLowerCase().includes('amazon')).map(cb => cb.value);
    const wooIds = checked.filter(cb => !cb.dataset.source.toLowerCase().includes('amazon')).map(cb => cb.value);

    const commandsToRun = [];
    if (amazonIds.length > 0) {
        commandsToRun.push({ cmd: 'sync-selected-amazon', orders: amazonIds.join(',') });
    }
    if (wooIds.length > 0) {
        commandsToRun.push({ cmd: 'sync-selected-liriya', orders: wooIds.join(',') });
    }

    if (commandsToRun.length === 0) return;

    // Run sequentially (the sync log opens automatically via runCommand)
    runSequentialCommands(commandsToRun);
}

function runSequentialCommands(commands) {
    if (commands.length === 0) {
        fetchState();
        fetchPendingOrders();
        showToast('All selected syncs completed');
        return;
    }

    const next = commands.shift(); // take the first one

    runCommand(next.cmd, next.orders, (code) => {
        if (code === 0) {
            // Run the next command in the queue
            runSequentialCommands(commands);
        } else {
            showToast('Sync stopped: a command failed', 'error');
            fetchState();
            fetchPendingOrders();
        }
    });
}

/* --------------------------------------------------------------------------
   Init
   -------------------------------------------------------------------------- */
fetchState();
fetchPendingOrders();
