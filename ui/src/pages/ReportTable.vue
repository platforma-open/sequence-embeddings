<script setup lang="ts">
import type { WorkflowStats } from "@platforma-open/milaboratories.sequence-embeddings.model";
import { modelTagLabel } from "@platforma-open/milaboratories.sequence-embeddings.model";
import type { PlAgHeaderComponentParams } from "@platforma-sdk/ui-vue";
import { useAgGridOptions } from "@platforma-sdk/ui-vue";
import type { ColDef, ValueFormatterParams } from "ag-grid-enterprise";
import { AgGridVue } from "ag-grid-vue3";
import { computed } from "vue";
import { useApp } from "../app";

// Run-report table. Reads model state directly (read-only display, no writes —
// so no hairpin risk) and renders one row per computed scope. The grid's own
// overlays cover every non-data state: not-ready guidance before a run, the
// standard loading animation while running, the empty overlay, and the rows once
// results are in.
const app = useApp();

const hasInput = computed(() => app.model.data.inputAnchor !== undefined);
const isRunning = computed(() => app.model.outputs.isRunning === true);
const stats = computed<WorkflowStats | undefined>(() => app.model.outputs.stats);
const resultsStale = computed(() => app.model.outputs.resultsStale === true);

// Columns are additive where it matters: Total = Successfully embedded + Skipped (no sequence)
// (every input entity ends up in one of those two). Trimmed is a SUBSET of
// Successfully embedded (an over-long sequence is truncated but still embedded),
// so it is NOT a third slice of Total — surfaced as a separate quality column
// with that caveat in its info tooltip.
type StatsRow = {
  key: string;
  region: string;
  model: string;
  total: number | undefined;
  embedded: number | undefined;
  dropped: number | undefined;
  trimmed: number | undefined;
  // This row's model residue limit (its token cap minus the 2 specials). Per-model —
  // the run can span models with different limits — so the threshold is per row, not
  // a single column header. Undefined if the report didn't carry it.
  maxResidues: number | undefined;
};

const rowData = computed<StatsRow[]>(() =>
  (stats.value?.scopes ?? []).map((s) => {
    const embedded = s.n_entities;
    const dropped = s.n_dropped_empty;
    // Total input entities for this scope = embedded + dropped. Same value across
    // scopes (the dataset's clonotype/peptide count), but shown per row so each
    // row is self-explanatory.
    const total =
      typeof embedded === "number" && typeof dropped === "number" ? embedded + dropped : undefined;
    return {
      key: s.name,
      region: s.label || s.name,
      model: modelTagLabel(s.model),
      total,
      embedded,
      dropped,
      trimmed: s.n_truncated,
      maxResidues: typeof s.max_length === "number" ? s.max_length - 2 : undefined,
    };
  }),
);

const numFmt = (p: ValueFormatterParams) =>
  typeof p.value === "number" ? p.value.toLocaleString() : "—";

const defaultColDef: ColDef = {
  suppressHeaderMenuButton: true,
  resizable: true,
  sortable: false,
};

// Trimming is rare; only surface the Trimmed column when at least one region
// actually had a truncated sequence.
const anyTrimmed = computed(() => rowData.value.some((r) => (r.trimmed ?? 0) > 0));

const columnDefs = computed<ColDef<StatsRow>[]>(() => {
  const cols: ColDef<StatsRow>[] = [
    {
      colId: "region",
      field: "region",
      headerName: "Embedded sequence",
      headerComponentParams: { type: "Text" } satisfies PlAgHeaderComponentParams,
      flex: 2,
      minWidth: 220,
    },
    {
      colId: "model",
      field: "model",
      headerName: "Model",
      headerComponentParams: {
        type: "Text",
        tooltip: "Embedding model used to compute this row's results.",
      } satisfies PlAgHeaderComponentParams,
      flex: 1,
      minWidth: 140,
    },
    {
      colId: "total",
      field: "total",
      headerName: "Total",
      type: "numericColumn",
      valueFormatter: numFmt,
      headerComponentParams: {
        type: "Number",
        tooltip: "Input clonotypes or peptides for this region.",
      } satisfies PlAgHeaderComponentParams,
      flex: 1,
      minWidth: 110,
    },
    {
      colId: "embedded",
      field: "embedded",
      headerName: "Successfully embedded",
      type: "numericColumn",
      valueFormatter: numFmt,
      headerComponentParams: {
        type: "Number",
        tooltip: "Successfully embedded clonotypes/peptides.",
      } satisfies PlAgHeaderComponentParams,
      flex: 1,
      minWidth: 150,
    },
    {
      colId: "dropped",
      field: "dropped",
      headerName: "Skipped (no sequence)",
      type: "numericColumn",
      valueFormatter: numFmt,
      headerComponentParams: {
        type: "Number",
        tooltip:
          "Clonotypes/peptides with no sequence for this region, removed before embedding. For Paired Fv, a clonotype missing either its VH or VL chain.",
      } satisfies PlAgHeaderComponentParams,
      flex: 1,
      minWidth: 110,
    },
  ];

  // Model-agnostic: the residue limit is PER MODEL (a run can mix models with
  // different limits), so it isn't in the header — the count goes in "Trimmed" and the
  // per-row limit in its own "Input Limit" column. Both appear only when at least one
  // (scope, model) row actually truncated something.
  if (anyTrimmed.value) {
    cols.push({
      colId: "trimmed",
      field: "trimmed",
      headerName: "Trimmed",
      type: "numericColumn",
      valueFormatter: numFmt,
      headerComponentParams: {
        type: "Number",
        tooltip:
          "Sequences longer than the model's input limit (see Input Limit), truncated before embedding.",
      } satisfies PlAgHeaderComponentParams,
      flex: 1,
      minWidth: 110,
    });
    cols.push({
      colId: "inputLimit",
      field: "maxResidues",
      headerName: "Input Limit",
      type: "numericColumn",
      valueFormatter: numFmt,
      headerComponentParams: {
        type: "Number",
        tooltip:
          "This model's maximum input length in amino acids; longer sequences are truncated before embedding. Differs per model.",
      } satisfies PlAgHeaderComponentParams,
      flex: 1,
      minWidth: 120,
    });
  }

  return cols;
});

// The selection is incomplete until both a sequence and a model are chosen.
const selectionIncomplete = computed(() => {
  const sel = app.model.data.embedding;
  return sel.scope === undefined || sel.model === undefined;
});

// notReady takes priority over loading, so it is gated on !isRunning to keep the
// running animation visible during the (first) run.
const notReady = computed(
  () =>
    !isRunning.value &&
    (!hasInput.value || selectionIncomplete.value || !stats.value || resultsStale.value),
);

const notReadyText = computed(() => {
  if (!hasInput.value) return "Open Settings to select an input dataset and the sequence to embed.";
  if (selectionIncomplete.value) return "Open Settings and choose a sequence and a model to embed.";
  if (resultsStale.value) return "Settings changed — press Run to update the results.";
  return "Press Run to compute embeddings.";
});

const { gridOptions } = useAgGridOptions<StatsRow>(() => ({
  columnDefs: columnDefs.value,
  defaultColDef,
  getRowId: (p) => String(p.data.key),
  rowData: rowData.value,
  loading: isRunning.value,
  loadingText: "Running analysis…",
  notReady: notReady.value,
  notReadyText: notReadyText.value,
  noRowsText: "No sequences were embedded.",
}));
</script>

<template>
  <div class="grid-fill">
    <AgGridVue :style="{ height: '100%' }" v-bind="gridOptions" />
  </div>
</template>

<style scoped>
/* The grid is height:100%; give it a flex-fill parent so it occupies the page
   body's remaining height (min-height keeps it visible if the body is short). */
.grid-fill {
  flex: 1;
  min-height: 360px;
}
</style>
