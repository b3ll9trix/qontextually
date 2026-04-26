const BASE = (import.meta.env.VITE_API_URL as string) || "http://localhost:8000";

export const apiBase = BASE;

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text || path}`);
  }
  return res.json() as Promise<T>;
}

export const SOURCE_TYPE_COLORS: Record<string, string> = {
  hr: "bg-blue-500/20 text-blue-300 border-blue-500/40",
  crm: "bg-indigo-500/20 text-indigo-300 border-indigo-500/40",
  policy: "bg-violet-500/20 text-violet-300 border-violet-500/40",
  ticket: "bg-orange-500/20 text-orange-300 border-orange-500/40",
  email: "bg-slate-500/20 text-slate-300 border-slate-500/40",
  chat: "bg-gray-500/20 text-gray-300 border-gray-500/40",
  unknown: "bg-neutral-500/20 text-neutral-300 border-neutral-500/40",
};

export const ENTITY_TYPE_COLORS: Record<string, string> = {
  Person: "bg-emerald-500/20 text-emerald-300 border-emerald-500/40",
  Organization: "bg-sky-500/20 text-sky-300 border-sky-500/40",
  Project: "bg-amber-500/20 text-amber-300 border-amber-500/40",
  Policy: "bg-violet-500/20 text-violet-300 border-violet-500/40",
  Ticket: "bg-orange-500/20 text-orange-300 border-orange-500/40",
};

export const ENTITY_TYPE_HEX: Record<string, string> = {
  Person: "#7dd3c0",
  Organization: "#8ec5ff",
  Project: "#f5d488",
  Policy: "#c4b5fd",
  Ticket: "#fbbf95",
  Document: "#cbd5e1",
  Event: "#f9a8d4",
  Product: "#a5f3fc",
  Meeting: "#c7b8ff",
  Message: "#94a3b8",
};

export function entityColor(type: string): string {
  return ENTITY_TYPE_HEX[type] || "#9ca3af";
}

export function sourceTypeBadge(t: string): string {
  return SOURCE_TYPE_COLORS[t] || SOURCE_TYPE_COLORS.unknown;
}

export function entityTypeBadge(t: string): string {
  return ENTITY_TYPE_COLORS[t] || "bg-gray-500/20 text-gray-300 border-gray-500/40";
}
