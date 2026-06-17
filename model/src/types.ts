import type { PlRef, SUniversalPColumnId } from "@platforma-sdk/model";

/** Receptor type. Same enum as sequence-properties to keep label conventions aligned. */
export type WorkflowReceptor = "IG" | "TCRAB" | "TCRGD";

/**
 * Model fidelity the user picks, projected into args. Default is `standard`.
 * `standard` → ESM-2 150M; `high` → ESM-2 650M.
 */
export type Fidelity = "high" | "standard";

/**
 * Model tag emitted on the `pl7.app/embedding/model` domain of every output
 * column.
 */
export type ModelTag = "esm2-650M" | "esm2-150M";

/** Embedding scope feature. `Fv` and `scFv` span/merge chains and carry no `chain`. */
export type ScopeFeature = "peptide" | "CDR3" | "VDJRegion" | "Fv" | "scFv";

/**
 * One embedding scope the user can select. `columns` carries the workflow-
 * resolvable `SUniversalPColumnId`(s) of the sequence column(s) to embed — one
 * for single-chain scopes, two (`[VH, VL]`) for the paired Fv scope. The
 * column ids are snapshotted into `BlockData` on the user's gesture (the
 * anchored-id storage pattern), so the args lambda stays `data`-only.
 */
export type SelectedScope = {
  /** Stable picker key. The sequence column id for single scopes; `"Fv"` for paired Fv. */
  id: string;
  feature: ScopeFeature;
  chain: "A" | "B" | "";
  columns: SUniversalPColumnId[];
  // Display label, snapshotted from the picker option.
  label: string;
};

/** A selectable scope. Alias of `SelectedScope` — the label lives on the base type. */
export type AvailableScope = SelectedScope;

/**
 * Scope picker config, computed by the model from the connected input. `options`
 * feeds the multi-select; `defaults` is the first-connection selection. Bundled
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
 * V1 BlockData. Kept as the seed for the DataModelBuilder migration chain;
 * grows as the block evolves.
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
  selectedScopes: SelectedScope[];
  /**
   * Init-guard: canonical id of the anchor whose defaults were last applied to
   * `selectedScopes`. Prevents re-seeding defaults on panel reopen / server
   * patch, and triggers re-default + reconciliation on input change.
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

/** Current BlockData shape — alias of V1 for now; will diverge on next migration. */
export type BlockData = BlockDataV1;

/**
 * Workflow input shape. Args projection in `index.ts` builds this from
 * `BlockData`, validating that `inputAnchor` is set (throws otherwise — the
 * V3 idiom replaces V1's `.argsValid()`).
 */
export type BlockArgs = {
  inputAnchor: PlRef;
  fidelity: Fidelity;
  /** Scopes to emit, projected from `data.selectedScopes` (validated non-empty). */
  selectedScopes: SelectedScope[];
  /** Advanced resource overrides for the embedding step (GiB / cores); undefined → workflow defaults. */
  mem?: number;
  cpu?: number;
};
