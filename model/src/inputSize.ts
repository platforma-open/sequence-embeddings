/**
 * Pre-run input-size gate.
 *
 * The workflow writes the embedding as a long-format table (one row per
 * clonotype × embedding dimension). The SDK concatenates every batch into one
 * file and imports it with ptabler, whose polars build indexes rows with u32: a
 * table above 2^32 − 1 rows fails at that final import, after every batch has
 * already been embedded. The gate keeps Run disabled for input + model
 * combinations that would cross that limit.
 *
 * The clonotype count comes from the `inputSize` model output (Parquet chunk
 * metadata, no download). V3 `.args()` sees only `data`, so the UI mirrors that
 * output into `data.inputSizeCheck`; the stored result is stamped with the
 * input it was computed for, and `.args()` only trusts it while that stamp
 * matches the live selection.
 */
import type { EmbeddingModelId, EmbeddingSelection, Fidelity, ModelTag } from "./types";
import type { PlRef, SUniversalPColumnId } from "@platforma-sdk/model";

/** Polars u32 row-index limit (`pow(2, 32)` rows is already rejected). */
export const MAX_TABLE_ROWS = 2 ** 32 - 1;

/**
 * Base embedding width per checkpoint. Mirrors `dim` in
 * `workflow/src/models.lib.tengo` — keep the two in sync.
 */
const MODEL_TAG_DIMS: Partial<Record<ModelTag, number>> = {
  "esm2-650M": 1280,
  "esm2-150M": 640,
  currab: 1280,
  vhhbert: 768,
  h3berta: 768,
  "tcr-bert": 768,
  peptideclm2: 1024,
  ablang2: 480,
};

/** Checkpoints that embed a paired Fv in one pass (`pairMode: "joint"` in
 *  `models.lib.tengo`); every other model concatenates VH⊕VL. */
const JOINT_PAIR_TAGS = new Set<ModelTag>(["currab", "ablang2"]);

/** User-facing model (+ ESM-2 fidelity) → checkpoint tag. Mirrors
 *  `resolveModelTag` in `models.lib.tengo`. */
export function resolveModelTag(model: EmbeddingModelId, fidelity?: Fidelity): ModelTag {
  if (model === "esm2") return fidelity === "high" ? "esm2-650M" : "esm2-150M";
  return model;
}

/**
 * Output vector length for a selection: the base width, doubled for a paired Fv
 * scope embedded by a concat model. Mirrors `outputVectorLength` in
 * `models.lib.tengo`. undefined for an incomplete selection or unmapped model.
 */
export function outputVectorLength(sel: EmbeddingSelection): number | undefined {
  if (sel.scope === undefined || sel.model === undefined) return undefined;
  const tag = resolveModelTag(sel.model, sel.fidelity);
  const dim = MODEL_TAG_DIMS[tag];
  if (dim === undefined) return undefined;
  return sel.scope.feature === "Fv" && !JOINT_PAIR_TAGS.has(tag) ? dim * 2 : dim;
}

/** Largest clonotype count a selection can embed in one run. */
export function maxInputSequences(sel: EmbeddingSelection): number | undefined {
  const len = outputVectorLength(sel);
  return len === undefined ? undefined : Math.floor(MAX_TABLE_ROWS / len);
}

/**
 * Size-check result, stamped with the input it was computed for. `rows` is the
 * largest row count among the scope's sequence columns (each keyed by the
 * clonotype axis alone, so rows = clonotypes); `null` = the data is ready but
 * its row count can't be read (non-Parquet storage or missing chunk stats).
 */
export type InputSizeCheck = {
  inputKey: string;
  rows: number | null;
};

/**
 * Fingerprint of the selections that determine the counted rows: input anchor
 * and the scope's sequence columns. Model and fidelity are excluded — they only
 * change the limit, which `.args()` derives from `data` directly. The anchor is
 * reduced to explicit fields because persisted `data` comes back with object
 * keys reordered, which would break a `JSON.stringify` of the whole ref.
 */
export function inputSizeKey(
  inputAnchor: PlRef | undefined,
  columns: SUniversalPColumnId[] | undefined,
): string | undefined {
  if (inputAnchor === undefined || columns === undefined) return undefined;
  return JSON.stringify({ anchor: [inputAnchor.blockId, inputAnchor.name], columns });
}

/**
 * Run-gate state for the current selection:
 *  - `checking`: no size result for this exact input yet,
 *  - `unknown`: the row count can't be read — Run stays blocked,
 *  - `tooLarge`: rows × vector length exceeds the table limit,
 *  - `ok`: within the limit.
 */
export type InputSizeStatus =
  | { kind: "checking" }
  | { kind: "unknown" }
  | { kind: "tooLarge"; rows: number; max: number }
  | { kind: "ok"; rows: number; max: number };

export function inputSizeStatus(
  inputAnchor: PlRef | undefined,
  sel: EmbeddingSelection,
  check: InputSizeCheck | undefined,
): InputSizeStatus {
  const key = inputSizeKey(inputAnchor, sel.scope?.columns);
  if (key === undefined || check === undefined || check.inputKey !== key) {
    return { kind: "checking" };
  }
  if (check.rows === null) return { kind: "unknown" };
  const max = maxInputSequences(sel);
  // No mapped width (model not wired yet) → nothing to enforce.
  if (max === undefined) return { kind: "ok", rows: check.rows, max: Infinity };
  return check.rows > max
    ? { kind: "tooLarge", rows: check.rows, max }
    : { kind: "ok", rows: check.rows, max };
}
