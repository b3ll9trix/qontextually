import { createFileRoute, Link } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { api, ENTITY_TYPE_HEX } from "@/lib/api";
import type { Stats } from "@/lib/api-types";
import { Card } from "@/components/ui/card";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertTriangle, BookOpenCheck, ArrowRight, Database, Network, FileText, Tag, Users } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell } from "recharts";
import { formatDistanceToNow } from "date-fns";

export const Route = createFileRoute("/")({
  component: Dashboard,
});

function Counter({ icon: Icon, label, value, sub, ts }: any) {
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <div className="text-xs uppercase tracking-wider text-muted-foreground">{label}</div>
        <Icon className="h-4 w-4 text-muted-foreground" />
      </div>
      <div className="mt-2 font-mono text-3xl font-semibold tabular-nums">
        {value?.toLocaleString() ?? "—"}
      </div>
      {sub && <div className="mt-1 text-xs text-muted-foreground">{sub}</div>}
      {ts && <div className="mt-3 text-[10px] text-muted-foreground/70">Last extraction: {ts}</div>}
    </Card>
  );
}

function Dashboard() {
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["stats"],
    queryFn: () => api<Stats>("/stats"),
    refetchInterval: 30000,
  });

  if (error) return <div className="p-6"><ErrorBanner error={error} onRetry={() => refetch()} /></div>;

  const lastExtraction = data?.last_extraction_at
    ? formatDistanceToNow(new Date(data.last_extraction_at), { addSuffix: true })
    : null;

  const chartData = (data?.entities_by_type ?? []).slice(0, 12);

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-7xl space-y-6 p-6">
        <div>
          <h1 className="text-2xl font-semibold">Dashboard</h1>
          <p className="text-sm text-muted-foreground">Overview of the knowledge graph and review queue.</p>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
          {isLoading ? (
            <>
              {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-32" />)}
            </>
          ) : (
            <>
              <Counter icon={Users} label="Entities" value={data?.entities_total} sub={`${data?.entities_by_type?.length ?? 0} types`} ts={lastExtraction} />
              <Counter icon={Network} label="Triples" value={data?.triples_total} sub={`${data?.avg_sources_per_triple?.toFixed(2)} avg sources`} ts={lastExtraction} />
              <Counter icon={FileText} label="Sources" value={data?.sources_total} sub="enterprise documents" ts={lastExtraction} />
              <Counter
                icon={Tag}
                label="Predicates"
                value={data?.predicates_total}
                sub={`${data?.predicates_seeded} seeded · ${data?.predicates_auto_canonical} auto · ${data?.predicates_merged} merged`}
                ts={lastExtraction}
              />
            </>
          )}
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          <Link to="/conflicts">
            <Card className={`group p-6 transition-all hover:border-red-500/60 ${(data?.conflicts_pending ?? 0) > 0 ? "border-red-500/40 bg-red-500/5" : ""}`}>
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className={`flex h-10 w-10 items-center justify-center rounded-md ${(data?.conflicts_pending ?? 0) > 0 ? "bg-red-500/20 text-red-300" : "bg-emerald-500/20 text-emerald-300"}`}>
                    <AlertTriangle className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="font-semibold">Pending Conflicts</div>
                    <div className="text-sm text-muted-foreground">
                      {(data?.conflicts_pending ?? 0) > 0
                        ? `${data?.conflicts_pending} conflicts need your review`
                        : "All clear — system is caught up"}
                    </div>
                  </div>
                </div>
                <ArrowRight className="h-5 w-5 text-muted-foreground transition-transform group-hover:translate-x-1" />
              </div>
            </Card>
          </Link>

          <Link to="/vocabulary">
            <Card className="group border-amber-500/40 bg-amber-500/5 p-6 transition-all hover:border-amber-500/60">
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-md bg-amber-500/20 text-amber-300">
                    <BookOpenCheck className="h-5 w-5" />
                  </div>
                  <div>
                    <div className="font-semibold">Discovered Vocabulary</div>
                    <div className="text-sm text-muted-foreground">
                      {data?.vocabulary_pending_review ?? 0} predicates awaiting promotion
                    </div>
                  </div>
                </div>
                <ArrowRight className="h-5 w-5 text-muted-foreground transition-transform group-hover:translate-x-1" />
              </div>
            </Card>
          </Link>
        </div>

        <Card className="p-6">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <div className="font-semibold">Entities by type</div>
              <div className="text-xs text-muted-foreground">Distribution across the canonical type vocabulary</div>
            </div>
          </div>
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={chartData} layout="vertical" margin={{ left: 30 }}>
                <XAxis type="number" stroke="#94a3b8" fontSize={11} />
                <YAxis type="category" dataKey="type" stroke="#94a3b8" fontSize={11} width={100} />
                <Tooltip
                  contentStyle={{ background: "#1e293b", border: "1px solid #334155", borderRadius: 6, fontSize: 12 }}
                  cursor={{ fill: "rgba(148,163,184,0.1)" }}
                />
                <Bar dataKey="count" radius={[0, 4, 4, 0]}>
                  {chartData.map((d, i) => (
                    <Cell key={i} fill={ENTITY_TYPE_HEX[d.type] || "#64748b"} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>
        </Card>

        <Card className="p-6">
          <div className="flex items-center gap-4">
            <div className="flex h-12 w-12 items-center justify-center rounded-md bg-primary/20 text-primary">
              <Database className="h-6 w-6" />
            </div>
            <div className="flex-1">
              <div className="font-mono text-2xl font-semibold">
                {data?.avg_sources_per_triple?.toFixed(2) ?? "—"}
              </div>
              <div className="text-sm text-muted-foreground">
                Average sources per triple — the graph is densely provenanced; most facts have more than one supporting source.
              </div>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}

