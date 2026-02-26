import React, { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D from "react-force-graph-2d";

const CLUSTER_COLORS = [
  "#1f77b4",
  "#ff7f0e",
  "#2ca02c",
  "#d62728",
  "#9467bd",
  "#8c564b",
  "#17becf",
  "#bcbd22",
];

const pickType = (n) => n?.labels?.[0] || (n?.properties?.case_key ? "Case" : "Node");

const pickTitle = (n) =>
  n?.properties?.case_key || n?.properties?.name || n?.properties?.order_key || String(n?.id);

const clusterColor = (cluster, type) => {
  if (cluster !== undefined && cluster !== null) {
    const idx =
      typeof cluster === "number"
        ? Math.abs(cluster)
        : Math.abs(Array.from(String(cluster)).reduce((a, c) => a + c.charCodeAt(0), 0));
    return CLUSTER_COLORS[idx % CLUSTER_COLORS.length];
  }
  if (type === "Case") return "#1f4b99";
  if (type === "Court") return "#1d8348";
  if (type === "Party") return "#b9770e";
  if (type === "Order") return "#7d3c98";
  return "#5f6d8a";
};

export default function GraphViz({ data, height = 520, onNodeSelect, backgroundColor = '#f6f8fc' }) {
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
        val: Math.max(4, Math.min(22, 4 + score * 0.8)),
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
    fgRef.current.d3Force("charge")?.strength(-240);
    fgRef.current.d3Force("link")?.distance((l) => Math.max(55, 160 - (l.weight || 1) * 18));
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
        cooldownTicks={180}
        onNodeHover={(n) => setHoverNode(n || null)}
        onNodeClick={(n) => {
          const node = n || {};
          setSelectedNode(node);
          if (onNodeSelect) onNodeSelect(node);
          fgRef.current?.centerAt(node.x || 0, node.y || 0, 700);
          fgRef.current?.zoom(2.2, 700);
        }}
        linkWidth={(l) => Math.max(0.5, (l.weight || 1) * 1.2)}
        linkColor={(l) => {
          if (!focused) return "rgba(120,140,180,0.35)";
          const s = typeof l.source === "object" ? l.source.id : l.source;
          const t = typeof l.target === "object" ? l.target.id : l.target;
          const hot = s === focused.id || t === focused.id;
          return hot ? "rgba(70,95,150,0.9)" : "rgba(120,140,180,0.12)";
        }}
        nodeCanvasObject={(nodeObj, ctx, globalScale) => {
          const node = nodeObj;
          const isFocused = focused?.id === node.id;
          const radius = isFocused ? node.val + 2 : node.val;
          const color = clusterColor(node.cluster, node.type);
          const x = node.x || 0;
          const y = node.y || 0;

          ctx.beginPath();
          ctx.arc(x, y, radius, 0, 2 * Math.PI, false);
          ctx.fillStyle = color;
          ctx.fill();

          ctx.lineWidth = isFocused ? 2 : 1;
          ctx.strokeStyle = "#ffffff";
          ctx.stroke();

          const showLabel = isFocused || globalScale >= 2.4;
          if (showLabel) {
            const fontSize = Math.max(10, 13 / globalScale);
            ctx.font = `${fontSize}px sans-serif`;
            ctx.fillStyle = "#1b2a49";
            ctx.fillText(node.title, x + radius + 2, y + 3);
          }
        }}
      />
    </div>
  );
}
