import { AlertCircle } from "lucide-react";
import { Button } from "@/components/ui/button";

export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="flex items-center gap-3 rounded-md border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm">
      <AlertCircle className="h-4 w-4 text-red-400" />
      <div className="flex-1">
        <div className="font-medium text-red-300">Request failed</div>
        <div className="font-mono text-xs text-red-300/80">{msg}</div>
      </div>
      {onRetry && (
        <Button size="sm" variant="outline" onClick={onRetry}>Retry</Button>
      )}
    </div>
  );
}
