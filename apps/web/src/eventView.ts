import type { JsonObject, NarrativeEvent } from "./types";

export type EventKind = "narration" | "dialogue" | "action" | "state" | "control" | "meta";

export type EventTone = "neutral" | "user" | "system" | "warn" | "danger" | "control";

export interface EventView {
  id: string;
  sequence: number;
  source: string;
  createdAt: string;
  kind: EventKind;
  label: string;
  actor: string | null;
  headline: string;
  details: string[];
  tone: EventTone;
  raw: JsonObject;
}

type NameLookup = (characterId: string) => string;

const LABELS: Record<string, string> = {
  character_moved: "Moved",
  character_spoke: "Dialogue",
  character_performed_action: "Action",
  relationship_changed: "Relationship",
  knowledge_acquired: "Learned",
  item_acquired: "Item gained",
  item_removed: "Item lost",
  injury_added: "Injury",
  injury_removed: "Recovered",
  location_changed: "Location",
  world_fact_created: "World fact",
  world_fact_modified: "World fact revised",
  character_died: "Death",
  scene_staged: "Scene staged",
  scene_staging_edited: "Staging edited",
  scene_started: "Scene opened",
  scene_ended: "Scene closed",
  scene_cancelled: "Scene cancelled",
  possession_changed: "Possession",
  director_intent_created: "Intent recorded",
  director_intent_completed: "Intent resolved",
  canon_override_created: "Canon override",
  canon_divergence: "Canon divergence",
  timeline_forked: "Branch forked",
  user_action: "Your action",
  ai_action: "Narration"
};

const KINDS: Record<string, EventKind> = {
  character_spoke: "dialogue",
  user_action: "action",
  character_performed_action: "action",
  ai_action: "narration",
  scene_staged: "meta",
  scene_staging_edited: "meta",
  scene_started: "meta",
  scene_ended: "meta",
  scene_cancelled: "meta",
  timeline_forked: "meta",
  possession_changed: "control",
  character_died: "control"
};

function asText(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function asList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(asText).filter(Boolean);
  const single = asText(value);
  return single ? [single] : [];
}

/** Picks the first present field from a payload, ignoring the plumbing keys. */
function pick(payload: JsonObject, keys: string[]): string {
  for (const key of keys) {
    const value = asText(payload[key]);
    if (value) return value;
  }
  return "";
}

function nameOf(payload: JsonObject, key: string, lookup: NameLookup): string {
  const id = asText(payload[key]);
  if (!id) return "";
  const known = lookup(id);
  return known && known !== id ? known : id;
}

function describe(event: NarrativeEvent, lookup: NameLookup): Pick<EventView, "kind" | "label" | "actor" | "headline" | "details" | "tone"> {
  const payload = event.payload ?? {};
  const type = event.event_type;
  const base = {
    kind: KINDS[type] ?? ("state" as EventKind),
    label: LABELS[type] ?? type,
    actor: null,
    details: [] as string[],
    tone: "neutral" as EventTone
  };

  switch (type) {
    case "ai_action":
      return {
        ...base,
        kind: "narration",
        actor: nameOf(payload, "character_id", lookup) || null,
        headline: pick(payload, ["prose", "text", "action", "narration"]) || "The world moves on.",
        tone: event.source === "ai" ? "neutral" : "system"
      };
    case "character_spoke": {
      const speaker = nameOf(payload, "character_id", lookup);
      const line = pick(payload, ["text", "action", "line"]);
      return { ...base, kind: "dialogue", actor: speaker || null, headline: line || "…", tone: "neutral" };
    }
    case "user_action":
      return {
        ...base,
        kind: "action",
        actor: nameOf(payload, "character_id", lookup) || "You",
        headline: pick(payload, ["text", "action", "prose"]) || "You act.",
        tone: "user"
      };
    case "character_performed_action": {
      const actor = nameOf(payload, "character_id", lookup);
      return { ...base, kind: "action", actor: actor || null, headline: pick(payload, ["text", "action"]) || "Acts." };
    }
    case "character_moved": {
      const actor = nameOf(payload, "character_id", lookup);
      const place = pick(payload, ["location_name", "label", "location_id", "location"]);
      return {
        ...base,
        actor: actor || null,
        headline: actor ? `${actor} moves to ${place || "an unrecorded place"}.` : `The scene shifts to ${place || "elsewhere"}.`
      };
    }
    case "location_changed": {
      const place = pick(payload, ["location_name", "label", "location_id", "location"]);
      return { ...base, headline: place ? `Location becomes ${place}.` : "The location changes." };
    }
    case "item_acquired":
    case "item_removed": {
      const actor = nameOf(payload, "character_id", lookup);
      const item = pick(payload, ["item", "item_id", "name"]);
      const verb = type === "item_acquired" ? "takes" : "loses";
      return { ...base, actor: actor || null, headline: actor ? `${actor} ${verb} ${item || "an item"}.` : verb };
    }
    case "injury_added":
    case "injury_removed": {
      const actor = nameOf(payload, "character_id", lookup);
      const injury = pick(payload, ["injury", "injury_id", "name", "text"]);
      const verb = type === "injury_added" ? "suffers" : "recovers from";
      return {
        ...base,
        actor: actor || null,
        headline: actor ? `${actor} ${verb} ${injury || "an injury"}.` : verb,
        tone: type === "injury_added" ? "warn" : "neutral"
      };
    }
    case "character_died": {
      const actor = nameOf(payload, "character_id", lookup);
      return { ...base, kind: "control", actor: actor || null, headline: actor ? `${actor} dies.` : "A character dies.", tone: "danger" };
    }
    case "relationship_changed": {
      const source = nameOf(payload, "source_character_id", lookup);
      const target = nameOf(payload, "target_character_id", lookup);
      const relation = pick(payload, ["relationship_type", "type", "state", "text"]);
      const strength = asText(payload.strength);
      return {
        ...base,
        actor: source || null,
        headline: source && target ? `${source} → ${target}: ${relation || "relationship shifts"}.` : "A relationship shifts.",
        details: strength ? [`strength ${strength}`] : []
      };
    }
    case "knowledge_acquired": {
      const actor = nameOf(payload, "character_id", lookup);
      const fact = pick(payload, ["fact", "fact_id", "text"]);
      return { ...base, actor: actor || null, headline: actor ? `${actor} now knows: ${fact || "something new"}.` : "New knowledge recorded." };
    }
    case "world_fact_created":
    case "world_fact_modified": {
      const factId = pick(payload, ["fact_id", "id"]);
      const statement = pick(payload, ["statement", "text", "description", "summary", "name"]);
      return {
        ...base,
        label: type === "world_fact_created" ? "World fact" : "World fact revised",
        headline: statement || (factId ? `Fact ${factId} ${type === "world_fact_created" ? "established" : "revised"}.` : "The world changes its mind about a fact.")
      };
    }
    case "possession_changed": {
      const actor = nameOf(payload, "character_id", lookup);
      const mode = asText(payload.control_mode);
      return {
        ...base,
        kind: "control",
        actor: actor || null,
        headline: actor ? (mode === "user" ? `You take control of ${actor}.` : `You release ${actor} to the simulation.`) : "Control mode changed.",
        tone: "control"
      };
    }
    case "director_intent_created": {
      const goal = pick(payload, ["goal", "text", "description", "target"]);
      return { ...base, kind: "meta", headline: goal ? `Intent logged: ${goal}` : "A story intent is recorded." };
    }
    case "director_intent_completed":
      return { ...base, kind: "meta", headline: `Intent ${pick(payload, ["intent_id", "id"]) || "closed"} resolved.` };
    case "canon_override_created":
    case "canon_divergence": {
      const text = pick(payload, ["text", "divergence", "statement"]);
      const reference = pick(payload, ["canon_reference", "reference"]);
      return {
        ...base,
        kind: "meta",
        label: type === "canon_override_created" ? "Canon override" : "Canon divergence",
        headline: text || "A divergence from canon is committed.",
        details: reference ? [`overrides: ${reference}`] : [],
        tone: "warn"
      };
    }
    case "timeline_forked": {
      const node = pick(payload, ["source_node_id", "node_id"]);
      return {
        ...base,
        kind: "meta",
        headline: node ? `Branch forked from checkpoint ${node}.` : "Branch forked.",
        details: node ? [node] : []
      };
    }
    case "scene_staged": {
      const proposal = payload.proposal && typeof payload.proposal === "object" ? (payload.proposal as JsonObject) : null;
      return {
        ...base,
        kind: "meta",
        headline: pick(payload, ["text", "objective"]) || asText(proposal?.objective) || "A scene is staged.",
        details: proposal ? [`objective: ${asText(proposal.objective) || "unset"}`] : []
      };
    }
    case "scene_staging_edited": {
      const revision = pick(payload, ["staging_revision", "revision"]);
      const proposal = payload.proposal && typeof payload.proposal === "object" ? (payload.proposal as JsonObject) : null;
      return {
        ...base,
        kind: "meta",
        label: "Staging edited",
        headline: asText(proposal?.objective) || "The staging proposal was revised.",
        details: revision ? [`revision ${revision}`] : []
      };
    }
    case "scene_started":
      return { ...base, kind: "meta", headline: "The scene begins." };
    case "scene_ended":
      return { ...base, kind: "meta", headline: "The scene closes." };
    case "scene_cancelled":
      return { ...base, kind: "meta", headline: "The scene was cancelled before it began.", tone: "warn" };
    default: {
      const text = pick(payload, ["text", "prose", "action", "goal", "description"]);
      return { ...base, headline: text || `A ${label(type)} event was committed.` };
    }
  }
}

function label(eventType: string): string {
  return (LABELS[eventType] ?? eventType).toLowerCase();
}

export function toEventView(event: NarrativeEvent, lookup: NameLookup): EventView {
  const described = describe(event, lookup);
  return {
    id: event.id,
    sequence: event.sequence,
    source: event.source,
    createdAt: event.created_at,
    raw: event.payload ?? {},
    ...described
  };
}

const META_EVENT_TYPES = new Set([
  "scene_started",
  "scene_ended",
  "scene_cancelled",
  "timeline_forked"
]);

/** Events that belong in the staged-briefing column rather than the live performance stream. */
export function isStagingEvent(eventType: string): boolean {
  return eventType === "scene_staged" || eventType === "scene_staging_edited";
}

/** Everything that is not a staging-proposal edit belongs in the performance stream. */
export function isPerformanceEvent(eventType: string): boolean {
  return !isStagingEvent(eventType) || META_EVENT_TYPES.has(eventType);
}

export function timelineEventViews(events: NarrativeEvent[], lookup: NameLookup): EventView[] {
  return events.map((event) => toEventView(event, lookup));
}

export function shortId(value: string | null | undefined, length = 8): string {
  return value ? value.slice(0, length) : "—";
}
