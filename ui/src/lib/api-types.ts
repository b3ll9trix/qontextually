export type EntityType = string;

export interface Stats {
  entities_total: number;
  entities_by_type: { type: string; count: number }[];
  triples_total: number;
  sources_total: number;
  predicates_total: number;
  predicates_seeded: number;
  predicates_auto_canonical: number;
  predicates_merged: number;
  conflicts_pending: number;
  vocabulary_pending_review: number;
  avg_sources_per_triple: number;
  last_extraction_at: string;
}

export interface Health {
  status: string;
  db_path: string;
}

export interface ConflictSource {
  source_id: number;
  document_path: string;
  source_type: string;
  authority: number;
  extracted_at: string;
  snippet: string;
}

export interface ScoreBreakdown {
  authority: number;
  confidence: number;
  recency: number;
  total: number;
}

export interface ConflictCandidate {
  triple_id: number;
  value: string;
  object_is_entity: boolean;
  sources: ConflictSource[];
  score_breakdown: ScoreBreakdown;
}

export interface Conflict {
  conflict_id: number;
  conflict_type: string;
  subject_entity: { id: string; name: string; type: string };
  predicate: string;
  candidate_a: ConflictCandidate;
  candidate_b: ConflictCandidate;
  auto_resolution_hint: { winner: "a" | "b" | "neither"; reason: string };
  created_at: string;
  status?: string;
  resolved_at?: string | null;
}

export interface Paginated<T> {
  total: number;
  items: T[];
}

export interface DiscoveredPredicate {
  predicate: string;
  occurrence_count: number;
  last_used: string;
  sample_triples: { subject_name: string; object: string }[];
  nearest_canonical: { predicate: string; cosine: number; occurrence_count: number } | null;
  description: string;
}

export interface EntitySummary {
  id: string;
  type: string;
  name: string;
  properties: Record<string, unknown>;
  aliases: { alias: string; alias_type: string; is_primary?: boolean }[];
  triple_count: number;
}

export interface EntityDetail {
  id: string;
  type: string;
  name: string;
  properties: Record<string, unknown>;
  aliases: { alias: string; alias_type: string; is_primary?: boolean }[];
  outgoing_triples: {
    triple_id: number;
    predicate: string;
    object_is_entity: boolean;
    object_id: string | null;
    object_name: string | null;
    object_value: string | null;
    source_count: number;
    status: string;
  }[];
  incoming_triples: {
    triple_id: number;
    subject_id: string;
    subject_name: string;
    predicate: string;
    status: string;
  }[];
}

export interface Provenance {
  triple_id: number;
  subject: { id: string; name: string; type: string };
  predicate: string;
  object: { id: string | null; name: string | null; type: string | null };
  object_is_entity: boolean;
  sources: {
    source_id: number;
    document_path: string;
    source_type: string;
    authority: number;
    confidence: number;
    extracted_at: string;
    raw_text: string;
    snippet_around_fact: string;
  }[];
}

export interface SourceSummary {
  source_id: number;
  document_path: string;
  source_type: string;
  authority: number;
  extracted_at: string;
  triple_count: number;
  entity_count: number;
  snippet: string;
}

export interface SourceDetail {
  source_id: number;
  document_path: string;
  source_type: string;
  authority: number;
  extracted_at: string;
  properties: Record<string, unknown>;
  raw_text: string;
  contributed_triples: {
    triple_id: number;
    subject_name: string;
    predicate: string;
    object_display: string;
  }[];
  contributed_entities: { id: string; name: string; type: string }[];
}

export interface GraphData {
  nodes: { id: string; name: string; type: string; degree: number; is_center?: boolean }[];
  edges: { source: string; target: string; predicate: string; source_count: number }[];
  meta: { total_nodes_in_graph: number; sampled_nodes: number; sampled_edges: number };
}
