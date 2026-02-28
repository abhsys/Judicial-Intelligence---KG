// frontend/src/components/GraphViz.tsx
import React, { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { ForceGraphMethods } from "react-force-graph-2d";

type RawNode = {
  id: string;
  labels?: string[];
  properties?: Record<string, any>;
  cluster?: number | string;
  score?: number;
};

type RawEdge = {
  id: string;
  source: string;
  target: string;
  type?: string;
  weight?: number;
};

type GraphData = {
  nodes: RawNode[];
  edges: RawEdge[];
};

type VizNode = RawNode & {
  title: string;
  type: string;
  val: number;
  degree: number;
};

type VizEdge = RawEdge & {
  source: string | VizNode;
  target: string | VizNode;
  weight: number;
};

type Props = {
  data: GraphData;
  height?: number;
  onNodeSelect?: (node: RawNode) => void;
};

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

const pickType = (n: RawNode) =>
  n.labels?.[0] || (n.properties?.case_key ? "Case" : "Node");

const pickTitle = (n: RawNode) =>
  n.properties?.case_key ||
  n.properties?.name ||
  n.properties?.order_key ||
  String(n.id);

const clusterColor = (cluster: number | string | undefined, type: string) => {
  if (cluster !== undefined && cluster !== null) {
    const idx =
      typeof cluster === "number"
        ? Math.abs(cluster)
        : Math.abs(
            Array.from(String(cluster)).reduce((a, c) => a + c.charCodeAt(0), 0)
          );
    return CLUSTER_COLORS[idx % CLUSTER_COLORS.length];
  }
  if (type === "Case") return "#1f4b99";
  if (type === "Court") return "#1d8348";
  if (type === "Party") return "#b9770e";
  if (type === "Order") return "#7d3c98";
  return "#5f6d8a";
};

export default function GraphViz({ data, height = 520, onNodeSelect }: Props) {
  const fgRef = useRef<ForceGraphMethods | undefined>(undefined);
  const [hoverNode, setHoverNode] = useState<VizNode | null>(null);
  const [selectedNode, setSelectedNode] = useState<VizNode | null>(null);

  const graph = useMemo(() => {
    const degreeMap = new Map<string, number>();
    data.edges.forEach((e) => {
      degreeMap.set(e.source, (degreeMap.get(e.source) || 0) + 1);
      degreeMap.set(e.target, (degreeMap.get(e.target) || 0) + 1);
    });

    const nodes: VizNode[] = data.nodes.map((n) => {
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

    const edges: VizEdge[] = data.edges.map((e) => ({
      ...e,
      weight: Math.max(0.1, e.weight ?? 1),
    }));

    return { nodes, links: edges };
  }, [data]);

  useEffect(() => {
    if (!fgRef.current) return;
    fgRef.current.d3Force("charge")?.strength(-120);
    fgRef.current.d3Force("link")?.distance((l: any) =>
      Math.max(35, 120 - (l.weight || 1) * 18)
    );
  }, [graph]);

  const focused = selectedNode || hoverNode;

  return (
    <div style={{ width: "100%", height, borderRadius: 12, overflow: "hidden" }}>
      <ForceGraph2D
        ref={fgRef}
        graphData={graph}
        width={undefined}
        height={height}
        backgroundColor="#f6f8fc"
        nodeRelSize={1}
        cooldownTicks={120}
        onNodeHover={(n) => setHoverNode((n as VizNode) || null)}
        onNodeClick={(n) => {
          const node = n as VizNode;
          setSelectedNode(node);
          onNodeSelect?.(node);
          fgRef.current?.centerAt(node.x || 0, node.y || 0, 700);
          fgRef.current?.zoom(2.2, 700);
        }}
        linkWidth={(l: any) => Math.max(0.4, (l.weight || 1) * 1.2)}
        linkColor={(l: any) => {
          if (!focused) return "rgba(120,140,180,0.35)";
          const s = typeof l.source === "object" ? l.source.id : l.source;
          const t = typeof l.target === "object" ? l.target.id : l.target;
          const hot = s === focused.id || t === focused.id;
          return hot ? "rgba(70,95,150,0.85)" : "rgba(120,140,180,0.12)";
        }}
        nodeCanvasObject={(nodeObj, ctx, globalScale) => {
          const node = nodeObj as VizNode;
          const isFocused = focused?.id === node.id;
          const radius = isFocused ? node.val + 2 : node.val;
          const color = clusterColor(node.cluster, node.type);

          ctx.beginPath();
          ctx.arc(node.x || 0, node.y || 0, radius, 0, 2 * Math.PI, false);
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
            ctx.fillText(node.title, (node.x || 0) + radius + 2, (node.y || 0) + 3);
          }
        }}
      />
    </div>
  );
}
