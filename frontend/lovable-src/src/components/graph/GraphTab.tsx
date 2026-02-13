import { useEffect, useRef, useState, useCallback } from "react";
import type { SearchResponse, PaperResult } from "@/types/api";
import { Network } from "vis-network";
import { DataSet } from "vis-data";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/shared/EmptyState";
import { Maximize2, RotateCcw } from "lucide-react";

interface GraphTabProps {
  data: SearchResponse | null;
  hasQuery: boolean;
}

const NODE_COLORS: Record<string, string> = {
  query: "#6366f1",
  paper: "#3b82f6",
  author: "#f59e0b",
  topic: "#10b981",
};

const NODE_SHAPES: Record<string, string> = {
  query: "diamond",
  paper: "dot",
  author: "triangle",
  topic: "square",
};

function buildGraph(results: PaperResult[], query: string) {
  const nodes = new DataSet<{ id: string; label: string; color: string; shape: string; group: string }>([]);
  const edges = new DataSet<{ id?: string; from: string; to: string; color?: string }>([]);

  const qId = "query__root";
  nodes.add({ id: qId, label: query, color: NODE_COLORS.query, shape: NODE_SHAPES.query, group: "query" });

  for (const p of results) {
    const pId = `paper__${p.paper_id}`;
    if (!nodes.get(pId)) {
      nodes.add({ id: pId, label: p.title.slice(0, 40), color: NODE_COLORS.paper, shape: NODE_SHAPES.paper, group: "paper" });
    }
    edges.add({ from: qId, to: pId });

    for (const a of p.authors) {
      const aId = `author__${a}`;
      if (!nodes.get(aId)) {
        nodes.add({ id: aId, label: a, color: NODE_COLORS.author, shape: NODE_SHAPES.author, group: "author" });
      }
      edges.add({ from: pId, to: aId });
    }

    for (const t of p.topics) {
      const tId = `topic__${t}`;
      if (!nodes.get(tId)) {
        nodes.add({ id: tId, label: t, color: NODE_COLORS.topic, shape: NODE_SHAPES.topic, group: "topic" });
      }
      edges.add({ from: pId, to: tId });
    }
  }

  return { nodes, edges };
}

export function GraphTab({ data, hasQuery }: GraphTabProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);
  const [selected, setSelected] = useState<{ id: string; label: string; group: string } | null>(null);
  const [visibleGroups, setVisibleGroups] = useState<Record<string, boolean>>({
    query: true, paper: true, author: true, topic: true,
  });

  const initNetwork = useCallback(() => {
    if (!containerRef.current || !data?.results.length) return;
    const { nodes, edges } = buildGraph(data.results, "query");

    const net = new Network(containerRef.current, { nodes, edges }, {
      physics: { stabilization: { iterations: 100 }, barnesHut: { gravitationalConstant: -3000 } },
      interaction: { hover: true, tooltipDelay: 200 },
      nodes: { font: { size: 11, color: "#94a3b8", face: "Inter" }, borderWidth: 2, borderWidthSelected: 3 },
      edges: { color: { color: "#334155", highlight: "#6366f1", hover: "#475569" }, width: 1, smooth: { enabled: true, type: "continuous", roundness: 0.5 } },
    });

    net.on("click", (params) => {
      if (params.nodes.length > 0) {
        const nodeId = params.nodes[0] as string;
        const node = nodes.get(nodeId);
        if (node) setSelected({ id: nodeId, label: node.label, group: node.group });
      } else {
        setSelected(null);
      }
    });

    networkRef.current = net;
    setTimeout(() => net.fit(), 200);
  }, [data]);

  useEffect(() => {
    initNetwork();
    return () => { networkRef.current?.destroy(); };
  }, [initNetwork]);

  if (!hasQuery || !data?.results.length)
    return <EmptyState message="Search for a topic to visualize the knowledge graph." />;

  return (
    <div className="flex h-[600px] gap-4">
      <div className="flex flex-1 flex-col gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          {Object.entries(NODE_COLORS).map(([group, color]) => (
            <button
              key={group}
              onClick={() => setVisibleGroups(v => ({ ...v, [group]: !v[group] }))}
              className={`flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[10px] font-medium uppercase tracking-wide border transition-all duration-200 ${
                visibleGroups[group] ? "opacity-100 bg-card" : "opacity-30"
              }`}
            >
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
              {group}
            </button>
          ))}
          <div className="ml-auto flex gap-1">
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => networkRef.current?.fit()}>
              <Maximize2 className="h-3.5 w-3.5" />
            </Button>
            <Button variant="ghost" size="icon" className="h-7 w-7" onClick={initNetwork}>
              <RotateCcw className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
        <div ref={containerRef} className="flex-1 rounded-xl border bg-card/30 glow-border" />
      </div>

      {selected && (
        <div className="w-64 shrink-0 card-elevated p-4 space-y-3 overflow-auto">
          <p className="section-label">{selected.group}</p>
          <p className="text-sm font-semibold">{selected.label}</p>
        </div>
      )}
    </div>
  );
}
