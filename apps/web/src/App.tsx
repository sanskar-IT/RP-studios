import { useCallback, useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from "react";

import { api, readStagingResult } from "./api";
import CastPanel, { type ImportKind, type PendingImport } from "./components/CastPanel";
import CommandDock from "./components/CommandDock";
import ContextInspector from "./components/ContextInspector";
import ErrorBanner from "./components/ErrorBanner";
import PerformanceStream from "./components/PerformanceStream";
import SessionBar from "./components/SessionBar";
import StagingPanel from "./components/StagingPanel";
import StateInspector from "./components/StateInspector";
import TimelinePanel from "./components/TimelinePanel";
import { isPerformanceEvent, timelineEventViews } from "./eventView";
import type {
  Character,
  Checkpoint,
  DirectorPlanSummary,
  GenerationTraceDetail,
  GenerationTraceSummary,
  InputMode,
  InspectResponse,
  JsonObject,
  LorebookSummary,
  MemoryInspection,
  Project,
  Scene,
  SceneActor,
  SceneSession,
  StagingProposal,
  Timeline
} from "./types";

interface Failure {
  message: string;
  retry: (() => void) | null;
  edit: (() => void) | null;
  cancel: (() => void) | null;
}

const BLOCKED_NO_SCENE = "Select or stage a scene before performing.";
const BLOCKED_NO_TIMELINE = "Select a timeline before overriding canon.";
const BLOCKED_NOT_ACTIVE = "Approve the staging proposal to unlock the performance.";

export default function App() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [timelineId, setTimelineId] = useState("");
  const [timelines, setTimelines] = useState<Timeline[]>([]);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [scenes, setScenes] = useState<Scene[]>([]);
  const [lorebooks, setLorebooks] = useState<LorebookSummary[]>([]);
  const [selectedSceneId, setSelectedSceneId] = useState("");
  const [actors, setActors] = useState<SceneActor[]>([]);
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [inspect, setInspect] = useState<InspectResponse | null>(null);
  const [stagingDraft, setStagingDraft] = useState<StagingProposal | null>(null);
  const [selectedCast, setSelectedCast] = useState<string[]>([]);
  const [actorOverride, setActorOverride] = useState("");
  const [pendingImport, setPendingImport] = useState<PendingImport | null>(null);
  const [importNotice, setImportNotice] = useState("");
  const [mode, setMode] = useState<InputMode>("Auto");
  const [premise, setPremise] = useState("");
  const [command, setCommand] = useState("");
  const [newProjectName, setNewProjectName] = useState("");
  const [newCharacterName, setNewCharacterName] = useState("");
  const [branchName, setBranchName] = useState("Alternate line");
  const [showState, setShowState] = useState(true);
  const [showLore, setShowLore] = useState(false);
  const [showContext, setShowContext] = useState(false);
  const [generations, setGenerations] = useState<GenerationTraceSummary[]>([]);
  const [selectedTrace, setSelectedTrace] = useState<GenerationTraceDetail | null>(null);
  const [memoryInspection, setMemoryInspection] = useState<MemoryInspection | null>(null);
  const [dismissedWarnings, setDismissedWarnings] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [session, setSession] = useState<SceneSession | null>(null);
  const [pendingPlanId, setPendingPlanId] = useState<string | null>(null);
  const [failure, setFailure] = useState<Failure | null>(null);
  const [setupError, setSetupError] = useState<string | null>(null);
  const sceneSelectionRef = useRef("");

  const project = useMemo(() => projects.find((item) => item.id === projectId) ?? null, [projectId, projects]);
  const timeline = useMemo(() => timelines.find((item) => item.id === timelineId) ?? null, [timelineId, timelines]);
  const scene = useMemo(() => scenes.find((item) => item.id === selectedSceneId) ?? null, [scenes, selectedSceneId]);
  const proposal = stagingDraft ?? scene?.staging ?? null;

  const characterName = useCallback(
    (characterId: string) => characters.find((item) => item.id === characterId)?.name ?? characterId,
    [characters]
  );

  const possessedId = useMemo(
    () => actors.find((actor) => actor.control_mode === "user")?.character_id ?? null,
    [actors]
  );

  const controlModes = useMemo(
    () => Object.fromEntries(actors.map((actor) => [actor.character_id, actor.control_mode])),
    [actors]
  );

  // The plan the studio shows. Prefers the plan awaiting a decision, so an
  // approval prompt is never hidden behind a different running plan.
  const activePlan = useMemo<DirectorPlanSummary | null>(() => {
    const candidate = session?.plan;
    if (!candidate) return null;
    return candidate as DirectorPlanSummary;
  }, [session?.plan]);

  const canPerform = scene?.status === "active";
  const canSubmit = mode === "Retcon" ? Boolean(timelineId) : canPerform;
  const blockedReason = canSubmit
    ? ""
    : !scene
      ? BLOCKED_NO_SCENE
      : !timelineId
        ? BLOCKED_NO_TIMELINE
        : BLOCKED_NOT_ACTIVE;

  const checkpointList = useMemo<Checkpoint[]>(() => {
    if (inspect?.checkpoints?.length) return inspect.checkpoints;
    return checkpoints;
  }, [checkpoints, inspect]);

  const latestNodeId = useMemo(() => {
    if (checkpointList.length === 0) return null;
    return [...checkpointList].sort((left, right) => left.sequence - right.sequence).at(-1)?.id ?? null;
  }, [checkpointList]);

  const run = useCallback(async (action: () => Promise<void>, recovery?: Omit<Failure, "message">) => {
    setBusy(true);
    setFailure(null);
    try {
      await action();
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "The operation failed";
      setFailure({
        message,
        retry: recovery?.retry ?? null,
        edit: recovery?.edit ?? null,
        cancel: recovery?.cancel ?? null
      });
    } finally {
      setBusy(false);
    }
  }, []);

  const loadInspection = useCallback(async (nextProjectId: string, nextTimelineId: string) => {
    if (!nextTimelineId) {
      setInspect(null);
      setCheckpoints([]);
      setGenerations([]);
      setSelectedTrace(null);
      setMemoryInspection(null);
      return;
    }
    const [inspection, timelineCheckpoints, traceList, memories] = await Promise.all([
      api.inspect(nextProjectId, nextTimelineId),
      api.listCheckpoints(nextProjectId, nextTimelineId).catch(() => [] as Checkpoint[]),
      api.listGenerations(nextProjectId, nextTimelineId).catch(() => [] as GenerationTraceSummary[]),
      api.inspectMemories(nextProjectId, nextTimelineId).catch(() => null as MemoryInspection | null)
    ]);
    setInspect(inspection);
    setCheckpoints(inspection.checkpoints?.length ? inspection.checkpoints : timelineCheckpoints);
    setGenerations(traceList);
    setMemoryInspection(memories);
    setDismissedWarnings([]);
  }, []);

  const loadActors = useCallback(async (nextProjectId: string, nextSceneId: string) => {
    if (!nextSceneId) {
      setActors([]);
      setSession(null);
      return;
    }
    setActors(await api.listSceneActors(nextProjectId, nextSceneId).catch(() => [] as SceneActor[]));
    // The session view is the RP loop's single source of truth for "who am I,
    // what is the beat, can I continue". Fetching it alongside the cast keeps the
    // client from assembling that from four endpoints, which is how orchestration
    // complexity ends up living in the UI.
    setSession(await api.sceneSession(nextProjectId, nextSceneId).catch(() => null as SceneSession | null));
  }, []);

  const loadWorkspace = useCallback(
    async (nextProjectId: string, preferredTimelineId?: string) => {
      const [nextCharacters, nextScenes, nextTimelines, nextLorebooks] = await Promise.all([
        api.listCharacters(nextProjectId),
        api.listScenes(nextProjectId),
        api.listTimelines(nextProjectId),
        api.listLorebooks(nextProjectId)
      ]);
      setCharacters(nextCharacters);
      setScenes(nextScenes);
      setTimelines(nextTimelines);
      setLorebooks(nextLorebooks);
      setSelectedCast((current) => {
        const valid = current.filter((id) => nextCharacters.some((character) => character.id === id));
        return valid.length ? valid : nextCharacters.map((character) => character.id);
      });
      const nextTimelineId = preferredTimelineId ?? nextTimelines[0]?.id ?? "";
      setTimelineId(nextTimelineId);
      setSelectedNodeId(null);
      const onTimeline = nextScenes.filter((item) => item.timeline_id === nextTimelineId);
      const current = sceneSelectionRef.current;
      const nextScene = onTimeline.find((item) => item.id === current) ?? onTimeline[0] ?? null;
      sceneSelectionRef.current = nextScene?.id ?? "";
      setSelectedSceneId(nextScene?.id ?? "");
      await loadInspection(nextProjectId, nextTimelineId);
      await loadActors(nextProjectId, nextScene?.id ?? "");
    },
    [loadActors, loadInspection]
  );

  const refresh = useCallback(async () => {
    if (!projectId) return;
    const preferred = timelineId || project?.active_timeline_id || undefined;
    await loadWorkspace(projectId, preferred);
  }, [loadWorkspace, project?.active_timeline_id, projectId, timelineId]);

  useEffect(() => {
    setBusy(true);
    setSetupError(null);
    void api
      .listProjects()
      .then((nextProjects) => {
        setProjects(nextProjects);
        if (!projectId && nextProjects[0]) setProjectId(nextProjects[0].id);
      })
      .catch((reason: unknown) => {
        setSetupError(reason instanceof Error ? reason.message : "Unable to load projects");
      })
      .finally(() => setBusy(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!projectId) return;
    void run(async () => {
      await loadWorkspace(projectId);
    });
  }, [loadWorkspace, projectId]);

  useEffect(() => {
    setActorOverride((current) => (actors.some((actor) => actor.character_id === current) ? current : ""));
  }, [actors]);

  const performanceViews = useMemo(
    () => timelineEventViews((inspect?.events ?? []).filter((event) => isPerformanceEvent(event.event_type)), characterName),
    [characterName, inspect]
  );
  const recentViews = useMemo(() => performanceViews.slice(-8).reverse(), [performanceViews]);

  function selectScene(nextSceneId: string) {
    sceneSelectionRef.current = nextSceneId;
    setSelectedSceneId(nextSceneId);
  }

  function chooseTimeline(nextTimelineId: string) {
    setTimelineId(nextTimelineId);
    selectScene("");
    setSelectedNodeId(null);
    setInspect(null);
    setCheckpoints([]);
    setSelectedTrace(null);
    void run(async () => {
      await loadInspection(projectId, nextTimelineId);
      const nextScene = scenes.find((item) => item.timeline_id === nextTimelineId);
      selectScene(nextScene?.id ?? "");
      await loadActors(projectId, nextScene?.id ?? "");
    });
  }

  function chooseScene(nextSceneId: string) {
    selectScene(nextSceneId);
    setStagingDraft(null);
    setActorOverride("");
    void run(async () => {
      await loadActors(projectId, nextSceneId);
    });
  }

  function toggleCast(characterId: string) {
    setSelectedCast((current) =>
      current.includes(characterId) ? current.filter((id) => id !== characterId) : [...current, characterId]
    );
  }

  async function setControlMode(characterId: string, currentMode: string, requestedMode: "ai" | "user") {
    if (!projectId || !scene || currentMode === requestedMode) return;
    await run(
      async () => {
        if (requestedMode === "user") {
          await api.possess(projectId, scene.id, characterId);
        } else {
          await api.release(projectId, scene.id, characterId);
        }
        await loadActors(projectId, scene.id);
        await loadInspection(projectId, scene.timeline_id);
      },
      { retry: () => void setControlMode(characterId, currentMode, requestedMode), edit: null, cancel: null }
    );
  }

  async function createProject(event: FormEvent) {
    event.preventDefault();
    if (!newProjectName.trim()) return;
    await run(async () => {
      const created = await api.createProject(newProjectName.trim(), "A new narrative workspace");
      setProjects((current) => [created, ...current]);
      setProjectId(created.id);
      setNewProjectName("");
      setTimelineId(created.active_timeline_id ?? "");
      setScenes([]);
      setCharacters([]);
      setActors([]);
      setInspect(null);
      setCheckpoints([]);
    });
  }

  async function addCharacter(name: string) {
    if (!projectId || !name.trim()) return;
    await run(
      async () => {
        await api.createCharacter(projectId, name.trim());
        setNewCharacterName("");
        await refresh();
      },
      { retry: () => void addCharacter(name), edit: null, cancel: null }
    );
  }

  function createCharacter(event: FormEvent) {
    event.preventDefault();
    void addCharacter(newCharacterName);
  }

  async function loadPreview(file: File, kind: ImportKind) {
    if (!projectId) return;
    await run(
      async () => {
        const preview =
          kind === "card" ? await api.previewCharacterCard(projectId, file) : await api.previewLorebook(projectId, file);
        setPendingImport({ kind, file, preview });
        setImportNotice("");
      },
      { retry: () => void loadPreview(file, kind), edit: null, cancel: null }
    );
  }

  function previewFile(event: ChangeEvent<HTMLInputElement>, kind: ImportKind) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    void loadPreview(file, kind);
  }

  async function confirmImport() {
    if (!projectId || !pendingImport) return;
    const payload = pendingImport;
    await run(
      async () => {
        const result =
          payload.kind === "card"
            ? await api.importCharacterCard(projectId, payload.file)
            : await api.importLorebook(projectId, payload.file);
        setImportNotice(
          payload.kind === "card"
            ? `Imported ${result.name} (card v${result.card_version}, ${result.entry_count} lore entries).`
            : `Imported lorebook with ${result.entry_count} entries.`
        );
        setPendingImport(null);
        await refresh();
      },
      { retry: confirmImport, edit: null, cancel: () => setPendingImport(null) }
    );
  }

  async function stageScene(event?: FormEvent) {
    event?.preventDefault();
    if (!projectId || !premise.trim()) return;
    const text = premise.trim();
    const targetScene = scene && scene.status !== "active" ? scene.id : undefined;
    await run(
      async () => {
        const staged = await api.stage(projectId, text, selectedCast, targetScene);
        setStagingDraft(staged);
        setPremise("");
        await refresh();
        const nextSceneId =
          staged.scene_id ?? (await api.listScenes(projectId)).find((item) => item.title === text.slice(0, 80))?.id;
        if (nextSceneId) {
          selectScene(nextSceneId);
          setStagingDraft(null);
          await loadActors(projectId, nextSceneId);
        }
      },
      { retry: () => void stageScene(), edit: null, cancel: null }
    );
  }

  async function approveScene() {
    if (!projectId || !scene) return;
    await run(
      async () => {
        await api.approveScene(projectId, scene.id, scene.staging_revision);
        setStagingDraft(null);
        await refresh();
      },
      { retry: approveScene, edit: () => setStagingDraft(scene.staging), cancel: () => void cancelScene() }
    );
  }

  async function saveStaging(updates: JsonObject) {
    if (!projectId || !scene) return;
    await run(
      async () => {
        const result = await api.editStaging(projectId, scene.id, updates, scene.staging_revision);
        setStagingDraft(readStagingResult(result));
        await refresh();
      },
      { retry: () => void saveStaging(updates), edit: null, cancel: null }
    );
  }

  async function regenerateStaging() {
    if (!projectId || !scene) return;
    await run(
      async () => {
        const result = await api.regenerateStaging(
          projectId,
          scene.id,
          scene.staging?.objective || scene.title,
          actors.map((actor) => actor.character_id)
        );
        setStagingDraft(readStagingResult(result));
        await refresh();
        await loadActors(projectId, scene.id);
      },
      { retry: regenerateStaging, edit: () => setStagingDraft(scene.staging), cancel: () => void cancelScene() }
    );
  }

  async function cancelScene() {
    if (!projectId || !scene) return;
    await run(
      async () => {
        await api.cancelScene(projectId, scene.id);
        setStagingDraft(null);
        await refresh();
      },
      { retry: cancelScene, edit: () => setStagingDraft(scene.staging), cancel: null }
    );
  }

  async function endScene() {
    if (!projectId || !scene) return;
    await run(
      async () => {
        await api.endScene(projectId, scene.id);
        setStagingDraft(null);
        await refresh();
      },
      { retry: endScene, edit: null, cancel: null }
    );
  }

  async function regeneratePerformance() {
    if (!projectId || !scene || !command.trim()) return;
    const text = command.trim();
    await run(
      async () => {
        const result = await api.regenerate(projectId, scene.id, mode, text);
        setCommand("");
        const nextTimelineId = typeof result.timeline_id === "string" ? result.timeline_id : timelineId;
        if (typeof result.scene_id === "string") {
          selectScene(result.scene_id);
          setStagingDraft(null);
        }
        await loadWorkspace(projectId, nextTimelineId);
      },
      { retry: regeneratePerformance, edit: null, cancel: null }
    );
  }

  async function takeControl(characterId: string) {
    if (!projectId || !scene) return;
    await run(async () => {
      await api.possess(projectId, scene.id, characterId);
      await refresh();
    });
  }

  async function releaseControl(characterId: string) {
    if (!projectId || !scene) return;
    await run(async () => {
      await api.release(projectId, scene.id, characterId);
      await refresh();
    });
  }

  async function approvePendingPlan() {
    if (!projectId || !pendingPlanId) return;
    await run(async () => {
      await api.decidePlan(projectId, pendingPlanId, "approve");
      setPendingPlanId(null);
      await api.executePlan(projectId, pendingPlanId);
      await refresh();
    });
  }

  async function cancelPendingPlan() {
    if (!projectId || !pendingPlanId) return;
    await run(async () => {
      await api.decidePlan(projectId, pendingPlanId, "cancel");
      setPendingPlanId(null);
      await refresh();
    });
  }

  async function interruptPlan() {
    if (!projectId || !activePlan) return;
    await run(async () => {
      await api.interruptPlan(projectId, activePlan.plan_id, "the user changed direction");
      setPendingPlanId(null);
      await refresh();
    });
  }

  async function submitCommand(event?: FormEvent) {
    event?.preventDefault();
    if (!projectId || !command.trim() || !canSubmit) return;
    const text = command.trim();
    const retry = () => void submitCommand(event);
    if (mode === "Retcon") {
      if (!timelineId) return;
      await run(
        async () => {
          await api.canonOverride(projectId, timelineId, text, "User-authorized divergence");
          setCommand("");
          await refresh();
        },
        { retry, edit: () => setCommand(text), cancel: () => setCommand("") }
      );
      return;
    }
    if (!scene) return;
    await run(
      async () => {
        if (mode === "Director") {
          await api.direct(projectId, scene.id, text, "story_commitment");
        } else if (mode === "World") {
          await api.direct(projectId, scene.id, text, "world");
        } else {
          const result = await api.continueScene(
            projectId,
            scene.id,
            mode,
            text,
            possessedId ?? undefined,
            actorOverride || undefined
          );
          // A turn that produced a plan awaiting a decision has performed
          // nothing. Surface it as a decision rather than as prose, so the user
          // is never told the scene moved when it did not.
          if (result.requires_approval && result.plan_id) {
            setPendingPlanId(result.plan_id);
          } else {
            setPendingPlanId(null);
          }
        }
        setCommand("");
        await refresh();
      },
      { retry, edit: () => setCommand(text), cancel: () => setCommand("") }
    );
  }

  async function releasePossession(characterId: string) {
    if (!projectId || !scene) return;
    await run(
      async () => {
        await api.release(projectId, scene.id, characterId);
        await loadActors(projectId, scene.id);
        await loadInspection(projectId, scene.timeline_id);
      },
      { retry: () => void releasePossession(characterId), edit: null, cancel: null }
    );
  }

  async function forkFrom(nodeId: string | null) {
    if (!projectId || !timelineId) return;
    const label = branchName.trim() || "Alternate line";
    const source = nodeId ?? latestNodeId;
    await run(
      async () => {
        const branch = await api.fork(projectId, timelineId, label, source ?? undefined);
        setTimelineId(branch.id);
        setSelectedNodeId(null);
        setStagingDraft(null);
        await loadWorkspace(projectId, branch.id);
      },
      { retry: () => void forkFrom(nodeId), edit: null, cancel: null }
    );
  }

  async function selectTrace(generationId: string) {
    if (!projectId) return;
    await run(async () => {
      setSelectedTrace(await api.generationTrace(projectId, generationId));
    });
  }

  async function rejectTrace(generationId: string) {
    if (!projectId) return;
    await run(
      async () => {
        await api.rejectGeneration(projectId, generationId, "rejected from the context panel");
        setSelectedTrace(null);
        await refresh();
      },
      { retry: () => void rejectTrace(generationId), edit: null, cancel: null }
    );
  }

  function acceptWarning(code: string) {
    setDismissedWarnings((current) => (current.includes(code) ? current : [...current, code]));
  }

  async function correctWarning(factId: string) {
    if (!projectId || !timelineId) return;
    await run(
      async () => {
        await api.correctState(projectId, timelineId, factId, { text: command.trim() || "corrected by the operator" });
        await refresh();
      },
      { retry: () => void correctWarning(factId), edit: null, cancel: null }
    );
  }

  const visibleContradictions = (inspect?.contradictions ?? []).filter(
    (warning) => !dismissedWarnings.includes(warning.code)
  );
  const contextInspect = inspect
    ? { ...inspect, contradictions: visibleContradictions }
    : inspect;

  if (!projects.length && !busy) {
    return (
      <main className="setup-shell">
        <section className="setup-card">
          <div className="eyebrow">AI NARRATIVE STUDIO</div>
          <h1>
            Build a world
            <br />
            worth changing.
          </h1>
          <p>A simulation desk for actors, directors, narrators, and world authorities.</p>
          <form onSubmit={createProject} className="setup-form">
            <label htmlFor="new-project">Project name</label>
            <input
              id="new-project"
              value={newProjectName}
              onChange={(event) => setNewProjectName(event.target.value)}
              placeholder="The Ashen Accord"
              autoFocus
            />
            <button className="primary-button" disabled={busy || !newProjectName.trim()}>
              {busy ? "Opening…" : "Open studio"}
            </button>
          </form>
          {setupError && (
            <div className="error-banner" role="alert">
              <span className="error-message">{setupError}</span>
              <div className="error-actions">
                <button className="text-button" onClick={() => window.location.reload()} disabled={busy}>
                  Retry
                </button>
              </div>
            </div>
          )}
        </section>
      </main>
    );
  }

  return (
    <div className="studio-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark">N</div>
          <div>
            <div className="brand-name">NARRATIVE / STUDIO</div>
            <div className="brand-subtitle">simulation desk</div>
          </div>
        </div>
        <div className="topbar-context">
          <select value={projectId} onChange={(event) => setProjectId(event.target.value)} aria-label="Project">
            {projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
          <span className="context-divider">/</span>
          <select value={timelineId} onChange={(event) => chooseTimeline(event.target.value)} aria-label="Timeline">
            {timelines.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
                {item.forked_from_node_id ? " (branch)" : ""}
              </option>
            ))}
          </select>
          <span className="revision-mark">REV {inspect?.state.revision ?? 0}</span>
        </div>
        <div className="topbar-actions">
          <select
            value={selectedSceneId}
            onChange={(event) => chooseScene(event.target.value)}
            aria-label="Scene"
            disabled={scenes.length === 0}
          >
            {scenes.length === 0 && <option value="">No scenes</option>}
            {scenes
              .filter((item) => item.timeline_id === timelineId)
              .map((item) => (
                <option key={item.id} value={item.id}>
                  {item.title} · {item.status}
                </option>
              ))}
          </select>
          <button className="quiet-button" onClick={() => setShowState((value) => !value)}>
            {showState ? "Hide state" : "Inspect state"}
          </button>
          <button className="quiet-button" onClick={() => setShowLore((value) => !value)}>
            {showLore ? "Hide lore" : "Lore debug"}
          </button>
          <button
            className="quiet-button"
            onClick={() => setShowContext((value) => !value)}
            aria-pressed={showContext}
          >
            {showContext ? "Hide context" : "Context"}
          </button>
          <button className="quiet-button" onClick={() => void run(() => refresh())} disabled={busy}>
            Refresh
          </button>
        </div>
      </header>

      {failure && (
        <ErrorBanner
          message={failure.message}
          onRetry={failure.retry}
          onEdit={failure.edit}
          onCancel={failure.cancel}
          onCheckpoint={timelineId ? () => void forkFrom(latestNodeId) : null}
          onDismiss={() => setFailure(null)}
          busy={busy}
        />
      )}

      <div className="workspace-grid">
        <CastPanel
          characters={characters}
          selectedCast={selectedCast}
          controlModes={controlModes}
          onToggleCast={toggleCast}
          onSetControl={(actor, requestedMode) => void setControlMode(actor.character_id, actor.control_mode, requestedMode)}
          newCharacterName={newCharacterName}
          onNewCharacterNameChange={setNewCharacterName}
          onCreateCharacter={createCharacter}
          onPreviewFile={previewFile}
          pendingImport={pendingImport}
          onConfirmImport={() => void confirmImport()}
          onDiscardImport={() => setPendingImport(null)}
          importNotice={importNotice}
          lorebooks={lorebooks}
          busy={busy}
          hasScene={Boolean(scene)}
        />

        <main className="performance-panel panel">
          <div className="performance-header">
            <div>
              <div className="eyebrow">02 / PERFORMANCE</div>
              <h1>{scene?.title ?? "Untitled scene"}</h1>
            </div>
            {scene ? <span className={`status-pill ${scene.status}`}>{scene.status}</span> : <span className="status-pill">no scene</span>}
          </div>

          <StagingPanel
            scene={scene}
            proposal={proposal}
            actors={actors}
            busy={busy}
            onApprove={() => void approveScene()}
            onSave={(updates) => void saveStaging(updates)}
            onRegenerate={() => void regenerateStaging()}
            onCancel={() => void cancelScene()}
            onEnd={() => void endScene()}
            onRegeneratePerformance={() => void regeneratePerformance()}
          />

          <SessionBar
            session={session}
            plan={activePlan}
            busy={busy}
            pendingApproval={Boolean(pendingPlanId)}
            onContinue={() => void submitCommand()}
            onRegenerate={() => void regeneratePerformance()}
            onInterrupt={() => void interruptPlan()}
            onPossess={(characterId) => void takeControl(characterId)}
            onRelease={(characterId) => void releaseControl(characterId)}
            onApprove={() => void approvePendingPlan()}
            onCancelPlan={() => void cancelPendingPlan()}
          />

          <PerformanceStream
            views={performanceViews}
            emptyHint={
              canPerform
                ? "Submit a command or let the engine choose its first relevant actor."
                : blockedReason
            }
            onForkFromLatest={() => void forkFrom(latestNodeId)}
            canFork={Boolean(timelineId)}
          />

          <form className="stage-form" onSubmit={stageScene}>
            <div className="stage-form-label">STAGE A NEW PREMISE</div>
            <div className="stage-input-row">
              <input
                value={premise}
                onChange={(event) => setPremise(event.target.value)}
                placeholder="I want to assassinate the target in a library…"
              />
              <button className="primary-button" disabled={busy || !premise.trim()}>
                Stage scene
              </button>
            </div>
          </form>
        </main>

        <aside className={`inspector-panel panel ${showState ? "" : "collapsed"}`}>
          <div className="panel-heading">
            <div>
              <div className="eyebrow">03 / STATE</div>
              <h2>What is true</h2>
            </div>
            <span className="revision-badge">{inspect?.state.revision ?? 0}</span>
          </div>
          {showState ? (
            <div>
              <StateInspector
                inspect={inspect}
                scene={scene}
                recentEvents={recentViews}
                showLore={showLore}
                onToggleLore={() => setShowLore((value) => !value)}
              />
              {showContext && (
                <div className="context-panel">
                  <div className="panel-heading">
                    <div>
                      <div className="eyebrow">CONTEXT</div>
                      <h2>Why the model saw that</h2>
                    </div>
                  </div>
                  <ContextInspector
                    inspect={contextInspect}
                    generations={generations}
                    selectedTrace={selectedTrace}
                    memoryInspection={memoryInspection}
                    busy={busy}
                    onSelectTrace={(generationId) => void selectTrace(generationId)}
                    onRejectTrace={(generationId) => void rejectTrace(generationId)}
                    onAcceptWarning={acceptWarning}
                    onRegenerateWarning={() => void regeneratePerformance()}
                    onCorrectWarning={(factId) => void correctWarning(factId)}
                  />
                </div>
              )}
            </div>
          ) : (
            <div className="collapsed-note">State inspection is hidden.</div>
          )}
        </aside>
      </div>

      <TimelinePanel
        timeline={timeline}
        checkpoints={checkpointList}
        checkpointCount={checkpointList.length}
        latestNodeId={latestNodeId}
        selectedNodeId={selectedNodeId}
        onSelect={setSelectedNodeId}
        branchName={branchName}
        onBranchNameChange={setBranchName}
        onFork={() => void forkFrom(selectedNodeId ?? latestNodeId)}
        busy={busy}
        canFork={Boolean(timelineId) && (selectedNodeId ?? latestNodeId) !== null}
      />

      <CommandDock
        mode={mode}
        onModeChange={setMode}
        command={command}
        onCommandChange={setCommand}
        onSubmit={submitCommand}
        actors={actors}
        actorOverride={actorOverride}
        onActorOverrideChange={setActorOverride}
        possessedId={possessedId}
        onRelease={(characterId) => void releasePossession(characterId)}
        canSubmit={canSubmit}
        blockedReason={blockedReason}
        busy={busy}
      />
    </div>
  );
}
