/**
 * JennMesh Topology Visualization — D3.js Force-Directed Graph
 *
 * Fetches topology data from GET /api/v1/topology and renders
 * an interactive force-directed graph with:
 *  - Color-coded nodes (online=teal, offline=red)
 *  - SPOF pulsing ring highlight
 *  - Edge thickness proportional to SNR
 *  - Click-to-inspect sidebar
 *  - Drag to reposition, scroll to zoom
 */
(function () {
    "use strict";

    const API_URL = "/api/v1/topology";
    const SVG_ID = "#topology-svg";

    // ── State ───────────────────────────────────────
    let simulation = null;
    let selectedNode = null;
    let topoData = null;

    // ── Init ────────────────────────────────────────
    document.addEventListener("DOMContentLoaded", () => {
        fetchAndRender();
        document.getElementById("btn-refresh")
            .addEventListener("click", fetchAndRender);
    });

    async function fetchAndRender() {
        try {
            const resp = await fetch(API_URL);
            if (!resp.ok) throw new Error("HTTP " + resp.status);
            topoData = await resp.json();
            updateStats(topoData);
            renderGraph(topoData);
        } catch (err) {
            console.error("Topology fetch failed:", err);
        }
    }

    // ── Stats Bar ───────────────────────────────────
    function updateStats(data) {
        setText("stat-nodes", data.total_nodes);
        setText("stat-edges", data.total_edges);
        setText("stat-components", data.connected_components);
        var spofList = data.single_points_of_failure || [];
        setText("stat-spof", spofList.length);
    }

    /** Safe text setter — uses textContent, never innerHTML */
    function setText(id, value) {
        var el = document.getElementById(id);
        if (el) el.textContent = String(value);
    }

    // ── D3 Graph ────────────────────────────────────
    function renderGraph(data) {
        var svg = d3.select(SVG_ID);
        svg.selectAll("*").remove();

        var container = svg.node().parentElement;
        var width = container.clientWidth;
        var height = container.clientHeight - 80; // account for toolbar + stats
        svg.attr("width", width).attr("height", height);

        var spofSet = new Set(data.single_points_of_failure || []);

        // Build node map and D3 data
        var nodeMap = new Map();
        var nodes = data.nodes.map(function (n) {
            var d = {
                id: n.node_id,
                label: n.display_name || n.node_id,
                role: n.role,
                online: n.is_online,
                isolated: n.is_isolated,
                spof: spofSet.has(n.node_id),
                lat: n.latitude,
                lon: n.longitude,
                neighborCount: n.neighbor_count,
            };
            nodeMap.set(n.node_id, d);
            return d;
        });

        var links = data.edges
            .filter(function (e) {
                return nodeMap.has(e.from_node) && nodeMap.has(e.to_node);
            })
            .map(function (e) {
                return {
                    source: e.from_node,
                    target: e.to_node,
                    snr: e.snr,
                    rssi: e.rssi,
                };
            });

        // Zoom behavior
        var g = svg.append("g");
        svg.call(
            d3.zoom()
                .scaleExtent([0.2, 4])
                .on("zoom", function (event) {
                    g.attr("transform", event.transform);
                })
        );

        // Edge thickness: map SNR to width (higher SNR = thicker)
        var snrExtent = d3.extent(links, function (l) { return l.snr || 0; });
        var edgeWidth = d3.scaleLinear()
            .domain([snrExtent[0] || -20, snrExtent[1] || 15])
            .range([1, 5])
            .clamp(true);

        // Links
        var link = g.append("g")
            .selectAll("line")
            .data(links)
            .join("line")
            .attr("class", function (d) {
                var snr = d.snr || 0;
                if (snr >= 5) return "edge-line strong";
                if (snr <= -5) return "edge-line weak";
                return "edge-line";
            })
            .attr("stroke-width", function (d) {
                return edgeWidth(d.snr || 0);
            });

        // Nodes
        var node = g.append("g")
            .selectAll("circle")
            .data(nodes)
            .join("circle")
            .attr("class", function (d) {
                var cls = "node-circle";
                cls += d.online ? " online" : " offline";
                if (d.spof) cls += " spof";
                return cls;
            })
            .attr("r", function (d) { return d.spof ? 12 : 10; })
            .on("click", function (event, d) { selectNode(d, data); })
            .call(d3.drag()
                .on("start", dragStart)
                .on("drag", dragging)
                .on("end", dragEnd)
            );

        // Labels
        var label = g.append("g")
            .selectAll("text")
            .data(nodes)
            .join("text")
            .attr("class", "node-label")
            .attr("dy", -16)
            .text(function (d) { return d.label; });

        // Simulation
        simulation = d3.forceSimulation(nodes)
            .force("link", d3.forceLink(links).id(function (d) { return d.id; }).distance(120))
            .force("charge", d3.forceManyBody().strength(-300))
            .force("center", d3.forceCenter(width / 2, height / 2))
            .force("collision", d3.forceCollide(20))
            .on("tick", function () {
                link
                    .attr("x1", function (d) { return d.source.x; })
                    .attr("y1", function (d) { return d.source.y; })
                    .attr("x2", function (d) { return d.target.x; })
                    .attr("y2", function (d) { return d.target.y; });
                node
                    .attr("cx", function (d) { return d.x; })
                    .attr("cy", function (d) { return d.y; });
                label
                    .attr("x", function (d) { return d.x; })
                    .attr("y", function (d) { return d.y; });
            });
    }

    // ── Drag Handlers ───────────────────────────────
    function dragStart(event, d) {
        if (!event.active) simulation.alphaTarget(0.3).restart();
        d.fx = d.x;
        d.fy = d.y;
    }
    function dragging(event, d) {
        d.fx = event.x;
        d.fy = event.y;
    }
    function dragEnd(event, d) {
        if (!event.active) simulation.alphaTarget(0);
        d.fx = null;
        d.fy = null;
    }

    // ── Node Selection ──────────────────────────────
    function selectNode(d, data) {
        selectedNode = d;

        // Update visual selection
        d3.selectAll(".node-circle").classed("selected", false);
        d3.selectAll(".node-circle")
            .filter(function (n) { return n.id === d.id; })
            .classed("selected", true);

        // Sidebar
        document.getElementById("sidebar-placeholder").style.display = "none";
        var content = document.getElementById("sidebar-content");
        content.style.display = "block";

        setText("detail-name", d.label);
        setText("detail-id", d.id);
        setText("detail-role", d.role || "unknown");
        setText("detail-status", d.online ? "Online" : "Offline");
        setText("detail-neighbors", d.neighborCount != null ? d.neighborCount : "\u2014");
        setText("detail-position",
            d.lat && d.lon
                ? d.lat.toFixed(4) + ", " + d.lon.toFixed(4)
                : "Unknown"
        );

        // Edge list — build safely using DOM methods
        var edgeList = document.getElementById("detail-edges");
        while (edgeList.firstChild) {
            edgeList.removeChild(edgeList.firstChild);
        }
        var nodeEdges = (data.edges || []).filter(function (e) {
            return e.from_node === d.id || e.to_node === d.id;
        });
        if (nodeEdges.length === 0) {
            var emptyLi = document.createElement("li");
            emptyLi.textContent = "No connections";
            edgeList.appendChild(emptyLi);
        } else {
            nodeEdges.forEach(function (e) {
                var peer = e.from_node === d.id ? e.to_node : e.from_node;
                var li = document.createElement("li");

                var peerText = document.createTextNode(peer + " ");
                li.appendChild(peerText);

                var snrSpan = document.createElement("span");
                snrSpan.className = "snr-value";
                snrSpan.textContent = "SNR:" + (e.snr || 0).toFixed(1);
                li.appendChild(snrSpan);

                li.appendChild(document.createTextNode(" "));

                var rssiSpan = document.createElement("span");
                rssiSpan.className = "rssi-value";
                rssiSpan.textContent = "RSSI:" + (e.rssi || 0).toFixed(0);
                li.appendChild(rssiSpan);

                edgeList.appendChild(li);
            });
        }
    }
})();
