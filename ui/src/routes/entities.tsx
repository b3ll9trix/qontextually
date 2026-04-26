import { createFileRoute, Link, useNavigate } from "@tanstack/react-router";
import { useQuery, useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { api, entityTypeBadge, sourceTypeBadge } from "@/lib/api";
import type { EntityDetail, EntitySummary, Paginated, Provenance } from "@/lib/api-types";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { cn } from "@/lib/utils";
import { Network as NetIcon } from "lucide-react";

const TYPES = ["All", "Person", "Organization", "Project", "Ticket", "Policy", "Document", "Event", "Product", "Meeting", "Message"];

type Search = { q?: string; type?: string; id?: string };

export const Route = createFileRoute("/entities")({
  validateSearch: (s: Record<string, unknown>): Search => ({
    q: typeof s.q === "string" ? s.q : undefined,
    type: typeof s.type === "string" ? s.type : undefined,
    id: typeof s.id === "string" ? s.id : undefined,
  }),
  component: EntitiesPage,
});

function useDebounced<T>(v: T, ms: number) {
  const [d, setD] = useState(v);
  useEffect(() => {
    const t = setTimeout(() => setD(v), ms);
    return () => clearTimeout(t);
  }, [v, ms]);
  return d;
}

function EntitiesPage() {
  const search = Route.useSearch();
  const navigate = useNavigate({ from: "/entities" });
  const [q, setQ] = useState(search.q ?? "");
  const debouncedQ = useDebounced(q, 300);
  const type = search.type ?? "All";

  useEffect(() => {
    navigate({ search: (p: Search) => ({ ...p, q: debouncedQ || undefined }), replace: true });
  }, [debouncedQ]);

  const list = useInfiniteQuery({
    queryKey: ["entities", debouncedQ, type],
    initialPageParam: 0,
    queryFn: async ({ pageParam }) => {
      const params = new URLSearchParams();
      if (type !== "All") params.set("type", type);
      if (debouncedQ) params.set("q", debouncedQ);
      params.set("limit", "50");
      params.set("offset", String(pageParam));
      return api<Paginated<EntitySummary>>(`/entities?${params}`);
    },
    getNextPageParam: (last, all) => {
      const loaded = all.reduce((acc, p) => acc + p.items.length, 0);
      return loaded < last.total ? loaded : undefined;
    },
  });

  const items = useMemo(() => list.data?.pages.flatMap((p) => p.items) ?? [], [list.data]);

  const detail = useQuery({
    enabled: !!search.id,
    queryKey: ["entity", search.id],
    queryFn: () => api<EntityDetail>(`/entities/${search.id}`),
  });

  const [provTripleId, setProvTripleId] = useState<number | null>(null);
  const prov = useQuery({
    enabled: provTripleId !== null,
    queryKey: ["provenance", provTripleId],
    queryFn: () => api<Provenance>(`/triples/${provTripleId}/provenance`),
  });

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[340px_minmax(0,1fr)]">
      <div className="flex min-h-0 flex-col border-r border-border">
        <div className="space-y-3 border-b border-border p-4">
          <Input placeholder="Search entities..." value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="flex flex-wrap gap-1">
            {TYPES.map((t) => (
              <button
                key={t}
                onClick={() =>
                  navigate({ search: (p: Search) => ({ ...p, type: t === "All" ? undefined : t }) })
                }
                className={cn(
                  "rounded-full border px-2.5 py-0.5 text-[11px] transition-colors",
                  (t === "All" && type === "All") || t === type
                    ? "border-primary bg-primary/20 text-primary"
                    : "border-border text-muted-foreground hover:bg-accent",
                )}
              >
                {t}
              </button>
            ))}
          </div>
          <div className="text-[11px] text-muted-foreground">
            {list.data?.pages[0]?.total?.toLocaleString() ?? "—"} matching
          </div>
        </div>
        <div className="flex-1 overflow-auto">
          {list.isLoading && Array.from({ length: 10 }).map((_, i) => <Skeleton key={i} className="m-3 h-12" />)}
          {list.error && <div className="p-3"><ErrorBanner error={list.error} onRetry={() => list.refetch()} /></div>}
          {!list.isLoading && items.length === 0 && (
            <div className="p-6 text-center text-sm text-muted-foreground">No entities match these filters.</div>
          )}
          <ul className="divide-y divide-border">
            {items.map((e) => {
              const sel = e.id === search.id;
              const propPreview = Object.entries(e.properties || {}).slice(0, 2).map(([, v]) => String(v)).join(" · ");
              return (
                <li key={e.id}>
                  <button
                    onClick={() => navigate({ search: (p: Search) => ({ ...p, id: e.id }) })}
                    className={cn(
                      "block w-full px-4 py-3 text-left",
                      sel ? "bg-accent" : "hover:bg-accent/40",
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-medium">{e.name}</span>
                      <span className={cn("rounded border px-1.5 py-0.5 text-[10px]", entityTypeBadge(e.type))}>{e.type}</span>
                    </div>
                    {propPreview && <div className="mt-0.5 truncate text-xs text-muted-foreground">{propPreview}</div>}
                    <div className="mt-0.5 flex items-center justify-between text-[10px] text-muted-foreground">
                      <span className="font-mono truncate">{e.id}</span>
                      <span>{e.triple_count} triples</span>
                    </div>
                  </button>
                </li>
              );
            })}
          </ul>
          {list.hasNextPage && (
            <div className="p-3">
              <Button variant="outline" className="w-full" onClick={() => list.fetchNextPage()} disabled={list.isFetchingNextPage}>
                {list.isFetchingNextPage ? "Loading..." : "Load more"}
              </Button>
            </div>
          )}
        </div>
      </div>

      <div className="overflow-auto">
        {!search.id && (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Select an entity from the list.
          </div>
        )}
        {search.id && detail.isLoading && (
          <div className="space-y-4 p-6">
            <Skeleton className="h-12 w-1/2" />
            <Skeleton className="h-32" />
            <Skeleton className="h-64" />
          </div>
        )}
        {search.id && detail.error && <div className="p-6"><ErrorBanner error={detail.error} onRetry={() => detail.refetch()} /></div>}
        {detail.data && (
          <div className="space-y-5 p-6">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="flex items-center gap-3">
                  <h1 className="text-2xl font-semibold">{detail.data.name}</h1>
                  <span className={cn("rounded border px-2 py-0.5 text-xs", entityTypeBadge(detail.data.type))}>
                    {detail.data.type}
                  </span>
                </div>
                <div className="mt-1 font-mono text-xs text-muted-foreground">{detail.data.id}</div>
              </div>
              <Link to="/graph" search={{ center: detail.data.id }}>
                <Button variant="outline">
                  <NetIcon className="mr-1.5 h-4 w-4" /> Open in Graph View
                </Button>
              </Link>
            </div>

            {Object.keys(detail.data.properties || {}).length > 0 && (
              <Card className="p-4">
                <div className="mb-2 text-xs uppercase tracking-wider text-muted-foreground">Properties</div>
                <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
                  {Object.entries(detail.data.properties).map(([k, v]) => (
                    <div key={k} className="flex items-baseline gap-2">
                      <dt className="font-mono text-xs text-muted-foreground">{k}</dt>
                      <dd className="break-words">{String(v)}</dd>
                    </div>
                  ))}
                </dl>
              </Card>
            )}

            {detail.data.aliases.length > 0 && (
              <Card className="p-4">
                <div className="mb-2 text-xs uppercase tracking-wider text-muted-foreground">Aliases</div>
                <div className="flex flex-wrap gap-1.5">
                  {detail.data.aliases.map((a, i) => (
                    <span key={i} className="rounded-full border border-border bg-muted/40 px-2.5 py-0.5 text-xs">
                      <span className="text-muted-foreground">{a.alias_type}:</span> <span className="font-mono">{a.alias}</span>
                      {a.is_primary && <span className="ml-0.5 text-amber-400">*</span>}
                    </span>
                  ))}
                </div>
              </Card>
            )}

            <Tabs defaultValue="out">
              <TabsList>
                <TabsTrigger value="out">Outgoing ({detail.data.outgoing_triples.length})</TabsTrigger>
                <TabsTrigger value="in">Incoming ({detail.data.incoming_triples.length})</TabsTrigger>
              </TabsList>
              <TabsContent value="out">
                <ul className="divide-y divide-border rounded-md border border-border">
                  {detail.data.outgoing_triples.map((t) => (
                    <li key={t.triple_id}>
                      <button
                        onClick={() => setProvTripleId(t.triple_id)}
                        className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm hover:bg-accent/40"
                      >
                        <div className="flex flex-1 items-center gap-2 truncate">
                          <span className="font-mono text-primary">{t.predicate}</span>
                          <span className="text-muted-foreground">→</span>
                          <span className="truncate">{t.object_is_entity ? t.object_name : t.object_value}</span>
                        </div>
                        <span className="ml-2 font-mono text-[11px] text-muted-foreground">
                          {t.source_count} src · #{t.triple_id}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              </TabsContent>
              <TabsContent value="in">
                <ul className="divide-y divide-border rounded-md border border-border">
                  {detail.data.incoming_triples.map((t) => (
                    <li key={t.triple_id}>
                      <button
                        onClick={() => setProvTripleId(t.triple_id)}
                        className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm hover:bg-accent/40"
                      >
                        <div className="flex flex-1 items-center gap-2 truncate">
                          <span className="truncate">{t.subject_name}</span>
                          <span className="text-muted-foreground">—</span>
                          <span className="font-mono text-primary">{t.predicate}</span>
                        </div>
                        <span className="ml-2 font-mono text-[11px] text-muted-foreground">#{t.triple_id}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              </TabsContent>
            </Tabs>
          </div>
        )}
      </div>

      <Sheet open={provTripleId !== null} onOpenChange={(o) => !o && setProvTripleId(null)}>
        <SheetContent className="w-full overflow-auto sm:max-w-2xl">
          <SheetHeader>
            <SheetTitle>Provenance · triple #{provTripleId}</SheetTitle>
          </SheetHeader>
          {prov.isLoading && <Skeleton className="mt-4 h-48" />}
          {prov.error && <ErrorBanner error={prov.error} />}
          {prov.data && (
            <div className="mt-4 space-y-4">
              <Card className="p-4 text-sm">
                <div className="font-mono">
                  <span>{prov.data.subject.name}</span>
                  <span className="mx-2 text-muted-foreground">—</span>
                  <span className="text-primary">{prov.data.predicate}</span>
                  <span className="mx-2 text-muted-foreground">→</span>
                  <span>{prov.data.object_is_entity ? prov.data.object.name : (prov.data.object as any).value ?? prov.data.object.name}</span>
                </div>
              </Card>
              <div className="text-xs uppercase tracking-wider text-muted-foreground">Sources ({prov.data.sources.length})</div>
              <div className="space-y-3">
                {prov.data.sources.map((s) => (
                  <Card key={s.source_id} className="p-4 text-xs">
                    <div className="flex items-center justify-between">
                      <span className={cn("rounded border px-2 py-0.5 text-[10px] uppercase", sourceTypeBadge(s.source_type))}>{s.source_type}</span>
                      <span className="font-mono text-muted-foreground">#{s.source_id} · auth {s.authority.toFixed(2)} · conf {s.confidence.toFixed(2)}</span>
                    </div>
                    <div className="mt-2 truncate font-mono text-[11px] text-muted-foreground" title={s.document_path}>
                      {s.document_path}
                    </div>
                    <div className="mt-2 rounded bg-muted/30 p-2 font-mono text-[11px] leading-relaxed">
                      {s.snippet_around_fact}
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}
