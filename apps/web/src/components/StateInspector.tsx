import type { ReactNode } from "react";

import type { InspectResponse, JsonObject, Scene, StagingProposal } from "../types";
import type { EventView } from "../eventView";

interface StateInspectorProps {
  inspect: InspectResponse | null;
  scene: Scene | null;
  recentEvents: EventView[];
  showLore: boolean;
  onToggleLore: () => void;
}

function readObject(record: JsonObject | undefined, key: string): JsonObject {
  const value = record?.[key];
  return value && typeof value === "object" && !Array.isArray(value) ? (value as JsonObject) : {};
}

function readList(record: JsonObject | undefined, key: string): string[] {
  const value = record?.[key];
  if (Array.isArray(value)) return value.map((item) => (typeof item === "string" ? item : JSON.stringify(item)));
  if (typeof value === "string") return [value];
  return [];
}

function readName(map: JsonObject | undefined, id: string): string {
  const record = readObject(map, id);
  const name = record.name ?? record.label ?? record.title;
  return typeof name === "string" && name ? name : id;
}

function factBody(record: JsonObject): string {
  const parts = Object.entries(record)
    .filter(([key]) => !["id", "fact_id", "event_type", "name", "title", "label"].includes(key))
    .map(([key, value]) => `${key}: ${formatValue(value)}`)
    .filter((text) => !text.endsWith(": "));
  return parts.join(" · ");
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (Array.isArray(value)) return value.map((item) => String(item)).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function Block({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <div className="inspector-block">
      <div className="block-title">
        {title} {count !== undefined && <span>{count}</span>}
      </div>
      {children}
    </div>
  );
}

function Line({ label, value }: { label: string; value: string }) {
  return (
    <div className="state-line">
      <strong>{label}</strong>
      <span>{value || "—"}</span>
    </div>
  );
}

export default function StateInspector({ inspect, scene, recentEvents, showLore, onToggleLore }: StateInspectorProps) {
  const state = inspect?.state;
  const characters = state?.characters ?? {};
  const controlModes = state?.control_modes ?? {};
  const dead = new Set(state?.dead ?? []);
  const characterIds = Object.keys(characters);
  const timeLabel = (scene?.staging as StagingProposal | undefined)?.time ?? "";

  const locations = Object.entries(state?.locations ?? {});
  const worldFacts = Object.entries(state?.world_facts ?? {});
  const relationships = Object.entries(state?.relationships ?? {});
  const items = Object.entries(state?.items ?? {});
  const injuries = Object.entries(state?.injuries ?? []);
  const knowledge = Object.entries(state?.knowledge ?? {});
  const divergences = Object.entries(state?.canon_divergences ?? {});
  const commitments = inspect?.planner_context?.commitments ?? [];
  const completedIntents = state?.completed_intents ?? [];
  const loreDebug = inspect?.lore_debug;

  return (
    <div className="inspector-scroll">
      <Block title="Where and when" count={locations.length}>
        {locations.length === 0 && timeLabel === "" ? (
          <div className="empty-copy">No location or time recorded yet.</div>
        ) : (
          <>
            {locations.map(([id, record]) => (
              <Line key={id} label={readName(state?.locations, id)} value={factBody(record) || "recorded"} />
            ))}
            {timeLabel && <Line label="Scene time" value={timeLabel} />}
          </>
        )}
      </Block>

      <Block title="Characters" count={characterIds.length}>
        {characterIds.length === 0 && <div className="empty-copy">No runtime character state yet.</div>}
        {characterIds.map((id) => {
          const record = readObject(characters, id);
          const mode = controlModes[id];
          return (
            <div className="state-line" key={id}>
              <strong>
                {readName(characters, id)}
                {dead.has(id) ? <i className="state-flag dead">dead</i> : null}
                {mode ? <i className={`state-flag ${mode}`}>{mode}</i> : null}
              </strong>
              <span>
                {[
                  record.alive === false ? "deceased" : null,
                  record.location_id ? `at ${readName(state?.locations, String(record.location_id))}` : null,
                  record.last_action ? String(record.last_action) : null
                ]
                  .filter(Boolean)
                  .join(" · ") || "no runtime facts"}
              </span>
            </div>
          );
        })}
      </Block>

      <Block title="Relationships" count={relationships.length}>
        {relationships.length === 0 && <div className="empty-copy">No relationship shifts recorded.</div>}
        {relationships.map(([key, record]) => {
          const sourceId = String(record.source_character_id ?? "");
          const targetId = String(record.target_character_id ?? "");
          return (
            <Line
              key={key}
              label={`${readName(characters, sourceId)} → ${readName(characters, targetId)}`}
              value={[
                formatValue(record.relationship_type),
                record.strength !== undefined ? `strength ${formatValue(record.strength)}` : null
              ]
                .filter(Boolean)
                .join(" · ")}
            />
          );
        })}
      </Block>

      <Block title="Items" count={items.reduce((total, list) => total + list[1].length, 0)}>
        {items.length === 0 && <div className="empty-copy">No items carried.</div>}
        {items.map(([id, list]) => (
          <Line key={id} label={readName(characters, id)} value={list.join(" · ")} />
        ))}
      </Block>

      <Block title="Injuries" count={injuries.reduce((total, list) => total + list[1].length, 0)}>
        {injuries.length === 0 && <div className="empty-copy">No injuries recorded.</div>}
        {injuries.map(([id, list]) => (
          <Line key={id} label={readName(characters, id)} value={list.join(" · ")} />
        ))}
      </Block>

      <Block title="Known facts" count={knowledge.reduce((total, list) => total + list[1].length, 0)}>
        {knowledge.length === 0 && <div className="empty-copy">No knowledge boundaries recorded.</div>}
        {knowledge.map(([id, list]) => (
          <Line key={id} label={readName(characters, id)} value={list.join(" · ")} />
        ))}
      </Block>

      <Block title="World facts" count={worldFacts.length}>
        {worldFacts.length === 0 && <div className="empty-copy">No world facts committed.</div>}
        {worldFacts.map(([id, record]) => (
          <Line key={id} label={readName(state?.world_facts, id)} value={factBody(record)} />
        ))}
      </Block>

      <Block title="Control and life state" count={characterIds.length}>
        {characterIds.length === 0 && <div className="empty-copy">No character control state yet.</div>}
        {characterIds.map((id) => (
          <Line
            key={id}
            label={readName(characters, id)}
            value={[
              dead.has(id) ? "dead" : readObject(characters, id).alive === false ? "dead" : "alive",
              controlModes[id] ? `control ${controlModes[id]}` : "control unset"
            ].join(" · ")}
          />
        ))}
        {dead.size > 0 && (
          <div className="state-note">
            Dead characters: {[...dead].map((id) => readName(characters, id)).join(", ")}
          </div>
        )}
      </Block>

      <Block title="Open commitments" count={commitments.length}>
        {commitments.length === 0 && <div className="empty-copy">No pending director commitments.</div>}
        {commitments.map((commitment, index) => (
          <div className="state-line" key={String(commitment.id ?? index)}>
            <strong>{String(commitment.status ?? "pending")}</strong>
            <span>
              {String(commitment.description ?? "unnamed commitment")}
              {commitment.priority !== undefined ? ` · priority ${formatValue(commitment.priority)}` : ""}
              {commitment.progress !== undefined ? ` · progress ${formatValue(commitment.progress)}` : ""}
            </span>
          </div>
        ))}
        {completedIntents.length > 0 && (
          <div className="state-note">Resolved intents: {completedIntents.length}</div>
        )}
      </Block>

      <Block title="Canon divergences" count={divergences.length}>
        {divergences.length === 0 && <div className="empty-copy">This timeline still matches canon.</div>}
        {divergences.map(([id, record]) => (
          <div className="state-line" key={id}>
            <strong>{String(record.canon_reference ?? `divergence ${id}`)}</strong>
            <span>{String(record.text ?? factBody(record) ?? "committed divergence")}</span>
          </div>
        ))}
      </Block>

      <Block title="Recent events" count={recentEvents.length}>
        {recentEvents.length === 0 && <div className="empty-copy">No events on this timeline yet.</div>}
        {recentEvents.map((view) => (
          <div className="state-line" key={view.id}>
            <strong>#{view.sequence} {view.label}</strong>
            <span>{view.headline}</span>
          </div>
        ))}
      </Block>

      <Block title="Memories" count={inspect?.memories.length ?? 0}>
        {(inspect?.memories ?? []).slice(0, 6).map((memory) => (
          <div className="memory-line" key={memory.id}>
            <span className={`memory-class ${memory.memory_class}`}>{memory.memory_class}</span>
            <span>{memory.content}</span>
          </div>
        ))}
        {!inspect?.memories.length && <div className="empty-copy">Memories appear after the first committed beat.</div>}
      </Block>

      <div className="inspector-block lore-block">
        <div className="block-title">
          <button type="button" className="block-toggle" onClick={onToggleLore}>
            Lore debug {showLore ? "▾" : "▸"}
          </button>
        </div>
        {showLore ? (
          <>
            <div className="lore-summary">
              <span>
                activated <b>{loreDebug?.activated?.length ?? 0}</b>
              </span>
              <span>
                considered <b>{loreDebug?.considered?.length ?? 0}</b>
              </span>
              <span>
                tokens <b>{loreDebug?.used_tokens ?? 0}</b> / {loreDebug?.token_budget ?? "—"}
              </span>
            </div>
            {(loreDebug?.activated ?? []).map((entry) => (
              <div className="lore-line" key={entry.entry_id}>
                <strong>{entry.name || entry.entry_id}</strong>
                <span>{entry.content}</span>
                <small>
                  {entry.scope} · {entry.token_count} tokens · reasons: {entry.reasons.join(" · ") || "n/a"}
                </small>
              </div>
            ))}
            {!loreDebug?.activated?.length && <div className="empty-copy">No lore activation recorded yet.</div>}

            <div className="lore-considered">
              <div className="block-title">
                Considered <span>{loreDebug?.considered?.length ?? 0}</span>
              </div>
              {(loreDebug?.considered ?? []).map((entry) => (
                <div className="lore-line" key={entry.entry_id}>
                  <strong>
                    {entry.entry_id}
                    <i className={`state-flag ${entry.activated ? "ai" : ""}`}>{entry.activated ? "used" : "skipped"}</i>
                  </strong>
                  <small>reasons: {entry.reasons.join(" · ") || "n/a"}</small>
                </div>
              ))}
              {!loreDebug?.considered?.length && (
                <div className="empty-copy">No rejected entries recorded for the last generation.</div>
              )}
            </div>
          </>
        ) : (
          <div className="empty-copy">Lore retrieval reasons are hidden.</div>
        )}
      </div>
    </div>
  );
}
