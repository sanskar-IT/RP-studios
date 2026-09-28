import type {
  Character,
  CharacterCardImportResult,
  Checkpoint,
  DirectorPlanSummary,
  GenerationTraceDetail,
  GenerationTraceSummary,
  ImportPreview,
  InspectResponse,
  JsonObject,
  LorebookImportResult,
  LorebookSummary,
  MemoryInspection,
  PipelineResult,
  Project,
  Scene,
  SceneActor,
  SceneSession,
  StagingProposal,
  Timeline
} from "./types";

const API_BASE = import.meta.env.VITE_API_URL ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {})
    }
  });
  const body = (await response.json().catch(() => ({}))) as { detail?: string };
  if (!response.ok) {
    throw new Error(body.detail ?? `Request failed with status ${response.status}`);
  }
  return body as T;
}

async function upload<T>(path: string, file: File): Promise<T> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch(`${API_BASE}${path}`, { method: "POST", body });
  const payload = (await response.json().catch(() => ({}))) as { detail?: string };
  if (!response.ok) {
    throw new Error(payload.detail ?? `Request failed with status ${response.status}`);
  }
  return payload as T;
}

/** Scene mutations answer with a PipelineResult envelope; the proposal lives under `structured_output`. */
export function readStagingResult(result: PipelineResult | JsonObject): StagingProposal | null {
  const structured = (result as PipelineResult).structured_output;
  if (structured && typeof structured === "object" && !Array.isArray(structured)) {
    return structured as unknown as StagingProposal;
  }
  const inline = result as unknown as StagingProposal;
  return typeof inline.objective === "string" && typeof inline.status === "string" ? inline : null;
}

export const api = {
  listProjects: () => request<Project[]>("/api/projects"),
  createProject: (name: string, description: string) =>
    request<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify({ name, description })
    }),
  previewCharacterCard: (projectId: string, file: File) =>
    upload<ImportPreview>(`/api/projects/${projectId}/imports/character-card/preview`, file),
  previewLorebook: (projectId: string, file: File) =>
    upload<ImportPreview>(`/api/projects/${projectId}/imports/lorebook/preview`, file),
  importCharacterCard: (projectId: string, file: File) =>
    upload<CharacterCardImportResult>(`/api/projects/${projectId}/imports/character-card`, file),
  importLorebook: (projectId: string, file: File) =>
    upload<LorebookImportResult>(`/api/projects/${projectId}/imports/lorebook`, file),
  listCharacters: (projectId: string) => request<Character[]>(`/api/projects/${projectId}/characters`),
  createCharacter: (projectId: string, name: string) =>
    request<Character>(`/api/projects/${projectId}/characters`, {
      method: "POST",
      body: JSON.stringify({ name, definition: {} })
    }),
  listScenes: (projectId: string) => request<Scene[]>(`/api/projects/${projectId}/scenes`),
  listTimelines: (projectId: string) => request<Timeline[]>(`/api/projects/${projectId}/timelines`),
  listLorebooks: (projectId: string) => request<LorebookSummary[]>(`/api/projects/${projectId}/lorebooks`),
  listSceneActors: (projectId: string, sceneId: string) =>
    request<SceneActor[]>(`/api/projects/${projectId}/scenes/${sceneId}/actors`),
  sceneSession: (projectId: string, sceneId: string) =>
    request<SceneSession>(`/api/projects/${projectId}/scenes/${sceneId}/session`),
  listPlans: (projectId: string, sceneId?: string) =>
    request<DirectorPlanSummary[]>(
      `/api/projects/${projectId}/plans${sceneId ? `?scene_id=${sceneId}` : ""}`
    ),
  getPlan: (projectId: string, planId: string) =>
    request<DirectorPlanSummary>(`/api/projects/${projectId}/plans/${planId}`),
  decidePlan: (projectId: string, planId: string, decision: "approve" | "edit" | "cancel" | "reject") =>
    request<DirectorPlanSummary>(`/api/projects/${projectId}/plans/${planId}/decide`, {
      method: "POST",
      body: JSON.stringify({ decision })
    }),
  executePlan: (projectId: string, planId: string, actorCharacterId?: string) =>
    request<PipelineResult>(`/api/projects/${projectId}/plans/${planId}/execute`, {
      method: "POST",
      body: JSON.stringify({ actor_character_id: actorCharacterId ?? null })
    }),
  interruptPlan: (projectId: string, planId: string, reason: string) =>
    request<DirectorPlanSummary>(`/api/projects/${projectId}/plans/${planId}/interrupt`, {
      method: "POST",
      body: JSON.stringify({ reason })
    }),
  listCheckpoints: (projectId: string, timelineId: string) =>
    request<Checkpoint[]>(`/api/projects/${projectId}/timelines/${timelineId}/checkpoints`),
  stage: (projectId: string, premise: string, characterIds: string[], sceneId?: string) =>
    request<StagingProposal>(`/api/projects/${projectId}/stage`, {
      method: "POST",
      body: JSON.stringify({ premise, character_ids: characterIds, scene_id: sceneId ?? null })
    }),
  approveScene: (projectId: string, sceneId: string, stagingRevision?: number) =>
    request<PipelineResult>(`/api/projects/${projectId}/scenes/${sceneId}/approve`, {
      method: "POST",
      body: JSON.stringify({ staging_revision: stagingRevision ?? null })
    }),
  editStaging: (projectId: string, sceneId: string, updates: JsonObject, stagingRevision?: number) =>
    request<PipelineResult>(`/api/projects/${projectId}/scenes/${sceneId}/staging`, {
      method: "PATCH",
      body: JSON.stringify({ scene_id: sceneId, staging_revision: stagingRevision ?? null, ...updates })
    }),
  regenerateStaging: (projectId: string, sceneId: string, premise?: string, characterIds?: string[]) =>
    request<PipelineResult>(`/api/projects/${projectId}/scenes/${sceneId}/staging/regenerate`, {
      method: "POST",
      body: JSON.stringify({
        premise: premise ?? null,
        character_ids: characterIds ?? null
      })
    }),
  cancelScene: (projectId: string, sceneId: string) =>
    request<PipelineResult>(`/api/projects/${projectId}/scenes/${sceneId}/cancel`, { method: "POST" }),
  endScene: (projectId: string, sceneId: string) =>
    request<PipelineResult>(`/api/projects/${projectId}/scenes/${sceneId}/end`, { method: "POST" }),
  continueScene: (
    projectId: string,
    sceneId: string,
    mode: string,
    userInput: string,
    possessedCharacterId?: string,
    actorCharacterId?: string
  ) =>
    request<PipelineResult>(`/api/projects/${projectId}/scenes/${sceneId}/continue`, {
      method: "POST",
      body: JSON.stringify({
        scene_id: sceneId,
        mode: mode.toLowerCase(),
        user_input: userInput,
        possessed_character_id: possessedCharacterId ?? null,
        actor_character_id: actorCharacterId ?? null
      })
    }),
  regenerate: (projectId: string, sceneId: string, mode: string, userInput: string) =>
    request<PipelineResult>(`/api/projects/${projectId}/scenes/${sceneId}/regenerate`, {
      method: "POST",
      body: JSON.stringify({ scene_id: sceneId, mode: mode.toLowerCase(), user_input: userInput })
    }),
  direct: (projectId: string, sceneId: string, text: string, intentType: string) =>
    request<PipelineResult>(`/api/projects/${projectId}/scenes/${sceneId}/direct`, {
      method: "POST",
      body: JSON.stringify({ scene_id: sceneId, text, intent_type: intentType, horizon: "short" })
    }),
  possess: (projectId: string, sceneId: string, characterId: string) =>
    request<PipelineResult>(`/api/projects/${projectId}/scenes/${sceneId}/possess`, {
      method: "POST",
      body: JSON.stringify({ scene_id: sceneId, character_id: characterId })
    }),
  release: (projectId: string, sceneId: string, characterId: string) =>
    request<PipelineResult>(`/api/projects/${projectId}/scenes/${sceneId}/release`, {
      method: "POST",
      body: JSON.stringify({ scene_id: sceneId, character_id: characterId })
    }),
  inspect: (projectId: string, timelineId: string) =>
    request<InspectResponse>(`/api/projects/${projectId}/timelines/${timelineId}/inspect`),
  fork: (projectId: string, timelineId: string, name: string, nodeId?: string) =>
    request<Timeline>(`/api/projects/${projectId}/timelines/${timelineId}/fork`, {
      method: "POST",
      body: JSON.stringify({ timeline_id: timelineId, node_id: nodeId ?? null, name })
    }),
  canonOverride: (projectId: string, timelineId: string, text: string, reference: string) =>
    request<PipelineResult>(`/api/projects/${projectId}/timelines/${timelineId}/canon-override`, {
      method: "POST",
      body: JSON.stringify({ text, canon_reference: reference })
    }),
  listGenerations: (projectId: string, timelineId: string) =>
    request<GenerationTraceSummary[]>(`/api/projects/${projectId}/timelines/${timelineId}/generations`),
  generationTrace: (projectId: string, generationId: string) =>
    request<GenerationTraceDetail>(`/api/projects/${projectId}/generations/${generationId}`),
  rejectGeneration: (projectId: string, generationId: string, reason: string) =>
    request<PipelineResult>(`/api/projects/${projectId}/generations/${generationId}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason })
    }),
  inspectMemories: (projectId: string, timelineId: string, query = "", characterId?: string) =>
    request<MemoryInspection>(`/api/projects/${projectId}/timelines/${timelineId}/memories`, {
      method: "POST",
      body: JSON.stringify({ query, character_id: characterId ?? null, limit: 40 })
    }),
  updateCommitment: (projectId: string, timelineId: string, commitmentId: string, status?: string, progress?: number) =>
    request<PipelineResult>(`/api/projects/${projectId}/timelines/${timelineId}/commitments/${commitmentId}`, {
      method: "POST",
      body: JSON.stringify({ status: status ?? null, progress: progress ?? null, note: "" })
    }),
  correctState: (projectId: string, timelineId: string, factId: string, changes: JsonObject) =>
    request<PipelineResult>(`/api/projects/${projectId}/timelines/${timelineId}/correct-state`, {
      method: "POST",
      body: JSON.stringify({ fact_id: factId, changes, note: "corrected from a contradiction warning" })
    })
};
