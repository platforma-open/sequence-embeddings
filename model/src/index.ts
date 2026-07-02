import type { InferOutputsType } from "@platforma-sdk/model";
import { BlockModelV3, PColumnCollection } from "@platforma-sdk/model";
import { isCompatible } from "./compat";
import { blockDataModel } from "./dataModel";
import { buildScopeConfig, resolveReceptor, SEQUENCE_SELECTORS } from "./scopes";
import type { BlockArgs, EmbeddingTask, ScopeConfig, WorkflowStats } from "./types";

export { blockDataModel } from "./dataModel";
// Model catalog + scope↔model compatibility (used by the UI for the card dropdowns).
export * from "./compat";
export type {
  AvailableScope,
  BlockArgs,
  BlockData,
  BlockDataV1,
  BlockDataV2,
  EmbeddingCard,
  EmbeddingModelId,
  EmbeddingTask,
  Fidelity,
  ModelTag,
  ScopeConfig,
  ScopeFeature,
  SelectedScope,
  WorkflowReceptor,
  WorkflowScopeStats,
  WorkflowStats,
} from "./types";

/**
 * Input-anchor shape filters for the dataset dropdown. Mirrors the recognised
 * entity-key axis patterns the workflow accepts (see `isKeyAxis`) so the user
 * sees only datasets the block can actually consume:
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
    if (data.embeddings.length === 0) {
      throw new Error("Add at least one embedding");
    }
    // Project each card to a (scope, model) task. Validate each card is complete
    // and the pair is compatible — data-only (scope carries feature/isHeavy/
    // receptor snapshots), so the lambda stays pure (the V3 idiom replacing
    // V1's .argsValid()).
    const tasks = data.embeddings.map((card): EmbeddingTask => {
      if (card.scope === undefined) throw new Error("Each embedding needs a sequence selected");
      if (card.model === undefined) throw new Error("Each embedding needs a model selected");
      if (!isCompatible(card.scope.feature, card.scope.isHeavy, card.scope.receptor, card.model)) {
        throw new Error(`${card.model} cannot embed "${card.scope.label}"`);
      }
      return {
        scope: card.scope,
        model: card.model,
        // Fidelity applies only to ESM-2; drop it for other models so it doesn't
        // perturb the args bytes.
        fidelity: card.model === "esm2" ? (card.fidelity ?? "standard") : undefined,
      };
    });
    // Reject exact-duplicate tasks (same scope + model + effective fidelity).
    const seen = new Set<string>();
    for (const t of tasks) {
      const key = `${t.scope.id}|${t.model}|${t.fidelity ?? ""}`;
      if (seen.has(key)) {
        throw new Error(
          `"${t.scope.label}" is already being embedded with this model — remove the duplicate embedding.`,
        );
      }
      seen.add(key);
    }
    // Sort by (scope id, model) so a pure reorder of the card list doesn't change
    // the args bytes and spuriously stale the block.
    tasks.sort((a, b) => a.scope.id.localeCompare(b.scope.id) || a.model.localeCompare(b.model));
    return {
      inputAnchor: data.inputAnchor,
      embeddings: tasks,
      mem: data.mem,
      cpu: data.cpu,
    };
  })
  // Prerun feeds a lightweight always-rerun template that reports whether the
  // backend advertises a GPU
  .prerunArgs(() => ({}))
  // Dropdown source for the input picker. Refs returned here populate the UI
  // selector; the user's pick is written back into `data.inputAnchor`.
  .output("inputOptions", (ctx) => ctx.resultPool.getOptions(inputAnchorSpecs))
  // Spec for the currently selected ref. UI uses this to display the dataset
  // shape (axis names, domain) and to gate subtitle / status text.
  .output("inputSpec", (ctx) =>
    ctx.data.inputAnchor ? ctx.resultPool.getPColumnSpecByRef(ctx.data.inputAnchor) : undefined,
  )
  // Scope picker config — derived from the connected input's sequence columns.
  // `options` feeds each card's sequence dropdown; `defaults` (+ receptor/paired)
  // seed the cards on first connection. The UI snapshots a chosen scope (column
  // id(s), feature, isHeavy, receptor) into the card so the args lambda stays
  // data-only. retentive: avoid the picker flickering empty while the pool re-resolves.
  .output(
    "availableScopes",
    (ctx): ScopeConfig | undefined => {
      const ref = ctx.data.inputAnchor;
      if (ref === undefined) return undefined;
      const spec = ctx.resultPool.getPColumnSpecByRef(ref);
      const anchorCtx = ctx.resultPool.resolveAnchorCtx({ main: ref });
      if (spec === undefined || !anchorCtx) return undefined;
      const receptor = resolveReceptor(spec.axesSpec[1]?.domain);
      // Bulk single-chain inputs carry heavy/light on the input axis (not on a
      // per-chain key); pass it through so scopes get the right `isHeavy`.
      const bulkChain = spec.axesSpec[1]?.domain?.["pl7.app/vdj/chain"];
      const entries = new PColumnCollection()
        .addColumnProvider(ctx.resultPool)
        .addAxisLabelProvider(ctx.resultPool)
        .getUniversalEntries(SEQUENCE_SELECTORS, {
          anchorCtx,
          labelOps: { includeNativeLabel: true },
        });
      if (entries === undefined) return undefined;
      return {
        // Native columns carry the label derived above; the synthetic Paired Fv
        // option gets this block's own label (set in buildScopeConfig).
        ...buildScopeConfig(
          entries.map((e) => ({ id: e.id, spec: e.spec, label: e.label })),
          receptor,
          bulkChain,
        ),
        // Stamp the anchor this config belongs to, so the UI seed watcher can
        // reject a retained (stale) config from the previous input.
        forAnchor: JSON.stringify(ref),
      };
    },
    { retentive: true },
  )
  // Workflow run summary — device, model, max_length and the per-scope list.
  // Per-scope counts are added post-run (the report step). UI renders this as the
  // "what was computed" panel.
  .output("stats", (ctx) => ctx.outputs?.resolve("stats")?.getDataAsJson<WorkflowStats>())
  // Quick gate for the UI's "running" indicator. Mirrors sequence-properties.
  .output("isRunning", (ctx) => ctx.outputs?.getIsReadyOrError() === false)
  // Whether the displayed results predate the current settings: true when the
  // current args differ from the args of the last completed run. The UI hides
  // the report and prompts a re-run when stale — the redefine-clonotypes pattern
  .output("resultsStale", (ctx) => {
    if (ctx.args === undefined || ctx.activeArgs === undefined) return false;
    return JSON.stringify(ctx.args) !== JSON.stringify(ctx.activeArgs);
  })
  // Backend GPU-availability flag from the prerun
  .output("gpuAvailable", (ctx): boolean | undefined =>
    ctx.prerun
      ?.resolve({ field: "gpuAvailable", assertFieldType: "Input", allowPermanentAbsence: true })
      ?.getDataAsJson<boolean>(),
  )
  // No single processing log in batch mode: inference fans out across per-batch
  // execs whose stdout the processColumn orchestrator does not surface as one
  // stream. Diagnostics come from the run report; batch errors surface via the
  // block's error panel.
  .title(() => "Sequence Embeddings")
  .subtitle((ctx) => ctx.data.defaultBlockLabel ?? "")
  .sections(() => [{ type: "link", href: "/", label: "Main" }])
  .done();

export type BlockOutputs = InferOutputsType<typeof platforma>;
