import type { PlRef, SUniversalPColumnId } from "@platforma-sdk/model";

/** Receptor type. Same enum as sequence-properties to keep label conventions aligned. */
export type WorkflowReceptor = "IG" | "TCRAB" | "TCRGD";

/**
 * ESM-2 fidelity the user picks per card, projected into args. Default is `standard`.
 * `standard` → ESM-2 150M; `high` → ESM-2 650M. Only meaningful when the card's
 * model is ESM-2; ignored for the single-checkpoint specialists.
 */
export type Fidelity = "high" | "standard";

/**
 * User-facing embedding-model choice — the value of a card's model dropdown. A
 * logical id; the workflow maps it (plus `Fidelity` for ESM-2) to a concrete
 * checkpoint `ModelTag`. The catalog and scope↔model compatibility live in
 * `compat.ts`.
 */
export type EmbeddingModelId =
  | "esm2"
  | "ablang2"
  | "currab"
  | "vhhbert"
  | "h3berta"
  | "tcr-bert"
  | "peptideclm2"
  | "sceptr"; // pass 2 — gated off in compat.ts until its input path lands

/**
 * Checkpoint tag emitted on the `pl7.app/embedding/model` domain of every output
 * column (model provenance). ESM-2 splits by fidelity; specialists are 1:1 with
 * their `EmbeddingModelId`.
 */
export type ModelTag =
  | "esm2-650M"
  | "esm2-150M"
  | "ablang2"
  | "currab"
  | "vhhbert"
  | "h3berta"
  | "tcr-bert"
  | "peptideclm2"
  | "sceptr";

/** Embedding scope feature. `Fv` and `scFv` span/merge chains and carry no `chain`. */
export type ScopeFeature = "peptide" | "CDR3" | "VDJRegion" | "Fv" | "scFv";

/**
 * One embedding scope the user can select. `columns` carries the workflow-
 * resolvable `SUniversalPColumnId`(s) of the sequence column(s) to embed — one
 * for single-chain scopes, two (`[VH, VL]`) for the paired Fv scope. The column
 * ids (and `isHeavy`/`receptor`) are snapshotted into `BlockData` on the user's
 * gesture (the anchored-id storage pattern), so the args lambda stays `data`-only.
 */
export type SelectedScope = {
  /** Stable picker key. The sequence column id for single scopes; `"Fv"` for paired Fv. */
  id: string;
  feature: ScopeFeature;
  chain: "A" | "B" | "";
  columns: SUniversalPColumnId[];
  // Display label, snapshotted from the picker option.
  label: string;
  /**
   * True when this is an IG heavy chain (single-cell chain `A`, or bulk
   * `IGHeavy`). Gates the heavy-only specialists (VHHBERT, H3BERTa) and the
   * VHH-vs-mAb default. Snapshotted so the args lambda stays `data`-only.
   */
  isHeavy: boolean;
  /** Receptor of the input this scope came from, snapshotted for `data`-only
   *  compatibility validation in the args lambda. */
  receptor: WorkflowReceptor;
};

/** A selectable scope. Alias of `SelectedScope` — the label lives on the base type. */
export type AvailableScope = SelectedScope;

/**
 * Frozen V1 scope shape (before per-card models). Kept only as the migration
 * source type so `BlockDataV1` stays historically accurate; new code uses
 * `SelectedScope`.
 */
export type SelectedScopeV1 = {
  id: string;
  feature: ScopeFeature;
  chain: "A" | "B" | "";
  columns: SUniversalPColumnId[];
  label: string;
};

/**
 * Scope picker config, computed by the model from the connected input. `options`
 * feeds the sequence dropdown; `defaults` is the first-connection seed. Bundled
 * so the UI never sees options without their defaults.
 */
export type ScopeConfig = {
  options: AvailableScope[];
  defaults: SelectedScope[];
  /**
   * Canonical key (`JSON.stringify`) of the anchor this config was computed for.
   * The UI's seed watcher gates on it so a retained (stale) config from the
   * previous input is never applied to the newly selected one.
   */
  forAnchor: string;
  /** Input receptor — feeds the UI's model-compatibility filtering (`compat.ts`). */
  receptor: WorkflowReceptor;
  /**
   * True when the IG input is conventional paired antibody (a light chain or Fv
   * is present), false for a heavy-only (nanobody-like) dataset. Drives the
   * VHHBERT-vs-CurrAb default in `recommendedModel`.
   */
  paired: boolean;
};

/**
 * One embedding card in the settings list: a (sequence scope, model) task the
 * user assembles. `scope` and `model` are each undefined until picked — the UI
 * fills one and bidirectionally filters the other. `fidelity` applies only when
 * `model` is ESM-2.
 */
export type EmbeddingCard = {
  /** Stable card key for the `PlElementList` (`get-item-key`). */
  id: string;
  scope?: SelectedScope;
  model?: EmbeddingModelId;
  /** ESM-2 fidelity; ignored for other models. */
  fidelity?: Fidelity;
  /** UI: card expanded in the list. */
  isExpanded?: boolean;
};

/**
 * Per-scope record in the Python step's `stats.json` output — one entry per
 * selected scope. `n_entities = 0` marks an empty scope.
 */
export type WorkflowScopeStats = {
  name: string;
  feature: "peptide" | "CDR3" | "VDJRegion" | "Fv" | "scFv";
  chain: "A" | "B" | "";
  /** Human-readable picker label (e.g. "Heavy CDR3 aa Primary") for the UI report. */
  label: string;
  model: ModelTag;
  /**
   * Per-scope counts. Optional: in batch mode they are aggregated post-run from the
   * batched outputs (the report step). Absent until then; the UI degrades gracefully.
   */
  n_entities?: number;
  /** Clones dropped before inference (empty or partial sequence for this scope). */
  n_dropped_empty?: number;
  /** Sequences truncated from the C-terminus for exceeding the token limit. */
  n_truncated?: number;
};

/**
 * Workflow run-summary shape. Consumed by the model layer to drive the "what was
 * computed" summary on the block UI and to surface device routing decisions.
 */
export type WorkflowStats = {
  device_used: "cpu" | "gpu";
  /** The single model loaded for the run (chosen by device tier). */
  model: ModelTag;
  /**
   * Truncation limit in tokens (max residues = max_length − 2). Optional: added
   * with the report step, sourced from the Python step that enforces it; the UI
   * falls back to the ESM-2 default (1024) until then.
   */
  max_length?: number;
  scopes: WorkflowScopeStats[];
};

/**
 * V1 BlockData. Frozen migration source: one universal model (ESM-2) picked via a
 * global `fidelity`, with a flat multi-select of scopes.
 */
export type BlockDataV1 = {
  inputAnchor?: PlRef;
  /** Model fidelity (Advanced Settings → Model fidelity). high|standard. */
  fidelity: Fidelity;
  /**
   * User-selected scopes to embed. Snapshotted from `availableScopes` on
   * the picker gesture so the args lambda stays `data`-only. Empty until the
   * first input connection initializes it.
   */
  selectedScopes: SelectedScopeV1[];
  /**
   * Init-guard: canonical id of the anchor whose defaults were last applied to
   * `selectedScopes`.
   */
  scopesInitializedForAnchor?: string;
  /**
   * Advanced — host resources for the embedding step (Resource Allocation in
   * Advanced Settings). `mem` is GiB, `cpu` is cores. Undefined → workflow
   * defaults (32 GiB / 16 cores).
   */
  mem?: number;
  cpu?: number;
  defaultBlockLabel?: string;
};

/**
 * V2 BlockData. Model selection moves per-scope: the global `fidelity` + flat
 * `selectedScopes` become a list of embedding cards, each a (scope, model,
 * fidelity?) task. Enables specialist models and same-scope model comparison.
 */
export type BlockDataV2 = {
  inputAnchor?: PlRef;
  /** The embedding cards (scope × model tasks) the user has assembled. */
  embeddings: EmbeddingCard[];
  /**
   * Init-guard: canonical id of the anchor whose default cards were last seeded.
   * Prevents re-seeding on panel reopen / server patch; triggers re-seed +
   * reconciliation on input change.
   */
  embeddingsInitializedForAnchor?: string;
  /** Advanced resource overrides (GiB / cores); undefined → workflow defaults. */
  mem?: number;
  cpu?: number;
  defaultBlockLabel?: string;
};

/** Current BlockData shape. */
export type BlockData = BlockDataV2;

/**
 * One embedding task in the workflow input: a scope plus the model to embed it
 * with (and ESM-2 fidelity, when applicable). Projected from `BlockData.embeddings`.
 */
export type EmbeddingTask = {
  scope: SelectedScope;
  model: EmbeddingModelId;
  /** ESM-2 fidelity; undefined for other models. */
  fidelity?: Fidelity;
};

/**
 * Workflow input shape. Args projection in `index.ts` builds this from
 * `BlockData`, validating that `inputAnchor` is set and every card is a complete,
 * compatible (scope, model) pair (throws otherwise — the V3 idiom replaces V1's
 * `.argsValid()`).
 */
export type BlockArgs = {
  inputAnchor: PlRef;
  /** Tasks to emit, projected from `data.embeddings` (validated complete + compatible). */
  embeddings: EmbeddingTask[];
  /** Advanced resource overrides for the embedding step (GiB / cores); undefined → workflow defaults. */
  mem?: number;
  cpu?: number;
};
