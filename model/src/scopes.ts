/**
 * Scope detection for the embedding picker (model side).
 *
 * Pure functions over the sequence columns discovered under the input anchor.
 * `index.ts` fetches the columns (`getUniversalEntries` with
 * `labelOps.includeNativeLabel`, which yields `{ id, spec, label }` entries — the
 * id is workflow-resolvable and storable, the label is the column's own native
 * label) and calls `buildScopeConfig` to turn them into the
 * `availableScopes` picker config.
 *
 * Each single-chain scope is one column; the paired Fv scope is the two
 * per-chain VDJRegion column ids (`[VH, VL]`), embedded separately and vector-
 * concatenated by the Python step.
 *
 * VDJRegion/Fv are offered only when a native VDJRegion column is present
 * (MiXCR assembled on VDJRegion). CDR3-assembled inputs expose CDR3 only — a
 * reconstructed full chain would be germline-dominated (TCR) or miss somatic
 * hypermutation (BCR), so it is deliberately not synthesized.
 *
 * Each scope carries `isHeavy` (IG heavy chain — single-cell chain A or bulk
 * IGHeavy) so the heavy-only specialists (VHHBERT, H3BERTa) and the VHH-vs-mAb
 * default can be resolved from the scope alone (see `compat.ts`).
 */
import type {
  AnchoredPColumnSelector,
  PColumnSpec,
  SUniversalPColumnId,
} from "@platforma-sdk/model";
import type { AvailableScope, ScopeConfig, SelectedScope, WorkflowReceptor } from "./types";

/** A discovered sequence column: its workflow-resolvable id, spec, and the
 *  label derived for it (native label forced via includeNativeLabel upstream). */
export type SeqEntry = { id: SUniversalPColumnId; spec: PColumnSpec; label: string };

/**
 * Selectors for candidate sequence columns under the `main` anchor's key axis.
 * Peptide-family inputs match the first two (`pl7.app/sequence` with a `peptide`
 * or `amplicon-sequence` feature — the latter is the synthetic-repertoire-profiler's
 * whole-variant sequence); antibody/TCR inputs match the VDJ ones. A selector's
 * domain is a single fixed map and can't OR two feature values, so the two
 * peptide-family features need two entries. All filtered on amino-acid alphabet —
 * embeddings read AA sequence.
 */
export const SEQUENCE_SELECTORS: AnchoredPColumnSelector[] = [
  {
    axes: [{ anchor: "main", idx: 1 }],
    name: "pl7.app/sequence",
    domain: { "pl7.app/feature": "peptide", "pl7.app/alphabet": "aminoacid" },
  },
  {
    axes: [{ anchor: "main", idx: 1 }],
    name: "pl7.app/sequence",
    // synthetic-repertoire-profiler tags its whole-variant AA sequence with the
    // generic `amplicon-sequence` feature (shared with other consumers), not
    // `peptide`; treat it as a peptide-family scope so it becomes embeddable.
    domain: { "pl7.app/feature": "amplicon-sequence", "pl7.app/alphabet": "aminoacid" },
  },
  {
    axes: [{ anchor: "main", idx: 1 }],
    name: "pl7.app/vdj/sequence",
    domain: { "pl7.app/alphabet": "aminoacid" },
  },
  {
    axes: [{ anchor: "main", idx: 1 }],
    name: "pl7.app/vdj/scFv-sequence",
    domain: { "pl7.app/alphabet": "aminoacid" },
  },
];

/** Bulk MiXCR chain enum → receptor. Mirrors sequence-properties. */
const CHAIN_TO_RECEPTOR: Record<string, WorkflowReceptor> = {
  IGHeavy: "IG",
  IGLight: "IG",
  IGKappa: "IG",
  IGLambda: "IG",
  TCRAlpha: "TCRAB",
  TCRBeta: "TCRAB",
  TCRGamma: "TCRGD",
  TCRDelta: "TCRGD",
};

/**
 * Resolve receptor from a domain record: explicit `pl7.app/vdj/receptor` wins,
 * then derive from `pl7.app/vdj/chain`, else default IG.
 */
export function resolveReceptor(domain: Record<string, string> | undefined): WorkflowReceptor {
  const r = domain?.["pl7.app/vdj/receptor"];
  if (r === "IG" || r === "TCRAB" || r === "TCRGD") return r;
  const chain = domain?.["pl7.app/vdj/chain"];
  return (chain && CHAIN_TO_RECEPTOR[chain]) || "IG";
}

function isAssembling(spec: PColumnSpec): boolean {
  const a = spec.annotations ?? {};
  return (
    a["pl7.app/vdj/isAssemblingFeature"] === "true" || a["pl7.app/isAssemblingFeature"] === "true"
  );
}

/**
 * Whether a VDJ scope is an IG heavy chain. Single-cell scopes carry the chain
 * (`A` = heavy / `B` = light); bulk scopes carry `""` and put heavy/light on the
 * input axis as `pl7.app/vdj/chain` (passed in as `bulkChain`).
 */
function deriveIsHeavy(
  receptor: WorkflowReceptor,
  chain: "A" | "B" | "",
  bulkChain: string | undefined,
): boolean {
  if (receptor !== "IG") return false;
  if (chain === "A") return true; // single-cell heavy
  if (chain === "B") return false; // single-cell light
  return bulkChain === "IGHeavy"; // bulk single-chain
}

/**
 * Build the scope picker config (options + first-connection defaults) from the
 * discovered sequence columns and the resolved receptor. Each entry carries its
 * own derived `label` (taken verbatim); the synthetic Paired Fv option uses this
 * block's own label. `bulkChain` is the input-axis `pl7.app/vdj/chain` (bulk
 * inputs only), used to resolve heavy/light when scopes carry no per-chain key.
 */
export function buildScopeConfig(
  entries: SeqEntry[],
  receptor: WorkflowReceptor,
  bulkChain?: string,
): Omit<ScopeConfig, "forAnchor"> {
  type Internal = AvailableScope & { assembling: boolean };
  const scopes: Internal[] = [];
  const vdjByChain: Partial<Record<"A" | "B", SUniversalPColumnId>> = {};
  let scFvPresent = false;

  for (const e of entries) {
    const name = e.spec.name;
    const d = e.spec.domain ?? {};
    const assembling = isAssembling(e.spec);

    // `peptide` (peptide-profiling) and `amplicon-sequence`
    // (synthetic-repertoire-profiler whole-variant sequence) both map to the
    // peptide scope feature — a single AA protein sequence embedded as-is.
    if (
      name === "pl7.app/sequence" &&
      (d["pl7.app/feature"] === "peptide" || d["pl7.app/feature"] === "amplicon-sequence")
    ) {
      scopes.push({
        id: e.id,
        feature: "peptide",
        chain: "",
        columns: [e.id],
        label: e.label,
        isHeavy: false,
        receptor,
        assembling,
      });
      continue;
    }
    if (name === "pl7.app/vdj/scFv-sequence") {
      scFvPresent = true;
      scopes.push({
        id: e.id,
        feature: "scFv",
        chain: "",
        columns: [e.id],
        label: e.label,
        isHeavy: false,
        receptor,
        assembling,
      });
      continue;
    }
    if (name === "pl7.app/vdj/sequence") {
      // Keep primary allele only — secondary alleles would duplicate a chain.
      const alleleIdx = d["pl7.app/vdj/scClonotypeChain/index"];
      if (alleleIdx !== undefined && alleleIdx !== "primary") continue;

      let feat = d["pl7.app/feature"] ?? d["pl7.app/vdj/feature"];
      if (feat === "VDJRegionInFrame") feat = "VDJRegion"; // amino-acid productive full chain
      const chain = (d["pl7.app/vdj/scClonotypeChain"] ?? "") as "A" | "B" | "";
      const isHeavy = deriveIsHeavy(receptor, chain, bulkChain);

      if (feat === "CDR3") {
        scopes.push({
          id: e.id,
          feature: "CDR3",
          chain,
          columns: [e.id],
          label: e.label,
          isHeavy,
          receptor,
          assembling,
        });
      } else if (feat === "VDJRegion") {
        scopes.push({
          id: e.id,
          feature: "VDJRegion",
          chain,
          columns: [e.id],
          label: e.label,
          isHeavy,
          receptor,
          assembling,
        });
        if (chain === "A" || chain === "B") vdjByChain[chain] = e.id;
      }
      // FR1/CDR1/… on their own are not selectable scopes — skip.
    }
  }

  // scFv inputs: VH and VL are merged inside a single polypeptide, so per-chain
  // VDJRegion is not a meaningful standalone scope and the paired-Fv concat path
  // does not apply
  let fvAvailable = false;
  if (scFvPresent) {
    for (let i = scopes.length - 1; i >= 0; i--) {
      if (scopes[i].feature === "VDJRegion") scopes.splice(i, 1);
    }
  } else {
    // Paired Fv — IG only, both heavy + light VDJRegion present.
    fvAvailable = receptor === "IG" && vdjByChain.A !== undefined && vdjByChain.B !== undefined;
    if (fvAvailable) {
      scopes.push({
        id: "Fv",
        feature: "Fv",
        chain: "",
        columns: [vdjByChain.A!, vdjByChain.B!], // [VH, VL] fixed order
        label: "Paired Fv",
        isHeavy: false,
        receptor,
        assembling: false,
      });
    }
  }

  // Conventional paired antibody (light chain or Fv present) vs heavy-only
  // (nanobody-like). Drives the VHHBERT-vs-CurrAb default in `recommendedModel`.
  const paired = receptor === "IG" && (vdjByChain.B !== undefined || fvAvailable);

  // First-connection defaults.
  let defaultsInternal: Internal[];
  if (scFvPresent) {
    defaultsInternal = scopes.filter((s) => s.feature === "scFv"); // scFv construct is the default
  } else if (fvAvailable) {
    defaultsInternal = scopes.filter((s) => s.feature === "Fv"); // prefer paired Fv
  } else if (scopes.some((s) => s.feature === "peptide")) {
    // Peptide input → seed the peptide scope(s). Peptide columns are not
    // assembling-annotated, so they'd otherwise fall through to an empty default
    // set; `recommendedModel` fills these with the peptide specialist (PeptideCLM-2).
    defaultsInternal = scopes.filter((s) => s.feature === "peptide");
  } else {
    defaultsInternal = scopes.filter((s) => s.assembling); // assembly-trusted columns
    // same-chain CDR3+VDJRegion → keep VDJRegion, drop the redundant CDR3.
    const vdjChains = new Set(
      defaultsInternal.filter((s) => s.feature === "VDJRegion").map((s) => s.chain),
    );
    defaultsInternal = defaultsInternal.filter(
      (s) => !(s.feature === "CDR3" && vdjChains.has(s.chain)),
    );
  }

  const toAvailable = (s: Internal): AvailableScope => ({
    id: s.id,
    feature: s.feature,
    chain: s.chain,
    columns: s.columns,
    label: s.label,
    isHeavy: s.isHeavy,
    receptor: s.receptor,
  });
  const toSelected = (s: Internal): SelectedScope => ({
    id: s.id,
    feature: s.feature,
    chain: s.chain,
    columns: s.columns,
    label: s.label,
    isHeavy: s.isHeavy,
    receptor: s.receptor,
  });
  return {
    options: scopes.map(toAvailable),
    defaults: defaultsInternal.map(toSelected),
    receptor,
    paired,
  };
}
