// JennMesh Dashboard — Client-side JavaScript
// All data rendered comes from the JennMesh API (trusted internal source).
// Text content uses textContent; HTML structure built via DOM APIs.

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    loadHealthData();
    loadFleetData();
    setupLocator();
    setupModal();
    setInterval(() => {
        loadHealthData();
        loadFleetData();
    }, 30000);
});

function initNavigation() {
    document.querySelectorAll('.nav-link').forEach(link => {
        link.addEventListener('click', (e) => {
            e.preventDefault();
            const tab = link.dataset.tab;
            document.querySelectorAll('.nav-link').forEach(l => l.classList.remove('active'));
            link.classList.add('active');
            document.querySelectorAll('.tab-content').forEach(t => t.classList.remove('active'));
            const target = document.getElementById('tab-' + tab);
            if (target) target.classList.add('active');
            if (tab === 'provision') loadProvisionLog();
            if (tab === 'alerts') loadAlerts();
            if (tab === 'config') loadConfigData();
        });
    });
}

// --- Helpers for safe DOM construction ---

function clearChildren(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
}

function createTextCell(text) {
    const td = document.createElement('td');
    td.textContent = text;
    return td;
}

function createBadge(label, cssClass) {
    const span = document.createElement('span');
    span.className = 'badge ' + cssClass;
    span.textContent = label;
    return span;
}

// --- Health ---

async function loadHealthData() {
    try {
        const resp = await fetch('/api/v1/fleet/health');
        const data = await resp.json();
        document.getElementById('total-devices').textContent = data.total_devices;
        document.getElementById('online-count').textContent = data.online_count;
        document.getElementById('offline-count').textContent = data.offline_count;
        document.getElementById('health-score').textContent = data.health_score.toFixed(0) + '%';
        document.getElementById('active-alerts').textContent = data.active_alerts;
    } catch (e) {
        console.error('Failed to load health data:', e);
    }
}

// --- Fleet Table ---

async function loadFleetData() {
    try {
        const resp = await fetch('/api/v1/fleet');
        const data = await resp.json();
        const tbody = document.getElementById('fleet-body');
        clearChildren(tbody);

        if (!data.devices || data.devices.length === 0) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 9;
            td.className = 'loading';
            td.textContent = 'No devices registered';
            tr.appendChild(td);
            tbody.appendChild(tr);
            return;
        }

        data.devices.forEach(d => {
            const tr = document.createElement('tr');
            tr.appendChild(createTextCell(d.node_id));
            tr.appendChild(createTextCell(d.long_name || '\u2014'));
            tr.appendChild(createTextCell(d.role));
            tr.appendChild(createTextCell(d.hardware));
            tr.appendChild(createTextCell(d.firmware));
            tr.appendChild(createTextCell(d.battery_level !== null ? d.battery_level + '%' : '\u2014'));
            tr.appendChild(createTextCell(d.signal_snr !== null ? d.signal_snr.toFixed(1) + ' dB' : '\u2014'));

            const statusTd = document.createElement('td');
            statusTd.appendChild(createBadge(
                d.is_online ? 'Online' : 'Offline',
                d.is_online ? 'badge-online' : 'badge-offline'
            ));
            tr.appendChild(statusTd);

            tr.appendChild(createTextCell(d.last_seen ? new Date(d.last_seen).toLocaleString() : 'Never'));
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('Failed to load fleet data:', e);
    }
}

// --- Provision Log ---

async function loadProvisionLog() {
    try {
        const resp = await fetch('/api/v1/provision/log');
        const data = await resp.json();
        const tbody = document.getElementById('provision-body');
        clearChildren(tbody);

        if (!data.entries || data.entries.length === 0) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 6;
            td.className = 'loading';
            td.textContent = 'No provisioning events';
            tr.appendChild(td);
            tbody.appendChild(tr);
            return;
        }

        data.entries.forEach(entry => {
            const tr = document.createElement('tr');
            tr.appendChild(createTextCell(entry.node_id));
            tr.appendChild(createTextCell(entry.action));
            tr.appendChild(createTextCell(entry.role || '\u2014'));
            tr.appendChild(createTextCell(entry.operator));
            tr.appendChild(createTextCell(entry.timestamp));
            tr.appendChild(createTextCell(entry.details || '\u2014'));
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('Failed to load provision log:', e);
    }
}

// --- Alerts ---

async function loadAlerts() {
    try {
        const resp = await fetch('/api/v1/fleet/alerts/active');
        const data = await resp.json();
        const container = document.getElementById('alerts-list');
        clearChildren(container);

        if (!data.alerts || data.alerts.length === 0) {
            const p = document.createElement('p');
            p.className = 'loading';
            p.textContent = 'No active alerts';
            container.appendChild(p);
            return;
        }

        data.alerts.forEach(a => {
            const card = document.createElement('div');
            card.className = 'alert-card';
            card.style.cssText = 'padding:0.75rem;margin-bottom:0.5rem;background:var(--bg-secondary);border-radius:6px;border-left:3px solid ' + (a.severity === 'critical' ? 'var(--danger)' : 'var(--warning)');

            const header = document.createElement('strong');
            header.textContent = a.node_id + ' \u2014 ' + a.alert_type + ' ';
            card.appendChild(header);
            card.appendChild(createBadge(a.severity, a.severity === 'critical' ? 'badge-offline' : 'badge-warning'));

            const msg = document.createElement('p');
            msg.style.cssText = 'color:var(--text-secondary);font-size:0.8125rem;margin-top:0.25rem';
            msg.textContent = a.message;
            card.appendChild(msg);

            container.appendChild(card);
        });
    } catch (e) {
        console.error('Failed to load alerts:', e);
    }
}

// --- Config ---

async function loadConfigData() {
    try {
        // Templates
        const resp = await fetch('/api/v1/config/templates');
        const data = await resp.json();
        const container = document.getElementById('config-templates');
        clearChildren(container);

        if (!data.templates || data.templates.length === 0) {
            const p = document.createElement('p');
            p.className = 'loading';
            p.textContent = 'No templates loaded';
            container.appendChild(p);
        } else {
            data.templates.forEach(t => {
                const div = document.createElement('div');
                div.style.cssText = 'padding:0.5rem 0.75rem;background:var(--bg-secondary);border-radius:6px;margin-bottom:0.5rem';
                const name = document.createElement('strong');
                name.style.color = 'var(--teal-light)';
                name.textContent = t.role;
                div.appendChild(name);
                const hash = document.createElement('code');
                hash.style.cssText = 'margin-left:1rem;color:var(--text-muted);font-size:0.75rem';
                hash.textContent = t.hash ? t.hash.substring(0, 16) + '...' : '\u2014';
                div.appendChild(hash);
                container.appendChild(div);
            });
        }

        // Drift report
        await loadDriftReport();

        // Config queue
        await loadConfigQueue();
    } catch (e) {
        console.error('Failed to load config data:', e);
    }
}

// --- Drift Remediation ---

async function loadDriftReport() {
    const driftContainer = document.getElementById('drift-report');
    const remediateAllBtn = document.getElementById('remediate-all-btn');
    clearChildren(driftContainer);

    try {
        const driftResp = await fetch('/api/v1/config/drift');
        const driftData = await driftResp.json();

        if (!driftData.drifted_devices || driftData.drifted_devices.length === 0) {
            const p = document.createElement('p');
            p.style.color = 'var(--success)';
            p.textContent = 'No configuration drift detected.';
            driftContainer.appendChild(p);
            if (remediateAllBtn) remediateAllBtn.style.display = 'none';
            return;
        }

        if (remediateAllBtn) {
            remediateAllBtn.style.display = '';
            remediateAllBtn.onclick = () => remediateAll();
        }

        driftData.drifted_devices.forEach(d => {
            const div = document.createElement('div');
            div.style.cssText = 'display:flex;align-items:center;justify-content:space-between;padding:0.5rem 0.75rem;background:rgba(239,68,68,0.1);border-radius:6px;margin-bottom:0.25rem';

            const info = document.createElement('span');
            info.textContent = d.node_id + ' (' + d.role + ') \u2014 drift detected';
            div.appendChild(info);

            const actions = document.createElement('span');
            actions.style.cssText = 'display:flex;gap:0.375rem';

            const previewBtn = document.createElement('button');
            previewBtn.className = 'btn-outline btn-sm';
            previewBtn.textContent = 'Preview';
            previewBtn.addEventListener('click', () => showRemediationPreview(d.node_id));
            actions.appendChild(previewBtn);

            const fixBtn = document.createElement('button');
            fixBtn.className = 'btn-danger btn-sm';
            fixBtn.textContent = 'Fix Drift';
            fixBtn.addEventListener('click', () => remediateDevice(d.node_id, fixBtn));
            actions.appendChild(fixBtn);

            div.appendChild(actions);
            driftContainer.appendChild(div);
        });
    } catch (e) {
        console.error('Failed to load drift report:', e);
    }
}

async function showRemediationPreview(nodeId) {
    const modal = document.getElementById('remediation-modal');
    const modalTitle = document.getElementById('modal-title');
    const modalBody = document.getElementById('modal-body');
    const confirmBtn = document.getElementById('modal-confirm-btn');

    modalTitle.textContent = 'Remediation Preview \u2014 ' + nodeId;
    clearChildren(modalBody);

    const loading = document.createElement('p');
    loading.className = 'loading';
    loading.textContent = 'Loading preview...';
    modalBody.appendChild(loading);
    modal.style.display = '';

    try {
        const resp = await fetch('/api/v1/config/drift/' + encodeURIComponent(nodeId) + '/preview');
        const data = await resp.json();
        clearChildren(modalBody);

        if (data.error) {
            const err = document.createElement('p');
            err.style.color = 'var(--danger)';
            err.textContent = data.error;
            modalBody.appendChild(err);
            return;
        }

        const fields = [
            ['Node', data.node_id || nodeId],
            ['Role', data.role || '\u2014'],
            ['Current Hash', data.current_hash ? data.current_hash.substring(0, 16) + '...' : '\u2014'],
            ['Target Hash', data.target_hash ? data.target_hash.substring(0, 16) + '...' : '\u2014'],
        ];

        fields.forEach(([label, value]) => {
            const p = document.createElement('p');
            const b = document.createElement('strong');
            b.textContent = label + ': ';
            p.appendChild(b);
            p.appendChild(document.createTextNode(value));
            modalBody.appendChild(p);
        });

        if (data.yaml_preview) {
            const pre = document.createElement('pre');
            pre.textContent = data.yaml_preview;
            modalBody.appendChild(pre);
        }

        confirmBtn.onclick = async () => {
            confirmBtn.disabled = true;
            confirmBtn.textContent = 'Pushing...';
            await remediateDevice(nodeId);
            closeModal();
            confirmBtn.disabled = false;
            confirmBtn.textContent = 'Confirm & Push';
        };
    } catch (e) {
        clearChildren(modalBody);
        const err = document.createElement('p');
        err.style.color = 'var(--danger)';
        err.textContent = 'Error: ' + e.message;
        modalBody.appendChild(err);
    }
}

async function remediateDevice(nodeId, btn) {
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Pushing...';
    }
    try {
        const resp = await fetch('/api/v1/config/drift/' + encodeURIComponent(nodeId) + '/remediate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed: true, operator: 'dashboard' }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            console.error('Remediation failed:', data);
        }
        await loadDriftReport();
        await loadConfigQueue();
    } catch (e) {
        console.error('Remediation error:', e);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Fix Drift';
        }
    }
}

async function remediateAll() {
    const btn = document.getElementById('remediate-all-btn');
    if (btn) {
        btn.disabled = true;
        btn.textContent = 'Pushing all...';
    }
    try {
        const resp = await fetch('/api/v1/config/drift/remediate-all', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed: true, operator: 'dashboard' }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            console.error('Remediate-all failed:', data);
        }
        await loadDriftReport();
        await loadConfigQueue();
    } catch (e) {
        console.error('Remediate-all error:', e);
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.textContent = 'Remediate All';
        }
    }
}

function setupModal() {
    const modal = document.getElementById('remediation-modal');
    const closeBtn = document.getElementById('modal-close-btn');
    const cancelBtn = document.getElementById('modal-cancel-btn');

    if (closeBtn) closeBtn.addEventListener('click', closeModal);
    if (cancelBtn) cancelBtn.addEventListener('click', closeModal);
    if (modal) modal.addEventListener('click', (e) => {
        if (e.target === modal) closeModal();
    });
}

function closeModal() {
    const modal = document.getElementById('remediation-modal');
    if (modal) modal.style.display = 'none';
}

// --- Config Queue ---

async function loadConfigQueue() {
    const tbody = document.getElementById('queue-body');
    const summaryEl = document.getElementById('queue-summary');
    if (!tbody) return;

    try {
        const resp = await fetch('/api/v1/config-queue/entries');
        const data = await resp.json();
        clearChildren(tbody);

        const entries = data.entries || [];

        // Queue summary counts
        if (summaryEl) {
            clearChildren(summaryEl);
            const counts = { pending: 0, retrying: 0, delivered: 0, failed: 0, cancelled: 0 };
            entries.forEach(e => {
                const s = (e.status || '').toLowerCase();
                if (s === 'pending') counts.pending++;
                else if (s === 'retrying') counts.retrying++;
                else if (s === 'delivered') counts.delivered++;
                else if (s === 'failed_permanent' || s === 'failed') counts.failed++;
                else if (s === 'cancelled') counts.cancelled++;
            });
            const parts = [];
            if (counts.pending) parts.push(counts.pending + ' pending');
            if (counts.retrying) parts.push(counts.retrying + ' retrying');
            if (counts.delivered) parts.push(counts.delivered + ' delivered');
            if (counts.failed) parts.push(counts.failed + ' failed');
            if (counts.cancelled) parts.push(counts.cancelled + ' cancelled');
            summaryEl.textContent = parts.length ? parts.join(' \u00B7 ') : 'Queue empty';
        }

        if (entries.length === 0) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 8;
            td.className = 'loading';
            td.textContent = 'No queue entries';
            tr.appendChild(td);
            tbody.appendChild(tr);
            return;
        }

        entries.forEach(entry => {
            const tr = document.createElement('tr');
            tr.appendChild(createTextCell(entry.id || '\u2014'));
            tr.appendChild(createTextCell(entry.target_node_id || '\u2014'));
            tr.appendChild(createTextCell(entry.template_role || '\u2014'));

            // Status badge
            const statusTd = document.createElement('td');
            const statusMap = {
                pending: 'badge-warning',
                retrying: 'badge-warning',
                delivered: 'badge-online',
                failed_permanent: 'badge-offline',
                cancelled: 'badge-offline',
            };
            const statusKey = (entry.status || '').toLowerCase();
            statusTd.appendChild(createBadge(entry.status || '\u2014', statusMap[statusKey] || ''));
            tr.appendChild(statusTd);

            tr.appendChild(createTextCell(entry.retry_count !== undefined ? entry.retry_count + '/' + (entry.max_retries || 10) : '\u2014'));
            tr.appendChild(createTextCell(entry.next_retry_at ? new Date(entry.next_retry_at).toLocaleString() : '\u2014'));
            tr.appendChild(createTextCell(entry.created_at ? new Date(entry.created_at).toLocaleString() : '\u2014'));

            // Action buttons
            const actionTd = document.createElement('td');
            const canRetry = statusKey === 'failed_permanent' || statusKey === 'cancelled';
            const canCancel = statusKey === 'pending' || statusKey === 'retrying';

            if (canRetry) {
                const retryBtn = document.createElement('button');
                retryBtn.className = 'btn-primary btn-sm';
                retryBtn.textContent = 'Retry';
                retryBtn.addEventListener('click', () => queueRetry(entry.id, retryBtn));
                actionTd.appendChild(retryBtn);
            }
            if (canCancel) {
                const cancelBtn = document.createElement('button');
                cancelBtn.className = 'btn-outline btn-sm';
                cancelBtn.textContent = 'Cancel';
                cancelBtn.addEventListener('click', () => queueCancel(entry.id, cancelBtn));
                actionTd.appendChild(cancelBtn);
            }

            tr.appendChild(actionTd);
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('Failed to load config queue:', e);
    }
}

async function queueRetry(entryId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Retrying...'; }
    try {
        await fetch('/api/v1/config-queue/entry/' + encodeURIComponent(entryId) + '/retry', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed: true }),
        });
        await loadConfigQueue();
    } catch (e) {
        console.error('Queue retry error:', e);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Retry'; }
    }
}

async function queueCancel(entryId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Cancelling...'; }
    try {
        await fetch('/api/v1/config-queue/entry/' + encodeURIComponent(entryId) + '/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed: true }),
        });
        await loadConfigQueue();
    } catch (e) {
        console.error('Queue cancel error:', e);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Cancel'; }
    }
}

// --- Locator ---

function setupLocator() {
    const btn = document.getElementById('locate-btn');
    const input = document.getElementById('locate-input');
    if (!btn || !input) return;
    btn.addEventListener('click', () => locateNode(input.value.trim()));
    input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') locateNode(input.value.trim());
    });
}

async function locateNode(nodeId) {
    if (!nodeId) return;
    const container = document.getElementById('locate-result');
    clearChildren(container);

    const loading = document.createElement('p');
    loading.className = 'loading';
    loading.textContent = 'Searching...';
    container.appendChild(loading);

    try {
        const resp = await fetch('/api/v1/locate/' + encodeURIComponent(nodeId));
        const data = await resp.json();
        clearChildren(container);

        if (!data.is_found) {
            const p = document.createElement('p');
            p.style.color = 'var(--danger)';
            p.textContent = 'No position data for ' + nodeId;
            container.appendChild(p);
            return;
        }

        const pos = data.last_known_position;
        const panel = document.createElement('div');
        panel.style.cssText = 'background:var(--bg-secondary);padding:1rem;border-radius:8px;margin-top:0.5rem';

        const title = document.createElement('h3');
        title.style.cssText = 'color:var(--teal-light);margin:0 0 0.75rem';
        title.textContent = 'Location Result';
        panel.appendChild(title);

        const fields = [
            ['Target', data.target_node_id],
            ['Position', pos.latitude.toFixed(6) + ', ' + pos.longitude.toFixed(6)],
            ['Age', data.position_age_hours ? data.position_age_hours.toFixed(1) + ' hours' : 'Unknown'],
            ['Nearby Nodes', String(data.nearby_nodes.length)],
        ];
        if (data.associated_edge_node) fields.push(['Edge Node', data.associated_edge_node]);

        fields.forEach(([label, value]) => {
            const p = document.createElement('p');
            const b = document.createElement('strong');
            b.textContent = label + ': ';
            p.appendChild(b);
            p.appendChild(document.createTextNode(value));
            panel.appendChild(p);
        });

        // Confidence badge
        const confP = document.createElement('p');
        const confB = document.createElement('strong');
        confB.textContent = 'Confidence: ';
        confP.appendChild(confB);
        const confClass = data.confidence === 'high' ? 'badge-online' : data.confidence === 'medium' ? 'badge-warning' : 'badge-offline';
        confP.appendChild(createBadge(data.confidence, confClass));
        panel.appendChild(confP);

        container.appendChild(panel);

        // Nearby nodes table
        if (data.nearby_nodes.length > 0) {
            const table = document.createElement('table');
            table.className = 'data-table';
            table.style.marginTop = '0.5rem';
            const thead = document.createElement('thead');
            const headRow = document.createElement('tr');
            ['Node', 'Distance', 'Status'].forEach(h => {
                const th = document.createElement('th');
                th.textContent = h;
                headRow.appendChild(th);
            });
            thead.appendChild(headRow);
            table.appendChild(thead);

            const tbody = document.createElement('tbody');
            data.nearby_nodes.slice(0, 10).forEach(n => {
                const tr = document.createElement('tr');
                tr.appendChild(createTextCell(n.node_id));
                tr.appendChild(createTextCell(n.distance_meters.toFixed(0) + 'm'));
                const statusTd = document.createElement('td');
                statusTd.appendChild(createBadge(
                    n.is_online ? 'Online' : 'Offline',
                    n.is_online ? 'badge-online' : 'badge-offline'
                ));
                tr.appendChild(statusTd);
                tbody.appendChild(tr);
            });
            table.appendChild(tbody);
            container.appendChild(table);
        }
    } catch (e) {
        clearChildren(container);
        const p = document.createElement('p');
        p.style.color = 'var(--danger)';
        p.textContent = 'Error: ' + e.message;
        container.appendChild(p);
    }
}
