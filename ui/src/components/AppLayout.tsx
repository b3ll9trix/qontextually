import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { useQuery } from "@tanstack/react-query";
import {
  LayoutDashboard,
  AlertTriangle,
  BookOpenCheck,
  Users,
  FileText,
  Network,
  HelpCircle,
} from "lucide-react";
import { useState } from "react";
import { api } from "@/lib/api";
import type { Stats, Health } from "@/lib/api-types";
import { ShortcutsModal } from "@/components/ShortcutsModal";
import { cn } from "@/lib/utils";

type NavItem = {
  to: "/" | "/conflicts" | "/vocabulary" | "/entities" | "/sources" | "/graph";
  label: string;
  icon: typeof LayoutDashboard;
  exact?: boolean;
  badge?: "conflicts_pending" | "vocabulary_pending_review";
};

const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard, exact: true },
  { to: "/conflicts", label: "Conflicts", icon: AlertTriangle, badge: "conflicts_pending" },
  { to: "/vocabulary", label: "Vocabulary", icon: BookOpenCheck, badge: "vocabulary_pending_review" },
  { to: "/entities", label: "Entities", icon: Users },
  { to: "/sources", label: "Sources", icon: FileText },
  { to: "/graph", label: "Graph", icon: Network },
];

export function AppLayout() {
  const [showShortcuts, setShowShortcuts] = useState(false);
  const { data: stats } = useQuery({
    queryKey: ["stats"],
    queryFn: () => api<Stats>("/stats"),
    refetchInterval: 30000,
  });
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => api<Health>("/health"),
    refetchInterval: 15000,
    retry: 1,
  });
  const router = useRouterState();
  const path = router.location.pathname;

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background text-foreground">
      <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-card/50">
        <div className="flex h-14 items-center border-b border-border px-4">
          <span className="text-lg font-semibold tracking-tight">
            Qontextually
          </span>
        </div>
        <nav className="flex-1 overflow-y-auto p-2">
          {NAV.map((item) => {
            const active = item.exact ? path === item.to : path.startsWith(item.to);
            const Icon = item.icon;
            const badgeCount =
              item.badge && stats ? (stats as any)[item.badge] : 0;
            return (
              <Link
                key={item.to}
                to={item.to}
                className={cn(
                  "mb-1 flex items-center justify-between rounded-md px-3 py-2 text-sm transition-colors",
                  active
                    ? "bg-accent text-accent-foreground"
                    : "text-muted-foreground hover:bg-accent/50 hover:text-foreground",
                )}
              >
                <span className="flex items-center gap-2">
                  <Icon className="h-4 w-4" />
                  {item.label}
                </span>
                {badgeCount > 0 && (
                  <span
                    className={cn(
                      "rounded-full px-2 py-0.5 text-xs font-mono",
                      item.badge === "conflicts_pending"
                        ? "bg-destructive/30 text-red-300"
                        : "bg-amber-500/30 text-amber-300",
                    )}
                  >
                    {badgeCount}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
        <div className="border-t border-border p-3">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span
              className={cn(
                "h-2 w-2 rounded-full",
                health?.status === "ok" ? "bg-emerald-500 shadow-[0_0_8px] shadow-emerald-500/60" : "bg-red-500",
              )}
            />
            <span>{health?.status === "ok" ? "Backend healthy" : "Backend unreachable"}</span>
          </div>
          {health?.db_path && (
            <div className="mt-1 truncate font-mono text-[10px] text-muted-foreground/70">
              {health.db_path}
            </div>
          )}
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-card/30 px-6">
          <div className="text-sm text-muted-foreground">
            Human-in-the-loop knowledge graph review
          </div>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <span className="font-mono">v0.1 · local</span>
            <button
              onClick={() => setShowShortcuts(true)}
              className="flex h-7 w-7 items-center justify-center rounded-md hover:bg-accent"
              aria-label="Shortcuts"
            >
              <HelpCircle className="h-4 w-4" />
            </button>
          </div>
        </header>
        <main className="flex-1 overflow-hidden">
          <Outlet />
        </main>
      </div>

      <ShortcutsModal open={showShortcuts} onOpenChange={setShowShortcuts} />
    </div>
  );
}
