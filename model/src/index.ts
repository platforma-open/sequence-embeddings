import type { InferOutputsType } from "@platforma-sdk/model";
import { BlockModelV3, getNumberOfRows, PColumnCollection } from "@platforma-sdk/model";
import { kind } from "@platforma-open/milaboratories.sequence-embeddings.kind";
import { EMBEDDING_MODELS, isCompatible } from "./compat";
import { blockDataModel } from "./dataModel";
import type { InputSizeCheck } from "./inputSize";
import { inputSizeKey, inputSizeStatus } from "./inputSize";
import { buildScopeConfig, isVdjModality, resolveReceptor, SEQUENCE_SELECTORS } from "./scopes";
import type { BlockArgs, EmbeddingTask, ScopeConfig, WorkflowStats } from "./types";

export { blockDataModel } from "./dataModel";
// Model catalog + scope↔model compatibility (used by the UI for the card dropdowns).
export * from "./compat";
// Pre-run input-size gate (shared by the args lambda and the UI).
export * from "./inputSize";
export type {
  AvailableScope,
  BlockArgs,
  BlockData,
  BlockDataV1,
  BlockDataV2,
  BlockDataV3,
  EmbeddingModelId,
  EmbeddingSelection,
  EmbeddingTask,
  Fidelity,
  ModelTag,
  ScopeConfig,
  ScopeFeature,
  ScopeReceptor,
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

export const platforma = BlockModelV3.create({ dataModel: blockDataModel, kind })
  .args<BlockArgs>((data) => {
    if (data.inputAnchor === undefined) {
      throw new Error("Select an input dataset");
    }
    // Single selection: `data.embedding` is the one (scope, model) the user
    // assembled. Project it to one task, validating the pair is complete and
    // compatible — data-only (scope carries feature/isHeavy/receptor snapshots), so
    // the lambda stays pure (the V3 idiom replacing V1's .argsValid()). Emitted as a
    // 1-element list: the workflow input contract is a task list, left unchanged by
    // the single-selection UI.
    const sel = data.embedding;
    if (sel.scope === undefined) throw new Error("Select a sequence to embed");
    if (sel.model === undefined) throw new Error("Select a model");
    if (!isCompatible(sel.scope.feature, sel.scope.isHeavy, sel.scope.receptor, sel.model)) {
      throw new Error(`${sel.model} cannot embed "${sel.scope.label}"`);
    }
    // Input-size gate (see inputSize.ts). Run stays disabled until the size
    // result for THIS input is mirrored into `data`, and when it is unreadable
    // or over the limit for this input + model combination.
    const size = inputSizeStatus(data.inputAnchor, sel, data.inputSizeCheck);
    if (size.kind === "checking") throw new Error("Checking input size…");
    if (size.kind === "unknown") {
      throw new Error("Input size could not be determined; this input is not supported");
    }
    if (size.kind === "tooLarge") {
      throw new Error(
        `Input has ${size.rows.toLocaleString()} sequences; this model supports up to ` +
          `${size.max.toLocaleString()} per run`,
      );
    }
    const task: EmbeddingTask = {
      scope: sel.scope,
      model: sel.model,
      // Fidelity applies only to ESM-2; drop it for other models so it doesn't
      // perturb the args bytes.
      fidelity: sel.model === "esm2" ? (sel.fidelity ?? "standard") : undefined,
    };
    return {
      inputAnchor: data.inputAnchor,
      embeddings: [task],
      mem: data.mem,
      cpu: data.cpu,
    };
  })
  // Prerun feeds a lightweight always-rerun template that reports whether the
  // backend advertises a GPU
  .prerunArgs(() => ({}))
  // The inverse of `init`: the two fields a template seeds are projected back out
  // unchanged. Live state travels as-is — a half-picked selection is ordinary
  // state, and the kind's parser accepts it.
  .templateParams((data) => ({
    inputAnchor: data.inputAnchor,
    embedding: data.embedding,
  }))
  // Dropdown source for the input picker. Refs returned here populate the UI
  // selector; the user's pick is written back into `data.inputAnchor`.
  .output("inputOptions", (ctx) => ctx.resultPool.getOptions(inputAnchorSpecs))
  // Scope picker config — derived from the connected input's sequence columns.
  // `options` feeds the sequence dropdown; `defaults` (+ receptor/paired) seed the
  // selection on first connection. The UI snapshots a chosen scope (column id(s),
  // feature, isHeavy, receptor) into the selection so the args lambda stays
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
      // Producer-declared modality. On a VDJ run the profiler's whole-variant and
      // CDR3 columns are V-domain scopes, not peptide ones — which is what makes the
      // antibody/TCR models reachable. Absent on pre-declaration projects, which
      // therefore keep the peptide classification they have today.
      const isVdj = isVdjModality(spec.axesSpec[1]?.domain);
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
          isVdj,
        ),
        // Stamp the anchor this config belongs to, so the UI seed watcher can
        // reject a retained (stale) config from the previous input.
        forAnchor: JSON.stringify(ref),
      };
    },
    { retentive: true },
  )
  // Row count of the selected scope's sequence columns, for the Run-button size
  // gate. Each column is keyed by the clonotype axis alone, so rows = clonotypes;
  // a paired Fv scope takes the larger of its two columns. `getNumberOfRows` sums
  // Parquet chunk stats from resource metadata without downloading blobs.
  // undefined while the columns' data is still pending (getAnchoredPColumns waits
  // for it); `rows: null` once ready but unreadable. Stamped with `inputKey` so
  // the UI can reject a value observed for the previous selection.
  .output("inputSize", (ctx): InputSizeCheck | undefined => {
    const ref = ctx.data.inputAnchor;
    const columns = ctx.data.embedding.scope?.columns;
    const inputKey = inputSizeKey(ref, columns);
    if (ref === undefined || columns === undefined || inputKey === undefined) return undefined;
    const anchorCtx = ctx.resultPool.resolveAnchorCtx({ main: ref });
    if (!anchorCtx) return undefined;
    const pool = ctx.resultPool.getAnchoredPColumns(anchorCtx, SEQUENCE_SELECTORS);
    if (pool === undefined) return undefined;
    let rows = 0;
    for (const id of columns) {
      const col = pool.find((c) => (c.id as string) === (id as string));
      if (col === undefined) return undefined;
      const n = getNumberOfRows(col.data);
      if (n === undefined) return { inputKey, rows: null };
      rows = Math.max(rows, n);
    }
    return { inputKey, rows };
  })
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
  // Subtitle: input dataset · sequence scope · model — each part appended as it is
  // picked. Pure derivation from `data` (no output read, no data write), so it
  // recomputes reactively without a hairpin.
  .subtitle((ctx) => {
    const parts: string[] = [];
    if (ctx.data.defaultBlockLabel) parts.push(ctx.data.defaultBlockLabel);
    const sel = ctx.data.embedding;
    if (sel.scope?.label) parts.push(sel.scope.label);
    if (sel.model) parts.push(EMBEDDING_MODELS[sel.model].label);
    return parts.join(" · ");
  })
  .sections(() => [{ type: "link", href: "/", label: "Main" }])
  .done();

export type BlockOutputs = InferOutputsType<typeof platforma>;
