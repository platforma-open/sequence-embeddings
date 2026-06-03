import type { InferOutputsType, PFrameHandle } from "@platforma-sdk/model";
import { BlockModelV3, createPFrameForGraphs, PColumnCollection } from "@platforma-sdk/model";
import { blockDataModel } from "./dataModel";
import { buildScopeConfig, resolveReceptor, SEQUENCE_SELECTORS } from "./scopes";
import type { BlockArgs, ScopeConfig, WorkflowStats } from "./types";

export { blockDataModel } from "./dataModel";
export type {
  AvailableScope,
  BlockArgs,
  BlockData,
  BlockDataV1,
  DeviceMode,
  ModelTag,
  ScopeConfig,
  ScopeFeature,
  SelectedScope,
  WorkflowMode,
  WorkflowReceptor,
  WorkflowScopeStats,
  WorkflowStats,
} from "./types";

/**
 * Input-anchor shape filters for the dataset dropdown. Mirrors the four
 * recognised axis patterns from the workflow's `detectMode` so the user sees
 * only datasets the block can actually consume:
 *  - Peptide mode (universal naming): `[pl7.app/sampleId, pl7.app/variantKey]`
 *    with the peptide extractionRunId domain on the variant key.
 *  - Antibody/TCR (legacy MiXCR bulk): `[pl7.app/sampleId, pl7.app/vdj/cloneId]`
 *    or `[..., pl7.app/vdj/clonotypeKey]`.
 *  - Antibody/TCR (legacy MiXCR single-cell): `[pl7.app/sampleId, pl7.app/vdj/scClonotypeKey]`.
 *
 * The `pl7.app/isAnchor` annotation marks anchor-defining columns — the
 * upstream block emits one such anchor per dataset.
 */
const inputAnchorSpecs = [
  {
    axes: [{ name: "pl7.app/sampleId" }, { name: "pl7.app/variantKey" }],
    annotations: { "pl7.app/isAnchor": "true" },
  },
  {
    axes: [{ name: "pl7.app/sampleId" }, { name: "pl7.app/vdj/cloneId" }],
    annotations: { "pl7.app/isAnchor": "true" },
  },
  {
    axes: [{ name: "pl7.app/sampleId" }, { name: "pl7.app/vdj/clonotypeKey" }],
    annotations: { "pl7.app/isAnchor": "true" },
  },
  {
    axes: [{ name: "pl7.app/sampleId" }, { name: "pl7.app/vdj/scClonotypeKey" }],
    annotations: { "pl7.app/isAnchor": "true" },
  },
];

export const platforma = BlockModelV3.create(blockDataModel)
  .args<BlockArgs>((data) => {
    if (data.inputAnchor === undefined) {
      throw new Error("Select an input dataset");
    }
    if (data.selectedScopes.length === 0) {
      throw new Error("Select at least one scope to embed"); // R6b
    }
    return {
      inputAnchor: data.inputAnchor,
      device: data.device,
      selectedScopes: data.selectedScopes,
      mem: data.mem,
      cpu: data.cpu,
    };
  })
  // Dropdown source for the input picker. Refs returned here populate the UI
  // selector; the user's pick is written back into `data.inputAnchor`.
  .output("inputOptions", (ctx) => ctx.resultPool.getOptions(inputAnchorSpecs))
  // Spec for the currently selected ref. UI uses this to display the dataset
  // shape (axis names, domain) and to gate subtitle / status text.
  .output("inputSpec", (ctx) =>
    ctx.data.inputAnchor ? ctx.resultPool.getPColumnSpecByRef(ctx.data.inputAnchor) : undefined,
  )
  // Scope picker config — derived from the connected input's sequence columns.
  // `options` feeds the multi-select; `defaults` is the first-connection
  // selection (Default Selection Rule). The UI snapshots a chosen scope's
  // column id(s) into `data.selectedScopes` so the args lambda stays data-only.
  // retentive: avoid the picker flickering empty while the pool re-resolves.
  .output(
    "availableScopes",
    (ctx): ScopeConfig | undefined => {
      const ref = ctx.data.inputAnchor;
      if (ref === undefined) return undefined;
      const spec = ctx.resultPool.getPColumnSpecByRef(ref);
      const anchorCtx = ctx.resultPool.resolveAnchorCtx({ main: ref });
      if (spec === undefined || !anchorCtx) return undefined;
      const receptor = resolveReceptor(spec.axesSpec[1]?.domain);
      const entries = new PColumnCollection()
        .addColumnProvider(ctx.resultPool)
        .addAxisLabelProvider(ctx.resultPool)
        .getUniversalEntries(SEQUENCE_SELECTORS, { anchorCtx });
      if (entries === undefined) return undefined;
      // Native sequence labels, derived exactly as clonotype-clustering does, so
      // per-sequence scopes show the same labels users see there. Keyed by the
      // same SUniversalPColumnId as the universal entries (both go through the
      // same anchorCtx). The synthetic Paired Fv option gets this block's label.
      const labeled = ctx.resultPool.getCanonicalOptions({ main: ref }, SEQUENCE_SELECTORS, {
        ignoreMissingDomains: true,
        labelOps: { includeNativeLabel: true },
      });
      const labelById = new Map<string, string>((labeled ?? []).map((o) => [o.value, o.label]));
      return buildScopeConfig(
        entries.map((e) => ({ id: e.id, spec: e.spec })),
        receptor,
        labelById,
      );
    },
    { retentive: true },
  )
  // Python step's stats.json — surfaces device used, per-scope counts, errors.
  // UI renders this as a "what was computed" summary panel.
  .output("stats", (ctx) => ctx.outputs?.resolve("stats")?.getDataAsJson<WorkflowStats>())
  // Quick gate for the UI's "running" indicator. Mirrors sequence-properties.
  .output("isRunning", (ctx) => ctx.outputs?.getIsReadyOrError() === false)
  // Workflow stderr — surfaced as a log viewer in the UI for diagnostics.
  .output("processingLog", (ctx) => ctx.outputs?.resolve("processingLog")?.getLogHandle())
  // Embedding PFrame handle — for downstream consumer integration (slices 02
  // and 03 read the exported PFrame via the result pool; this output exists
  // for any in-block visualization we add later, plus to give the UI a way to
  // confirm the PFrame is ready).
  .outputWithStatus("embeddingsPfHandle", (ctx): PFrameHandle | undefined => {
    const pCols = ctx.outputs?.resolve("embeddingsPf")?.getPColumns();
    if (pCols === undefined) return undefined;
    // No in-block visualization in v1 (per the brief — clonotype-space handles
    // repertoire visualization downstream). createPFrameForGraphs is the
    // standard handle factory; even without UI it gives the right shape for
    // the result pool consumers.
    return createPFrameForGraphs(ctx, pCols);
  })
  .title(() => "Sequence Embeddings")
  .subtitle((ctx) => ctx.data.defaultBlockLabel ?? "")
  .sections(() => [{ type: "link", href: "/", label: "Main" }])
  .done();

export type BlockOutputs = InferOutputsType<typeof platforma>;
