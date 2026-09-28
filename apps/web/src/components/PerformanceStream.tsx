import type { EventView } from "../eventView";

interface PerformanceStreamProps {
  views: EventView[];
  emptyHint: string;
  onForkFromLatest: () => void;
  canFork: boolean;
}

const KIND_GLYPH: Record<string, string> = {
  narration: "✧",
  dialogue: "“",
  action: "▸",
  state: "◇",
  control: "◈",
  meta: "·"
};

function formatClock(value: string): string {
  if (!value) return "";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  return parsed.toISOString().slice(11, 19);
}

export default function PerformanceStream({ views, emptyHint, onForkFromLatest, canFork }: PerformanceStreamProps) {
  if (views.length === 0) {
    return (
      <div className="event-stream">
        <div className="performance-empty">
          <div className="empty-orbit">✦</div>
          <h3>The stage is quiet.</h3>
          <p>{emptyHint}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="event-stream">
      {views.map((view) => (
        <article className={`event-card ${view.kind} tone-${view.tone} source-${view.source}`} key={view.id}>
          <div className="event-meta">
            <span className="event-kind">
              <i className="event-glyph" aria-hidden="true">
                {KIND_GLYPH[view.kind] ?? "◇"}
              </i>
              {view.label}
            </span>
            <span>
              #{view.sequence}
              {formatClock(view.createdAt) ? ` · ${formatClock(view.createdAt)}` : ""}
            </span>
          </div>
          {view.actor && <div className="event-actor">{view.actor}</div>}
          <p className="event-headline">{view.headline}</p>
          {view.details.length > 0 && (
            <div className="event-details">
              {view.details.map((detail, index) => (
                <span className="event-chip" key={`${view.id}-detail-${index}`}>
                  {detail}
                </span>
              ))}
            </div>
          )}
          {view.kind === "meta" && (
            <details className="event-raw">
              <summary>Raw payload</summary>
              <pre>{JSON.stringify(view.raw, null, 2)}</pre>
            </details>
          )}
        </article>
      ))}
      {canFork && (
        <div className="stream-footer">
          <button className="text-button" onClick={onForkFromLatest}>
            Return to a checkpoint by branching
          </button>
        </div>
      )}
    </div>
  );
}
