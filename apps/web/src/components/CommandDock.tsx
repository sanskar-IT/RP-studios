import type { FormEvent } from "react";

import type { InputMode, SceneActor } from "../types";

const MODES: InputMode[] = ["Auto", "Actor", "Director", "Narrator", "World", "Retcon"];

interface CommandDockProps {
  mode: InputMode;
  onModeChange: (mode: InputMode) => void;
  command: string;
  onCommandChange: (value: string) => void;
  onSubmit: (event: FormEvent) => void;
  actors: SceneActor[];
  actorOverride: string;
  onActorOverrideChange: (value: string) => void;
  possessedId: string | null;
  onRelease: (characterId: string) => void;
  canSubmit: boolean;
  blockedReason: string;
  busy: boolean;
}

const PROMPTS: Record<InputMode, { label: string; placeholder: string; action: string }> = {
  Auto: { label: "What happens next?", placeholder: "Describe an action, thought, or world change…", action: "Continue" },
  Actor: { label: "Speak or act as…", placeholder: "What does your character do?", action: "Continue" },
  Director: { label: "Set the horizon…", placeholder: "Have the General betray the Emperor…", action: "Record intent" },
  Narrator: { label: "Describe the beat…", placeholder: "The rain begins to fall on the parade ground…", action: "Continue" },
  World: { label: "Change the world…", placeholder: "A storm breaks over the valley…", action: "Apply world change" },
  Retcon: { label: "Declare a canon change…", placeholder: "The Emperor survived the assassination…", action: "Override canon" }
};

export default function CommandDock({
  mode,
  onModeChange,
  command,
  onCommandChange,
  onSubmit,
  actors,
  actorOverride,
  onActorOverrideChange,
  possessedId,
  onRelease,
  canSubmit,
  blockedReason,
  busy
}: CommandDockProps) {
  const prompt = PROMPTS[mode];
  const possessedActor = actors.find((actor) => actor.character_id === possessedId) ?? null;
  const overridePresent = actors.some((actor) => actor.character_id === actorOverride);

  return (
    <footer className="command-dock">
      <div className="mode-tabs" role="tablist" aria-label="Interaction mode">
        {MODES.map((item) => (
          <button key={item} role="tab" aria-selected={mode === item} className={mode === item ? "active" : ""} onClick={() => onModeChange(item)}>
            {item}
          </button>
        ))}
      </div>

      <form className="command-form" onSubmit={onSubmit}>
        <div className="command-context">
          <span className="command-prompt">{prompt.label}</span>
          {possessedActor && (
            <button type="button" className="possession-chip" onClick={() => onRelease(possessedActor.character_id)}>
              possessing {possessedActor.name} · release
            </button>
          )}
        </div>
        <input
          value={command}
          onChange={(event) => onCommandChange(event.target.value)}
          placeholder={canSubmit ? prompt.placeholder : blockedReason}
          disabled={!canSubmit}
          aria-label="Performance input"
        />
        <button className="command-button" disabled={busy || !canSubmit || !command.trim()}>
          {busy ? "…" : prompt.action}
        </button>
      </form>

      <div className="command-extras">
        <div className="next-actor">
          <span className="command-label">NEXT ACTOR</span>
          <select
            className="actor-override"
            value={overridePresent ? actorOverride : ""}
            onChange={(event) => onActorOverrideChange(event.target.value)}
            disabled={mode === "Retcon" || actors.length === 0}
            aria-label="Next actor override"
          >
            <option value="">Engine chooses</option>
            {actors.map((actor) => (
              <option key={actor.character_id} value={actor.character_id}>
                {actor.name} ({actor.control_mode})
              </option>
            ))}
          </select>
        </div>
        <div className="possession-controls">
          <span>POSSESSION</span>
          {actors.length === 0 && <span className="empty-copy">no participants</span>}
          {actors.map((actor) => (
            <button
              key={actor.character_id}
              type="button"
              className={possessedId === actor.character_id ? "possessed" : ""}
              onClick={() => onRelease(actor.character_id)}
              disabled={busy || !canSubmit || possessedId !== actor.character_id}
              title={
                possessedId === actor.character_id
                  ? `Release ${actor.name}`
                  : `${actor.name} is driven by the simulation; take control from the cast panel`
              }
            >
              {actor.name} · {actor.control_mode}
            </button>
          ))}
        </div>
        {!canSubmit && <div className="command-guard">{blockedReason}</div>}
      </div>
    </footer>
  );
}
