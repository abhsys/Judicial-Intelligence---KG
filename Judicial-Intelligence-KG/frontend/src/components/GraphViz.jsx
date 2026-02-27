import React, { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";

const TEAL_SCALE = ["#8bb5b6", "#78aeb0", "#5a999e", "#498b8f", "#2e7278", "#245c61"];

const pickType = (n) => n?.labels?.[0] || (n?.properties?.case_key ? "Case" : "Node");

const shorten = (value, max = 56) => {
  const text = String(value || "").trim();
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
};

const hostFromUrl = (url) => {
  try {
    return new URL(url).host;
  } catch (_) {
    return "";
  }
};

const pickTitle = (n) => {
  const p = n?.properties || {};
  return (
    p.case_key ||
    p.title ||
    p.value ||
    p.name ||
    p.filename ||
    p.order_key ||
    (p.result_url ? hostFromUrl(p.result_url) || shorten(p.result_url, 42) : "") ||
    (p.external_case_id ? `ext:${String(p.external_case_id).slice(0, 10)}` : "") ||
    (p.upload_id ? `upload:${String(p.upload_id).slice(0, 8)}` : "") ||
    `node:${String(n?.id || "").split(":").pop() || "unknown"}`
  );
};

const clusterColor = (cluster, type) => {
  if (cluster !== undefined && cluster !== null) {
    const idx =
      typeof cluster === "number"
        ? Math.abs(cluster)
        : Math.abs(Array.from(String(cluster)).reduce((a, c) => a + c.charCodeAt(0), 0));
    return TEAL_SCALE[idx % TEAL_SCALE.length];
  }
  if (type === "Case") return "#2e7278";
  if (type === "Court") return "#4d9499";
  if (type === "Party") return "#6aa8aa";
  if (type === "Order") return "#3f8086";
  return "#5a999e";
};

export default function GraphViz({ data, height = 520, onNodeSelect, backgroundColor = "#eceff1" }) {
  const fgRef = useRef();
  const [hoverNode, setHoverNode] = useState(null);
  const [selectedNode, setSelectedNode] = useState(null);

  const graph = useMemo(() => {
    const degreeMap = new Map();
    (data?.edges || []).forEach((e) => {
      degreeMap.set(e.source, (degreeMap.get(e.source) || 0) + 1);
      degreeMap.set(e.target, (degreeMap.get(e.target) || 0) + 1);
    });

    const nodes = (data?.nodes || []).map((n) => {
      const degree = degreeMap.get(n.id) || 0;
      const score = typeof n.score === "number" ? n.score : degree;
      return {
        ...n,
        type: pickType(n),
        title: pickTitle(n),
        degree,
        val: Math.max(2.5, Math.min(16, 2.6 + score * 0.55)),
      };
    });

    const links = (data?.edges || []).map((e) => ({
      ...e,
      weight: Math.max(0.1, e.weight ?? 1),
    }));

    return { nodes, links };
  }, [data]);

  useEffect(() => {
    if (!fgRef.current) return;
    const nodeCount = graph?.nodes?.length || 0;
    const chargeStrength = nodeCount > 240 ? -520 : nodeCount > 140 ? -420 : -320;
    const baseDistance = nodeCount > 240 ? 240 : nodeCount > 140 ? 210 : 175;
    fgRef.current.d3Force("charge")?.strength(chargeStrength);
    fgRef.current
      .d3Force("link")
      ?.distance((l) => Math.max(95, baseDistance - (l.weight || 1) * 11));
    fgRef.current.zoomToFit(700, 80);
  }, [graph]);

  const focused = selectedNode || hoverNode;

  return (
    <div style={{ width: "100%", height, borderRadius: 12, overflow: "hidden" }}>
      <ForceGraph2D
        ref={fgRef}
        graphData={graph}
        height={height}
        backgroundColor={backgroundColor}
        nodeRelSize={1}
        minZoom={0.18}
        maxZoom={6}
        cooldownTicks={340}
        onEngineStop={() => fgRef.current?.zoomToFit(500, 70)}
        onNodeHover={(n) => setHoverNode(n || null)}
        onNodeClick={(n) => {
          const node = n || {};
          setSelectedNode(node);
          if (onNodeSelect) onNodeSelect(node);
          fgRef.current?.centerAt(node.x || 0, node.y || 0, 700);
          fgRef.current?.zoom(2.2, 700);
        }}
        linkWidth={(l) => Math.max(0.4, (l.weight || 1) * 0.95)}
        linkColor={(l) => {
          if (!focused) return "rgba(120,130,140,0.32)";
          const s = typeof l.source === "object" ? l.source.id : l.source;
          const t = typeof l.target === "object" ? l.target.id : l.target;
          const hot = s === focused.id || t === focused.id;
          return hot ? "rgba(70,80,95,0.65)" : "rgba(130,140,150,0.1)";
        }}
        nodeCanvasObject={(nodeObj, ctx, globalScale) => {
          const node = nodeObj;
          const isFocused = focused?.id === node.id;
          const radius = isFocused ? node.val + 3 : node.val;
          const color = clusterColor(node.cluster, node.type);
          const x = node.x || 0;
          const y = node.y || 0;

          if (isFocused) {
            ctx.beginPath();
            ctx.arc(x, y, radius + 4, 0, 2 * Math.PI, false);
            ctx.fillStyle = "rgba(134, 49, 123, 0.32)";
            ctx.fill();
          }

          ctx.beginPath();
          ctx.arc(x, y, radius, 0, 2 * Math.PI, false);
          ctx.fillStyle = color;
          ctx.fill();

          ctx.lineWidth = isFocused ? 2.2 : 1;
          ctx.strokeStyle = "rgba(255,255,255,0.92)";
          ctx.stroke();

          const showLabel = isFocused || globalScale >= 2.2;
          if (showLabel) {
            const fontSize = Math.max(10, 13 / globalScale);
            ctx.font = `${fontSize}px "Segoe UI", sans-serif`;
            ctx.fillStyle = isFocused ? "#6f2a65" : "#2f3133";
            ctx.fillText(shorten(node.title, 58), x + radius + 2, y + 3);
          }
        }}
        nodePointerAreaPaint={(nodeObj, color, ctx) => {
          const node = nodeObj;
          ctx.fillStyle = color;
          ctx.beginPath();
          ctx.arc(node.x || 0, node.y || 0, (node.val || 7) + 6, 0, 2 * Math.PI, false);
          ctx.fill();
        }}
      />
    </div>
  );
}
