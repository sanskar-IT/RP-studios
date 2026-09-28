export type JsonObject = Record<string, unknown>;

export type ControlMode = "ai" | "user";

export type SceneStatus = "draft" | "staged" | "active" | "completed" | "cancelled";

export interface Project {
  id: string;
  name: string;
  description: string;
  active_timeline_id: string | null;
  extra_data: JsonObject;
}

export interface Character {
  id: string;
  name: string;
  definition: JsonObject;
  is_active: boolean;
}

export interface Scene {
  id: string;
  project_id: string;
  timeline_id: string;
  title: string;
  status: string;
  staging: StagingProposal;
  staging_revision: number;
  approved_staging_revision: number | null;
  current: boolean;
}

export interface Timeline {
  id: string;
  project_id: string;
  name: string;
  parent_timeline_id: string | null;
  forked_from_node_id: string | null;
}

export interface NarrativeEvent {
  id: string;
  timeline_id: string;
  node_id: string;
  sequence: number;
  event_type: string;
  payload: JsonObject;
  source: string;
  created_at: string;
}

export interface StateSnapshot {
  characters: Record<string, JsonObject>;
  relationships: Record<string, JsonObject>;
  locations: Record<string, JsonObject>;
  world_facts: Record<string, JsonObject>;
  knowledge: Record<string, string[]>;
  items: Record<string, string[]>;
  injuries: Record<string, string[]>;
  control_modes: Record<string, string>;
  completed_intents: string[];
  canon_divergences: Record<string, JsonObject>;
  dead: string[];
  active_scene_id: string | null;
  revision: number;
}

export interface MemoryRecord {
  id: string;
  content: string;
  memory_class: string;
  scope?: string;
  importance: number;
  is_active?: boolean;
  character_id: string | null;
  scene_id: string | null;
  created_at: string | null;
}

export interface ActivatedLore {
  entry_id: string;
  name: string;
  scope: string;
  reasons: string[];
  token_count: number;
  content: string;
}

export interface LoreDebug {
  activated?: ActivatedLore[];
  considered?: Array<{ entry_id: string; activated: boolean; reasons: string[] }>;
  used_tokens?: number;
  token_budget?: number;
}

export interface Checkpoint {
  id: string;
  timeline_id: string;
  sequence: number;
  parent_node_id: string | null;
  event_id: string | null;
  event_type: string | null;
  generation_id: string | null;
}

export interface SceneActor {
  character_id: string;
  name: string;
  control_mode: ControlMode;
  presence: string;
}

export interface InspectResponse {
  project_id: string;
  timeline_id: string;
  state: StateSnapshot;
  knowledge?: Record<string, { certain: string[]; suspected: string[] }>;
  memories: MemoryRecord[];
  commitments?: CommitmentRecord[];
  context_debug?: ContextDebug;
  validation?: ValidationReport;
  contradictions?: Contradiction[];
  lore_debug: LoreDebug;
  events: NarrativeEvent[];
  checkpoints?: Checkpoint[];
  planner_context?: { timeline_id: string; commitments: Array<JsonObject> };
  active_timeline_id: string | null;
}

export interface CommitmentRecord {
  id: string;
  description: string;
  status: string;
  raw_status?: string;
  priority: number;
  progress: number;
  timeline_id: string | null;
  forked_from_commitment_id: string | null;
  created_sequence: number;
}

export interface ContextComponentDebug {
  key: string;
  label: string;
  priority: number;
  priority_label: string;
  source: string;
  retrieval: string;
  tokens: number;
  required: boolean;
  stale: boolean;
  note: string;
  included: boolean;
  reason: string;
  item_count: number;
  dropped_items: number;
}

export interface ContextDebug {
  total_tokens: number;
  component_tokens?: number;
  system_tokens?: number;
  contract_tokens?: number;
  max_input_tokens: number;
  remaining_tokens?: number;
  estimator?: string;
  budget?: JsonObject;
  components: ContextComponentDebug[];
  by_priority?: Record<string, number>;
  excluded?: Array<{ key: string; label: string; reason: string; dropped_items: number; would_cost: number }>;
}

export interface ValidationReport {
  status: string;
  valid: boolean;
  accepted_event_count: number;
  rejected_event_count: number;
  errors: string[];
  warnings?: Contradiction[];
  recovery_actions?: string[];
}

export interface Contradiction {
  code: string;
  severity: string;
  summary: string;
  expected?: string;
  observed?: string;
  subject_character_id?: string | null;
  event_type?: string;
  payload?: JsonObject;
  actions: string[];
}

export interface GenerationTraceSummary {
  generation_id: string;
  scene_id: string | null;
  status: string;
  provider_name: string;
  model_name: string;
  input_text: string;
  total_tokens: number | null;
  max_input_tokens: number | null;
  validation_status: string | null;
  contradiction_count: number;
  created_at: string | null;
}

export interface GenerationTraceDetail extends JsonObject {
  generation_id: string;
  status: string;
  provider_name: string;
  model_name: string;
  context: ContextDebug;
  validation: ValidationReport;
  trace: JsonObject;
}

export interface MemoryInspection {
  project_id: string;
  timeline_id: string;
  timeline_lineage: string[];
  total: number;
  active: number;
  superseded: number;
  by_class: Record<string, number>;
  by_scope: Record<string, number>;
  retrieval: JsonObject;
  knowledge: Record<string, { certain: string[]; suspected: string[] }>;
  selected: Array<JsonObject>;
  memories: Array<MemoryRecord & { scope: string; is_active: boolean; timeline_id: string | null }>;
}

export interface StagingProposal {
  scene_id?: string;
  location: string;
  time: string;
  characters_present: string[];
  objective: string;
  initial_conditions: string[];
  relevant_source_state: string[];
  relevant_context: string[];
  environmental_assumptions: string[];
  potential_consequences: string[];
  intended_consequences: string[];
  canon_conflicts: string[];
  possible_canon_conflicts: string[];
  assumptions: string[];
  unresolved_assumptions: string[];
  staging_revision: number;
  status: string;
}

export interface LorebookSummary {
  id: string;
  name: string;
  scope: string;
  owner_character_id: string | null;
  entry_count: number;
  extra_data: JsonObject;
}

export interface ImportPreview {
  kind: "character_card" | "lorebook";
  name: string;
  card_version?: string;
  source_format?: string;
  source_filename?: string;
  warnings: string[];
  extensions_preserved?: string[];
  entry_count: number;
  token_budget?: number;
  scan_depth?: number;
  lorebook?: { name?: string; scan_depth?: number; token_budget?: number } | null;
}

export interface CharacterCardImportResult extends JsonObject {
  character_id: string;
  name: string;
  card_version: string;
  source_format: string;
  source_filename: string;
  lorebook_id: string | null;
  entry_count: number;
  warnings: string[];
  extensions_preserved: string[];
}

export interface LorebookImportResult extends JsonObject {
  lorebook_id: string;
  entry_count: number;
  source_filename: string;
  warnings: string[];
}

export type InputMode = "Actor" | "Director" | "Narrator" | "World" | "Retcon" | "Auto";

export interface PipelineResult extends JsonObject {
  scene_id?: string;
  generation_id?: string;
  output_text?: string;
  structured_output?: JsonObject | null;
  lore_debug?: LoreDebug | null;
  intent_id?: string;
  commitment_id?: string;
  timeline_id?: string;
  state?: JsonObject | null;
  planner_context?: { timeline_id: string; commitments: Array<JsonObject> } | null;
  checkpoint_node_id?: string;
  // Director plan fields. Present on a direction or long-horizon turn; absent
  // when the Director had no plan to make, which is the normal case for a
  // user acting through their own character.
  plan_id?: string;
  requires_approval?: boolean | null;
  plan?: JsonObject | null;
  director?: JsonObject | null;
}

export type PlanStatus = "proposed" | "approved" | "executing" | "completed" | "superseded" | "cancelled";

export interface PlanBeat {
  description: string;
  status: "pending" | "active" | "completed" | "skipped" | "invalidated";
  required: boolean;
  note: string;
}

export interface DirectorPlanSummary {
  plan_id: string;
  status: PlanStatus;
  lane: string;
  objective: string;
  summary: string;
  beats: PlanBeat[];
  current_beat?: PlanBeat | null;
  remaining_beats?: PlanBeat[];
  required_approval?: boolean;
}

export interface SessionScene {
  scene_id: string;
  title: string;
  status: string;
  location: string;
  objective: string;
}

export interface SessionCastMember {
  character_id: string;
  name: string;
  control_mode: ControlMode;
  user_controlled: boolean;
  alive: boolean;
  presence: string;
}

/**
 * Everything the RP loop needs in one response.
 *
 * The studio is a place to play, not a dashboard. Assembling this client-side
 * from four endpoints would put the orchestration in the UI, which is the one
 * place it must not be.
 */
export interface SceneSession {
  scene: SessionScene;
  cast: SessionCastMember[];
  current_actor: string | null;
  plan: DirectorPlanSummary | JsonObject | null;
  can_continue: boolean;
  open_commitments: OpenCommitment[];
}

export interface OpenCommitment {
  id: string;
  description: string;
  status: string;
  priority: number;
  progress: number;
}
