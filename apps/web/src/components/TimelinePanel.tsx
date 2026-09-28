import { useEffect, useRef } from "react";

import { shortId } from "../eventView";
import type { Checkpoint, Timeline } from "../types";

interface TimelinePanelProps {
  timeline: Timeline | null;
  checkpoints: Checkpoint[];
  checkpointCount: number;
  latestNodeId: string | null;
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
  branchName: string;
  onBranchNameChange: (value: string) => void;
  onFork: () => void;
  busy: boolean;
  canFork: boolean;
}

export default function TimelinePanel({
  timeline,
  checkpoints,
  checkpointCount,
  latestNodeId,
  selectedNodeId,
  onSelect,
  branchName,
  onBranchNameChange,
  onFork,
  busy,
  canFork
}: TimelinePanelProps) {
  const railRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const rail = railRef.current;
    if (rail) rail.scrollLeft = rail.scrollWidth;
  }, [checkpoints.length, timeline?.id]);

  const ordered = [...checkpoints].sort((left, right) => left.sequence - right.sequence);
  const effectiveSelection = selectedNodeId ?? latestNodeId;
  const selected = ordered.find((item) => item.id === effectiveSelection) ?? null;

  return (
    <section className="timeline-panel" aria-label="Timeline and checkpoints">
      <div className="timeline-heading">
        <div>
          <div className="eyebrow">04 / TIMELINE</div>
          <h2>{timeline ? timeline.name : "No timeline selected"}</h2>
        </div>
        <div className="timeline-facts">
          {timeline?.parent_timeline_id && (
            <span className="timeline-fact">
              branch of <b>{shortId(timeline.parent_timeline_id)}</b>
            </span>
          )}
          {timeline?.forked_from_node_id && (
            <span className="timeline-fact">
              forked from checkpoint <b>{shortId(timeline.forked_from_node_id)}</b>
            </span>
          )}
          <span className="count-badge">{checkpointCount}</span>
        </div>
      </div>

      <div className="timeline-body">
        <div className="checkpoint-rail" ref={railRef}>
          {ordered.length === 0 && <div className="empty-copy">No checkpoints recorded on this timeline yet.</div>}
          {ordered.map((checkpoint) => {
            const isSelected = checkpoint.id === effectiveSelection;
            const isLatest = checkpoint.id === latestNodeId;
            return (
              <button
                key={checkpoint.id}
                type="button"
                className={`checkpoint-card ${isSelected ? "selected" : ""}`}
                onClick={() => onSelect(checkpoint.id)}
                aria-pressed={isSelected}
                title={`Checkpoint ${checkpoint.id}`}
              >
                <span className="checkpoint-sequence">
                  #{checkpoint.sequence}
                  {isLatest && <i className="checkpoint-latest" title="Latest checkpoint" />}
                </span>
                <strong>{checkpoint.event_type ?? "root"}</strong>
                <small>{shortId(checkpoint.id)}</small>
              </button>
            );
          })}
        </div>

        <div className="branch-controls">
          <div className="branch-readout">
            <span className="branch-label">FORK SOURCE</span>
            <span className="branch-value">
              {selected ? `#${selected.sequence} · ${selected.event_type ?? "root"}` : "latest checkpoint"}
            </span>
            <small>{selected ? shortId(selected.id) : "nothing committed yet"}</small>
          </div>
          <input
            className="fork-name"
            value={branchName}
            onChange={(event) => onBranchNameChange(event.target.value)}
            aria-label="Branch name"
            placeholder="Name this branch"
          />
          <button className="action-button" onClick={onFork} disabled={busy || !canFork}>
            {busy ? "…" : "Fork branch"}
          </button>
        </div>
      </div>
    </section>
  );
}
