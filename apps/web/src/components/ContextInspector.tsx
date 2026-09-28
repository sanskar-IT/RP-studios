import { useState, type ReactNode } from "react";

import { shortId } from "../eventView";
import type {
  ContextComponentDebug,
  Contradiction,
  GenerationTraceDetail,
  GenerationTraceSummary,
  InspectResponse,
  JsonObject,
  MemoryInspection,
  ValidationReport
} from "../types";

interface ContextInspectorProps {
  inspect: InspectResponse | null;
  generations: GenerationTraceSummary[];
  selectedTrace: GenerationTraceDetail | null;
  memoryInspection: MemoryInspection | null;
  busy: boolean;
  onSelectTrace: (generationId: string) => void;
  onRejectTrace: (generationId: string) => void;
  onAcceptWarning: (code: string) => void;
  onRegenerateWarning: () => void;
  onCorrectWarning: (factId: string) => void;
}

function Block({ title, count, children }: { title: string; count?: number; children: ReactNode }) {
  return (
    <div className="inspector-block">
      <div className="block-title">
        {title}
        {count !== undefined && <span>{count}</span>}
      </div>
      {children}
    </div>
  );
}

function componentState(component: ContextComponentDebug): string {
  if (!component.included) return `excluded (${component.reason})`;
  if (component.dropped_items > 0) return `${component.dropped_items} item(s) dropped`;
  return "included";
}

function ContextComposition({ inspect }: { inspect: InspectResponse | null }) {
  const context = inspect?.context_debug;
  if (!context) return <div className="empty-copy">No generation has run on this timeline yet.</div>;
  return (
    <div>
      <div className="context-total">
        <strong>{context.total_tokens.toLocaleString()}</strong>
        <span>
          {" "}
          / {context.max_input_tokens.toLocaleString()} input tokens ({context.estimator ?? "unknown estimator"})
        </span>
      </div>
      {(context.components ?? []).map((component) => (
        <div key={component.key} className={`context-line ${component.included ? "" : "excluded"}`}>
          <span className="context-priority">P{component.priority}</span>
          <span className="context-label">{component.label}</span>
          <span className="context-tokens">{component.tokens.toLocaleString()} tok</span>
          <span className="context-state">{componentState(component)}</span>
          <small title={`${component.source} — ${component.retrieval}`}>{component.source}</small>
        </div>
      ))}
      {(context.excluded ?? []).length > 0 && (
        <div className="context-excluded">
          Excluded: {(context.excluded ?? []).map((item) => `${item.label} (${item.reason})`).join("; ")}
        </div>
      )}
    </div>
  );
}

function ValidationBlock({ validation }: { validation?: ValidationReport | null }) {
  if (!validation) return <div className="empty-copy">No validation recorded.</div>;
  return (
    <div>
      <div className="validation-status">
        <span className={`status-pill ${validation.valid ? "" : "rejected"}`}>{validation.status}</span>
        <small>
          {validation.accepted_event_count} accepted · {validation.rejected_event_count} rejected
        </small>
      </div>
      {validation.errors.map((error, index) => (
        <div key={index} className="validation-error">
          {error}
        </div>
      ))}
      {validation.errors.length === 0 && <div className="empty-copy">Every claim validated.</div>}
    </div>
  );
}

function ContradictionBlock({
  contradictions,
  busy,
  onAcceptWarning,
  onRegenerateWarning,
  onCorrectWarning
}: {
  contradictions: Contradiction[];
  busy: boolean;
  onAcceptWarning: (code: string) => void;
  onRegenerateWarning: () => void;
  onCorrectWarning: (factId: string) => void;
}) {
  const [factId, setFactId] = useState("");
  if (!contradictions.length) return <div className="empty-copy">No contradictions detected.</div>;
  return (
    <div>
      {contradictions.map((warning, index) => (
        <div key={`${warning.code}-${index}`} className={`contradiction-card severity-${warning.severity}`}>
          <div className="contradiction-head">
            <strong>{warning.code}</strong>
            <span className="status-pill">{warning.severity}</span>
          </div>
          <p className="contradiction-summary">{warning.summary}</p>
          {warning.expected && <small>Expected: {warning.expected}</small>}
          {warning.observed && <small>Generated: {String(warning.observed).slice(0, 240)}</small>}
          <div className="contradiction-actions">
            <button className="text-button" onClick={() => onAcceptWarning(warning.code)} disabled={busy}>
              Accept
            </button>
            <button className="text-button" onClick={onRegenerateWarning} disabled={busy}>
              Regenerate
            </button>
          </div>
        </div>
      ))}
      <div className="contradiction-correct">
        <input
          className="fork-name"
          value={factId}
          onChange={(event) => setFactId(event.target.value)}
          aria-label="World fact id to correct"
          placeholder="fact id to correct"
        />
        <button
          className="text-button"
          onClick={() => factId.trim() && onCorrectWarning(factId.trim())}
          disabled={busy || !factId.trim()}
        >
          Correct state
        </button>
      </div>
    </div>
  );
}

function MemoryBlock({ memoryInspection }: { memoryInspection: MemoryInspection | null }) {
  if (!memoryInspection) return <div className="empty-copy">Memory inspection has not loaded.</div>;
  return (
    <div>
      <div className="memory-counts">
        <span>
          <strong>{memoryInspection.active}</strong> active
        </span>
        <span>
          <strong>{memoryInspection.superseded}</strong> superseded
        </span>
        <span>
          <strong>{memoryInspection.total}</strong> total
        </span>
      </div>
      <div className="memory-scopes">
        {Object.entries(memoryInspection.by_scope ?? {}).map(([scope, count]) => (
          <span key={scope} className="event-chip">
            {scope} ×{count}
          </span>
        ))}
      </div>
      {(memoryInspection.memories ?? []).slice(0, 12).map((memory) => (
        <div key={memory.id} className={`memory-line ${memory.is_active ? "" : "superseded"}`}>
          <span>{memory.content.slice(0, 160)}</span>
          <span className={`memory-class ${memory.memory_class}`}>{memory.memory_class}</span>
          <small>
            {memory.scope} · {memory.is_active ? "active" : "superseded"} · {shortId(memory.id)}
          </small>
        </div>
      ))}
      {(memoryInspection.memories ?? []).length === 0 && <div className="empty-copy">No memories stored.</div>}
    </div>
  );
}

function TraceBlock({
  generations,
  selectedTrace,
  busy,
  onSelectTrace,
  onRejectTrace
}: {
  generations: GenerationTraceSummary[];
  selectedTrace: GenerationTraceDetail | null;
  busy: boolean;
  onSelectTrace: (generationId: string) => void;
  onRejectTrace: (generationId: string) => void;
}) {
  return (
    <div>
      {generations.length === 0 && <div className="empty-copy">No generations recorded.</div>}
      {generations.slice(0, 10).map((generation) => (
        <button
          key={generation.generation_id}
          type="button"
          className={`checkpoint-card ${selectedTrace?.generation_id === generation.generation_id ? "selected" : ""}`}
          onClick={() => onSelectTrace(generation.generation_id)}
          aria-pressed={selectedTrace?.generation_id === generation.generation_id}
        >
          <span className="checkpoint-sequence">
            {generation.status}
            {generation.contradiction_count > 0 && <i className="checkpoint-latest" title="Has contradictions" />}
          </span>
          <strong>{generation.input_text.slice(0, 60) || "—"}</strong>
          <small>
            {generation.total_tokens ?? "—"} tok · {generation.validation_status ?? "—"} ·{" "}
            {shortId(generation.generation_id)}
          </small>
        </button>
      ))}
      {selectedTrace && (
        <div className="trace-detail">
          <div className="trace-head">
            <strong>{shortId(selectedTrace.generation_id)}</strong>
            <span className="status-pill">{selectedTrace.status}</span>
          </div>
          <small>
            {selectedTrace.provider_name} · {selectedTrace.model_name}
          </small>
          <details className="event-raw">
            <summary>Context ({selectedTrace.context?.total_tokens ?? "—"} tok)</summary>
            <pre>{JSON.stringify(selectedTrace.context ?? {}, null, 2)}</pre>
          </details>
          <details className="event-raw">
            <summary>Validation</summary>
            <pre>{JSON.stringify(selectedTrace.validation ?? {}, null, 2)}</pre>
          </details>
          <details className="event-raw">
            <summary>Trace</summary>
            <pre>{JSON.stringify(selectedTrace.trace ?? ({} as JsonObject), null, 2)}</pre>
          </details>
          {selectedTrace.status === "completed" && (
            <button
              className="text-button"
              onClick={() => onRejectTrace(selectedTrace.generation_id)}
              disabled={busy}
            >
              Reject this generation
            </button>
          )}
        </div>
      )}
    </div>
  );
}

export default function ContextInspector({
  inspect,
  generations,
  selectedTrace,
  memoryInspection,
  busy,
  onSelectTrace,
  onRejectTrace,
  onAcceptWarning,
  onRegenerateWarning,
  onCorrectWarning
}: ContextInspectorProps) {
  const commitments = inspect?.commitments ?? [];
  const contradictions = inspect?.contradictions ?? [];
  const validation = inspect?.validation;
  return (
    <div className="inspector-scroll">
      <Block title="Context composition" count={inspect?.context_debug?.total_tokens}>
        <ContextComposition inspect={inspect} />
      </Block>
      <Block title="Validation" count={validation ? validation.accepted_event_count : undefined}>
        <ValidationBlock validation={validation} />
      </Block>
      <Block title="Contradictions" count={contradictions.length}>
        <ContradictionBlock
          contradictions={contradictions}
          busy={busy}
          onAcceptWarning={onAcceptWarning}
          onRegenerateWarning={onRegenerateWarning}
          onCorrectWarning={onCorrectWarning}
        />
      </Block>
      <Block title="Commitments" count={commitments.length}>
        {commitments.length === 0 && <div className="empty-copy">No commitments on this timeline.</div>}
        {commitments.map((commitment) => (
          <div key={commitment.id} className="state-line">
            <strong>{commitment.status}</strong>
            <span>{commitment.description}</span>
            <small>
              {commitment.forked_from_commitment_id ? "branched · " : ""}
              {shortId(commitment.id)}
            </small>
          </div>
        ))}
      </Block>
      <Block title="Memories" count={memoryInspection?.active}>
        <MemoryBlock memoryInspection={memoryInspection} />
      </Block>
      <Block title="Generation traces" count={generations.length}>
        <TraceBlock
          generations={generations}
          selectedTrace={selectedTrace}
          busy={busy}
          onSelectTrace={onSelectTrace}
          onRejectTrace={onRejectTrace}
        />
      </Block>
    </div>
  );
}
