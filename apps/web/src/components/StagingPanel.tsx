import { useEffect, useMemo, useState } from "react";

import type { JsonObject, SceneActor, StagingProposal } from "../types";

type ListField =
  | "characters_present"
  | "initial_conditions"
  | "assumptions"
  | "unresolved_assumptions"
  | "relevant_context"
  | "environmental_assumptions"
  | "potential_consequences";

const LIST_FIELDS: Array<{ key: ListField; label: string; editable: boolean; hint: string }> = [
  { key: "initial_conditions", label: "Initial conditions", editable: true, hint: "One condition per line." },
  { key: "assumptions", label: "Assumptions", editable: true, hint: "One assumption per line." },
  { key: "unresolved_assumptions", label: "Unresolved assumptions", editable: true, hint: "One assumption per line." },
  { key: "environmental_assumptions", label: "Environmental assumptions", editable: true, hint: "One assumption per line." },
  { key: "relevant_context", label: "Relevant context / lore", editable: true, hint: "One reference per line." },
  { key: "potential_consequences", label: "Consequences", editable: true, hint: "One consequence per line." }
];

interface StagingPanelProps {
  scene: { id: string; title: string; status: string; staging: StagingProposal; staging_revision: number } | null;
  proposal: StagingProposal | null;
  actors: SceneActor[];
  busy: boolean;
  onApprove: () => void;
  onSave: (updates: JsonObject) => void;
  onRegenerate: () => void;
  onCancel: () => void;
  onEnd: () => void;
  onRegeneratePerformance: () => void;
}

function toList(value: string): string[] {
  return value
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function toText(value: string[]): string {
  return value.join("\n");
}

type ListKey = {
  [K in keyof StagingProposal]-?: StagingProposal[K] extends string[] ? K : never;
}[keyof StagingProposal];

function readList(proposal: StagingProposal | null, key: ListKey): string[] {
  const value = proposal?.[key];
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}

function readText(proposal: StagingProposal | null, key: "objective" | "location" | "time"): string {
  const value = proposal?.[key];
  return typeof value === "string" ? value : "";
}

export default function StagingPanel({
  scene,
  proposal,
  actors,
  busy,
  onApprove,
  onSave,
  onRegenerate,
  onCancel,
  onEnd,
  onRegeneratePerformance
}: StagingPanelProps) {
  const [editing, setEditing] = useState(false);
  const [objective, setObjective] = useState("");
  const [location, setLocation] = useState("");
  const [time, setTime] = useState("");
  const [lists, setLists] = useState<Record<ListField, string>>({
    characters_present: "",
    initial_conditions: "",
    assumptions: "",
    unresolved_assumptions: "",
    relevant_context: "",
    environmental_assumptions: "",
    potential_consequences: ""
  });

  useEffect(() => {
    setObjective(readText(proposal, "objective"));
    setLocation(readText(proposal, "location"));
    setTime(readText(proposal, "time"));
    setLists({
      characters_present: toText(readList(proposal, "characters_present")),
      initial_conditions: toText(readList(proposal, "initial_conditions")),
      assumptions: toText(readList(proposal, "assumptions")),
      unresolved_assumptions: toText(readList(proposal, "unresolved_assumptions")),
      relevant_context: toText(readList(proposal, "relevant_context")),
      environmental_assumptions: toText(readList(proposal, "environmental_assumptions")),
      potential_consequences: toText(readList(proposal, "potential_consequences"))
    });
  }, [proposal]);

  const status = scene?.status ?? "none";
  const isStaged = status === "staged" && proposal !== null;
  const isActive = status === "active";
  const isClosed = status === "completed" || status === "cancelled";

  const canonConflicts = useMemo(() => {
    const merged = [
      ...readList(proposal, "canon_conflicts"),
      ...readList(proposal, "possible_canon_conflicts")
    ];
    return [...new Set(merged)];
  }, [proposal]);

  const relevantSourceState = readList(proposal, "relevant_source_state");
  const intendedConsequences = readList(proposal, "intended_consequences");

  function submitEdits() {
    const participantNames = toList(lists.characters_present);
    const participantIds = participantNames
      .map((name) => actors.find((actor) => actor.name === name)?.character_id)
      .filter((id): id is string => Boolean(id));
    const updates: JsonObject = {
      objective: objective.trim(),
      location: location.trim(),
      time: time.trim(),
      characters_present: participantNames,
      initial_conditions: toList(lists.initial_conditions),
      assumptions: toList(lists.assumptions),
      unresolved_assumptions: toList(lists.unresolved_assumptions),
      relevant_context: toList(lists.relevant_context),
      environmental_assumptions: toList(lists.environmental_assumptions),
      potential_consequences: toList(lists.potential_consequences)
    };
    if (participantNames.length > 0 && participantIds.length === participantNames.length) {
      updates.character_ids = participantIds;
    }
    onSave(updates);
    setEditing(false);
  }

  return (
    <section className="staging-panel" aria-label="Staging">
      <div className="staging-head">
        <div>
          <div className="eyebrow">STAGING</div>
          <h2>{proposal?.objective || scene?.title || "No scene staged"}</h2>
        </div>
        <div className="staging-head-meta">
          <span className={`status-pill ${status}`}>{status}</span>
          {scene && <span className="revision-mark">REV {scene.staging_revision}</span>}
        </div>
      </div>

      {!proposal ? (
        <div className="empty-copy">Stage a premise to reveal the inferred conditions of the scene.</div>
      ) : (
        <>
          <div className="staging-grid">
            <div className="staging-cell">
              <span className="staging-key">Objective</span>
              {editing ? (
                <textarea value={objective} onChange={(event) => setObjective(event.target.value)} rows={3} />
              ) : (
                <p className="staging-value">{proposal.objective || "—"}</p>
              )}
            </div>
            <div className="staging-cell">
              <span className="staging-key">Location</span>
              {editing ? (
                <input value={location} onChange={(event) => setLocation(event.target.value)} />
              ) : (
                <p className="staging-value">{proposal.location || "—"}</p>
              )}
            </div>
            <div className="staging-cell">
              <span className="staging-key">Time</span>
              {editing ? (
                <input value={time} onChange={(event) => setTime(event.target.value)} />
              ) : (
                <p className="staging-value">{proposal.time || "—"}</p>
              )}
            </div>
            <div className="staging-cell">
              <span className="staging-key">Participants</span>
              {editing ? (
                <textarea
                  value={lists.characters_present}
                  onChange={(event) => setLists({ ...lists, characters_present: event.target.value })}
                  rows={3}
                />
              ) : (
                <div className="staging-chips">
                  {actors.length === 0 && (proposal.characters_present ?? []).length === 0 && <span className="empty-copy">None</span>}
                  {actors.map((actor) => (
                    <span className={`staging-chip ${actor.control_mode}`} key={actor.character_id}>
                      {actor.name}
                      <i>{actor.control_mode}</i>
                    </span>
                  ))}
                  {(proposal.characters_present ?? []).map((name) => (
                    <span className="staging-chip" key={name}>
                      {name}
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>

          {LIST_FIELDS.map((field) => {
            const values = readList(proposal, field.key);
            return (
              <div className="staging-block" key={field.key}>
                <div className="block-title">
                  {field.label} <span>{values.length}</span>
                </div>
                {editing ? (
                  <textarea
                    value={lists[field.key]}
                    onChange={(event) => setLists({ ...lists, [field.key]: event.target.value })}
                    rows={3}
                    placeholder={field.hint}
                  />
                ) : values.length === 0 ? (
                  <div className="empty-copy">None recorded.</div>
                ) : (
                  <ul className="staging-list">
                    {values.map((value, index) => (
                      <li key={`${field.key}-${index}`}>{value}</li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}

          {relevantSourceState.length > 0 && (
            <div className="staging-block">
              <div className="block-title">
                Source state used <span>{relevantSourceState.length}</span>
              </div>
              <ul className="staging-list">
                {relevantSourceState.map((value, index) => (
                  <li key={`source-${index}`}>{value}</li>
                ))}
              </ul>
            </div>
          )}

          {intendedConsequences.length > 0 && (
            <div className="staging-block">
              <div className="block-title">
                Intended consequences <span>{intendedConsequences.length}</span>
              </div>
              <ul className="staging-list">
                {intendedConsequences.map((value, index) => (
                  <li key={`intended-${index}`}>{value}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="staging-block canon-block">
            <div className="block-title">
              Canon conflicts <span>{canonConflicts.length}</span>
            </div>
            {canonConflicts.length === 0 ? (
              <div className="empty-copy">The proposal does not conflict with recorded canon.</div>
            ) : (
              <ul className="staging-list conflict-list">
                {canonConflicts.map((value, index) => (
                  <li key={`conflict-${index}`}>{value}</li>
                ))}
              </ul>
            )}
            <small className="staging-note">
              Canon conflicts are read-only: the staging edit contract accepts objective, location, time,
              participants, conditions, assumptions, context and consequences, but no conflict field. Use Retcon to
              override canon.
            </small>
          </div>
        </>
      )}

      <div className="staging-actions">
        {editing ? (
          <>
            <button className="action-button approve" onClick={submitEdits} disabled={busy}>
              Save revision
            </button>
            <button className="text-button" onClick={() => setEditing(false)} disabled={busy}>
              Discard changes
            </button>
          </>
        ) : (
          <>
            {isStaged && (
              <button className="action-button approve" onClick={onApprove} disabled={busy}>
                Accept staging
              </button>
            )}
            {isStaged && (
              <button className="text-button" onClick={() => setEditing(true)} disabled={busy}>
                Edit proposal
              </button>
            )}
            {isStaged && (
              <button className="text-button" onClick={onRegenerate} disabled={busy}>
                Regenerate
              </button>
            )}
            {isStaged && (
              <button className="text-button" onClick={onCancel} disabled={busy}>
                Cancel staging
              </button>
            )}
            {isActive && (
              <button className="action-button" onClick={onEnd} disabled={busy}>
                End scene
              </button>
            )}
            {isActive && (
              <button className="text-button" onClick={onRegeneratePerformance} disabled={busy}>
                Regenerate on branch
              </button>
            )}
          </>
        )}
        {isClosed && <div className="empty-copy">This scene is {status}. Stage a new premise to continue.</div>}
        {status === "none" && <div className="empty-copy">Approval gates the performance. Nothing can be performed yet.</div>}
      </div>
    </section>
  );
}
