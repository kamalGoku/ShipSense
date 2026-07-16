/**
 * ShipSense — Revenue Dashboard page logic.
 *
 * Depends on sse.js (escapeHtml, apiHeaders, runCommandStream), loaded first.
 * Renders server-computed aggregates for "All Time"; recomputes locally from
 * orders[] for date-filtered views (approximate — primary SKU basis).
 */

document.addEventListener('DOMContentLoaded', () => {
    showTableSkeletons();
    fetchDashboardData();

    document.getElementById('syncBtn').addEventListener('click', triggerSync);

    document.getElementById('orderSearchBtn').addEventListener('click', lookupOrder);
    document.getElementById('orderSearchInput').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            lookupOrder();
        }
    });

    // Timeline filter buttons
    document.querySelectorAll('.filter-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            const range = btn.dataset.range;

            // Toggle active state
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');

            // Show/hide custom date range inputs
            const customPanel = document.getElementById('customDateRange');
            if (range === 'custom') {
                customPanel.classList.remove('hidden');
                return; // Don't apply yet; wait for "Apply" click
            } else {
                customPanel.classList.add('hidden');
            }

            applyPresetFilter(range);
        });
    });

    // Custom date range apply
    document.getElementById('applyCustomRange').addEventListener('click', () => {
        if (!applyCustomRangeFromInputs()) {
            showToast('Select both From and To dates first.', 'warning');
        }
    });

    // Clear filter
    document.getElementById('clearFilter').addEventListener('click', () => {
        document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
        document.querySelector('.filter-btn[data-range="all"]').classList.add('active');
        document.getElementById('customDateRange').classList.add('hidden');
        applyPresetFilter('all');
    });
});

let monthlyChartInstance = null;
let toastTimer = null;

const TABLE_MAX_ROWS = 15;
const FEE_ESTIMATED_TITLE = 'Estimated (real data unavailable)';

// ─── Helpers ────────────────────────────────────────────────

function formatCurrency(amount) {
    const n = parseFloat(amount);
    if (amount === null || amount === undefined || isNaN(n)) return '₹0.00';
    return '₹' + n.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// Parse an <input type="date"> value ("YYYY-MM-DD") as a LOCAL date.
// new Date("2026-07-01") would parse as UTC midnight and shift the day
// in non-UTC timezones; constructing from parts avoids that.
function parseLocalDate(dateInputValue) {
    if (!dateInputValue) return null;
    const parts = dateInputValue.split('-').map(Number);
    if (parts.length !== 3 || parts.some(isNaN)) return null;
    return new Date(parts[0], parts[1] - 1, parts[2]);
}

function endOfDay(date) {
    const d = new Date(date);
    d.setHours(23, 59, 59, 999);
    return d;
}

function startOfDay(date) {
    const d = new Date(date);
    d.setHours(0, 0, 0, 0);
    return d;
}

function formatDateShort(date) {
    return date.toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' });
}

function applyCustomRangeFromInputs() {
    const from = parseLocalDate(document.getElementById('dateFrom').value);
    const to = parseLocalDate(document.getElementById('dateTo').value);
    if (from && to) {
        applyDateFilter(startOfDay(from), endOfDay(to), `${formatDateShort(from)} – ${formatDateShort(to)}`);
        return true;
    }
    return false;
}

function profitClassOf(value) {
    return value > 0 ? 'positive' : (value < 0 ? 'negative' : 'neutral');
}

function statusBadgeClass(status) {
    switch (String(status || '').toLowerCase()) {
        case 'shipped': return 'badge-success';
        case 'canceled':
        case 'cancelled': return 'badge-danger';
        case 'pending': return 'badge-warning';
        case 'new': return 'badge-info';
        default: return 'badge-neutral';
    }
}

function platformBadgeClass(platform) {
    switch (String(platform || '').toLowerCase()) {
        case 'amazon': return 'badge-platform-amazon';
        case 'woocommerce': return 'badge-platform-woo';
        default: return 'badge-neutral';
    }
}

function asteriskNote(title) {
    return ` <span class="fee-estimated" title="${escapeHtml(title)}" aria-label="${escapeHtml(title)}">*</span>`;
}

function feeAsterisk(estimated) {
    return estimated ? asteriskNote(FEE_ESTIMATED_TITLE) : '';
}

// ─── Toast ──────────────────────────────────────────────────

function showToast(message, kind) {
    const toast = document.getElementById('toast');
    if (!toast) return;
    const icons = {
        success: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21.801 10A10 10 0 1 1 17 3.335"/><path d="m9 11 3 3L22 4"/></svg>',
        error: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="m15 9-6 6"/><path d="m9 9 6 6"/></svg>',
        warning: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/><path d="M12 9v4"/><path d="M12 17h.01"/></svg>',
        info: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/></svg>'
    };
    const k = icons[kind] ? kind : 'info';
    toast.className = `toast toast-${k}`;
    toast.innerHTML = `${icons[k]}<span>${escapeHtml(message)}</span>`;

    // Force reflow so the slide-in transition replays
    void toast.offsetWidth;
    toast.classList.add('visible');

    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => toast.classList.remove('visible'), 4000);
}

// ─── Loading / empty states ─────────────────────────────────

function skeletonRows(cols, rows) {
    let html = '';
    for (let r = 0; r < rows; r++) {
        html += '<tr class="skeleton-row">';
        for (let c = 0; c < cols; c++) {
            html += '<td><div class="skeleton"></div></td>';
        }
        html += '</tr>';
    }
    return html;
}

function showTableSkeletons() {
    document.getElementById('platformsTbody').innerHTML = skeletonRows(9, 2);
    document.getElementById('productsTbody').innerHTML = skeletonRows(4, 5);
    document.getElementById('ordersTbody').innerHTML = skeletonRows(5, 5);
}

function emptyRow(cols, message) {
    return `
        <tr class="empty-row">
            <td colspan="${cols}">
                <div class="empty-state">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5"/><path d="M12 22V12"/></svg>
                    <span class="empty-state-text">${escapeHtml(message)}</span>
                </div>
            </td>
        </tr>`;
}

function setTableFooter(id, shown, total) {
    const el = document.getElementById(id);
    if (!el) return;
    if (total > shown) {
        el.textContent = `showing ${shown} of ${total}`;
        el.classList.remove('hidden');
    } else {
        el.classList.add('hidden');
    }
}

// ─── Date Range Calculations ────────────────────────────────

function getDateRange(rangeKey) {
    const now = new Date();
    const today = endOfDay(now);

    switch (rangeKey) {
        case 'this-month': {
            const start = new Date(now.getFullYear(), now.getMonth(), 1);
            return { from: start, to: today, label: 'This Month' };
        }
        case 'last-month': {
            const start = new Date(now.getFullYear(), now.getMonth() - 1, 1);
            const end = new Date(now.getFullYear(), now.getMonth(), 0, 23, 59, 59, 999);
            return { from: start, to: end, label: 'Last Month' };
        }
        case 'last-2-months': {
            const start = new Date(now.getFullYear(), now.getMonth() - 2, 1);
            return { from: start, to: today, label: 'Last 2 Months' };
        }
        case 'last-3-months': {
            const start = new Date(now.getFullYear(), now.getMonth() - 3, 1);
            return { from: start, to: today, label: 'Last 3 Months' };
        }
        case 'last-6-months': {
            const start = new Date(now.getFullYear(), now.getMonth() - 6, 1);
            return { from: start, to: today, label: 'Last 6 Months' };
        }
        case 'last-year': {
            const start = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate());
            return { from: start, to: today, label: 'Last Year' };
        }
        case 'all':
        default:
            return null; // No filter
    }
}

// ─── Filter Logic ───────────────────────────────────────────

function applyPresetFilter(rangeKey) {
    const range = getDateRange(rangeKey);
    if (!range) {
        // "All Time" — render the server-computed aggregates (authoritative:
        // they cover ALL line items, not just each order's primary SKU).
        showFilterTag(null);
        renderFromServerData();
    } else {
        applyDateFilter(range.from, range.to, range.label);
    }
}

function applyDateFilter(from, to, label) {
    if (!window.dashboardData || !window.dashboardData.orders) return;

    const filtered = window.dashboardData.orders.filter(o => {
        if (!o.date) return true; // Include orders without dates
        const d = new Date(o.date);
        return d >= from && d <= to;
    });

    showFilterTag(`${label} · ${filtered.length} order${filtered.length !== 1 ? 's' : ''} · approximate (primary SKU basis)`);
    renderFromOrders(filtered);
}

function showFilterTag(text) {
    const tag = document.getElementById('activeFilterTag');
    const tagText = document.getElementById('filterTagText');
    if (text) {
        tagText.textContent = text;
        tag.classList.remove('hidden');
    } else {
        tag.classList.add('hidden');
    }
}

// ─── Render (unfiltered) from server-computed aggregates ────

function renderFromServerData() {
    const data = window.dashboardData;
    if (!data) return;

    updateSummary({
        last_synced: data.last_synced,
        summary: data.summary || {}
    });
    renderPlatformBreakdown(data.platform_breakdown || {});
    renderChart((data.monthly || []).slice());
    renderProducts((data.products || []).slice(), false);
    renderOrders(data.orders || []);
}

// ─── Recompute & Render All Sections from Orders ────────────
// Used ONLY for date-range filtered views. The orders[] array carries one
// row per order with its PRIMARY SKU only, so per-product units/revenue are
// approximate; the UI labels these views "(filtered view — primary SKU basis)".

function renderFromOrders(orders) {
    // Recompute summary
    const summary = {
        total_orders: orders.length,
        total_shipped: 0,
        total_cancelled: 0,
        total_revenue: 0,
        total_profit: 0,
        total_shipping_cost: 0,
        total_amazon_fees: 0
    };

    const platformBreakdown = {};
    const monthlyMap = {};
    const productsMap = {};

    for (const o of orders) {
        // Summary
        if (o.status === 'Shipped') summary.total_shipped++;
        if (o.status === 'Canceled') summary.total_cancelled++;
        summary.total_revenue += o.sale_price || 0;
        summary.total_profit += o.profit || 0;
        summary.total_shipping_cost += o.shipping_cost || 0;
        summary.total_amazon_fees += o.amazon_fees || 0;

        // Platform breakdown
        const plat = o.platform || 'Unknown';
        if (!platformBreakdown[plat]) {
            platformBreakdown[plat] = { orders: 0, shipped: 0, cancelled: 0, revenue: 0, fees: 0, shipping_cost: 0, profit: 0, cancellation_fees: 0 };
        }
        platformBreakdown[plat].orders++;
        if (o.status === 'Shipped') platformBreakdown[plat].shipped++;
        if (o.status === 'Canceled') platformBreakdown[plat].cancelled++;
        platformBreakdown[plat].revenue += o.sale_price || 0;
        platformBreakdown[plat].fees += o.amazon_fees || 0;
        platformBreakdown[plat].shipping_cost += o.shipping_cost || 0;
        platformBreakdown[plat].profit += o.profit || 0;
        // cancellation_fees: orders that were fully refunded
        if ((o.refunds || 0) > 0 && (o.refunds >= ((o.sale_price || 0) - 1.0))) {
            platformBreakdown[plat].cancellation_fees += o.amazon_fees || 0;
        }

        // Monthly
        if (o.date) {
            const mk = o.date.substring(0, 7); // YYYY-MM
            if (!monthlyMap[mk]) {
                monthlyMap[mk] = { month: mk, revenue: 0, profit: 0, shipped: 0, cancelled: 0, shipping_cost: 0 };
            }
            monthlyMap[mk].revenue += o.sale_price || 0;
            monthlyMap[mk].profit += o.profit || 0;
            monthlyMap[mk].shipping_cost += o.shipping_cost || 0;
            if (o.status === 'Shipped') monthlyMap[mk].shipped++;
            if (o.status === 'Canceled') monthlyMap[mk].cancelled++;
        }

        // Products (approximate — we only have the primary SKU per order here)
        if (o.status !== 'Canceled') {
            const sku = o.sku || 'UNKNOWN';
            if (!productsMap[sku]) {
                productsMap[sku] = { sku: sku, name: '', units_sold: 0, revenue: 0, profit: 0 };
            }
            productsMap[sku].units_sold++;
            productsMap[sku].revenue += o.sale_price || 0;
            productsMap[sku].profit += o.profit || 0;
        }
    }

    // Try to fill in product names from original data
    if (window.dashboardData && window.dashboardData.products) {
        for (const p of window.dashboardData.products) {
            if (productsMap[p.sku] && p.name) {
                productsMap[p.sku].name = p.name;
            }
        }
    }

    // Round platform values
    for (const p of Object.values(platformBreakdown)) {
        for (const key of ['revenue', 'fees', 'shipping_cost', 'profit', 'cancellation_fees']) {
            p[key] = Math.round(p[key] * 100) / 100;
        }
    }

    // Render everything
    updateSummary({
        last_synced: window.dashboardData ? window.dashboardData.last_synced : null,
        summary: summary
    });
    renderPlatformBreakdown(platformBreakdown);
    renderChart(Object.values(monthlyMap));
    renderProducts(Object.values(productsMap), true);
    renderOrders(orders);
}

// ─── Data Fetching ──────────────────────────────────────────

async function fetchDashboardData() {
    try {
        const response = await fetch('/api/dashboard', { headers: apiHeaders() });
        const data = await response.json();

        if (data.error) {
            console.warn(data.error);
            showToast(String(data.error), 'error');
            return;
        }

        window.dashboardData = data;

        // Re-apply whatever filter is currently active
        const activeBtn = document.querySelector('.filter-btn.active');
        const activeRange = activeBtn ? activeBtn.dataset.range : 'all';

        if (activeRange === 'custom') {
            if (!applyCustomRangeFromInputs()) {
                renderFromServerData();
            }
        } else {
            applyPresetFilter(activeRange);
        }

        // Auto-refresh search lookup if an order ID is already typed in
        const searchInput = document.getElementById('orderSearchInput');
        if (searchInput && searchInput.value.trim()) {
            lookupOrder();
        }

    } catch (e) {
        console.error('Error fetching dashboard data:', e);
        showToast('Failed to load dashboard data.', 'error');
    }
}

// ─── Rendering Functions ────────────────────────────────────

function updateSummary(data) {
    const synced = data.last_synced ? new Date(data.last_synced).toLocaleString() : 'Never';
    document.getElementById('lastSynced').textContent = `Last synced: ${synced}`;

    const s = data.summary || {};
    document.getElementById('valRevenue').textContent = formatCurrency(s.total_revenue);

    const profitEl = document.getElementById('valProfit');
    profitEl.textContent = formatCurrency(s.total_profit);
    const profitCard = profitEl.closest('.stat-card');
    if (profitCard) {
        profitCard.classList.remove('positive', 'negative');
        const p = parseFloat(s.total_profit);
        if (!isNaN(p) && p !== 0) profitCard.classList.add(p > 0 ? 'positive' : 'negative');
    }

    document.getElementById('valShipped').textContent = s.total_shipped || 0;
    document.getElementById('valCancelled').textContent = s.total_cancelled || 0;
    document.getElementById('valShipping').textContent = formatCurrency(s.total_shipping_cost);
    document.getElementById('valFees').textContent = formatCurrency(s.total_amazon_fees);
}

function renderPlatformBreakdown(breakdown) {
    const tbody = document.getElementById('platformsTbody');

    const entries = Object.entries(breakdown || {});
    if (entries.length === 0) {
        tbody.innerHTML = emptyRow(9, 'No platform data. Run a sync to load orders.');
        return;
    }

    // Sort by profit descending
    entries.sort((a, b) => (b[1].profit || 0) - (a[1].profit || 0));

    tbody.innerHTML = entries.map(([platformName, p]) => `
        <tr>
            <td><span class="badge ${platformBadgeClass(platformName)}">${escapeHtml(platformName)}</span></td>
            <td class="number">${escapeHtml(p.orders)}</td>
            <td class="number">${escapeHtml(p.shipped)}</td>
            <td class="number">${escapeHtml(p.cancelled)}</td>
            <td class="number">${formatCurrency(p.revenue)}</td>
            <td class="number">${formatCurrency(p.fees)}</td>
            <td class="number negative">${p.cancellation_fees ? formatCurrency(p.cancellation_fees) : '₹0.00'}</td>
            <td class="number">${formatCurrency(p.shipping_cost)}</td>
            <td class="number ${profitClassOf(p.profit)}">${formatCurrency(p.profit)}</td>
        </tr>
    `).join('');
}

function renderChart(monthlyData) {
    const canvas = document.getElementById('monthlyChart');
    if (!canvas) return;

    const wrap = canvas.parentElement;

    // Guard: Chart.js is loaded from a CDN and may be unavailable (offline,
    // CDN failure). Degrade gracefully so table rendering still proceeds.
    if (typeof Chart === 'undefined') {
        if (wrap && !wrap.querySelector('.empty-state')) {
            canvas.classList.add('hidden');
            const note = document.createElement('div');
            note.className = 'empty-state';
            note.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 3v16a2 2 0 0 0 2 2h16"/><path d="m19 9-5 5-4-4-3 3"/></svg><span class="empty-state-title">Chart unavailable</span><span class="empty-state-text">Chart.js failed to load.</span>';
            wrap.appendChild(note);
        }
        return;
    }

    const ctx = canvas.getContext('2d');

    // Sort by month
    monthlyData.sort((a, b) => a.month.localeCompare(b.month));

    const labels = monthlyData.map(d => d.month);
    const revenue = monthlyData.map(d => d.revenue);
    const profit = monthlyData.map(d => d.profit);

    if (monthlyChartInstance) {
        monthlyChartInstance.destroy();
    }

    const styles = getComputedStyle(document.documentElement);
    const borderColor = (styles.getPropertyValue('--border') || '#DBEAFE').trim();
    const mutedText = (styles.getPropertyValue('--text-muted') || '#64748B').trim();
    const fontFamily = '"Fira Sans", sans-serif';

    monthlyChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Revenue',
                    data: revenue,
                    borderColor: '#1E40AF',
                    backgroundColor: 'rgba(30, 64, 175, 0.10)',
                    fill: true,
                    borderWidth: 2,
                    tension: 0.3,
                    pointRadius: 3,
                    pointBackgroundColor: '#1E40AF'
                },
                {
                    label: 'Profit',
                    data: profit,
                    borderColor: '#16A34A',
                    backgroundColor: 'rgba(22, 163, 74, 0.10)',
                    fill: true,
                    borderWidth: 2,
                    tension: 0.3,
                    pointRadius: 3,
                    pointBackgroundColor: '#16A34A'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: {
                legend: {
                    labels: {
                        color: '#1E3A8A',
                        font: { family: fontFamily, size: 12 },
                        boxWidth: 12,
                        boxHeight: 12
                    }
                },
                tooltip: {
                    backgroundColor: '#1E3A8A',
                    titleFont: { family: fontFamily },
                    bodyFont: { family: fontFamily },
                    callbacks: {
                        label: (context) => `${context.dataset.label}: ${formatCurrency(context.parsed.y)}`
                    }
                }
            },
            scales: {
                y: {
                    grid: { color: borderColor },
                    ticks: {
                        color: mutedText,
                        font: { family: fontFamily, size: 11 },
                        callback: (value) => formatCurrency(value)
                    }
                },
                x: {
                    grid: { display: false },
                    ticks: {
                        color: mutedText,
                        font: { family: fontFamily, size: 11 }
                    }
                }
            }
        }
    });
}

function renderProducts(products, approximate) {
    const tbody = document.getElementById('productsTbody');
    const approxNote = document.getElementById('productsApproxNote');
    approxNote.classList.toggle('hidden', !approximate);

    if (!products || products.length === 0) {
        tbody.innerHTML = emptyRow(4, 'No product data yet.');
        setTableFooter('productsFooter', 0, 0);
        return;
    }

    // Sort by revenue
    products.sort((a, b) => (b.revenue || 0) - (a.revenue || 0));

    const shown = products.slice(0, TABLE_MAX_ROWS);
    tbody.innerHTML = shown.map(p => `
        <tr>
            <td>
                <span class="mono">${escapeHtml(p.sku)}</span>${approximate ? asteriskNote('Approximate: computed from each order’s primary SKU only') : ''}
                ${p.name ? `<br><small class="text-muted">${escapeHtml(p.name)}</small>` : ''}
            </td>
            <td class="number">${escapeHtml(p.units_sold)}</td>
            <td class="number">${formatCurrency(p.revenue)}</td>
            <td class="number ${profitClassOf(p.profit)}">${formatCurrency(p.profit)}</td>
        </tr>
    `).join('');

    setTableFooter('productsFooter', shown.length, products.length);
}

function renderOrders(orders) {
    const tbody = document.getElementById('ordersTbody');

    if (!orders || orders.length === 0) {
        tbody.innerHTML = emptyRow(5, 'No orders in this range. Try widening the timeline or run a sync.');
        setTableFooter('ordersFooter', 0, 0);
        return;
    }

    // Sort by date descending
    const sorted = [...orders].sort((a, b) => {
        if (!a.date && !b.date) return 0;
        if (!a.date) return 1;
        if (!b.date) return -1;
        return new Date(b.date) - new Date(a.date);
    });

    const shown = sorted.slice(0, TABLE_MAX_ROWS);
    tbody.innerHTML = shown.map(o => `
        <tr>
            <td>
                <span class="mono">${escapeHtml(o.amazon_order_id)}</span>
                ${o.sku ? `<br><small class="text-muted mono">${escapeHtml(o.sku)}</small>` : ''}
            </td>
            <td><span class="badge ${platformBadgeClass(o.platform)}">${escapeHtml(o.platform)}</span></td>
            <td><span class="badge ${statusBadgeClass(o.status)}">${escapeHtml(o.status)}</span></td>
            <td class="number">${formatCurrency(o.sale_price)}</td>
            <td class="number ${profitClassOf(o.profit)}">${formatCurrency(o.profit)}${feeAsterisk(o.fees_estimated)}</td>
        </tr>
    `).join('');

    setTableFooter('ordersFooter', shown.length, sorted.length);
}

// ─── Sync ───────────────────────────────────────────────────

async function triggerSync() {
    const btn = document.getElementById('syncBtn');
    const icon = btn.querySelector('.sync-icon');
    const logSection = document.getElementById('syncLogSection');
    const log = document.getElementById('syncLog');

    icon.classList.add('spinning');
    btn.disabled = true;
    logSection.classList.remove('hidden');
    log.textContent = 'Starting background sync...\n';

    const finishUi = () => {
        icon.classList.remove('spinning');
        btn.disabled = false;

        // Hide log after a few seconds
        setTimeout(() => {
            logSection.classList.add('hidden');
        }, 5000);
    };

    // Shared POST + JSON SSE helper from sse.js; onComplete always fires.
    await runCommandStream('sync-dashboard', null, {
        onLog: (text) => {
            log.textContent += text + '\n';
            log.scrollTop = log.scrollHeight;
        },
        onComplete: (code) => {
            log.textContent += `\nProcess completed with code ${code}\n`;
            finishUi();
            if (code === 0) {
                showToast('Sync complete. Data refreshed.', 'success');
            } else {
                showToast(`Sync finished with code ${code}. Check the log.`, 'error');
            }
            // Refresh data
            fetchDashboardData();
        }
    });
}

// ─── Order P&L Lookup ───────────────────────────────────────

function lookupOrder() {
    const searchInput = document.getElementById('orderSearchInput').value.trim();
    const resultContainer = document.getElementById('orderSearchResult');

    if (!searchInput) {
        resultContainer.classList.add('hidden');
        return;
    }

    if (!window.dashboardData || !window.dashboardData.orders) {
        resultContainer.classList.remove('hidden');
        resultContainer.innerHTML = '<div class="result-error">Dashboard data not loaded yet.</div>';
        return;
    }

    const order = window.dashboardData.orders.find(o => String(o.amazon_order_id) === searchInput);

    resultContainer.classList.remove('hidden');

    if (!order) {
        resultContainer.innerHTML = `
            <div class="result-error">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                Order not found in synced data. Run a sync or check the Order ID.
            </div>`;
        return;
    }

    // Map amazon_fees to fees as requested
    const fees = order.amazon_fees;
    const sale_price = order.sale_price;
    const profit = order.profit;
    const margin = sale_price > 0 ? (profit / sale_price * 100).toFixed(1) + '%' : 'N/A';

    const profitClass = profitClassOf(profit);
    const feeNote = feeAsterisk(order.fees_estimated);

    resultContainer.innerHTML = `
        <div class="result-grid">
            <div class="result-item">
                <span class="result-label">Platform &amp; Status</span>
                <span class="result-val">
                    <span class="badge ${platformBadgeClass(order.platform)}">${escapeHtml(order.platform)}</span>
                    <span class="badge ${statusBadgeClass(order.status)}">${escapeHtml(order.status)}</span>
                </span>
            </div>
            <div class="result-item">
                <span class="result-label">Sale Price (Revenue)</span>
                <span class="result-val number">${formatCurrency(sale_price)}</span>
            </div>
            <div class="result-item">
                <span class="result-label">Product Cost (COGS)</span>
                <span class="result-val number">${formatCurrency(order.product_cost)}</span>
            </div>
            <div class="result-item">
                <span class="result-label">Amazon / Gateway Fees${feeNote}</span>
                <span class="result-val number negative">${formatCurrency(fees)}${feeNote}</span>
            </div>
            <div class="result-item">
                <span class="result-label">Shipping / Freight Cost</span>
                <span class="result-val number negative">${formatCurrency(order.shipping_cost)}</span>
            </div>
            ${order.refunds && order.refunds > 0 ? `
            <div class="result-item">
                <span class="result-label">Refunds / Adjustments</span>
                <span class="result-val number negative">${formatCurrency(order.refunds)}</span>
            </div>` : ''}
            <div class="result-item">
                <span class="result-label">Net Profit</span>
                <span class="result-val number ${profitClass}">${formatCurrency(profit)}</span>
            </div>
            <div class="result-item">
                <span class="result-label">Margin</span>
                <span class="result-val number ${profitClass}">${escapeHtml(margin)}</span>
            </div>
        </div>
    `;
}
