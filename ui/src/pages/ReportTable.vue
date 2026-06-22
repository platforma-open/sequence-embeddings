<script setup lang="ts">
import type { WorkflowStats } from "@platforma-open/milaboratories.sequence-embeddings.model";
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

// The model's length cap in amino acids = its token limit (stats.max_length) minus
// the 2 special tokens (<cls>/<eos>). Shown in the "Trimmed" column info tooltip.
const maxResidues = computed(() => (stats.value?.max_length ?? 1024) - 2);

// Columns are additive where it matters: Total = Successfully embedded + Skipped (no sequence)
// (every input entity ends up in one of those two). Trimmed is a SUBSET of
// Successfully embedded (an over-long sequence is truncated but still embedded),
// so it is NOT a third slice of Total — surfaced as a separate quality column
// with that caveat in its info tooltip.
type StatsRow = {
  key: string;
  region: string;
  total: number | undefined;
  embedded: number | undefined;
  dropped: number | undefined;
  trimmed: number | undefined;
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
      total,
      embedded,
      dropped,
      trimmed: s.n_truncated,
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

  // The current model length limit is shown in the header so the trim threshold
  // is visible at a glance (and repeated in the info tooltip).
  if (anyTrimmed.value) {
    cols.push({
      colId: "trimmed",
      field: "trimmed",
      headerName: `Trimmed (>${maxResidues.value.toLocaleString()} aa)`,
      type: "numericColumn",
      valueFormatter: numFmt,
      headerComponentParams: {
        type: "Number",
        tooltip: `Sequences longer than the model's ${maxResidues.value.toLocaleString()} amino-acid limit, truncated before embedding.`,
      } satisfies PlAgHeaderComponentParams,
      flex: 1,
      minWidth: 160,
    });
  }

  return cols;
});

// notReady takes priority over loading, so it is gated on !isRunning to keep the
// running animation visible during the (first) run.
const notReady = computed(
  () =>
    !isRunning.value &&
    (!hasInput.value ||
      app.model.data.selectedScopes.length === 0 ||
      !stats.value ||
      resultsStale.value),
);

const notReadyText = computed(() => {
  if (!hasInput.value)
    return "Open Settings to select an input dataset and the sequences to embed.";
  if (app.model.data.selectedScopes.length === 0)
    return "Open Settings and choose which sequences to embed.";
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
