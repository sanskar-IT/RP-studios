import type { ChangeEvent, FormEvent } from "react";

import type { Character, ImportPreview, LorebookSummary } from "../types";

export type ImportKind = "card" | "lorebook";

export interface PendingImport {
  kind: ImportKind;
  file: File;
  preview: ImportPreview;
}

interface CastPanelProps {
  characters: Character[];
  selectedCast: string[];
  controlModes: Record<string, string>;
  onToggleCast: (characterId: string) => void;
  onSetControl: (character: SceneParticipantLike, mode: "ai" | "user") => void;
  newCharacterName: string;
  onNewCharacterNameChange: (value: string) => void;
  onCreateCharacter: (event: FormEvent) => void;
  onPreviewFile: (event: ChangeEvent<HTMLInputElement>, kind: ImportKind) => void;
  pendingImport: PendingImport | null;
  onConfirmImport: () => void;
  onDiscardImport: () => void;
  importNotice: string;
  lorebooks: LorebookSummary[];
  busy: boolean;
  hasScene: boolean;
}

export interface SceneParticipantLike {
  character_id: string;
  control_mode: string;
}

function previewTokenBudget(preview: ImportPreview): number | null {
  if (typeof preview.token_budget === "number") return preview.token_budget;
  if (preview.lorebook && typeof preview.lorebook.token_budget === "number") return preview.lorebook.token_budget;
  return null;
}

export default function CastPanel({
  characters,
  selectedCast,
  controlModes,
  onToggleCast,
  onSetControl,
  newCharacterName,
  onNewCharacterNameChange,
  onCreateCharacter,
  onPreviewFile,
  pendingImport,
  onConfirmImport,
  onDiscardImport,
  importNotice,
  lorebooks,
  busy,
  hasScene
}: CastPanelProps) {
  return (
    <aside className="cast-panel panel">
      <div className="panel-heading">
        <div>
          <div className="eyebrow">01 / CAST</div>
          <h2>Who is here</h2>
        </div>
        <span className="count-badge">{characters.length}</span>
      </div>

      <div className="cast-list">
        {characters.length === 0 && <div className="empty-copy">Add a character to begin casting the scene.</div>}
        {characters.map((character) => {
          const mode = controlModes[character.id];
          const isUserControlled = mode === "user";
          return (
            <div
              key={character.id}
              className={`cast-card ${selectedCast.includes(character.id) ? "selected" : ""}`}
            >
              <button type="button" className="cast-card-main" onClick={() => onToggleCast(character.id)}>
                <span className="character-avatar">{character.name.slice(0, 1).toUpperCase()}</span>
                <span className="cast-card-copy">
                  <strong>{character.name}</strong>
                  <small>
                    {!hasScene
                      ? selectedCast.includes(character.id)
                        ? "in scene pool"
                        : "available"
                      : mode
                        ? isUserControlled
                          ? "user controlled"
                          : "ai controlled"
                        : "not in scene"}
                  </small>
                </span>
                <span className="cast-dot" />
              </button>
              {mode && (
                <div className="control-toggle" role="group" aria-label={`Control mode for ${character.name}`}>
                  <button
                    type="button"
                    className={mode === "ai" ? "active" : ""}
                    onClick={() => onSetControl({ character_id: character.id, control_mode: mode }, "ai")}
                    disabled={busy}
                    aria-pressed={mode === "ai"}
                  >
                    AI
                  </button>
                  <button
                    type="button"
                    className={mode === "user" ? "active user" : ""}
                    onClick={() => onSetControl({ character_id: character.id, control_mode: mode }, "user")}
                    disabled={busy}
                    aria-pressed={mode === "user"}
                  >
                    USER
                  </button>
                </div>
              )}
            </div>
          );
        })}
      </div>

      <form className="inline-form" onSubmit={onCreateCharacter}>
        <input
          value={newCharacterName}
          onChange={(event) => onNewCharacterNameChange(event.target.value)}
          placeholder="Add character"
          aria-label="Character name"
        />
        <button className="square-button" disabled={busy || !newCharacterName.trim()} aria-label="Add character">
          +
        </button>
      </form>

      <div className="import-controls">
        <span>IMPORT</span>
        <label className="import-button">
          Character card
          <input
            type="file"
            accept=".json,.png,.apng,image/png,application/json"
            onChange={(event) => onPreviewFile(event, "card")}
            disabled={busy}
          />
        </label>
        <label className="import-button">
          Lorebook
          <input
            type="file"
            accept=".json,application/json"
            onChange={(event) => onPreviewFile(event, "lorebook")}
            disabled={busy}
          />
        </label>
      </div>

      {importNotice && <div className="import-notice">{importNotice}</div>}

      {pendingImport && (
        <div className="import-preview">
          <div className="import-preview-title">
            {pendingImport.kind === "card" ? "Character card preview" : "Lorebook preview"}
          </div>
          <strong>{pendingImport.preview.name}</strong>
          <div className="import-facts">
            <span>
              <b>Version</b> {pendingImport.preview.card_version ?? pendingImport.preview.source_format ?? "n/a"}
            </span>
            <span>
              <b>Entries</b> {pendingImport.preview.entry_count}
            </span>
            <span>
              <b>Token budget</b> {previewTokenBudget(pendingImport.preview) ?? "provider default"}
            </span>
            {pendingImport.preview.scan_depth !== undefined && (
              <span>
                <b>Scan depth</b> {pendingImport.preview.scan_depth}
              </span>
            )}
            {pendingImport.preview.source_filename && (
              <span>
                <b>File</b> {pendingImport.preview.source_filename}
              </span>
            )}
          </div>
          {pendingImport.preview.lorebook?.name && (
            <div className="import-subnote">Embedded book: {pendingImport.preview.lorebook.name}</div>
          )}
          {pendingImport.preview.extensions_preserved && pendingImport.preview.extensions_preserved.length > 0 && (
            <div className="import-subnote">Extensions: {pendingImport.preview.extensions_preserved.join(", ")}</div>
          )}
          <div className="import-warnings">
            <span className="import-preview-title">Warnings · {pendingImport.preview.warnings.length}</span>
            {pendingImport.preview.warnings.length === 0 ? (
              <small>No warnings from the parser.</small>
            ) : (
              <ul>
                {pendingImport.preview.warnings.map((warning, index) => (
                  <li key={`${index}-${warning}`}>{warning}</li>
                ))}
              </ul>
            )}
          </div>
          <div className="import-preview-actions">
            <button className="primary-button" onClick={onConfirmImport} disabled={busy}>
              Confirm import
            </button>
            <button className="text-button" onClick={onDiscardImport} disabled={busy}>
              Discard
            </button>
          </div>
        </div>
      )}

      {lorebooks.length > 0 && (
        <div className="imported-lorebooks">
          <div className="import-preview-title">Imported lorebooks</div>
          {lorebooks.map((book) => (
            <div className="imported-book-line" key={book.id}>
              <strong>{book.name}</strong>
              <span>
                {book.entry_count} entries · {String(book.extra_data.token_budget ?? 2048)} budget · {book.scope}
              </span>
            </div>
          ))}
        </div>
      )}

      <div className="panel-footer-note">Definitions stay separate from runtime state.</div>
    </aside>
  );
}
