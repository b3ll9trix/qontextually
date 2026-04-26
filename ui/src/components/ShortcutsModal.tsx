import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";

export function ShortcutsModal({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Keyboard shortcuts</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 text-sm">
          <div>
            <div className="mb-2 font-medium">Conflict Queue</div>
            <ul className="space-y-1 text-muted-foreground">
              <li><kbd className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">A</kbd> — Accept Candidate A</li>
              <li><kbd className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">B</kbd> — Accept Candidate B</li>
              <li><kbd className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">N</kbd> — Neither</li>
            </ul>
          </div>
          <div>
            <div className="mb-2 font-medium">Vocabulary</div>
            <ul className="space-y-1 text-muted-foreground">
              <li><kbd className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">M</kbd> — Merge into nearest canonical</li>
              <li><kbd className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">P</kbd> — Promote as canonical</li>
              <li><kbd className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs">D</kbd> — Dismiss</li>
            </ul>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
