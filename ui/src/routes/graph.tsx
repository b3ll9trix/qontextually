import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { lazy, Suspense } from "react";
import { api, entityColor, entityTypeBadge } from "@/lib/api";
import type { GraphData, EntitySummary, Paginated } from "@/lib/api-types";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const ForceGraph3D = lazy(() => import("react-force-graph-3d").then((m) => ({ default: m.default })));

type Search = { center?: string; depth?: number; max?: number };

export const Route = createFileRoute("/graph")({
  validateSearch: (s: Record<string, unknown>): Search => ({
    center: typeof s.center === "string" ? s.center : undefined,
    depth: typeof s.depth === "number" ? s.depth : (typeof s.depth === "string" ? Number(s.depth) : undefined),
    max: typeof s.max === "number" ? s.max : (typeof s.max === "string" ? Number(s.max) : undefined),
  }),
  component: GraphPage,
});

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

function GraphPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: "/graph" });
  const depth = search.depth ?? 2;
  const max = search.max ?? 300;
  const [containerSize, setContainerSize] = useState({ w: 800, h: 600 });
  const containerRef = useRef<HTMLDivElement>(null);
  const fgRef = useRef<any>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const ro = new ResizeObserver((entries) => {
      for (const e of entries) {
        setContainerSize({ w: e.contentRect.width, h: e.contentRect.height });
      }
    });
    ro.observe(containerRef.current);
    return () => ro.disconnect();
  }, []);

  const params = new URLSearchParams();
  if (search.center) params.set("center", search.center);
  params.set("depth", String(depth));
  params.set("max_nodes", String(max));

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["graph", search.center, depth, max],
    queryFn: () => api<GraphData>(`/graph/subgraph?${params}`),
  });

  const graphData = useMemo(() => {
    if (!data) return { nodes: [], links: [] };
    return {
      nodes: data.nodes.map((n) => ({
        id: n.id,
        name: n.name,
        type: n.type,
        degree: n.degree,
        is_center: n.is_center,
        val: clamp(n.degree, 4, 20),
        color: entityColor(n.type),
      })),
      links: data.edges.map((e) => ({
        source: e.source,
        target: e.target,
        predicate: e.predicate,
        source_count: e.source_count,
        width: clamp(e.source_count, 1, 5),
      })),
    };
  }, [data]);

  // Configure bloom + soft physics once the graph mounts/data changes
  useEffect(() => {
    const fg = fgRef.current;
    if (!fg || !data || data.nodes.length === 0) return;
    let cancelled = false;
    (async () => {
      try {
        const THREE = await import("three");
        const { UnrealBloomPass } = await import(
          // @ts-ignore - example module from three
          "three/examples/jsm/postprocessing/UnrealBloomPass.js"
        );
        if (cancelled) return;
        const bloom = new UnrealBloomPass(
          new THREE.Vector2(containerSize.w, containerSize.h),
          1.1, // strength
          0.85, // radius
          0.2, // threshold
        );
        // Avoid stacking multiple bloom passes on re-renders
        const composer = fg.postProcessingComposer?.();
        if (composer) {
          composer.passes = composer.passes.filter(
            (p: any) => !(p instanceof UnrealBloomPass),
          );
          composer.addPass(bloom);
        }
        // Softer forces for a more serene layout
        fg.d3Force?.("charge")?.strength?.(-180);
        fg.d3Force?.("link")?.distance?.(70);
        fg.d3VelocityDecay?.(0.5);
      } catch {
        /* postprocessing optional */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [data, containerSize.w, containerSize.h]);

  const [selectedNode, setSelectedNode] = useState<any>(null);

  // Search box
  const [q, setQ] = useState("");
  const [showSuggest, setShowSuggest] = useState(false);
  const suggestQuery = useQuery({
    enabled: q.length >= 2,
    queryKey: ["graph-search", q],
    queryFn: () => api<Paginated<EntitySummary>>(`/entities?q=${encodeURIComponent(q)}&limit=10&offset=0`),
  });

  return (
    <div className="relative h-full w-full overflow-hidden bg-[#0b1220]">
      <div ref={containerRef} className="absolute inset-0">
        {error && (
          <div className="p-6"><ErrorBanner error={error} onRetry={() => refetch()} /></div>
        )}
        {isLoading && !error && (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Loading graph...
          </div>
        )}
        {!isLoading && !error && data && data.nodes.length === 0 && (
          <div className="flex h-full items-center justify-center">
            <Card className="max-w-md p-6 text-center">
              <div className="text-lg font-semibold">Graph is empty</div>
              <div className="mt-2 text-sm text-muted-foreground">
                The graph is empty or the backend is unreachable. Check the health indicator in the sidebar.
              </div>
            </Card>
          </div>
        )}
        {data && data.nodes.length > 0 && (
          <Suspense fallback={<Skeleton className="m-6 h-96" />}>
            <ForceGraph3D
              ref={fgRef}
              width={containerSize.w}
              height={containerSize.h}
              graphData={graphData}
              backgroundColor="#070b16"
              nodeLabel={(n: any) => `<div style="font-family:sans-serif;padding:4px 6px"><b>${n.name}</b><br/><span style="opacity:.7">${n.type} · degree ${n.degree}</span></div>`}
              nodeColor={(n: any) => n.color}
              nodeVal={(n: any) => n.val}
              nodeOpacity={0.92}
              nodeResolution={24}
              linkColor={() => "rgba(186,205,234,0.22)"}
              linkWidth={(l: any) => l.width * 0.6}
              linkOpacity={0.55}
              linkCurvature={0.18}
              linkDirectionalParticles={(l: any) => Math.min(4, l.source_count)}
              linkDirectionalParticleWidth={(l: any) => Math.min(2.5, 0.8 + l.source_count * 0.25)}
              linkDirectionalParticleSpeed={() => 0.0035}
              linkDirectionalParticleColor={() => "rgba(199,222,255,0.85)"}
              linkLabel={(l: any) => `<div style="font-family:sans-serif;padding:4px 6px"><b>${l.predicate}</b><br/><span style="opacity:.7">${l.source_count} sources</span></div>`}
              cooldownTicks={120}
              warmupTicks={40}
              enableNodeDrag={true}
              onNodeClick={(n: any) => setSelectedNode(n)}
            />
          </Suspense>
        )}
      </div>

      {/* Serene ambient overlay — soft radial vignettes for depth */}
      <div
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 80% 60% at 20% 15%, rgba(125,211,192,0.10), transparent 60%), radial-gradient(ellipse 70% 55% at 85% 90%, rgba(142,197,255,0.10), transparent 60%), radial-gradient(ellipse 60% 50% at 50% 50%, transparent 40%, rgba(7,11,22,0.6) 100%)",
        }}
      />

      {/* Controls */}
      <Card className="absolute right-4 top-4 w-72 space-y-3 p-4">
        <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Graph controls</div>
        <div className="relative">
          <Input
            placeholder="Center on entity..."
            value={q}
            onChange={(e) => { setQ(e.target.value); setShowSuggest(true); }}
            onFocus={() => setShowSuggest(true)}
          />
          {showSuggest && suggestQuery.data && suggestQuery.data.items.length > 0 && (
            <div className="absolute left-0 right-0 top-full z-20 mt-1 max-h-64 overflow-auto rounded-md border border-border bg-popover shadow-lg">
              {suggestQuery.data.items.map((e) => (
                <button
                  key={e.id}
                  onClick={() => {
                    navigate({ search: (p: Search) => ({ ...p, center: e.id }) });
                    setQ(e.name);
                    setShowSuggest(false);
                  }}
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-sm hover:bg-accent"
                >
                  <span className="truncate">{e.name}</span>
                  <span className={cn("ml-2 rounded border px-1.5 py-0.5 text-[10px]", entityTypeBadge(e.type))}>{e.type}</span>
                </button>
              ))}
            </div>
          )}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">Depth</div>
            <Select value={String(depth)} onValueChange={(v) => navigate({ search: (p: Search) => ({ ...p, depth: Number(v) }) })}>
              <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="1">1</SelectItem>
                <SelectItem value="2">2</SelectItem>
                <SelectItem value="3">3</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div>
            <div className="mb-1 text-[10px] uppercase tracking-wider text-muted-foreground">Max nodes</div>
            <Select value={String(max)} onValueChange={(v) => navigate({ search: (p: Search) => ({ ...p, max: Number(v) }) })}>
              <SelectTrigger className="h-8"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="100">100</SelectItem>
                <SelectItem value="300">300</SelectItem>
                <SelectItem value="500">500</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <Button variant="outline" size="sm" className="w-full" onClick={() => fgRef.current?.zoomToFit?.(800, 60)}>
          Reset camera
        </Button>
        {data?.meta && (
          <div className="border-t border-border pt-2 text-[10px] text-muted-foreground">
            Showing {data.meta.sampled_nodes} of {data.meta.total_nodes_in_graph.toLocaleString()} nodes · {data.meta.sampled_edges} edges
          </div>
        )}
      </Card>

      {/* Node panel */}
      {selectedNode && (
        <Card className="absolute bottom-4 right-4 w-80 p-4">
          <div className="flex items-start justify-between">
            <div>
              <div className="text-lg font-semibold">{selectedNode.name}</div>
              <div className="mt-1 flex items-center gap-2">
                <span className={cn("rounded border px-1.5 py-0.5 text-[10px]", entityTypeBadge(selectedNode.type))}>{selectedNode.type}</span>
                <span className="font-mono text-[10px] text-muted-foreground">{selectedNode.id}</span>
              </div>
              <div className="mt-1 text-xs text-muted-foreground">degree {selectedNode.degree}</div>
            </div>
            <button onClick={() => setSelectedNode(null)} className="text-muted-foreground hover:text-foreground">×</button>
          </div>
          <div className="mt-3 flex gap-2">
            <Button size="sm" onClick={() => navigate({ search: (p: Search) => ({ ...p, center: selectedNode.id }) })}>
              Re-center here
            </Button>
            <Button size="sm" variant="outline" onClick={() => navigate({ to: "/entities", search: { id: selectedNode.id } })}>
              Inspect
            </Button>
          </div>
        </Card>
      )}
    </div>
  );
}
