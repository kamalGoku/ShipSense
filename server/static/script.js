// Navigation
document.querySelectorAll('nav li').forEach(item => {
    item.addEventListener('click', () => {
        // Update active class on nav
        document.querySelectorAll('nav li').forEach(li => li.classList.remove('active'));
        item.classList.add('active');

        // Show selected view
        const viewId = item.getAttribute('data-view');
        document.querySelectorAll('.view').forEach(view => {
            if (view.id === `view-${viewId}`) {
                view.classList.remove('hidden');
            } else {
                view.classList.add('hidden');
            }
        });

        if (viewId === 'orders') fetchState();
        if (viewId === 'dashboard') fetchPendingOrders();
    });
});

// Toast Notification
let toastTimeoutId = null;

function showToast(message, type = 'success') {
    const toast = document.getElementById('toast');
    const icon = toast.querySelector('.toast-icon');
    const msg = toast.querySelector('.toast-message');

    icon.textContent = type === 'error' ? '❌' : '✅';
    msg.textContent = message;

    toast.classList.remove('hidden');
    if (toastTimeoutId) clearTimeout(toastTimeoutId);
    toastTimeoutId = setTimeout(() => {
        toast.classList.add('hidden');
        toastTimeoutId = null;
    }, 3000);
}

// Terminal Logic
const terminalOutput = document.getElementById('terminal-output');
let commandRunning = false;

function appendToTerminal(text, type = '') {
    const line = document.createElement('div');
    line.className = `log-line ${type}`;
    line.textContent = text;
    terminalOutput.appendChild(line);
    terminalOutput.scrollTop = terminalOutput.scrollHeight;

    // Auto-color lines based on content
    if (!type) {
        if (text.includes('❌') || text.toLowerCase().includes('error') || text.toLowerCase().includes('failed')) {
            line.classList.add('error');
        } else if (text.includes('✅') || text.toLowerCase().includes('success')) {
            line.classList.add('success');
        } else if (text.includes('⚠️') || text.toLowerCase().includes('skipping')) {
            line.classList.add('warning');
        }
    }
}

function clearTerminal() {
    terminalOutput.innerHTML = '';
}

// Runs a command through the shared POST + JSON SSE helper (sse.js).
function runCommand(cmdName, orders = null, onDone = null) {
    if (commandRunning) {
        showToast('A command is already running.', 'error');
        return;
    }
    commandRunning = true;

    // Switch to terminal view
    document.querySelector('[data-view="terminal"]').click();
    appendToTerminal(`\n> Running: ${cmdName}${orders ? ' IDs: ' + orders : ''}`, 'cmd');

    runCommandStream(cmdName, orders, {
        onLog: (text) => appendToTerminal(text),
        onComplete: (code) => {
            commandRunning = false;
            appendToTerminal(`> Process finished with code ${code}`, 'cmd');
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
    const id = document.getElementById('manual-print-id').value.trim();
    if (!id) {
        showToast('Please enter an ID', 'error');
        return;
    }
    if (!/^[A-Za-z0-9_-]+$/.test(id)) {
        showToast('Invalid ID: only letters, numbers, dashes and underscores allowed', 'error');
        return;
    }
    runCommand(`print-order ${id}`);
    document.getElementById('manual-print-id').value = '';
}

// State fetching
async function fetchState() {
    try {
        const res = await fetch('/api/state', { headers: apiHeaders() });
        const data = await res.json();

        if (data.error) {
            console.error(data.error);
            return;
        }

        const orders = data.orders || [];

        // Update Dashboard Stats
        document.getElementById('stat-total').textContent = orders.length;
        document.getElementById('stat-synced').textContent = orders.filter(o => o.synced_to_amazon).length;
        document.getElementById('stat-printed').textContent = orders.filter(o => o.label_printed).length;

        // Count errors
        const errorCount = orders.filter(o => o.error && o.error !== "null").length;
        document.getElementById('stat-errors').textContent = errorCount;

        // Populate Table
        const tbody = document.getElementById('orders-table-body');
        tbody.innerHTML = '';

        // Reverse to show newest first
        [...orders].reverse().forEach(order => {
            const tr = document.createElement('tr');

            // Format badges
            const syncBadge = order.synced_to_amazon
                ? '<span class="badge badge-green">Yes</span>'
                : '<span class="badge badge-gray">No</span>';

            const printBadge = order.label_printed
                ? '<span class="badge badge-green">Yes</span>'
                : '<span class="badge badge-gray">No</span>';

            tr.innerHTML = `
                <td style="font-family: monospace;">${escapeHtml(order.amazon_order_id || '-')}</td>
                <td>${order.amazon_order_id?.startsWith('LIRIYA') ? 'WooCommerce' : 'Amazon'}</td>
                <td style="font-family: monospace;">${escapeHtml(order.awb_number || '-')}</td>
                <td>${escapeHtml(order.courier_name || '-')}</td>
                <td>${syncBadge}</td>
                <td>${printBadge}</td>
            `;
            tbody.appendChild(tr);
        });

    } catch (e) {
        console.error('Failed to fetch state', e);
    }
}

// Pending Orders fetching
async function fetchPendingOrders() {
    try {
        const tbody = document.getElementById('pending-orders-body');
        if (!tbody) return;

        tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; padding: 1rem;">Loading...</td></tr>';

        const res = await fetch('/api/pending-orders', { headers: apiHeaders() });
        const data = await res.json();
        const orders = data.pending_orders || [];

        tbody.innerHTML = '';
        if (orders.length === 0) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; padding: 1rem; color: #888;">No pending orders found.</td></tr>';
            document.getElementById('selectAllPending').checked = false;
            updateSyncButtonState();
            return;
        }

        orders.forEach(order => {
            const tr = document.createElement('tr');

            let sourceBadge = '';
            if (order.source === 'Amazon') {
                sourceBadge = '<span class="badge" style="background: rgba(59, 130, 246, 0.2); color: #60a5fa; padding: 0.25rem 0.5rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 500;">Amazon</span>';
            } else {
                sourceBadge = '<span class="badge" style="background: rgba(168, 85, 247, 0.2); color: #c084fc; padding: 0.25rem 0.5rem; border-radius: 9999px; font-size: 0.75rem; font-weight: 500;">WooCommerce</span>';
            }

            const dateStr = order.date ? new Date(order.date).toLocaleString() : '-';

            tr.innerHTML = `
                <td style="padding: 0.75rem; border-top: 1px solid rgba(255, 255, 255, 0.05);">
                    <input type="checkbox" class="pending-order-cb" data-source="${escapeHtml(order.source)}" onclick="updateSyncButtonState()">
                </td>
                <td style="padding: 0.75rem; border-top: 1px solid rgba(255, 255, 255, 0.05);">${sourceBadge}</td>
                <td style="font-family: monospace; padding: 0.75rem; border-top: 1px solid rgba(255, 255, 255, 0.05);">${escapeHtml(order.order_id || '-')}</td>
                <td style="padding: 0.75rem; border-top: 1px solid rgba(255, 255, 255, 0.05);">${escapeHtml(dateStr)}</td>
                <td style="padding: 0.75rem; border-top: 1px solid rgba(255, 255, 255, 0.05);">${escapeHtml(order.status || '-')}</td>
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
        const tbody = document.getElementById('pending-orders-body');
        if (tbody) {
            tbody.innerHTML = '<tr><td colspan="5" style="text-align: center; padding: 1rem; color: #ef4444;">Error loading pending orders.</td></tr>';
        }
    }
}

// Initial fetch
fetchState();
fetchPendingOrders();

// Selection Logic
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

    // Auto-check or uncheck the master select all box
    const allCb = document.getElementById('selectAllPending');
    if (allCb) {
        allCb.checked = allChecked;
    }
}

function runSelectedSync() {
    const checked = Array.from(document.querySelectorAll('.pending-order-cb:checked'));
    if (checked.length === 0) return;

    const amazonIds = checked.filter(cb => cb.dataset.source === 'Amazon').map(cb => cb.value);
    const wooIds = checked.filter(cb => cb.dataset.source === 'WooCommerce').map(cb => cb.value);

    const commandsToRun = [];
    if (amazonIds.length > 0) {
        commandsToRun.push({ cmd: 'sync-selected-amazon', orders: amazonIds.join(',') });
    }
    if (wooIds.length > 0) {
        commandsToRun.push({ cmd: 'sync-selected-liriya', orders: wooIds.join(',') });
    }

    if (commandsToRun.length === 0) return;

    // Switch to terminal view
    document.querySelector('[data-view="terminal"]').click();

    // Run sequentially
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
