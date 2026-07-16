document.addEventListener('DOMContentLoaded', () => {
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
            
            // Show/hide custom date range
            const customPanel = document.getElementById('customDateRange');
            if (range === 'custom') {
                customPanel.classList.remove('hidden');
                return; // Don't apply yet, wait for "Apply" click
            } else {
                customPanel.classList.add('hidden');
            }

            applyPresetFilter(range);
        });
    });

    // Custom date range apply
    document.getElementById('applyCustomRange').addEventListener('click', () => {
        const from = document.getElementById('dateFrom').value;
        const to = document.getElementById('dateTo').value;
        if (from && to) {
            applyDateFilter(new Date(from), endOfDay(new Date(to)), `${formatDateShort(new Date(from))} – ${formatDateShort(new Date(to))}`);
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

// ─── Helpers ────────────────────────────────────────────────

function formatCurrency(amount) {
    if (amount === "N/A" || isNaN(amount)) return "N/A";
    return '₹' + parseFloat(amount).toLocaleString('en-IN', { maximumFractionDigits: 2 });
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
        // "All Time" — render unfiltered
        showFilterTag(null);
        renderFromOrders(window.dashboardData.orders);
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

    showFilterTag(`${label}  •  ${filtered.length} order${filtered.length !== 1 ? 's' : ''}`);
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

// ─── Recompute & Render All Sections from Orders ────────────

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

        // Products (simplified — we only have the primary SKU per order here)
        const sku = o.sku || 'UNKNOWN';
        if (!productsMap[sku]) {
            productsMap[sku] = { sku: sku, name: '', units_sold: 0, revenue: 0, profit: 0 };
        }
        productsMap[sku].units_sold++;
        productsMap[sku].revenue += o.sale_price || 0;
        productsMap[sku].profit += o.profit || 0;
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
        last_synced: window.dashboardData.last_synced,
        summary: summary
    });
    renderPlatformBreakdown(platformBreakdown);
    renderChart(Object.values(monthlyMap));
    renderProducts(Object.values(productsMap));
    renderOrders(orders);
}

// ─── Data Fetching ──────────────────────────────────────────

async function fetchDashboardData() {
    try {
        const response = await fetch('/api/dashboard');
        const data = await response.json();
        
        if (data.error) {
            console.warn(data.error);
            return;
        }
        
        window.dashboardData = data;
        
        // Re-apply whatever filter is currently active
        const activeBtn = document.querySelector('.filter-btn.active');
        const activeRange = activeBtn ? activeBtn.dataset.range : 'all';

        if (activeRange === 'custom') {
            const from = document.getElementById('dateFrom').value;
            const to = document.getElementById('dateTo').value;
            if (from && to) {
                applyDateFilter(new Date(from), endOfDay(new Date(to)), `${formatDateShort(new Date(from))} – ${formatDateShort(new Date(to))}`);
            } else {
                renderFromOrders(data.orders);
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
        console.error("Error fetching dashboard data:", e);
    }
}

// ─── Rendering Functions ────────────────────────────────────

function updateSummary(data) {
    document.getElementById('lastSynced').textContent = `Last Synced: ${new Date(data.last_synced).toLocaleString()}`;
    
    document.getElementById('valRevenue').textContent = formatCurrency(data.summary.total_revenue);
    document.getElementById('valProfit').textContent = formatCurrency(data.summary.total_profit);
    document.getElementById('valShipped').textContent = data.summary.total_shipped;
    document.getElementById('valCancelled').textContent = data.summary.total_cancelled;
    document.getElementById('valShipping').textContent = formatCurrency(data.summary.total_shipping_cost);
    document.getElementById('valFees').textContent = formatCurrency(data.summary.total_amazon_fees);
}

function renderPlatformBreakdown(breakdown) {
    const tbody = document.getElementById('platformsTbody');
    tbody.innerHTML = '';
    
    if (!breakdown) return;
    
    for (const [platformName, p] of Object.entries(breakdown)) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><strong>${platformName}</strong></td>
            <td>${p.orders}</td>
            <td>${p.shipped}</td>
            <td>${p.cancelled}</td>
            <td>${formatCurrency(p.revenue)}</td>
            <td>${formatCurrency(p.fees)}</td>
            <td class="negative">${p.cancellation_fees ? formatCurrency(p.cancellation_fees) : '₹0.00'}</td>
            <td>${formatCurrency(p.shipping_cost)}</td>
            <td class="${p.profit > 0 ? 'positive' : (p.profit < 0 ? 'negative' : 'neutral')}">${formatCurrency(p.profit)}</td>
        `;
        tbody.appendChild(tr);
    }
}

function renderChart(monthlyData) {
    const ctx = document.getElementById('monthlyChart').getContext('2d');
    
    // Sort by month
    monthlyData.sort((a, b) => a.month.localeCompare(b.month));
    
    const labels = monthlyData.map(d => d.month);
    const revenue = monthlyData.map(d => d.revenue);
    const profit = monthlyData.map(d => d.profit);

    if (monthlyChartInstance) {
        monthlyChartInstance.destroy();
    }

    monthlyChartInstance = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [
                {
                    label: 'Revenue',
                    data: revenue,
                    backgroundColor: 'rgba(59, 130, 246, 0.5)',
                    borderColor: 'rgb(59, 130, 246)',
                    borderWidth: 1,
                    borderRadius: 4
                },
                {
                    label: 'Profit',
                    data: profit,
                    type: 'line',
                    borderColor: 'rgb(16, 185, 129)',
                    backgroundColor: 'rgb(16, 185, 129)',
                    borderWidth: 3,
                    tension: 0.3,
                    pointBackgroundColor: '#0f172a'
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { labels: { color: '#f8fafc' } }
            },
            scales: {
                y: {
                    grid: { color: 'rgba(255,255,255,0.1)' },
                    ticks: { color: '#94a3b8' }
                },
                x: {
                    grid: { display: false },
                    ticks: { color: '#94a3b8' }
                }
            }
        }
    });
}

function renderProducts(products) {
    const tbody = document.getElementById('productsTbody');
    tbody.innerHTML = '';
    
    // Sort by revenue
    products.sort((a, b) => b.revenue - a.revenue);
    
    products.slice(0, 10).forEach(p => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td>${p.sku}<br><small class="neutral">${p.name || ''}</small></td>
            <td>${p.units_sold}</td>
            <td>${formatCurrency(p.revenue)}</td>
            <td class="${p.profit > 0 ? 'positive' : (p.profit < 0 ? 'negative' : 'neutral')}">${formatCurrency(p.profit)}</td>
        `;
        tbody.appendChild(tr);
    });
}

function renderOrders(orders) {
    const tbody = document.getElementById('ordersTbody');
    tbody.innerHTML = '';
    
    // Sort by date descending, then show last 50
    const sorted = [...orders].sort((a, b) => {
        if (!a.date && !b.date) return 0;
        if (!a.date) return 1;
        if (!b.date) return -1;
        return new Date(b.date) - new Date(a.date);
    });

    sorted.slice(0, 50).forEach(o => {
        const tr = document.createElement('tr');
        const statusClass = 'status-' + o.status;
        const profitClass = o.profit > 0 ? 'positive' : (o.profit < 0 ? 'negative' : 'neutral');
        
        tr.innerHTML = `
            <td>${o.amazon_order_id}<br><small class="neutral">${o.sku}</small></td>
            <td>${o.platform}</td>
            <td><span class="status-badge ${statusClass}">${o.status}</span></td>
            <td>${formatCurrency(o.sale_price)}</td>
            <td class="${profitClass}">${formatCurrency(o.profit)}</td>
        `;
        tbody.appendChild(tr);
    });
}

// ─── Sync ───────────────────────────────────────────────────

async function triggerSync() {
    const btn = document.getElementById('syncBtn');
    const icon = btn.querySelector('.sync-icon');
    const logContainer = document.getElementById('syncLogContainer');
    const log = document.getElementById('syncLog');
    
    icon.classList.add('spinning');
    btn.disabled = true;
    logContainer.classList.remove('hidden');
    log.textContent = "Starting background sync...\n";
    
    try {
        const response = await fetch('/api/run?cmd=sync-dashboard');
        const reader = response.body.getReader();
        const decoder = new TextDecoder("utf-8");

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value);
            const events = chunk.split("\n\n");
            
            for (const event of events) {
                if (event.startsWith("data: ")) {
                    const jsonStr = event.substring(6);
                    try {
                        const data = JSON.parse(jsonStr);
                        if (data.text === "[PROCESS_COMPLETE]") {
                            log.textContent += `\nProcess completed with code ${data.code}\n`;
                            // Refresh data
                            fetchDashboardData();
                            break;
                        } else {
                            log.textContent += data.text + "\n";
                            log.scrollTop = log.scrollHeight;
                        }
                    } catch (e) {
                        // ignore parse errors
                    }
                }
            }
        }
    } catch (e) {
        console.error(e);
        log.textContent += "\nError executing sync: " + e.message;
    } finally {
        icon.classList.remove('spinning');
        btn.disabled = false;
        
        // Hide log after a few seconds
        setTimeout(() => {
            logContainer.classList.add('hidden');
        }, 5000);
    }
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
        resultContainer.innerHTML = `<div class="result-error">Dashboard data not loaded yet.</div>`;
        return;
    }

    const order = window.dashboardData.orders.find(o => String(o.amazon_order_id) === searchInput);
    
    resultContainer.classList.remove('hidden');
    
    if (!order) {
        resultContainer.innerHTML = `
            <div class="result-error">
                <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="min-width: 20px;"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                Order not found in synced data. Please run a sync or check the Order ID.
            </div>`;
        return;
    }

    // Map amazon_fees to fees as requested
    const fees = order.amazon_fees;
    const sale_price = order.sale_price;
    const profit = order.profit;
    const margin = sale_price > 0 ? (profit / sale_price * 100).toFixed(1) + '%' : "N/A";
    
    const statusClass = 'status-' + order.status;
    const profitClass = profit > 0 ? 'positive' : (profit < 0 ? 'negative' : 'neutral');

    resultContainer.innerHTML = `
        <div class="result-grid">
            <div class="result-item">
                <span class="result-label">Platform & Status</span>
                <span class="result-val">${order.platform} <span class="status-badge ${statusClass}" style="margin-left: 0.5rem;">${order.status}</span></span>
            </div>
            <div class="result-item">
                <span class="result-label">Sale Price (Revenue)</span>
                <span class="result-val">${formatCurrency(sale_price)}</span>
            </div>
            <div class="result-item">
                <span class="result-label">Product Cost (COGS)</span>
                <span class="result-val">${formatCurrency(order.product_cost)}</span>
            </div>
            <div class="result-item">
                <span class="result-label">Amazon / Gateway Fees</span>
                <span class="result-val negative">${formatCurrency(fees)}</span>
            </div>
            <div class="result-item">
                <span class="result-label">Shipping / Freight Cost</span>
                <span class="result-val negative">${formatCurrency(order.shipping_cost)}</span>
            </div>
            ${order.refunds && order.refunds > 0 ? `
            <div class="result-item">
                <span class="result-label">Refunds / Adjustments</span>
                <span class="result-val negative">${formatCurrency(order.refunds)}</span>
            </div>` : ''}
            <div class="result-item">
                <span class="result-label">Net Profit</span>
                <span class="result-val ${profitClass}">${formatCurrency(profit)}</span>
            </div>
            <div class="result-item">
                <span class="result-label">Margin</span>
                <span class="result-val ${profitClass}">${margin}</span>
            </div>
        </div>
    `;
}
