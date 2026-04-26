import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api, sourceTypeBadge, entityTypeBadge } from "@/lib/api";
import type { Conflict, ConflictCandidate, Paginated } from "@/lib/api-types";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorBanner } from "@/components/ErrorBanner";
import { CheckCircle2, ChevronRight, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";
import { formatDistanceToNow } from "date-fns";

export const Route = createFileRoute("/conflicts")({
  component: ConflictsPage,
});

function ScoreBars({ s }: { s: ConflictCandidate["score_breakdown"] }) {
  const Bar = ({ label, v }: { label: string; v: number }) => (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-20 text-muted-foreground">{label}</span>
      <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
        <div className="h-full bg-primary" style={{ width: `${v * 100}%` }} />
      </div>
      <span className="w-10 text-right font-mono tabular-nums">{v.toFixed(2)}</span>
    </div>
  );
  return (
    <div className="space-y-1.5">
      <Bar label="authority" v={s.authority} />
      <Bar label="confidence" v={s.confidence} />
      <Bar label="recency" v={s.recency} />
      <div className="mt-2 flex items-center justify-between border-t border-border pt-2 text-xs">
        <span className="font-medium">total</span>
        <span className="font-mono text-base font-semibold">{s.total.toFixed(2)}</span>
      </div>
    </div>
  );
}

function CandidatePanel({ label, color, c }: { label: string; color: string; c: ConflictCandidate }) {
  const [open, setOpen] = useState<Record<number, boolean>>({});
  return (
    <Card className={`flex flex-col gap-4 border-l-4 p-5 ${color}`}>
      <div className="flex items-center justify-between">
        <span className="text-xs uppercase tracking-wider text-muted-foreground">Candidate {label}</span>
        <span className="font-mono text-[10px] text-muted-foreground">triple #{c.triple_id}</span>
      </div>
      <div className="rounded-md bg-muted/40 p-4">
        <div className="text-xs text-muted-foreground">Value</div>
        <div className="mt-1 break-words text-lg font-semibold">{c.value}</div>
      </div>
      <ScoreBars s={c.score_breakdown} />
      <div>
        <div className="mb-2 text-xs font-medium text-muted-foreground">Sources ({c.sources.length})</div>
        <div className="space-y-2">
          {c.sources.map((s) => (
            <div key={s.source_id} className="rounded-md border border-border bg-background/40 p-3 text-xs">
              <div className="flex items-center justify-between gap-2">
                <span className={cn("rounded border px-2 py-0.5 text-[10px] font-medium uppercase", sourceTypeBadge(s.source_type))}>
                  {s.source_type}
                </span>
                <span className="text-muted-foreground">authority {s.authority.toFixed(2)}</span>
              </div>
              <div className="mt-1.5 truncate font-mono text-[11px] text-muted-foreground" title={s.document_path}>
                {s.document_path}
              </div>
              <div className="mt-1 text-[10px] text-muted-foreground">
                {formatDistanceToNow(new Date(s.extracted_at), { addSuffix: true })}
              </div>
              <button
                onClick={() => setOpen((o) => ({ ...o, [s.source_id]: !o[s.source_id] }))}
                className="mt-2 text-[11px] text-primary hover:underline"
              >
                {open[s.source_id] ? "hide snippet" : "view snippet"}
              </button>
              {open[s.source_id] && (
                <div className="mt-2 rounded bg-muted/30 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
                  {s.snippet}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function ConflictsPage() {
  const qc = useQueryClient();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["conflicts", "pending"],
    queryFn: () => api<Paginated<Conflict>>("/conflicts?status=pending&limit=50&offset=0"),
  });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [note, setNote] = useState("");

  const items = data?.items ?? [];
  const selected = items.find((c) => c.conflict_id === selectedId) ?? items[0];

  const resolve = useMutation({
    mutationFn: async ({ id, winner }: { id: number; winner: "a" | "b" | "neither" }) => {
      return api(`/conflicts/${id}/resolve`, {
        method: "POST",
        body: JSON.stringify({ winner, note: note || undefined }),
      });
    },
    onSuccess: () => {
      setNote("");
      const idx = items.findIndex((c) => c.conflict_id === selected?.conflict_id);
      const next = items[idx + 1] ?? items[0];
      setSelectedId(next?.conflict_id ?? null);
      qc.invalidateQueries({ queryKey: ["conflicts"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
    },
  });

  useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const k = e.key.toLowerCase();
      if (k === "a") resolve.mutate({ id: selected.conflict_id, winner: "a" });
      else if (k === "b") resolve.mutate({ id: selected.conflict_id, winner: "b" });
      else if (k === "n") resolve.mutate({ id: selected.conflict_id, winner: "neither" });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, resolve]);

  if (error) return <div className="p-6"><ErrorBanner error={error} onRetry={() => refetch()} /></div>;

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
      {/* List */}
      <div className="overflow-auto border-r border-border">
        <div className="sticky top-0 z-10 border-b border-border bg-background/80 px-6 py-4 backdrop-blur">
          <h1 className="text-xl font-semibold">Conflict Queue</h1>
          <p className="text-xs text-muted-foreground">
            {data?.total ?? 0} pending — functional predicates with conflicting values
          </p>
        </div>
        <div className="divide-y divide-border">
          {isLoading && Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="m-4 h-20" />)}
          {!isLoading && items.length === 0 && (
            <div className="flex h-[60vh] flex-col items-center justify-center text-center">
              <CheckCircle2 className="h-12 w-12 text-emerald-400" />
              <p className="mt-3 font-medium">No pending conflicts</p>
              <p className="text-sm text-muted-foreground">The system is caught up.</p>
            </div>
          )}
          {items.map((c) => {
            const isSel = c.conflict_id === selected?.conflict_id;
            return (
              <button
                key={c.conflict_id}
                onClick={() => setSelectedId(c.conflict_id)}
                className={cn(
                  "block w-full px-6 py-4 text-left transition-colors",
                  isSel ? "bg-accent" : "hover:bg-accent/40",
                )}
              >
                <div className="flex items-center justify-between">
                  <div className="font-medium">{c.subject_entity.name}</div>
                  <ChevronRight className="h-4 w-4 text-muted-foreground" />
                </div>
                <div className="mt-1 flex items-center gap-2 text-xs">
                  <span className={cn("rounded border px-1.5 py-0.5", entityTypeBadge(c.subject_entity.type))}>
                    {c.subject_entity.type}
                  </span>
                  <span className="font-mono text-muted-foreground">{c.predicate}</span>
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 text-xs">
                  <div className="truncate rounded bg-muted/40 px-2 py-1"><span className="text-muted-foreground">A: </span>{c.candidate_a.value}</div>
                  <div className="truncate rounded bg-muted/40 px-2 py-1"><span className="text-muted-foreground">B: </span>{c.candidate_b.value}</div>
                </div>
                <div className="mt-2 text-[10px] text-muted-foreground">
                  hint: <span className="uppercase">{c.auto_resolution_hint.winner}</span> · {formatDistanceToNow(new Date(c.created_at), { addSuffix: true })}
                </div>
              </button>
            );
          })}
        </div>
      </div>

      {/* Detail */}
      <div className="overflow-auto">
        {!selected && !isLoading && (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            Select a conflict to review.
          </div>
        )}
        {selected && (
          <div className="space-y-5 p-6">
            <Card className="p-5">
              <div className="text-xs uppercase tracking-wider text-muted-foreground">Subject</div>
              <div className="mt-1 flex items-center gap-3">
                <span className="text-2xl font-semibold">{selected.subject_entity.name}</span>
                <span className={cn("rounded border px-2 py-0.5 text-xs", entityTypeBadge(selected.subject_entity.type))}>
                  {selected.subject_entity.type}
                </span>
                <span className="font-mono text-xs text-muted-foreground">{selected.subject_entity.id}</span>
              </div>
              <div className="mt-2 text-sm text-muted-foreground">
                Predicate: <span className="font-mono text-foreground">{selected.predicate}</span>
              </div>
            </Card>

            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <CandidatePanel label="A" color="border-l-emerald-500" c={selected.candidate_a} />
              <CandidatePanel label="B" color="border-l-orange-500" c={selected.candidate_b} />
            </div>

            <Card className="border-primary/40 bg-primary/5 p-5">
              <div className="flex items-start gap-3">
                <Sparkles className="mt-0.5 h-5 w-5 text-primary" />
                <div className="flex-1 text-sm">
                  <div className="font-medium">
                    Scoring suggests <span className="uppercase">{selected.auto_resolution_hint.winner}</span> wins
                  </div>
                  <div className="mt-1 text-muted-foreground">{selected.auto_resolution_hint.reason}</div>
                </div>
              </div>
            </Card>

            <div className="space-y-3">
              <Input
                placeholder="Note (optional)"
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
              <div className="flex flex-wrap gap-2">
                <Button
                  className="bg-emerald-600 text-white hover:bg-emerald-500"
                  disabled={resolve.isPending}
                  onClick={() => resolve.mutate({ id: selected.conflict_id, winner: "a" })}
                >
                  Accept A <kbd className="ml-2 rounded bg-black/30 px-1 text-[10px]">A</kbd>
                </Button>
                <Button
                  className="bg-orange-600 text-white hover:bg-orange-500"
                  disabled={resolve.isPending}
                  onClick={() => resolve.mutate({ id: selected.conflict_id, winner: "b" })}
                >
                  Accept B <kbd className="ml-2 rounded bg-black/30 px-1 text-[10px]">B</kbd>
                </Button>
                <Button
                  variant="secondary"
                  disabled={resolve.isPending}
                  onClick={() => resolve.mutate({ id: selected.conflict_id, winner: "neither" })}
                >
                  Neither <kbd className="ml-2 rounded bg-black/30 px-1 text-[10px]">N</kbd>
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
