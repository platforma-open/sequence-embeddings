/**
 * Embedding-model catalog and scope↔model compatibility (model side).
 *
 * Single source of truth for three things:
 *  - which models exist and how they consume a scope (`EMBEDDING_MODELS`),
 *  - which models can embed a given scope (`compatibleModels`),
 *  - the specialist-first default model for a scope (`recommendedModel`).
 *
 * The per-card UI dropdowns (bidirectional filtering) and the args validator both
 * read from here, so the offered options and the validated pairs cannot drift.
 *
 * Rollout: pass 1 ships the models whose input is the AA sequence column already
 * gathered per scope (plus PeptideCLM-2, which converts AA→SMILES in the Python
 * step). SCEPTR is pass 2 — it needs paired V-gene + CDR1/2/3 assembly. A model is
 * only offered once it is **fully wired end-to-end** (weight asset packaged +
 * workflow routing); see `ENABLED_MODELS`.
 */
import type { EmbeddingModelId, ModelTag, ScopeFeature, WorkflowReceptor } from "./types";

/**
 * How a model consumes the scope's sequence. Drives the Python step's per-model
 * input preparation:
 *  - `aa`               — the AA sequence column as-is (most models).
 *  - `aa-cdr3-trimmed`  — CDR3 AA with the conserved flanking C/W stripped (H3BERTa).
 *  - `aa-spaced`        — residues space-separated, e.g. "C A S S" (TCR-BERT).
 *  - `smiles`           — AA converted to a SMILES string (PeptideCLM-2).
 *  - `paired-structured`— paired V-gene symbols + CDR1/2/3 loops (SCEPTR, pass 2).
 *
 * Mirror of the workflow's per-tag `inputKind` (compute-embeddings.tpl.tengo); the
 * two must agree. The workflow value is the one actually passed to the Python step.
 */
export type ModelInputKind =
  | "aa"
  | "aa-cdr3-trimmed"
  | "aa-spaced"
  | "smiles"
  | "paired-structured";

export type EmbeddingModelSpec = {
  id: EmbeddingModelId;
  /** Dropdown label. */
  label: string;
  inputKind: ModelInputKind;
  /** Receptors this model serves. Ignored for the `peptide` feature (peptide
   *  inputs carry no receptor); gated on for VDJ features. */
  receptors: readonly WorkflowReceptor[];
  /** Scope features this model can embed. */
  features: readonly ScopeFeature[];
  /** Heavy-chain-only specialist (VHHBERT, H3BERTa): only embeds IG heavy scopes
   *  (`SelectedScope.isHeavy`). Omitted/false = any chain. */
  heavyOnly?: boolean;
  /** Embedding dimension (fixed per model; ESM-2 varies by fidelity). */
  dim: number | "varies";
  /** true = shipped as a downloaded weight asset; false = pip dep in the runenv. */
  asset: boolean;
  /** Rollout pass (informational): 1 = AA-sequence models, 2 = SCEPTR. */
  pass: 1 | 2;
  /** Default-selection priority among compatible models (higher wins). ESM-2 is
   *  0 so any specialist beats it; the VHH-vs-mAb tiebreak is applied in
   *  `recommendedModel`, not via priority. */
  priority: number;
};

/**
 * Models fully wired end-to-end (weight asset packaged + workflow routing + input
 * prep). Wired so far: ESM-2, CurrAb, VHHBERT, H3BERTa and TCR-BERT (all HF
 * checkpoints via the shared `AutoModel` loader), PeptideCLM-2 (HF custom model via
 * trust_remote_code) and AbLang2 (the `ablang2` pip model with asset-shipped
 * weights). Input prep per model: AA as-is (ESM-2/CurrAb/VHHBERT/AbLang2), CDR3
 * flank-trim (H3BERTa, `aa-cdr3-trimmed`), space-separated residues (TCR-BERT,
 * `aa-spaced`), or AA→SMILES (PeptideCLM-2, `smiles`) — all applied in the Python
 * step. Only SCEPTR (pass 2) remains: it needs paired V-gene + CDR assembly the
 * block does not yet gather. Add a model's id here as its asset/runenv/routing
 * lands — the UI and matrix pick it up automatically.
 */
const ENABLED_MODELS = new Set<EmbeddingModelId>([
  "esm2",
  "currab",
  "vhhbert",
  "h3berta",
  "tcr-bert",
  "peptideclm2",
  "ablang2",
]);

export const EMBEDDING_MODELS: Record<EmbeddingModelId, EmbeddingModelSpec> = {
  esm2: {
    id: "esm2",
    label: "ESM-2 (universal)",
    inputKind: "aa",
    receptors: ["IG", "TCRAB", "TCRGD"],
    features: ["peptide", "CDR3", "VDJRegion", "Fv", "scFv"],
    dim: "varies", // 640 (150M) / 1280 (650M) by fidelity
    asset: true,
    pass: 1,
    priority: 0, // universal fallback — always beaten by a compatible specialist
  },
  currab: {
    id: "currab",
    label: "CurrAb (antibody)",
    inputKind: "aa",
    receptors: ["IG"],
    features: ["VDJRegion", "Fv"],
    dim: 1280,
    asset: true,
    pass: 1,
    priority: 50,
  },
  ablang2: {
    id: "ablang2",
    label: "AbLang2 (antibody)",
    inputKind: "aa",
    receptors: ["IG"],
    features: ["VDJRegion", "Fv"],
    dim: 480,
    asset: false, // pip dep (custom ablang2 package)
    pass: 1,
    priority: 40,
  },
  vhhbert: {
    id: "vhhbert",
    label: "VHHBERT (nanobody)",
    inputKind: "aa",
    receptors: ["IG"],
    features: ["VDJRegion"],
    heavyOnly: true,
    dim: 768,
    asset: true,
    pass: 1,
    priority: 45,
  },
  h3berta: {
    id: "h3berta",
    label: "H3BERTa (CDR-H3)",
    inputKind: "aa-cdr3-trimmed",
    receptors: ["IG"],
    features: ["CDR3"],
    heavyOnly: true,
    dim: 768,
    asset: true,
    pass: 1,
    priority: 50,
  },
  "tcr-bert": {
    id: "tcr-bert",
    label: "TCR-BERT (CDR3)",
    inputKind: "aa-spaced",
    receptors: ["TCRAB"],
    features: ["CDR3"],
    dim: 768,
    asset: true,
    pass: 1,
    priority: 50,
  },
  peptideclm2: {
    id: "peptideclm2",
    label: "PeptideCLM-2 (peptide)",
    inputKind: "smiles",
    receptors: ["IG", "TCRAB", "TCRGD"], // ignored — peptide feature carries no receptor
    features: ["peptide"],
    dim: 1024,
    asset: true,
    pass: 1,
    priority: 50, // peptide default, per product decision (re-evaluate after testing)
  },
  sceptr: {
    id: "sceptr",
    label: "SCEPTR (paired αβ)",
    inputKind: "paired-structured",
    receptors: ["TCRAB"],
    features: ["VDJRegion", "Fv"],
    dim: 64,
    asset: false, // pip dep (libtcrlm + tidytcells)
    pass: 2, // gated off until the paired V-gene+CDR assembly path lands
    priority: 60,
  },
};

/**
 * Display label for a checkpoint `ModelTag` — the model-provenance value carried
 * on each output column and each run-report row. Shorter than the dropdown labels
 * in `EMBEDDING_MODELS` (which append the modality, e.g. "(antibody)"): the report
 * already shows the embedded sequence/region in its own column, so the model
 * column only needs the model's name. ESM-2 splits by fidelity so its two tags get
 * distinct labels; the specialists are 1:1 with their `EmbeddingModelId`.
 */
export const MODEL_TAG_LABELS: Record<ModelTag, string> = {
  "esm2-650M": "ESM-2 (650M)",
  "esm2-150M": "ESM-2 (150M)",
  ablang2: "AbLang2",
  currab: "CurrAb",
  vhhbert: "VHHBERT",
  h3berta: "H3BERTa",
  "tcr-bert": "TCR-BERT",
  peptideclm2: "PeptideCLM-2",
  sceptr: "SCEPTR",
};

/** Display label for a checkpoint `ModelTag`; falls back to the raw tag for any
 *  tag not in the map (forward-compat if a new checkpoint ships before its label). */
export function modelTagLabel(tag: string): string {
  return MODEL_TAG_LABELS[tag as ModelTag] ?? tag;
}

/** Context the recommendation needs beyond the scope itself, derived from the
 *  full set of available scopes for the connected input. */
export type ScopeContext = {
  receptor: WorkflowReceptor;
  /** True when the IG input is conventional paired antibody (light chain or Fv
   *  present) rather than a heavy-only (nanobody-like) dataset. Drives the
   *  VHHBERT-vs-CurrAb default. */
  paired: boolean;
};

/** Does `spec` accept a scope with this (feature, isHeavy) under this receptor? */
function modelSupports(
  spec: EmbeddingModelSpec,
  feature: ScopeFeature,
  isHeavy: boolean,
  receptor: WorkflowReceptor,
): boolean {
  if (!ENABLED_MODELS.has(spec.id)) return false;
  if (!spec.features.includes(feature)) return false;
  // Peptide inputs carry no receptor — gate on receptor only for VDJ features.
  if (feature !== "peptide" && !spec.receptors.includes(receptor)) return false;
  if (spec.heavyOnly && !isHeavy) return false;
  return true;
}

/** Models that can embed this scope, highest-priority first. ESM-2 is always
 *  present (universal), so the list is never empty. */
export function compatibleModels(
  feature: ScopeFeature,
  isHeavy: boolean,
  receptor: WorkflowReceptor,
): EmbeddingModelId[] {
  return (Object.keys(EMBEDDING_MODELS) as EmbeddingModelId[])
    .filter((id) => modelSupports(EMBEDDING_MODELS[id], feature, isHeavy, receptor))
    .sort((a, b) => EMBEDDING_MODELS[b].priority - EMBEDDING_MODELS[a].priority);
}

/** Scopes (from the available set) this model can embed — for model-first
 *  filtering of the sequence dropdown. */
export function compatibleScopes<T extends { feature: ScopeFeature; isHeavy: boolean }>(
  model: EmbeddingModelId,
  scopes: T[],
  receptor: WorkflowReceptor,
): T[] {
  const spec = EMBEDDING_MODELS[model];
  return scopes.filter((s) => modelSupports(spec, s.feature, s.isHeavy, receptor));
}

/** True if a (scope, model) pair is valid — used by the args validator. */
export function isCompatible(
  feature: ScopeFeature,
  isHeavy: boolean,
  receptor: WorkflowReceptor,
  model: EmbeddingModelId,
): boolean {
  return modelSupports(EMBEDDING_MODELS[model], feature, isHeavy, receptor);
}

/** Specialist-first default model for a scope. Highest-priority compatible
 *  model, with the VHH tiebreak: for a heavy-chain full-domain antibody scope on
 *  a heavy-only (unpaired) dataset, prefer the nanobody specialist VHHBERT over
 *  the mAb specialist CurrAb. (No nanobody signal exists in the data, so this
 *  heavy-only heuristic is the best available — see module note / revisit.) */
export function recommendedModel(
  feature: ScopeFeature,
  isHeavy: boolean,
  ctx: ScopeContext,
): EmbeddingModelId {
  const compatible = compatibleModels(feature, isHeavy, ctx.receptor);
  if (
    feature === "VDJRegion" &&
    ctx.receptor === "IG" &&
    isHeavy &&
    !ctx.paired &&
    compatible.includes("vhhbert")
  ) {
    return "vhhbert";
  }
  return compatible[0] ?? "esm2";
}
