import type { SUniversalPColumnId } from "@platforma-sdk/model";

/**
 * The types the block's init-params contract is built from.
 *
 * They live in the kind, not in the model, because the kind owns the contract:
 * `BlockParams` is what a project template serializes, and a second copy of these
 * shapes in the model would drift the moment one side gains a case the other
 * misses. The model re-exports them from here, so every existing import path
 * keeps working.
 */

/** Receptor type. Same enum as sequence-properties to keep label conventions aligned. */
export type WorkflowReceptor = "IG" | "TCRAB" | "TCRGD";

/**
 * A scope's receptor, or `"unknown"` when the producer declares VDJ data but no
 * receptor at all.
 *
 * `synthetic-repertoire-profiler` is the case: it declares `pl7.app/modality: vdj`
 * but emits neither `pl7.app/vdj/receptor` nor `pl7.app/vdj/chain` — germline
 * auto-detection builds a custom reference from the user's own parent sequences, so
 * there is no library locus to read a receptor from. Guessing `IG` there would
 * silently exclude the TCR specialists from a TCR dataset, so the unknown is carried
 * explicitly and `compat.ts` relaxes receptor/chain gating rather than filtering on
 * a value nobody supplied.
 *
 * Widening only — every previously persisted `WorkflowReceptor` stays valid, so
 * snapshotted scopes in existing projects deserialize unchanged.
 */
export type ScopeReceptor = WorkflowReceptor | "unknown";

/**
 * ESM-2 fidelity the user picks, projected into args. Default is `standard`.
 * `standard` → ESM-2 150M; `high` → ESM-2 650M. Only meaningful when the
 * model is ESM-2; ignored for the single-checkpoint specialists.
 */
export type Fidelity = "high" | "standard";

/**
 * User-facing embedding-model choice — the value of the model dropdown. A
 * logical id; the workflow maps it (plus `Fidelity` for ESM-2) to a concrete
 * checkpoint `ModelTag`. The catalog and scope↔model compatibility live in the
 * model package's `compat.ts`.
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
 * Embedding scope feature. `Fv` and `scFv` span/merge chains and carry no `chain`.
 *
 * `region` is any other amino-acid subsequence of a variant the input exports as
 * its own column.
 */
export type ScopeFeature = "peptide" | "CDR3" | "VDJRegion" | "Fv" | "scFv" | "region";

/**
 * One embedding scope the user can select. `columns` carries the workflow-
 * resolvable `SUniversalPColumnId`(s) of the sequence column(s) to embed — one
 * for single-chain scopes, two (`[VH, VL]`) for the paired Fv scope. The column
 * ids (and `isHeavy`/`receptor`) are snapshotted into `BlockData` on the user's
 * gesture (the anchored-id storage pattern), so the args lambda stays `data`-only.
 *
 * Those ids are anchored — they resolve against the block's own `inputAnchor`.
 * That is why a template that carries a scope must carry the matching
 * `inputAnchor` too; the two params travel as a pair or not at all.
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
   *
   * Always `false` when `receptor` is `"unknown"` — a producer that supplies no
   * receptor supplies no chain either, so this carries no information there and
   * `compat.ts` does not gate on it.
   */
  isHeavy: boolean;
  /** Receptor of the input this scope came from, snapshotted for `data`-only
   *  compatibility validation in the args lambda. `"unknown"` when the producer
   *  declares VDJ data without a receptor — see `ScopeReceptor`. */
  receptor: ScopeReceptor;
  /**
   * The source column's region feature value, verbatim from its spec (e.g. `FR2`,
   * `FR4InFrame`, a custom region name). Set exactly when `feature` is `region`.
   */
  region?: string;
};

/**
 * The single (sequence scope, model) selection the user assembles. `scope` and
 * `model` are each undefined until picked — the UI fills one and bidirectionally
 * filters the other. `fidelity` applies only when `model` is ESM-2. Always present
 * in `BlockData` (initialised to `{}`); the args lambda wraps it into the workflow's
 * 1-element task list, so the workflow's list contract is unchanged.
 */
export type EmbeddingSelection = {
  scope?: SelectedScope;
  model?: EmbeddingModelId;
  /** ESM-2 fidelity; ignored for other models. */
  fidelity?: Fidelity;
};
