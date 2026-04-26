import { createFileRoute } from "@tanstack/react-router";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { DiscoveredPredicate, Paginated } from "@/lib/api-types";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorBanner } from "@/components/ErrorBanner";
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter } from "@/components/ui/dialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Textarea } from "@/components/ui/textarea";
import { Info, Sparkles } from "lucide-react";
import { cn } from "@/lib/utils";

export const Route = createFileRoute("/vocabulary")({
  component: VocabularyPage,
});

function similarityDot(c: number) {
  if (c >= 0.9) return "bg-emerald-500";
  if (c >= 0.85) return "bg-amber-500";
  return "bg-gray-500";
}

function VocabularyPage() {
  const qc = useQueryClient();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["vocabulary", "discovered"],
    queryFn: () => api<Paginated<DiscoveredPredicate>>("/vocabulary/discovered?limit=200&offset=0&min_occurrences=1"),
  });

  const items = (data?.items ?? []).slice().sort((a, b) => b.occurrence_count - a.occurrence_count);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const selected = items.find((p) => p.predicate === selectedName) ?? items[0];

  const [mergeTarget, setMergeTarget] = useState("");
  const [promoteOpen, setPromoteOpen] = useState(false);
  const [promoteFunctional, setPromoteFunctional] = useState(false);
  const [promoteDesc, setPromoteDesc] = useState("");

  useEffect(() => {
    if (selected) setMergeTarget(selected.nearest_canonical?.predicate ?? "");
  }, [selected?.predicate]);

  const advance = () => {
    const idx = items.findIndex((p) => p.predicate === selected?.predicate);
    const next = items[idx + 1];
    setSelectedName(next?.predicate ?? null);
  };

  const merge = useMutation({
    mutationFn: () =>
      api(`/vocabulary/${encodeURIComponent(selected!.predicate)}/merge`, {
        method: "POST",
        body: JSON.stringify({ into: mergeTarget }),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["vocabulary"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      advance();
    },
  });

  const promote = useMutation({
    mutationFn: () =>
      api(`/vocabulary/${encodeURIComponent(selected!.predicate)}/promote`, {
        method: "POST",
        body: JSON.stringify({ is_functional: promoteFunctional, description: promoteDesc || undefined }),
      }),
    onSuccess: () => {
      setPromoteOpen(false);
      setPromoteFunctional(false);
      setPromoteDesc("");
      qc.invalidateQueries({ queryKey: ["vocabulary"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      advance();
    },
  });

  const dismiss = useMutation({
    mutationFn: () =>
      api(`/vocabulary/${encodeURIComponent(selected!.predicate)}/dismiss`, {
        method: "POST",
        body: JSON.stringify({}),
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["vocabulary"] });
      qc.invalidateQueries({ queryKey: ["stats"] });
      advance();
    },
  });

  useEffect(() => {
    if (!selected) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const k = e.key.toLowerCase();
      if (k === "m" && mergeTarget) merge.mutate();
      else if (k === "p") setPromoteOpen(true);
      else if (k === "d") dismiss.mutate();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [selected, mergeTarget, merge, dismiss]);

  if (error) return <div className="p-6"><ErrorBanner error={error} onRetry={() => refetch()} /></div>;

  return (
    <div className="grid h-full grid-cols-1 lg:grid-cols-[420px_minmax(0,1fr)]">
      <div className="flex min-h-0 flex-col border-r border-border">
        <div className="border-b border-border px-6 py-4">
          <h1 className="text-xl font-semibold">Discovered Vocabulary</h1>
          <p className="text-xs text-muted-foreground">{data?.total ?? 0} awaiting review</p>
        </div>
        <div className="border-b border-border bg-amber-500/5 px-6 py-3 text-xs text-muted-foreground">
          <div className="flex items-start gap-2">
            <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
            <span>
              These predicates scored between 0.75 and 0.95 cosine similarity to an existing canonical predicate. Our system treats this band as ambiguous: lexical variants (works_at ≈ works_for) usually cluster ≥ 0.95, but polarity-flipped pairs (seeks_advice vs offers_advice) can also score here. Your call is the final signal.
            </span>
          </div>
        </div>
        <div className="flex-1 overflow-auto">
          {isLoading && Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="m-3 h-14" />)}
          {!isLoading && items.length === 0 && (
            <div className="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">
              No predicates awaiting review.
            </div>
          )}
          <ul className="divide-y divide-border">
            {items.map((p) => {
              const isSel = p.predicate === selected?.predicate;
              return (
                <li key={p.predicate}>
                  <button
                    onClick={() => setSelectedName(p.predicate)}
                    className={cn(
                      "block w-full px-6 py-3 text-left",
                      isSel ? "bg-accent" : "hover:bg-accent/40",
                    )}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-sm">{p.predicate}</span>
                      <span className="font-mono text-xs text-muted-foreground tabular-nums">
                        {p.occurrence_count.toLocaleString()}
                      </span>
                    </div>
                    {p.nearest_canonical && (
                      <div className="mt-1 flex items-center gap-2 text-xs text-muted-foreground">
                        <span className={cn("h-2 w-2 rounded-full", similarityDot(p.nearest_canonical.cosine))} />
                        <span className="font-mono">{p.nearest_canonical.predicate}</span>
                        <span className="font-mono tabular-nums">{p.nearest_canonical.cosine.toFixed(2)}</span>
                      </div>
                    )}
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      </div>

      <div className="overflow-auto">
        {!selected && !isLoading && (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">Select a predicate</div>
        )}
        {selected && (
          <div className="space-y-5 p-6">
            <div>
              <div className="text-xs uppercase tracking-wider text-muted-foreground">Predicate</div>
              <div className="mt-1 font-mono text-3xl font-semibold">{selected.predicate}</div>
              <div className="mt-2 text-sm text-muted-foreground">
                Used <span className="font-mono">{selected.occurrence_count.toLocaleString()}</span> times · {selected.description}
              </div>
            </div>

            {selected.nearest_canonical && (
              <Card className="border-primary/30 bg-primary/5 p-5">
                <div className="flex items-center gap-2 text-xs text-muted-foreground">
                  <Sparkles className="h-3.5 w-3.5" /> Nearest canonical match
                </div>
                <div className="mt-2 flex items-baseline gap-3">
                  <span className="font-mono text-xl font-semibold">{selected.nearest_canonical.predicate}</span>
                  <span className="font-mono text-sm text-muted-foreground">cosine {selected.nearest_canonical.cosine.toFixed(3)}</span>
                  <span className="text-xs text-muted-foreground">{selected.nearest_canonical.occurrence_count.toLocaleString()} uses</span>
                </div>
                <div className="mt-2 text-xs text-muted-foreground">
                  Similarity is above review threshold but below auto-merge. Your call.
                </div>
              </Card>
            )}

            <Card className="p-5">
              <div className="mb-3 text-sm font-medium">Sample triples</div>
              <ul className="space-y-2 font-mono text-sm">
                {selected.sample_triples.slice(0, 5).map((t, i) => (
                  <li key={i} className="rounded bg-muted/40 px-3 py-2">
                    <span>{t.subject_name}</span>
                    <span className="mx-2 text-muted-foreground">—</span>
                    <span className="text-primary">{selected.predicate}</span>
                    <span className="mx-2 text-muted-foreground">→</span>
                    <span>{t.object}</span>
                  </li>
                ))}
              </ul>
            </Card>

            <Card className="space-y-3 p-5">
              <div className="text-sm font-medium">Resolve</div>
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  className="max-w-xs font-mono"
                  placeholder="merge target predicate"
                  value={mergeTarget}
                  onChange={(e) => setMergeTarget(e.target.value)}
                />
                <Button
                  disabled={!mergeTarget || merge.isPending}
                  onClick={() => merge.mutate()}
                >
                  Merge into canonical <kbd className="ml-2 rounded bg-black/30 px-1 text-[10px]">M</kbd>
                </Button>
                <Button variant="secondary" onClick={() => setPromoteOpen(true)}>
                  Promote as canonical <kbd className="ml-2 rounded bg-black/30 px-1 text-[10px]">P</kbd>
                </Button>
                <Button variant="ghost" onClick={() => dismiss.mutate()} disabled={dismiss.isPending}>
                  Dismiss <kbd className="ml-2 rounded bg-black/30 px-1 text-[10px]">D</kbd>
                </Button>
              </div>
            </Card>
          </div>
        )}
      </div>

      <Dialog open={promoteOpen} onOpenChange={setPromoteOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Promote <span className="font-mono">{selected?.predicate}</span> as canonical</DialogTitle>
          </DialogHeader>
          <div className="space-y-4">
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={promoteFunctional} onCheckedChange={(v) => setPromoteFunctional(!!v)} />
              Mark as functional (one value per subject)
            </label>
            <Textarea
              placeholder="Description (optional)"
              value={promoteDesc}
              onChange={(e) => setPromoteDesc(e.target.value)}
            />
          </div>
          <DialogFooter>
            <Button variant="ghost" onClick={() => setPromoteOpen(false)}>Cancel</Button>
            <Button onClick={() => promote.mutate()} disabled={promote.isPending}>Promote</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
