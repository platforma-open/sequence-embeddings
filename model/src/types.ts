import type { PlRef, SUniversalPColumnId } from "@platforma-sdk/model";

/**
 * Workflow modality, mirrored from `workflow/src/main.tpl.tengo` `detectMode`.
 * Used in workflow stats output and for UI affordances that depend on the
 * detected modality.
 */
export type WorkflowMode =
  | "peptide"
  | "antibody_tcr_universal"
  | "antibody_tcr_legacy_bulk"
  | "antibody_tcr_legacy_sc";

/** Receptor type. Same enum as sequence-properties to keep label conventions aligned. */
export type WorkflowReceptor = "IG" | "TCRAB" | "TCRGD";

/** Device-mode preference set on the BlockData and projected into args. */
export type DeviceMode = "auto" | "cpu" | "gpu";

/**
 * Model tag emitted on the `pl7.app/embedding/model` domain of every output
 * column. Permitted v1 values per spec slice 01 R13 (GPU → esm2-650M,
 * CPU → esm2-150M). Forward-compatible with v2 additions (e.g.
 * `"esm2-150M-int8"` if the ONNX path lands, `"sceptr"`, `"esmc"`).
 */
export type ModelTag = "esm2-650M" | "esm2-150M";

/** Embedding scope feature. `Fv` and `scFv` span/merge chains and carry no `chain`. */
export type ScopeFeature = "peptide" | "CDR3" | "VDJRegion" | "Fv" | "scFv";

/**
 * One embedding scope the user can select. `columns` carries the workflow-
 * resolvable `SUniversalPColumnId`(s) of the sequence column(s) to embed — one
 * for single-chain scopes, two (`[VH, VL]`) for the paired Fv scope (R10). The
 * column ids are snapshotted into `BlockData` on the user's gesture (the
 * anchored-id storage pattern), so the args lambda stays `data`-only.
 */
export type SelectedScope = {
  /** Stable picker key. The sequence column id for single scopes; `"Fv"` for paired Fv. */
  id: string;
  feature: ScopeFeature;
  chain: "A" | "B" | "";
  columns: SUniversalPColumnId[];
};

/** A selectable scope plus its UI label. Returned by the `availableScopes` output. */
export type AvailableScope = SelectedScope & { label: string };

/**
 * Scope picker config, computed by the model from the connected input. `options`
 * feeds the multi-select; `defaults` is the first-connection selection (Default
 * Selection Rule). Bundled so the UI never sees options without their defaults.
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
 * Per-scope record in the Python step's `stats.json` output. One entry per
 * scope the workflow asked to compute, regardless of whether the inference
 * succeeded — `n_entities = 0` and `tsv_file = null` mark empty / skipped
 * scopes; `errors[]` (top-level) carries per-scope failures.
 */
export type WorkflowScopeStats = {
  name: string;
  feature: "peptide" | "CDR3" | "VDJRegion" | "Fv" | "scFv";
  chain: "A" | "B" | "";
  model: ModelTag;
  n_entities: number;
  embedding_dim: number;
  /** Clones dropped before inference (empty or partial sequence for this scope) — R5/R16. */
  n_dropped_empty: number;
  /** Sequences truncated from the C-terminus for exceeding the token limit. */
  n_truncated: number;
  tsv_file: string | null;
};

/**
 * Python step's `stats.json` shape. Consumed by the model layer to drive the
 * "what was computed" summary on the block UI and to surface device routing
 * decisions.
 */
export type WorkflowStats = {
  device_requested: DeviceMode;
  device_used: "cpu" | "gpu";
  /** torch dtype actually used: "float16" on CUDA, "float32" otherwise. */
  dtype: string;
  /** The single model loaded for the run (chosen by device tier). */
  model: ModelTag;
  /** Embedding dimension D of the loaded model (single-chain scopes). */
  embedding_dim: number;
  mode: WorkflowMode;
  receptor: WorkflowReceptor | "";
  max_length: number;
  token_budget: number;
  scopes: WorkflowScopeStats[];
  errors: { scope: string; error: string }[];
};

/**
 * V1 BlockData. Kept as the seed for the DataModelBuilder migration chain;
 * grows as the block evolves. `defaultBlockLabel` mirrors the sequence-
 * properties pattern (a UI-only string the watcher fills in from the selected
 * input dataset's label).
 */
export type BlockDataV1 = {
  inputAnchor?: PlRef;
  device: DeviceMode;
  /**
   * User-selected scopes to embed (R6). Snapshotted from `availableScopes` on
   * the picker gesture so the args lambda stays `data`-only. Empty until the
   * first input connection initializes it via the Default Selection Rule.
   */
  selectedScopes: SelectedScope[];
  /**
   * Init-guard: canonical id of the anchor whose defaults were last applied to
   * `selectedScopes`. Prevents re-seeding defaults on panel reopen / server
   * patch, and triggers re-default + reconciliation (R6c) on input change.
   */
  scopesInitializedForAnchor?: string;
  /**
   * Advanced — host resources for the embedding step (Resource Allocation in
   * Advanced Settings). `mem` is GiB, `cpu` is cores. Undefined → workflow
   * defaults (32 GiB / 16 cores). R21.
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
  device: DeviceMode;
  /** Scopes to emit, projected from `data.selectedScopes` (validated non-empty, R6b). */
  selectedScopes: SelectedScope[];
  /** Advanced resource overrides for the embedding step (GiB / cores); undefined → workflow defaults. */
  mem?: number;
  cpu?: number;
};
