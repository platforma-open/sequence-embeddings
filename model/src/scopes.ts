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
 * Every other amino-acid sequence column under the anchor axis — a framework or
 * CDR1/CDR2 loop, a sub-region, a region of a custom scheme, a custom MiXCR span —
 * becomes a `region` scope.
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
import type {
  AvailableScope,
  ScopeConfig,
  ScopeReceptor,
  SelectedScope,
  WorkflowReceptor,
} from "./types";

/**
 * Run modality declared by the producer on the entity axis (`pl7.app/modality`).
 * `"vdj"` means the variants are antibody/TCR V-domains; anything else (including
 * absent) is treated as the general amplicon/peptide case.
 */
export const MODALITY_DOMAIN_KEY = "pl7.app/modality";

/** A discovered sequence column: its workflow-resolvable id, spec, and the
 *  label derived for it (native label forced via includeNativeLabel upstream). */
export type SeqEntry = { id: SUniversalPColumnId; spec: PColumnSpec; label: string };

/**
 * Selectors for candidate sequence columns under the `main` anchor's key axis,
 * filtered on amino-acid alphabet only — embeddings read AA sequence. Which scope
 * a column becomes is decided from its spec in `buildScopeConfig`, so no feature
 * value is listed here.
 *
 * `pl7.app/sequence` covers peptide-extraction (`peptide`) and the
 * synthetic-repertoire-profiler: its whole-variant sequence (`amplicon-sequence`)
 * and one column per region, keyed by the region name in `pl7.app/feature`
 * (FR1…FR4, CDR1…CDR3, sub-regions and custom-scheme regions alike).
 */
export const SEQUENCE_SELECTORS: AnchoredPColumnSelector[] = [
  {
    axes: [{ anchor: "main", idx: 1 }],
    name: "pl7.app/sequence",
    domain: { "pl7.app/alphabet": "aminoacid" },
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

/** True when the producer declared this run's modality as antibody/TCR V-domains. */
export function isVdjModality(domain: Record<string, string> | undefined): boolean {
  return domain?.[MODALITY_DOMAIN_KEY] === "vdj";
}

/**
 * Resolve receptor from a domain record: explicit `pl7.app/vdj/receptor` wins,
 * then derive from `pl7.app/vdj/chain`.
 *
 * With neither key present the fallback depends on the declared modality. A
 * producer that declares `pl7.app/modality: vdj` and supplies no receptor leaves it
 * genuinely `"unknown"` (synthetic-repertoire-profiler) — guessing IG there hides
 * the TCR specialists. Every other input keeps the historical `IG` default, so
 * legacy MiXCR datasets that never carried the key behave exactly as before.
 */
export function resolveReceptor(domain: Record<string, string> | undefined): ScopeReceptor {
  const r = domain?.["pl7.app/vdj/receptor"];
  if (r === "IG" || r === "TCRAB" || r === "TCRGD") return r;
  const chain = domain?.["pl7.app/vdj/chain"];
  if (chain && CHAIN_TO_RECEPTOR[chain]) return CHAIN_TO_RECEPTOR[chain];
  return isVdjModality(domain) ? "unknown" : "IG";
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
  receptor: ScopeReceptor,
  chain: "A" | "B" | "",
  bulkChain: string | undefined,
): boolean {
  // Non-IG and "unknown" both fall out here: an unknown receptor carries no chain
  // either, so heaviness is not asserted — `compat.ts` relaxes the gate instead.
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
  receptor: ScopeReceptor,
  bulkChain?: string,
  isVdj = false,
): Omit<ScopeConfig, "forAnchor"> {
  type Internal = AvailableScope & { assembling: boolean };
  const scopes: Internal[] = [];
  const vdjByChain: Partial<Record<"A" | "B", SUniversalPColumnId>> = {};
  let scFvPresent = false;

  for (const e of entries) {
    const name = e.spec.name;
    const d = e.spec.domain ?? {};
    const assembling = isAssembling(e.spec);

    // `pl7.app/sequence` covers two producers, split by feature and modality.
    //
    // On a VDJ run the profiler's whole-variant sequence IS the V-domain, so it maps
    // to `VDJRegion` and its CDR3 region column to `CDR3` — that is what puts the
    // antibody/TCR models in reach. On an amplicon run the same whole-variant column
    // is a flat protein sequence and maps to `peptide`, as does peptide-extraction's.
    // Every other feature value is a region column and maps to `region` on either
    // run.
    //
    // `isHeavy` stays false throughout: the profiler declares no chain, so the
    // heavy-only specialists are reached via the relaxed unknown-receptor gate in
    // `compat.ts`, not by asserting a chain the data does not carry.
    if (name === "pl7.app/sequence") {
      const feature = d["pl7.app/feature"];
      // A sequence column with no feature value names no region — not a scope.
      if (feature === undefined || feature === "") continue;
      const isWholeVariant = feature === "peptide" || feature === "amplicon-sequence";
      let scopeFeature: SelectedScope["feature"];
      if (isVdj && feature === "amplicon-sequence") {
        scopeFeature = "VDJRegion";
      } else if (isVdj && feature === "CDR3") {
        scopeFeature = "CDR3";
      } else if (isWholeVariant) {
        scopeFeature = "peptide";
      } else {
        scopeFeature = "region";
      }
      scopes.push({
        id: e.id,
        feature: scopeFeature,
        chain: "",
        columns: [e.id],
        label: e.label,
        isHeavy: false,
        receptor,
        ...(scopeFeature === "region" ? { region: feature } : {}),
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
      } else if (feat !== undefined && feat !== "") {
        // Any other feature is a region subsequence: FR1…FR4 (FR4InFrame in aa),
        // CDR1/CDR2, or a custom assembling span such as `{CDR1Begin:FR4End}`.
        scopes.push({
          id: e.id,
          feature: "region",
          chain,
          columns: [e.id],
          label: e.label,
          isHeavy,
          receptor,
          region: feat,
          assembling,
        });
      }
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
    ...(s.region !== undefined ? { region: s.region } : {}),
  });
  const toSelected = (s: Internal): SelectedScope => ({
    id: s.id,
    feature: s.feature,
    chain: s.chain,
    columns: s.columns,
    label: s.label,
    isHeavy: s.isHeavy,
    receptor: s.receptor,
    ...(s.region !== undefined ? { region: s.region } : {}),
  });
  return {
    options: scopes.map(toAvailable),
    defaults: defaultsInternal.map(toSelected),
    receptor,
    paired,
  };
}
