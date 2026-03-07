// JennMesh Dashboard — Client-side JavaScript
// All data rendered comes from the JennMesh API (trusted internal source).
// Text content uses textContent; HTML structure built via DOM APIs.

document.addEventListener('DOMContentLoaded', () => {
    initNavigation();
    loadHealthData();
    loadFleetData();
    setupLocator();
    setupModal();
    setupSyncTrigger();
    setupFailoverAssess();
    setupAdvisor();
    setupWatchdogTrigger();
    initThemeToggle();
    setInterval(() => {
        loadHealthData();
        loadFleetData();
        checkProvisionEvents();
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
            if (tab === 'alerts') { loadAlerts(); loadAlertSummary(); loadAnomalyData(); }
            if (tab === 'config') loadConfigData();
            if (tab === 'watchdog') loadWatchdogData();
            if (tab === 'sync') loadSyncData();
            if (tab === 'failover') loadFailoverData();
            if (tab === 'analytics') loadAnalyticsData();
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
            card.style.cssText = 'padding:0.75rem;margin-bottom:0.5rem;background:var(--surface);border-radius:6px;border-left:3px solid ' + (a.severity === 'critical' ? 'var(--error)' : 'var(--warning)');

            const header = document.createElement('strong');
            header.textContent = a.node_id + ' \u2014 ' + a.alert_type + ' ';
            card.appendChild(header);
            card.appendChild(createBadge(a.severity, a.severity === 'critical' ? 'badge-offline' : 'badge-warning'));

            const msg = document.createElement('p');
            msg.style.cssText = 'color:var(--text-muted);font-size:0.8125rem;margin-top:0.25rem';
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
                div.style.cssText = 'padding:0.5rem 0.75rem;background:var(--surface);border-radius:6px;margin-bottom:0.5rem';
                const name = document.createElement('strong');
                name.style.color = 'var(--primary-light)';
                name.textContent = t.role;
                div.appendChild(name);
                const hash = document.createElement('code');
                hash.style.cssText = 'margin-left:1rem;color:var(--text-dim);font-size:0.75rem';
                hash.textContent = t.hash ? t.hash.substring(0, 16) + '...' : '\u2014';
                div.appendChild(hash);
                container.appendChild(div);
            });
        }

        // Drift report
        await loadDriftReport();

        // Config queue
        await loadConfigQueue();

        // Config rollback snapshots (MESH-040)
        await loadRollbackData();
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
            err.style.color = 'var(--error)';
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
        err.style.color = 'var(--error)';
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
            p.style.color = 'var(--error)';
            p.textContent = 'No position data for ' + nodeId;
            container.appendChild(p);
            return;
        }

        const pos = data.last_known_position;
        const panel = document.createElement('div');
        panel.style.cssText = 'background:var(--surface);padding:1rem;border-radius:8px;margin-top:0.5rem';

        const title = document.createElement('h3');
        title.style.cssText = 'color:var(--primary-light);margin:0 0 0.75rem';
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

        // Trigger AI reasoning in background
        loadAiReasoning(nodeId);
    } catch (e) {
        clearChildren(container);
        const p = document.createElement('p');
        p.style.color = 'var(--error)';
        p.textContent = 'Error: ' + e.message;
        container.appendChild(p);
    }
}

// --- Sync Relay ---

async function loadSyncData() {
    await loadSyncStatus();
    await loadSyncSessions();
    await loadSyncLog();
}

async function loadSyncStatus() {
    const summaryEl = document.getElementById('sync-summary');
    if (!summaryEl) return;

    try {
        const resp = await fetch('/api/v1/sync-relay/status');
        const data = await resp.json();

        clearChildren(summaryEl);
        const parts = [];
        if (data.active_sessions !== undefined) parts.push(data.active_sessions + ' active');
        if (data.queue_depth !== undefined) parts.push(data.queue_depth + ' queued');
        if (data.total_synced !== undefined) parts.push(data.total_synced + ' synced');
        summaryEl.textContent = parts.length ? parts.join(' \u00B7 ') : 'No sync activity';
    } catch (e) {
        console.error('Failed to load sync status:', e);
        if (summaryEl) summaryEl.textContent = 'Unavailable';
    }
}

async function loadSyncSessions() {
    const container = document.getElementById('sync-sessions');
    if (!container) return;
    clearChildren(container);

    try {
        const resp = await fetch('/api/v1/sync-relay/sessions?limit=20');
        const data = await resp.json();
        const sessions = data.sessions || [];

        if (sessions.length === 0) {
            const p = document.createElement('p');
            p.className = 'loading';
            p.textContent = 'No active sync sessions';
            container.appendChild(p);
            return;
        }

        sessions.forEach(s => {
            const card = document.createElement('div');
            card.className = 'sync-session-card';

            // Header row: session ID + status badge
            const header = document.createElement('div');
            header.className = 'sync-session-header';
            const idEl = document.createElement('strong');
            idEl.textContent = s.session_id || s.id || '\u2014';
            header.appendChild(idEl);

            const statusStr = s.status || 'unknown';
            const statusMap = {
                pending: 'badge-warning',
                sending: 'badge-warning',
                completed: 'badge-online',
                failed: 'badge-offline',
                partial: 'badge-warning',
            };
            header.appendChild(createBadge(statusStr, statusMap[statusStr] || ''));
            card.appendChild(header);

            // Meta row: node, direction, priority
            const meta = document.createElement('div');
            meta.className = 'sync-session-meta';

            const nodeSpan = document.createElement('span');
            nodeSpan.textContent = 'Node: ' + (s.node_id || s.target_node_id || '\u2014');
            meta.appendChild(nodeSpan);

            if (s.direction) {
                const dirSpan = document.createElement('span');
                dirSpan.textContent = 'Dir: ' + s.direction;
                meta.appendChild(dirSpan);
            }

            if (s.priority !== undefined) {
                const priSpan = document.createElement('span');
                priSpan.textContent = 'P' + s.priority;
                meta.appendChild(priSpan);
            }

            card.appendChild(meta);

            // Progress bar (if fragment info available)
            const total = s.total_fragments || s.total || 0;
            const acked = s.acked_fragments || s.acked || 0;
            if (total > 0) {
                const pct = Math.round((acked / total) * 100);

                const track = document.createElement('div');
                track.className = 'progress-bar-track';
                const fill = document.createElement('div');
                fill.className = 'progress-bar-fill';
                if (statusStr === 'completed') fill.classList.add('complete');
                if (statusStr === 'failed') fill.classList.add('failed');
                fill.style.width = pct + '%';
                track.appendChild(fill);
                card.appendChild(track);

                const label = document.createElement('div');
                label.className = 'progress-label';
                label.textContent = acked + '/' + total + ' fragments (' + pct + '%)';
                card.appendChild(label);
            }

            container.appendChild(card);
        });
    } catch (e) {
        console.error('Failed to load sync sessions:', e);
    }
}

async function loadSyncLog() {
    const tbody = document.getElementById('sync-log-body');
    if (!tbody) return;
    clearChildren(tbody);

    try {
        const resp = await fetch('/api/v1/sync-relay/log?limit=50');
        const data = await resp.json();
        const entries = data.entries || [];

        if (entries.length === 0) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 9;
            td.className = 'loading';
            td.textContent = 'No sync log entries';
            tr.appendChild(td);
            tbody.appendChild(tr);
            return;
        }

        entries.forEach(entry => {
            const tr = document.createElement('tr');
            tr.appendChild(createTextCell(entry.session_id || '\u2014'));
            tr.appendChild(createTextCell(entry.node_id || entry.target_node_id || '\u2014'));
            tr.appendChild(createTextCell(entry.direction || '\u2014'));

            // Priority cell
            const priText = entry.priority !== undefined ? 'P' + entry.priority : '\u2014';
            tr.appendChild(createTextCell(priText));

            tr.appendChild(createTextCell(entry.items_synced !== undefined ? String(entry.items_synced) : '\u2014'));
            tr.appendChild(createTextCell(entry.bytes_transferred !== undefined ? formatBytes(entry.bytes_transferred) : '\u2014'));
            tr.appendChild(createTextCell(entry.duration_ms !== undefined ? (entry.duration_ms / 1000).toFixed(1) + 's' : '\u2014'));

            // Status badge
            const statusTd = document.createElement('td');
            const st = (entry.status || '').toLowerCase();
            const stMap = {
                completed: 'badge-online',
                failed: 'badge-offline',
                pending: 'badge-warning',
                sending: 'badge-warning',
                partial: 'badge-warning',
            };
            statusTd.appendChild(createBadge(entry.status || '\u2014', stMap[st] || ''));
            tr.appendChild(statusTd);

            tr.appendChild(createTextCell(entry.created_at ? new Date(entry.created_at).toLocaleString() : '\u2014'));
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('Failed to load sync log:', e);
    }
}

function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const units = ['B', 'KB', 'MB'];
    const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    return (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
}

function setupSyncTrigger() {
    const btn = document.getElementById('sync-trigger-btn');
    const input = document.getElementById('sync-trigger-input');
    if (!btn || !input) return;
    btn.addEventListener('click', () => triggerSync(input.value.trim()));
    input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') triggerSync(input.value.trim());
    });
}

async function triggerSync(nodeId) {
    if (!nodeId) return;
    const container = document.getElementById('sync-trigger-result');
    clearChildren(container);

    const loading = document.createElement('p');
    loading.className = 'loading';
    loading.textContent = 'Triggering sync for ' + nodeId + '...';
    container.appendChild(loading);

    try {
        const resp = await fetch('/api/v1/sync-relay/trigger/' + encodeURIComponent(nodeId), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed: true }),
        });
        const data = await resp.json();
        clearChildren(container);

        if (!resp.ok) {
            const p = document.createElement('p');
            p.style.color = 'var(--error)';
            p.textContent = data.detail || 'Sync trigger failed';
            container.appendChild(p);
            return;
        }

        const p = document.createElement('p');
        p.style.color = 'var(--success)';
        p.textContent = 'Sync triggered for ' + nodeId;
        if (data.session_id) p.textContent += ' (session: ' + data.session_id + ')';
        container.appendChild(p);

        // Refresh sessions
        await loadSyncSessions();
        await loadSyncLog();
    } catch (e) {
        clearChildren(container);
        const p = document.createElement('p');
        p.style.color = 'var(--error)';
        p.textContent = 'Error: ' + e.message;
        container.appendChild(p);
    }
}

// --- Failover ---

async function loadFailoverData() {
    await loadActiveFailovers();
    await loadFailoverEvents();
}

async function loadActiveFailovers() {
    const container = document.getElementById('failover-active');
    const summaryEl = document.getElementById('failover-summary');
    if (!container) return;
    clearChildren(container);

    try {
        const resp = await fetch('/api/v1/failover/active');
        const data = await resp.json();
        const events = data.events || [];

        if (summaryEl) {
            summaryEl.textContent = events.length ? events.length + ' active' : 'No active failovers';
        }

        if (events.length === 0) {
            const p = document.createElement('p');
            p.className = 'loading';
            p.textContent = 'No active failovers';
            container.appendChild(p);
            return;
        }

        events.forEach(evt => {
            const card = document.createElement('div');
            card.className = 'failover-card';

            const header = document.createElement('div');
            header.className = 'failover-card-header';

            const title = document.createElement('strong');
            title.textContent = 'Node ' + (evt.failed_node_id || '\u2014');
            header.appendChild(title);

            const statusStr = evt.status || 'active';
            const statusMap = {
                active: 'badge-offline',
                reverted: 'badge-online',
                cancelled: 'badge-warning',
                revert_failed: 'badge-offline',
            };
            header.appendChild(createBadge(statusStr, statusMap[statusStr] || ''));
            card.appendChild(header);

            // Dependent nodes
            const deps = evt.dependent_nodes || [];
            if (deps.length > 0) {
                const depLabel = document.createElement('div');
                depLabel.style.cssText = 'font-size:0.75rem;color:var(--text-dim);margin-bottom:0.25rem';
                depLabel.textContent = 'Dependent: ' + deps.join(', ');
                card.appendChild(depLabel);
            }

            // Compensations
            const comps = evt.compensations || [];
            if (comps.length > 0) {
                const compList = document.createElement('div');
                compList.className = 'compensation-list';

                comps.forEach(c => {
                    const chip = document.createElement('span');
                    chip.className = 'compensation-chip';
                    if (c.status) chip.classList.add(c.status);
                    chip.textContent = (c.comp_node_id || '') + ' ' + (c.comp_type || '') + ' ' + (c.original_value || '') + '\u2192' + (c.new_value || '');
                    compList.appendChild(chip);
                });

                card.appendChild(compList);
            }

            // Action buttons
            const actions = document.createElement('div');
            actions.style.cssText = 'display:flex;gap:0.375rem;margin-top:0.5rem';

            if (statusStr === 'active') {
                const revertBtn = document.createElement('button');
                revertBtn.className = 'btn-primary btn-sm';
                revertBtn.textContent = 'Revert';
                revertBtn.addEventListener('click', () => revertFailover(evt.id, revertBtn));
                actions.appendChild(revertBtn);

                const cancelBtn = document.createElement('button');
                cancelBtn.className = 'btn-outline btn-sm';
                cancelBtn.textContent = 'Cancel';
                cancelBtn.addEventListener('click', () => cancelFailover(evt.id, cancelBtn));
                actions.appendChild(cancelBtn);
            }

            card.appendChild(actions);
            container.appendChild(card);
        });
    } catch (e) {
        console.error('Failed to load active failovers:', e);
    }
}

async function loadFailoverEvents() {
    const tbody = document.getElementById('failover-body');
    if (!tbody) return;
    clearChildren(tbody);

    try {
        const resp = await fetch('/api/v1/failover/active');
        const data = await resp.json();
        const events = data.events || [];

        if (events.length === 0) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 8;
            td.className = 'loading';
            td.textContent = 'No failover events';
            tr.appendChild(td);
            tbody.appendChild(tr);
            return;
        }

        events.forEach(evt => {
            const tr = document.createElement('tr');
            tr.appendChild(createTextCell(evt.id !== undefined ? String(evt.id) : '\u2014'));
            tr.appendChild(createTextCell(evt.failed_node_id || '\u2014'));
            tr.appendChild(createTextCell((evt.dependent_nodes || []).join(', ') || '\u2014'));

            // Compensations count
            const comps = evt.compensations || [];
            tr.appendChild(createTextCell(comps.length ? comps.length + ' actions' : '\u2014'));

            // Status badge
            const statusTd = document.createElement('td');
            const st = (evt.status || '').toLowerCase();
            const stMap = {
                active: 'badge-offline',
                reverted: 'badge-online',
                cancelled: 'badge-warning',
                revert_failed: 'badge-offline',
            };
            statusTd.appendChild(createBadge(evt.status || '\u2014', stMap[st] || ''));
            tr.appendChild(statusTd);

            tr.appendChild(createTextCell(evt.operator || '\u2014'));
            tr.appendChild(createTextCell(evt.created_at ? new Date(evt.created_at).toLocaleString() : '\u2014'));

            // Action buttons
            const actionTd = document.createElement('td');
            if (st === 'active') {
                const revertBtn = document.createElement('button');
                revertBtn.className = 'btn-primary btn-sm';
                revertBtn.textContent = 'Revert';
                revertBtn.addEventListener('click', () => revertFailover(evt.id, revertBtn));
                actionTd.appendChild(revertBtn);

                const cancelBtn = document.createElement('button');
                cancelBtn.className = 'btn-outline btn-sm';
                cancelBtn.textContent = 'Cancel';
                cancelBtn.style.marginLeft = '0.25rem';
                cancelBtn.addEventListener('click', () => cancelFailover(evt.id, cancelBtn));
                actionTd.appendChild(cancelBtn);
            }
            tr.appendChild(actionTd);
            tbody.appendChild(tr);
        });
    } catch (e) {
        console.error('Failed to load failover events:', e);
    }
}

function setupFailoverAssess() {
    const btn = document.getElementById('failover-assess-btn');
    const input = document.getElementById('failover-assess-input');
    if (!btn || !input) return;
    btn.addEventListener('click', () => assessNode(input.value.trim()));
    input.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') assessNode(input.value.trim());
    });
}

async function assessNode(nodeId) {
    if (!nodeId) return;
    const container = document.getElementById('failover-assessment');
    clearChildren(container);

    const loading = document.createElement('p');
    loading.className = 'loading';
    loading.textContent = 'Assessing failover impact for ' + nodeId + '...';
    container.appendChild(loading);

    try {
        const resp = await fetch('/api/v1/failover/' + encodeURIComponent(nodeId) + '/assess');
        const data = await resp.json();
        clearChildren(container);

        if (!resp.ok) {
            const p = document.createElement('p');
            p.style.color = 'var(--error)';
            p.textContent = data.detail || 'Assessment failed';
            container.appendChild(p);
            return;
        }

        const panel = document.createElement('div');
        panel.className = 'assessment-panel';

        // SPOF indicator
        const spofH = document.createElement('h4');
        spofH.textContent = 'Single Point of Failure';
        panel.appendChild(spofH);

        const spofBadge = document.createElement('span');
        spofBadge.className = 'spof-indicator ' + (data.is_spof ? 'yes' : 'no');
        spofBadge.textContent = data.is_spof ? 'Yes \u2014 SPOF' : 'No \u2014 Redundant';
        panel.appendChild(spofBadge);

        // Dependent nodes
        const deps = data.dependent_nodes || [];
        if (deps.length > 0) {
            const depH = document.createElement('h4');
            depH.textContent = 'Dependent Nodes (' + deps.length + ')';
            panel.appendChild(depH);

            const depList = document.createElement('div');
            depList.className = 'node-list';
            deps.forEach(nid => {
                const chip = document.createElement('span');
                chip.className = 'node-chip';
                chip.textContent = nid;
                depList.appendChild(chip);
            });
            panel.appendChild(depList);
        }

        // Compensation candidates
        const candidates = data.compensation_candidates || [];
        if (candidates.length > 0) {
            const candH = document.createElement('h4');
            candH.textContent = 'Compensation Candidates (' + candidates.length + ')';
            panel.appendChild(candH);

            const candList = document.createElement('div');
            candList.className = 'node-list';
            candidates.forEach(c => {
                const chip = document.createElement('span');
                chip.className = 'node-chip';
                chip.textContent = (c.node_id || '') + (c.battery !== undefined ? ' (' + c.battery + '%)' : '');
                candList.appendChild(chip);
            });
            panel.appendChild(candList);
        }

        // Suggested compensations
        const suggestions = data.suggested_compensations || [];
        if (suggestions.length > 0) {
            const sugH = document.createElement('h4');
            sugH.textContent = 'Suggested Compensations (' + suggestions.length + ')';
            panel.appendChild(sugH);

            const sugList = document.createElement('div');
            sugList.className = 'compensation-list';
            suggestions.forEach(s => {
                const chip = document.createElement('span');
                chip.className = 'compensation-chip';
                chip.textContent = (s.comp_node_id || s.node_id || '') + ' ' + (s.comp_type || '') + ' ' + (s.config_key || '') + ' \u2192 ' + (s.new_value || '');
                sugList.appendChild(chip);
            });
            panel.appendChild(sugList);
        }

        // Execute button (only if there are suggestions)
        if (suggestions.length > 0) {
            const execBtn = document.createElement('button');
            execBtn.className = 'btn-danger btn-sm';
            execBtn.style.marginTop = '0.75rem';
            execBtn.textContent = 'Execute Failover';
            execBtn.addEventListener('click', () => executeFailover(nodeId, execBtn));
            panel.appendChild(execBtn);
        }

        container.appendChild(panel);
    } catch (e) {
        clearChildren(container);
        const p = document.createElement('p');
        p.style.color = 'var(--error)';
        p.textContent = 'Error: ' + e.message;
        container.appendChild(p);
    }
}

async function executeFailover(nodeId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Executing...'; }
    try {
        const resp = await fetch('/api/v1/failover/' + encodeURIComponent(nodeId) + '/execute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed: true }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            console.error('Failover execute failed:', data);
        }
        await loadFailoverData();
    } catch (e) {
        console.error('Failover execute error:', e);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Execute Failover'; }
    }
}

async function revertFailover(eventId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Reverting...'; }
    try {
        const resp = await fetch('/api/v1/failover/' + encodeURIComponent(eventId) + '/revert', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed: true }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            console.error('Failover revert failed:', data);
        }
        await loadFailoverData();
    } catch (e) {
        console.error('Failover revert error:', e);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Revert'; }
    }
}

async function cancelFailover(eventId, btn) {
    if (btn) { btn.disabled = true; btn.textContent = 'Cancelling...'; }
    try {
        const resp = await fetch('/api/v1/failover/' + encodeURIComponent(eventId) + '/cancel', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed: true }),
        });
        const data = await resp.json();
        if (!resp.ok) {
            console.error('Failover cancel failed:', data);
        }
        await loadFailoverData();
    } catch (e) {
        console.error('Failover cancel error:', e);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Cancel'; }
    }
}

// --- Provisioning Advisor (MESH-032) ---

function setupAdvisor() {
    const btn = document.getElementById('advisor-btn');
    if (!btn) return;
    btn.addEventListener('click', () => requestRecommendation());
    loadAdvisorStatus();
}

async function loadAdvisorStatus() {
    const statusEl = document.getElementById('advisor-status');
    if (!statusEl) return;

    try {
        const resp = await fetch('/api/v1/advisor/status');
        const data = await resp.json();
        const parts = [];
        if (data.ollama_available) parts.push('Ollama connected');
        else parts.push('Deterministic mode');
        if (data.model) parts.push(data.model);
        statusEl.textContent = parts.join(' \u00B7 ');
    } catch (e) {
        statusEl.textContent = 'Unavailable';
    }
}

async function requestRecommendation() {
    const btn = document.getElementById('advisor-btn');
    const container = document.getElementById('advisor-result');
    clearChildren(container);

    const terrain = document.getElementById('advisor-terrain').value;
    const numNodes = parseInt(document.getElementById('advisor-nodes').value, 10) || 5;
    const powerSource = document.getElementById('advisor-power').value;
    const coverage = parseFloat(document.getElementById('advisor-coverage').value) || 5000;

    if (btn) { btn.disabled = true; btn.textContent = 'Analyzing...'; }

    const loading = document.createElement('p');
    loading.className = 'loading';
    loading.textContent = 'Generating deployment recommendations...';
    container.appendChild(loading);

    try {
        const resp = await fetch('/api/v1/advisor/recommend', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                terrain: terrain,
                num_nodes: numNodes,
                power_source: powerSource,
                desired_coverage_m: coverage,
            }),
        });
        const data = await resp.json();
        clearChildren(container);

        if (!resp.ok) {
            const p = document.createElement('p');
            p.style.color = 'var(--error)';
            p.textContent = data.detail || 'Recommendation failed';
            container.appendChild(p);
            return;
        }

        const panel = document.createElement('div');
        panel.className = 'recommendation-panel';

        // Source badge
        const sourceH = document.createElement('h4');
        sourceH.textContent = 'Source ';
        const srcBadge = document.createElement('span');
        srcBadge.className = 'source-badge ' + (data.source || 'deterministic');
        srcBadge.textContent = data.source || 'deterministic';
        sourceH.appendChild(srcBadge);
        panel.appendChild(sourceH);

        // Summary
        if (data.summary) {
            const sumP = document.createElement('p');
            sumP.style.cssText = 'color:var(--text-muted);font-size:0.875rem;margin-bottom:0.5rem';
            sumP.textContent = data.summary;
            panel.appendChild(sumP);
        }

        // Recommended roles
        const roles = data.recommended_roles || [];
        if (roles.length > 0) {
            const rolesH = document.createElement('h4');
            rolesH.textContent = 'Recommended Roles (' + roles.length + ')';
            panel.appendChild(rolesH);

            const roleList = document.createElement('div');
            roleList.className = 'node-list';
            roles.forEach(r => {
                const chip = document.createElement('span');
                chip.className = 'node-chip';
                chip.textContent = typeof r === 'string' ? r : (r.role || r.node || JSON.stringify(r));
                roleList.appendChild(chip);
            });
            panel.appendChild(roleList);
        }

        // Power settings
        if (data.power_settings) {
            const pwrH = document.createElement('h4');
            pwrH.textContent = 'Power Settings';
            panel.appendChild(pwrH);

            const pwrP = document.createElement('p');
            pwrP.style.cssText = 'font-family:"JetBrains Mono",monospace;font-size:0.8125rem;color:var(--text-muted)';
            const ps = data.power_settings;
            const parts = [];
            if (ps.tx_power !== undefined) parts.push('TX: ' + ps.tx_power + ' dBm');
            if (ps.region) parts.push('Region: ' + ps.region);
            pwrP.textContent = parts.length ? parts.join(' \u00B7 ') : JSON.stringify(ps);
            panel.appendChild(pwrP);
        }

        // Channel config
        if (data.channel_config) {
            const chH = document.createElement('h4');
            chH.textContent = 'Channel Config';
            panel.appendChild(chH);

            const chP = document.createElement('p');
            chP.style.cssText = 'font-family:"JetBrains Mono",monospace;font-size:0.8125rem;color:var(--text-muted)';
            const cc = data.channel_config;
            const cParts = [];
            if (cc.modem_preset) cParts.push('Preset: ' + cc.modem_preset);
            if (cc.name) cParts.push('Name: ' + cc.name);
            chP.textContent = cParts.length ? cParts.join(' \u00B7 ') : JSON.stringify(cc);
            panel.appendChild(chP);
        }

        // Deployment order
        const order = data.deployment_order || [];
        if (order.length > 0) {
            const orderH = document.createElement('h4');
            orderH.textContent = 'Deployment Order';
            panel.appendChild(orderH);

            const orderList = document.createElement('div');
            orderList.className = 'node-list';
            order.forEach((item, i) => {
                const chip = document.createElement('span');
                chip.className = 'node-chip';
                chip.textContent = (i + 1) + '. ' + (typeof item === 'string' ? item : JSON.stringify(item));
                orderList.appendChild(chip);
            });
            panel.appendChild(orderList);
        }

        // Warnings
        const warnings = data.warnings || [];
        if (warnings.length > 0) {
            const warnH = document.createElement('h4');
            warnH.style.color = 'var(--warning)';
            warnH.textContent = 'Warnings';
            panel.appendChild(warnH);

            warnings.forEach(w => {
                const wP = document.createElement('p');
                wP.style.cssText = 'color:var(--warning);font-size:0.8125rem;margin-bottom:0.125rem';
                wP.textContent = '\u26A0 ' + w;
                panel.appendChild(wP);
            });
        }

        container.appendChild(panel);
    } catch (e) {
        clearChildren(container);
        const p = document.createElement('p');
        p.style.color = 'var(--error)';
        p.textContent = 'Error: ' + e.message;
        container.appendChild(p);
    } finally {
        if (btn) { btn.disabled = false; btn.textContent = 'Get Recommendations'; }
    }
}

// === MESH-018: Alert Summarization ===

async function loadAlertSummary() {
    const container = document.getElementById('alert-summary-panel');
    const statusEl = document.getElementById('alert-summary-status');
    if (!container) return;
    clearChildren(container);

    try {
        const resp = await fetch('/api/v1/alerts/summary');
        const data = await resp.json();

        // Status badge
        if (statusEl) {
            statusEl.textContent = 'source: ' + (data.source || 'unknown');
        }

        const card = document.createElement('div');
        card.className = 'alert-summary-card';

        // Summary text
        const summaryP = document.createElement('p');
        summaryP.className = 'summary-text';
        summaryP.textContent = data.summary || 'No summary available.';
        card.appendChild(summaryP);

        // Count
        const countP = document.createElement('p');
        countP.style.cssText = 'font-size:0.8125rem;color:var(--text-dim);margin-bottom:0.5rem';
        countP.textContent = (data.alert_count || 0) + ' active alert(s)';
        card.appendChild(countP);

        // Breakdown chips — flatten nested breakdown (by_type, by_severity, etc.)
        const breakdown = data.breakdown || {};
        const chips = document.createElement('div');
        chips.className = 'breakdown-chips';
        let hasChips = false;
        Object.keys(breakdown).forEach(category => {
            const inner = breakdown[category];
            if (inner && typeof inner === 'object') {
                Object.keys(inner).forEach(k => {
                    const chip = document.createElement('span');
                    chip.className = 'breakdown-chip';
                    chip.textContent = k + ': ' + inner[k];
                    chips.appendChild(chip);
                    hasChips = true;
                });
            }
        });
        if (hasChips) card.appendChild(chips);

        container.appendChild(card);
    } catch (e) {
        const p = document.createElement('p');
        p.className = 'loading';
        p.textContent = 'Alert summary unavailable';
        container.appendChild(p);
    }
}

// === MESH-040: Config Rollback ===

async function loadRollbackData() {
    const tbody = document.getElementById('rollback-body');
    const statusEl = document.getElementById('rollback-status');
    if (!tbody) return;
    clearChildren(tbody);

    try {
        // Load status
        const statusResp = await fetch('/api/v1/config-rollback/status');
        if (statusResp.ok) {
            const statusData = await statusResp.json();
            if (statusEl) {
                statusEl.textContent = 'monitoring: ' + (statusData.monitoring_count || 0) + ' nodes';
            }
        }

        // Load snapshots
        const resp = await fetch('/api/v1/config-rollback/snapshots?limit=20');
        const data = await resp.json();

        if (!data.snapshots || data.snapshots.length === 0) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 6;
            td.className = 'loading';
            td.textContent = 'No config snapshots recorded';
            tr.appendChild(td);
            tbody.appendChild(tr);
            return;
        }

        data.snapshots.forEach(s => {
            const tr = document.createElement('tr');
            tr.appendChild(createTextCell(s.id || s.snapshot_id || '\u2014'));
            tr.appendChild(createTextCell(s.node_id || '\u2014'));
            tr.appendChild(createTextCell(s.timestamp ? new Date(s.timestamp).toLocaleString() : '\u2014'));
            tr.appendChild(createTextCell(s.trigger || s.change_type || '\u2014'));

            const hashBefore = s.hash_before || s.yaml_hash_before || '';
            tr.appendChild(createTextCell(hashBefore ? hashBefore.substring(0, 16) + '...' : '\u2014'));

            // Rollback button
            const actionTd = document.createElement('td');
            const rollBtn = document.createElement('button');
            rollBtn.className = 'btn-outline btn-sm';
            rollBtn.textContent = 'Rollback';
            const snapId = s.id || s.snapshot_id;
            rollBtn.addEventListener('click', () => confirmRollback(snapId, s.node_id, rollBtn));
            actionTd.appendChild(rollBtn);
            tr.appendChild(actionTd);

            tbody.appendChild(tr);
        });
    } catch (e) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 6;
        td.className = 'loading';
        td.textContent = 'Config rollback system unavailable';
        tr.appendChild(td);
        tbody.appendChild(tr);
    }
}

async function confirmRollback(snapshotId, nodeId, btn) {
    if (!confirm('Roll back ' + (nodeId || 'node') + ' to snapshot #' + snapshotId + '? This pushes config to a live mesh node.')) {
        return;
    }
    btn.disabled = true;
    btn.textContent = 'Rolling back...';

    try {
        const resp = await fetch('/api/v1/config-rollback/snapshot/' + snapshotId + '/rollback', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed: true }),
        });
        const data = await resp.json();
        if (resp.ok) {
            btn.textContent = 'Done';
            btn.className = 'btn-primary btn-sm';
        } else {
            btn.textContent = 'Failed';
            btn.className = 'btn-danger btn-sm';
            console.error('Rollback failed:', data.detail || data.error);
        }
    } catch (e) {
        btn.textContent = 'Error';
        btn.className = 'btn-danger btn-sm';
        console.error('Rollback error:', e);
    }
}

// === MESH-030: Watchdog (Part 1 — Status Grid + History) ===

async function loadWatchdogData() {
    await loadWatchdogStatus();
    await populateWatchdogChecks();
    await loadWatchdogHistory();
}

async function loadWatchdogStatus() {
    const grid = document.getElementById('watchdog-grid');
    const summaryEl = document.getElementById('watchdog-summary');
    if (!grid) return;
    clearChildren(grid);

    try {
        const resp = await fetch('/api/v1/watchdog/status');
        const data = await resp.json();

        if (summaryEl) {
            summaryEl.textContent = 'cycles: ' + (data.total_cycles || 0) +
                ' \u00B7 loop: ' + (data.loop_sleep_seconds || 60) + 's';
        }

        const checks = data.checks || {};
        Object.keys(checks).forEach(name => {
            const info = checks[name];
            const card = document.createElement('div');
            card.className = 'watchdog-check-card';

            const nameDiv = document.createElement('div');
            nameDiv.className = 'check-name';
            const dot = document.createElement('span');
            dot.className = 'check-dot ' + (info.enabled ? 'enabled' : 'disabled');
            nameDiv.appendChild(dot);
            nameDiv.appendChild(document.createTextNode(name.replace(/_/g, ' ')));
            card.appendChild(nameDiv);

            const meta = document.createElement('div');
            meta.className = 'check-meta';
            const interval = info.interval_seconds || 0;
            const since = info.seconds_since_last_run;
            meta.textContent = 'every ' + interval + 's' +
                (since !== null && since !== undefined ? ' \u00B7 last: ' + Math.round(since) + 's ago' : ' \u00B7 not yet run');
            card.appendChild(meta);

            grid.appendChild(card);
        });
    } catch (e) {
        const p = document.createElement('p');
        p.className = 'loading';
        p.textContent = 'Watchdog unavailable';
        grid.appendChild(p);
    }
}

async function loadWatchdogHistory() {
    const tbody = document.getElementById('watchdog-body');
    if (!tbody) return;
    clearChildren(tbody);

    try {
        const resp = await fetch('/api/v1/watchdog/history?limit=30');
        const data = await resp.json();

        if (!data.runs || data.runs.length === 0) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 5;
            td.className = 'loading';
            td.textContent = 'No watchdog runs recorded';
            tr.appendChild(td);
            tbody.appendChild(tr);
            return;
        }

        data.runs.forEach(run => {
            const tr = document.createElement('tr');
            tr.appendChild(createTextCell(run.id || run.run_id || '\u2014'));
            tr.appendChild(createTextCell(run.check_name || '\u2014'));

            const statusTd = document.createElement('td');
            const hasError = run.error || (run.result_summary && run.result_summary.includes('"error"'));
            statusTd.appendChild(createBadge(
                hasError ? 'error' : 'ok',
                hasError ? 'badge-offline' : 'badge-online'
            ));
            tr.appendChild(statusTd);

            // Summary (truncated)
            const summary = run.result_summary || run.error || '\u2014';
            const truncated = summary.length > 80 ? summary.substring(0, 80) + '...' : summary;
            tr.appendChild(createTextCell(truncated));

            tr.appendChild(createTextCell(run.completed_at ? new Date(run.completed_at).toLocaleString() : run.started_at || '\u2014'));
            tbody.appendChild(tr);
        });
    } catch (e) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 5;
        td.className = 'loading';
        td.textContent = 'Watchdog history unavailable';
        tr.appendChild(td);
        tbody.appendChild(tr);
    }
}

// === MESH-017: Anomaly Detection UI ===

async function loadAnomalyData() {
    const panel = document.getElementById('anomaly-panel');
    const statusEl = document.getElementById('anomaly-status');
    if (!panel) return;
    clearChildren(panel);

    try {
        // Fetch status and fleet analysis in parallel
        const [statusResp, fleetResp] = await Promise.all([
            fetch('/api/v1/anomaly/status'),
            fetch('/api/v1/anomaly/fleet'),
        ]);
        const status = await statusResp.json();
        const fleet = await fleetResp.json();

        if (statusEl) {
            statusEl.textContent = (status.ollama_available ? 'AI: active' : 'baseline only') +
                ' \u00B7 threshold: ' + (status.deviation_threshold || 'N/A');
        }

        const reports = fleet.reports || [];
        if (reports.length === 0) {
            const p = document.createElement('p');
            p.className = 'loading';
            p.textContent = 'No anomalies detected';
            panel.appendChild(p);
            return;
        }

        reports.forEach(report => {
            const card = document.createElement('div');
            card.className = 'anomaly-card';

            // Header: node + source
            const header = document.createElement('div');
            header.className = 'anomaly-header';
            const nodeBadge = document.createElement('span');
            nodeBadge.className = 'node-badge';
            nodeBadge.textContent = report.node_id || '\u2014';
            header.appendChild(nodeBadge);
            const srcBadge = document.createElement('span');
            srcBadge.className = 'source-badge ' + (report.source || 'baseline');
            srcBadge.textContent = report.source || 'baseline';
            header.appendChild(srcBadge);
            card.appendChild(header);

            // Deviating metrics chips
            const metrics = report.deviating_metrics || [];
            if (metrics.length > 0) {
                const list = document.createElement('div');
                list.className = 'metrics-list';
                metrics.forEach(m => {
                    const chip = document.createElement('span');
                    chip.className = 'metric-chip';
                    chip.textContent = m.metric + ': ' + (m.current !== undefined ? m.current : '?') +
                        ' (avg ' + (m.baseline_mean !== undefined ? Number(m.baseline_mean).toFixed(1) : '?') + ')';
                    list.appendChild(chip);
                });
                card.appendChild(list);
            }

            // AI analysis if present
            const ai = report.ai_analysis;
            if (ai && ai.summary) {
                const aiDiv = document.createElement('div');
                aiDiv.className = 'ai-analysis';
                const sevSpan = document.createElement('strong');
                sevSpan.textContent = (ai.severity || 'unknown').toUpperCase();
                aiDiv.appendChild(sevSpan);
                aiDiv.appendChild(document.createTextNode(' \u2014 ' + ai.summary));
                if (ai.recommended_action) {
                    const rec = document.createElement('p');
                    rec.style.cssText = 'margin-top:0.25rem;font-size:0.8125rem;color:var(--text-dim)';
                    rec.textContent = '\u2192 ' + ai.recommended_action;
                    aiDiv.appendChild(rec);
                }
                card.appendChild(aiDiv);
            }

            panel.appendChild(card);
        });
    } catch (e) {
        console.error('Failed to load anomaly data:', e);
        const p = document.createElement('p');
        p.className = 'loading';
        p.textContent = 'Anomaly detection unavailable';
        panel.appendChild(p);
    }
}

// === MESH-030 Part 2: Watchdog Manual Trigger ===

function setupWatchdogTrigger() {
    const btn = document.getElementById('watchdog-trigger-btn');
    if (!btn) return;

    btn.addEventListener('click', async () => {
        const select = document.getElementById('watchdog-check-select');
        const resultEl = document.getElementById('watchdog-trigger-result');
        const checkName = select ? select.value : '';
        if (!checkName) {
            if (resultEl) resultEl.textContent = 'Select a check first';
            return;
        }

        btn.disabled = true;
        btn.textContent = 'Running...';
        if (resultEl) resultEl.textContent = '';

        try {
            const resp = await fetch('/api/v1/watchdog/trigger/' + encodeURIComponent(checkName), {
                method: 'POST',
            });
            const data = await resp.json();

            if (resultEl) {
                clearChildren(resultEl);
                const pre = document.createElement('pre');
                pre.textContent = JSON.stringify(data.result || data, null, 2);
                resultEl.appendChild(pre);
            }

            // Refresh history after trigger
            await loadWatchdogHistory();
        } catch (e) {
            if (resultEl) resultEl.textContent = 'Trigger failed: ' + e.message;
        } finally {
            btn.disabled = false;
            btn.textContent = 'Run Check';
        }
    });
}

async function populateWatchdogChecks() {
    const select = document.getElementById('watchdog-check-select');
    if (!select || select.options.length > 1) return;

    try {
        const resp = await fetch('/api/v1/watchdog/status');
        const data = await resp.json();
        const checks = data.checks || {};
        Object.keys(checks).forEach(name => {
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = name.replace(/_/g, ' ');
            select.appendChild(opt);
        });
    } catch (e) {
        console.error('Failed to load watchdog checks:', e);
    }
}

// === MESH-039: Environmental Telemetry UI (Part 1) ===

async function loadAnalyticsData() {
    await loadEnvFleetSummary();
    await loadEnvAlerts();

    // Wire refresh button (idempotent)
    const refreshBtn = document.getElementById('env-refresh-btn');
    if (refreshBtn && !refreshBtn._wired) {
        refreshBtn._wired = true;
        refreshBtn.addEventListener('click', () => loadAnalyticsData());
    }
}

async function loadEnvFleetSummary() {
    const grid = document.getElementById('env-fleet-summary');
    const summaryEl = document.getElementById('analytics-summary');
    if (!grid) return;
    clearChildren(grid);

    try {
        const resp = await fetch('/api/v1/environment/fleet/summary');
        const data = await resp.json();

        if (summaryEl) {
            summaryEl.textContent = 'nodes: ' + (data.node_count || 0) +
                ' \u00B7 readings: ' + (data.readings ? data.readings.length : 0);
        }

        // Fleet-wide stat cards
        const stats = [
            { label: 'Avg Temperature', value: data.avg_temperature, unit: '\u00B0C' },
            { label: 'Avg Humidity', value: data.avg_humidity, unit: '%' },
            { label: 'Avg Pressure', value: data.avg_pressure, unit: 'hPa' },
            { label: 'Nodes Reporting', value: data.node_count, unit: '' },
        ];

        stats.forEach(s => {
            const card = document.createElement('div');
            card.className = 'env-stat-card';

            const val = document.createElement('div');
            val.className = 'env-value';
            val.textContent = s.value !== null && s.value !== undefined
                ? Number(s.value).toFixed(1) + s.unit
                : '\u2014';
            card.appendChild(val);

            const label = document.createElement('div');
            label.className = 'env-label';
            label.textContent = s.label;
            card.appendChild(label);

            grid.appendChild(card);
        });

        // Per-node latest readings
        const readings = data.readings || [];
        if (readings.length > 0) {
            readings.forEach(r => {
                const nodeCard = document.createElement('div');
                nodeCard.className = 'env-node-reading';

                const nodeId = document.createElement('div');
                nodeId.className = 'env-node-id';
                nodeId.textContent = r.node_id || '\u2014';
                nodeCard.appendChild(nodeId);

                const readingsDiv = document.createElement('div');
                readingsDiv.className = 'env-readings';
                const parts = [];
                if (r.temperature !== null && r.temperature !== undefined) parts.push(Number(r.temperature).toFixed(1) + '\u00B0C');
                if (r.humidity !== null && r.humidity !== undefined) parts.push(Number(r.humidity).toFixed(0) + '% RH');
                if (r.pressure !== null && r.pressure !== undefined) parts.push(Number(r.pressure).toFixed(0) + ' hPa');
                if (r.air_quality !== null && r.air_quality !== undefined) parts.push('AQI ' + r.air_quality);
                readingsDiv.textContent = parts.join(' \u00B7 ') || 'No data';
                nodeCard.appendChild(readingsDiv);

                grid.appendChild(nodeCard);
            });
        }
    } catch (e) {
        console.error('Failed to load env fleet summary:', e);
        const p = document.createElement('p');
        p.className = 'loading';
        p.textContent = 'Environmental data unavailable';
        grid.appendChild(p);
    }
}

async function loadEnvAlerts() {
    const tbody = document.getElementById('env-alerts-body');
    if (!tbody) return;
    clearChildren(tbody);

    try {
        const resp = await fetch('/api/v1/environment/alerts?limit=50');
        const data = await resp.json();

        const alerts = data.alerts || [];
        if (alerts.length === 0) {
            const tr = document.createElement('tr');
            const td = document.createElement('td');
            td.colSpan = 5;
            td.className = 'loading';
            td.textContent = 'No environmental alerts';
            tr.appendChild(td);
            tbody.appendChild(tr);
            return;
        }

        alerts.forEach(a => {
            const tr = document.createElement('tr');
            tr.appendChild(createTextCell(a.node_id || '\u2014'));
            tr.appendChild(createTextCell(a.metric || a.alert_type || '\u2014'));

            const sevTd = document.createElement('td');
            const sev = a.severity || 'warning';
            sevTd.appendChild(createBadge(sev, sev === 'critical' ? 'badge-offline' : 'badge-warning'));
            tr.appendChild(sevTd);

            tr.appendChild(createTextCell(a.message || '\u2014'));
            tr.appendChild(createTextCell(a.timestamp ? new Date(a.timestamp).toLocaleString() : '\u2014'));
            tbody.appendChild(tr);
        });
    } catch (e) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 5;
        td.className = 'loading';
        td.textContent = 'Env alerts unavailable';
        tr.appendChild(td);
        tbody.appendChild(tr);
    }
}

// --- Lost Node AI Reasoning (MESH-033) ---

async function loadAiReasoning(nodeId) {
    const container = document.getElementById('ai-reasoning-result');
    if (!container) return;
    clearChildren(container);

    const loading = document.createElement('p');
    loading.className = 'loading';
    loading.textContent = 'Running AI reasoning for ' + nodeId + '...';
    container.appendChild(loading);

    try {
        const resp = await fetch('/api/v1/locate/' + encodeURIComponent(nodeId) + '/ai-reasoning');
        const data = await resp.json();
        clearChildren(container);

        if (!resp.ok) {
            const p = document.createElement('p');
            p.style.color = 'var(--text-dim)';
            p.textContent = 'AI reasoning unavailable';
            container.appendChild(p);
            return;
        }

        const panel = document.createElement('div');
        panel.className = 'reasoning-panel';

        // Header with source and confidence
        const headerH = document.createElement('h4');
        headerH.textContent = 'AI Reasoning ';
        const srcBadge = document.createElement('span');
        srcBadge.className = 'source-badge ' + (data.source || 'deterministic');
        srcBadge.textContent = data.source || 'deterministic';
        headerH.appendChild(srcBadge);
        panel.appendChild(headerH);

        // Confidence
        const confP = document.createElement('p');
        confP.style.marginBottom = '0.5rem';
        const confLabel = document.createElement('strong');
        confLabel.textContent = 'Confidence: ';
        confP.appendChild(confLabel);
        const confBadge = document.createElement('span');
        const conf = data.confidence || 'low';
        confBadge.className = 'confidence-badge ' + conf;
        confBadge.textContent = conf;
        confP.appendChild(confBadge);
        panel.appendChild(confP);

        // Probable location
        if (data.probable_location) {
            const locH = document.createElement('h4');
            locH.textContent = 'Probable Location';
            panel.appendChild(locH);

            const locP = document.createElement('p');
            locP.style.cssText = 'font-family:"JetBrains Mono",monospace;font-size:0.8125rem;color:var(--text-muted)';
            locP.textContent = data.probable_location;
            panel.appendChild(locP);
        }

        // Reasoning
        if (data.reasoning) {
            const resH = document.createElement('h4');
            resH.textContent = 'Analysis';
            panel.appendChild(resH);

            const resP = document.createElement('p');
            resP.style.cssText = 'font-size:0.875rem;color:var(--text-muted);line-height:1.6;white-space:pre-wrap';
            resP.textContent = data.reasoning;
            panel.appendChild(resP);
        }

        // Search recommendations
        const recs = data.search_recommendations || [];
        if (recs.length > 0) {
            const recH = document.createElement('h4');
            recH.textContent = 'Search Recommendations';
            panel.appendChild(recH);

            const recList = document.createElement('ul');
            recList.className = 'search-rec-list';
            recs.forEach(r => {
                const li = document.createElement('li');
                li.textContent = r;
                recList.appendChild(li);
            });
            panel.appendChild(recList);
        }

        container.appendChild(panel);
    } catch (e) {
        clearChildren(container);
        const p = document.createElement('p');
        p.style.color = 'var(--text-dim)';
        p.textContent = 'AI reasoning unavailable';
        container.appendChild(p);
    }
}

// --- Theme Toggle ---

function initThemeToggle() {
    const btn = document.getElementById('theme-toggle');
    if (!btn) return;
    const moon = document.getElementById('theme-icon-moon');
    const sun = document.getElementById('theme-icon-sun');

    function applyIcons(theme) {
        if (!moon || !sun) return;
        moon.style.display = theme === 'dark' ? 'none' : '';
        sun.style.display = theme === 'dark' ? '' : 'none';
    }

    applyIcons(document.documentElement.getAttribute('data-theme') || 'dark');

    btn.addEventListener('click', () => {
        const current = document.documentElement.getAttribute('data-theme');
        const next = current === 'dark' ? 'light' : 'dark';
        document.documentElement.setAttribute('data-theme', next);
        localStorage.setItem('jennmesh-theme', next);
        applyIcons(next);
    });

    window.matchMedia('(prefers-color-scheme: dark)')
        .addEventListener('change', (e) => {
            if (!localStorage.getItem('jennmesh-theme')) {
                const t = e.matches ? 'dark' : 'light';
                document.documentElement.setAttribute('data-theme', t);
                applyIcons(t);
            }
        });
}

// ── Toast Notification System ───────────────────────────────────

const TOAST_ICONS = {
    info: '\u{1F4E1}',      // satellite antenna
    success: '\u2705',       // check mark
    error: '\u274C',         // cross mark
};

const TOAST_ACTION_MAP = {
    radio_detected:     { type: 'info',    fmt: (e) => `New radio detected on ${e.details || 'USB'}` },
    erase_started:      { type: 'info',    fmt: (e) => `Erasing flash on ${_port(e)}...` },
    flash_started:      { type: 'info',    fmt: (e) => `Flashing firmware to ${_port(e)}...` },
    config_applied:     { type: 'info',    fmt: (e) => `Golden config applied to ${e.node_id || 'radio'}` },
    provision_complete: { type: 'success', fmt: (e) => `Radio ${e.node_id || ''} provisioned successfully` },
    provision_failed:   { type: 'error',   fmt: (e) => `Provisioning failed: ${e.details || 'unknown error'}` },
    edge_yield:         { type: 'info',    fmt: () => 'Yielding radio priority to JennEdge...' },
};

function _port(entry) {
    const m = (entry.details || '').match(/port=(\S+)/);
    return m ? m[1] : 'USB';
}

function showToast(message, type) {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = `toast toast--${type || 'info'}`;

    const icon = document.createElement('span');
    icon.className = 'toast-icon';
    icon.textContent = TOAST_ICONS[type] || TOAST_ICONS.info;
    toast.appendChild(icon);

    const body = document.createElement('span');
    body.className = 'toast-body';
    body.textContent = message;
    toast.appendChild(body);

    container.appendChild(toast);

    // Auto-dismiss after 10 seconds
    setTimeout(() => {
        toast.style.opacity = '0';
        setTimeout(() => toast.remove(), 400);
    }, 10000);
}

function updateProvisionBadge(activeCount) {
    const badge = document.getElementById('provision-badge');
    if (!badge) return;
    if (activeCount > 0) {
        badge.textContent = String(activeCount);
        badge.style.display = '';
    } else {
        badge.style.display = 'none';
    }
}

let _lastProvisionTimestamp = null;

async function checkProvisionEvents() {
    try {
        const resp = await fetch('/api/v1/provision/recent');
        if (!resp.ok) return;
        const data = await resp.json();

        updateProvisionBadge(data.active_count || 0);

        if (!data.entries || data.entries.length === 0) return;

        for (const entry of data.entries) {
            if (_lastProvisionTimestamp && entry.timestamp <= _lastProvisionTimestamp) break;
            const mapping = TOAST_ACTION_MAP[entry.action];
            if (mapping) {
                showToast(mapping.fmt(entry), mapping.type);
            }
        }
        _lastProvisionTimestamp = data.entries[0].timestamp;
    } catch { /* ignore — dashboard may be loading */ }
}
