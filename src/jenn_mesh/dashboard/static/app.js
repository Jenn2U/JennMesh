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
            if (tab === 'sync') loadSyncData();
            if (tab === 'failover') loadFailoverData();
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
            p.style.color = 'var(--danger)';
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
        p.style.color = 'var(--danger)';
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
                depLabel.style.cssText = 'font-size:0.75rem;color:var(--text-muted);margin-bottom:0.25rem';
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
            p.style.color = 'var(--danger)';
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
        p.style.color = 'var(--danger)';
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
