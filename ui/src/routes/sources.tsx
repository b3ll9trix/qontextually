import { createFileRoute } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api, sourceTypeBadge, entityTypeBadge } from "@/lib/api";
import type { Paginated, SourceDetail, SourceSummary } from "@/lib/api-types";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { format } from "date-fns";

const SOURCE_TYPES = ["all", "hr", "crm", "policy", "ticket", "email", "chat", "unknown"];

export const Route = createFileRoute("/sources")({
  component: SourcesPage,
});

function SourcesPage() {
  const [type, setType] = useState("all");
  const [offset, setOffset] = useState(0);
  const limit = 50;
  const [openId, setOpenId] = useState<number | null>(null);

  const params = new URLSearchParams();
  if (type !== "all") params.set("source_type", type);
  params.set("limit", String(limit));
  params.set("offset", String(offset));

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["sources", type, offset],
    queryFn: () => api<Paginated<SourceSummary>>(`/sources?${params}`),
  });

  const detail = useQuery({
    enabled: openId !== null,
    queryKey: ["source", openId],
    queryFn: () => api<SourceDetail>(`/sources/${openId}`),
  });

  return (
    <div className="h-full overflow-auto">
      <div className="mx-auto max-w-7xl space-y-4 p-6">
        <div className="flex items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold">Sources</h1>
            <p className="text-sm text-muted-foreground">{data?.total?.toLocaleString() ?? "—"} documents</p>
          </div>
          <div className="flex items-center gap-2">
            <Select value={type} onValueChange={(v) => { setType(v); setOffset(0); }}>
              <SelectTrigger className="w-40"><SelectValue /></SelectTrigger>
              <SelectContent>
                {SOURCE_TYPES.map((t) => <SelectItem key={t} value={t}>{t}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </div>

        {error && <ErrorBanner error={error} onRetry={() => refetch()} />}

        <Card className="overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-xs uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="px-4 py-2.5 text-left">Type</th>
                <th className="px-4 py-2.5 text-left">Document</th>
                <th className="px-4 py-2.5 text-left">Extracted</th>
                <th className="px-4 py-2.5 text-right">Triples</th>
                <th className="px-4 py-2.5 text-right">Entities</th>
                <th className="px-4 py-2.5"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {isLoading && Array.from({ length: 10 }).map((_, i) => (
                <tr key={i}><td colSpan={6} className="p-3"><Skeleton className="h-6" /></td></tr>
              ))}
              {data?.items.map((s) => (
                <tr key={s.source_id} className="hover:bg-accent/40">
                  <td className="px-4 py-2">
                    <span className={cn("rounded border px-2 py-0.5 text-[10px] uppercase", sourceTypeBadge(s.source_type))}>
                      {s.source_type}
                    </span>
                  </td>
                  <td className="max-w-xl px-4 py-2">
                    <div className="truncate font-mono text-xs" title={s.document_path}>{s.document_path}</div>
                  </td>
                  <td className="px-4 py-2 text-xs text-muted-foreground">
                    {format(new Date(s.extracted_at), "MMM d, HH:mm")}
                  </td>
                  <td className="px-4 py-2 text-right font-mono tabular-nums">{s.triple_count}</td>
                  <td className="px-4 py-2 text-right font-mono tabular-nums">{s.entity_count}</td>
                  <td className="px-4 py-2 text-right">
                    <Button size="sm" variant="ghost" onClick={() => setOpenId(s.source_id)}>View</Button>
                  </td>
                </tr>
              ))}
              {!isLoading && data?.items.length === 0 && (
                <tr><td colSpan={6} className="p-8 text-center text-sm text-muted-foreground">No sources match these filters.</td></tr>
              )}
            </tbody>
          </table>
        </Card>

        <div className="flex items-center justify-between text-sm">
          <div className="text-muted-foreground">
            {offset + 1}–{Math.min(offset + limit, data?.total ?? 0)} of {data?.total?.toLocaleString() ?? "—"}
          </div>
          <div className="flex gap-2">
            <Button variant="outline" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - limit))}>Previous</Button>
            <Button variant="outline" disabled={offset + limit >= (data?.total ?? 0)} onClick={() => setOffset(offset + limit)}>Next</Button>
          </div>
        </div>
      </div>

      <Dialog open={openId !== null} onOpenChange={(o) => !o && setOpenId(null)}>
        <DialogContent className="max-h-[85vh] max-w-5xl overflow-hidden">
          <DialogHeader>
            <DialogTitle className="font-mono text-sm">Source #{openId}</DialogTitle>
          </DialogHeader>
          {detail.isLoading && <Skeleton className="h-96" />}
          {detail.error && <ErrorBanner error={detail.error} />}
          {detail.data && (
            <div className="grid max-h-[70vh] grid-cols-1 gap-4 overflow-hidden md:grid-cols-[1.4fr_1fr]">
              <div className="flex min-h-0 flex-col">
                <div className="mb-2 flex items-center gap-2 text-xs">
                  <span className={cn("rounded border px-2 py-0.5 uppercase", sourceTypeBadge(detail.data.source_type))}>
                    {detail.data.source_type}
                  </span>
                  <span className="text-muted-foreground">authority {detail.data.authority.toFixed(2)}</span>
                </div>
                <div className="mb-2 truncate font-mono text-xs text-muted-foreground" title={detail.data.document_path}>
                  {detail.data.document_path}
                </div>
                <pre className="flex-1 overflow-auto whitespace-pre-wrap rounded-md bg-muted/40 p-3 font-mono text-xs leading-relaxed">
                  {detail.data.raw_text}
                </pre>
              </div>
              <div className="min-h-0 space-y-4 overflow-auto">
                <div>
                  <div className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                    Contributed entities ({detail.data.contributed_entities.length})
                  </div>
                  <ul className="space-y-1">
                    {detail.data.contributed_entities.map((e) => (
                      <li key={e.id}>
                        <a href={`/entities?id=${e.id}`} className="flex items-center justify-between rounded px-2 py-1 text-sm hover:bg-accent/40">
                          <span className="truncate">{e.name}</span>
                          <span className={cn("rounded border px-1.5 py-0.5 text-[10px]", entityTypeBadge(e.type))}>{e.type}</span>
                        </a>
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <div className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                    Contributed triples ({detail.data.contributed_triples.length})
                  </div>
                  <ul className="space-y-1 font-mono text-xs">
                    {detail.data.contributed_triples.map((t) => (
                      <li key={t.triple_id} className="rounded bg-muted/30 px-2 py-1">
                        <span>{t.subject_name}</span>
                        <span className="mx-1.5 text-primary">{t.predicate}</span>
                        <span>{t.object_display}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
}
