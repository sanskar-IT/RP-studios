import type { DirectorPlanSummary, PlanBeat, SceneSession } from "../types";

/**
 * The RP loop's control surface.
 *
 * Deliberately small: who you are playing, what beat you are on, whether a plan
 * is running, and the four things you can do next. The Director plan is present
 * but summarised — a plan you have to study is a plan that has taken over the
 * screen, and the product is supposed to feel like a studio, not a console.
 */

export type SessionAction = "continue" | "interrupt" | "regenerate" | "possess" | "release";

interface SessionBarProps {
  session: SceneSession | null;
  plan: DirectorPlanSummary | null;
  busy: boolean;
  pendingApproval: boolean;
  onContinue: () => void;
  onInterrupt: () => void;
  onRegenerate: () => void;
  onPossess: (characterId: string) => void;
  onRelease: (characterId: string) => void;
  onApprove: () => void;
  onCancelPlan: () => void;
}

function beatLabel(beat: PlanBeat | null | undefined): string {
  if (!beat) return "No beat — improvise the moment";
  return beat.description;
}

function planBadge(plan: DirectorPlanSummary | null): { text: string; tone: string } {
  if (!plan) return { text: "No plan", tone: "idle" };
  switch (plan.status) {
    case "proposed":
      return { text: "Awaiting your decision", tone: "waiting" };
    case "approved":
      return { text: "Approved", tone: "ready" };
    case "executing":
      return {
        text: `Playing — ${(plan.remaining_beats ?? []).length} beat(s) left`,
        tone: "playing"
      };
    case "completed":
      return { text: "Plan complete", tone: "done" };
    case "superseded":
      return { text: "Superseded by your direction", tone: "idle" };
    default:
      return { text: plan.status, tone: "idle" };
  }
}

export default function SessionBar({
  session,
  plan,
  busy,
  pendingApproval,
  onContinue,
  onInterrupt,
  onRegenerate,
  onPossess,
  onRelease,
  onApprove,
  onCancelPlan
}: SessionBarProps) {
  if (!session) {
    return (
      <section className="session-bar session-bar--empty" aria-label="Session">
        <p className="session-bar__hint">Stage and approve a scene to begin.</p>
      </section>
    );
  }

  const badge = planBadge(plan);
  const actorName =
    session.cast.find((member) => member.character_id === session.current_actor)?.name ?? null;
  const canContinue = session.can_continue && !busy;
  const planRunning = plan?.status === "executing" || plan?.status === "approved";

  return (
    <section className="session-bar" aria-label="Session">
      <div className="session-bar__row">
        <div className="session-bar__scene">
          <span className="session-bar__label">Scene</span>
          <strong>{session.scene.title || "Untitled"}</strong>
          <span className="session-bar__meta">
            {session.scene.location || "unplaced"} · {session.scene.status}
          </span>
        </div>

        <div className="session-bar__actor">
          <span className="session-bar__label">You are</span>
          <strong>{actorName ?? "no one (auto)"}</strong>
        </div>

        <div className="session-bar__beat">
          <span className="session-bar__label">Now</span>
          <strong>{beatLabel(plan?.current_beat)}</strong>
        </div>

        <div className="session-bar__plan">
          <span className="session-bar__label">Plan</span>
          <span className={`plan-badge plan-badge--${badge.tone}`}>{badge.text}</span>
        </div>
      </div>

      {pendingApproval && (
        <div className="session-bar__decision" role="status">
          <p>
            The Director has a plan for this scene. Nothing has been performed yet — approve it
            to continue, or change the direction.
          </p>
          <div className="session-bar__actions">
            <button type="button" onClick={onApprove} disabled={busy}>
              Approve and perform
            </button>
            <button type="button" onClick={onCancelPlan} disabled={busy}>
              Discard plan
            </button>
          </div>
        </div>
      )}

      <div className="session-bar__actions">
        <button type="button" onClick={onContinue} disabled={!canContinue || pendingApproval}>
          Continue
        </button>
        <button type="button" onClick={onRegenerate} disabled={!canContinue || busy}>
          Regenerate
        </button>
        <button
          type="button"
          onClick={onInterrupt}
          disabled={!planRunning || busy}
          title="Stop the current plan and play the moment freely"
        >
          Interrupt
        </button>
      </div>

      <div className="session-bar__cast">
        <span className="session-bar__label">Take control</span>
        {session.cast.map((member) => (
          <span key={member.character_id} className="session-bar__cast-member">
            <span className={member.alive ? "" : "is-dead"}>{member.name}</span>
            {member.user_controlled ? (
              <button type="button" onClick={() => onRelease(member.character_id)} disabled={busy}>
                Release
              </button>
            ) : (
              <button
                type="button"
                onClick={() => onPossess(member.character_id)}
                disabled={busy || !member.alive}
              >
                Possess
              </button>
            )}
          </span>
        ))}
      </div>
    </section>
  );
}
