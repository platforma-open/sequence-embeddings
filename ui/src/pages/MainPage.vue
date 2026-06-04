<script setup lang="ts">
import type {
  AvailableScope,
  Fidelity,
  WorkflowStats,
} from "@platforma-open/milaboratories.sequence-embeddings.model";
import type { PlRef } from "@platforma-sdk/model";
import {
  PlAccordionSection,
  PlAlert,
  PlBlockPage,
  PlBtnGhost,
  PlBtnGroup,
  PlDropdownMulti,
  PlDropdownRef,
  PlLoaderCircular,
  PlLogView,
  PlMaskIcon24,
  PlNumberField,
  PlRow,
  PlSectionSeparator,
  PlSlideModal,
  PlSpacer,
} from "@platforma-sdk/ui-vue";
import { computed, ref } from "vue";
import { useApp } from "../app";

const app = useApp();

const logOpen = ref(false);

// Setter, not a watch on `inputAnchor` — see sequence-properties for the
// reasoning. A watcher would fire on server-patch object replacements (other
// client edits, app reopen) and reset state the user did not touch.
function setInput(ref?: PlRef) {
  app.model.data.inputAnchor = ref;
}

const fidelityOptions: { value: Fidelity; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "high", label: "High" },
  { value: "standard", label: "Standard" },
];

// Scope multi-select. Options come from the model's input-shape detection
// (availableScopes). The picker tracks scope ids; on change we snapshot the full
// SelectedScope (incl. the column ids) into data so the args lambda stays data-only.
const scopeOptions = computed(() =>
  (app.model.outputs.availableScopes?.options ?? []).map((o) => ({ value: o.id, label: o.label })),
);
const selectedScopeIds = computed(() => app.model.data.selectedScopes.map((s) => s.id));

function onScopesChange(ids: string[]) {
  const opts = app.model.outputs.availableScopes?.options ?? [];
  app.model.data.selectedScopes = ids
    .map((id) => opts.find((o) => o.id === id))
    .filter((o): o is AvailableScope => o !== undefined)
    .map((o) => ({
      id: o.id,
      feature: o.feature,
      chain: o.chain,
      columns: o.columns,
      label: o.label,
    }));
}

const hasInput = computed(() => app.model.data.inputAnchor !== undefined);
const hasScopes = computed(() => app.model.data.selectedScopes.length > 0);
const isRunning = computed(() => app.model.outputs.isRunning === true);
const stats = computed<WorkflowStats | undefined>(() => app.model.outputs.stats);
const resultsStale = computed(() => app.model.outputs.resultsStale === true);

// Report is shown once a run matching the current settings has completed.
const showResults = computed(
  () =>
    hasInput.value && hasScopes.value && !isRunning.value && !!stats.value && !resultsStale.value,
);

// Map each workflow scope name (feature[_chain]) to the picker label, so the
// report reads with the same names the user selected (e.g. "Heavy CDR3").
const scopeLabelByName = computed(() => {
  const m = new Map<string, string>();
  for (const o of app.model.outputs.availableScopes?.options ?? []) {
    const name = o.chain ? `${o.feature}_${o.chain}` : o.feature;
    m.set(name, o.label);
  }
  return m;
});
function scopeLabel(name: string): string {
  return scopeLabelByName.value.get(name) ?? name;
}

// One line per computed scope: embedded count, plus dropped (+ reason) only when
// some clonotypes lacked the sequence for that region.
const reportRows = computed(() =>
  (stats.value?.scopes ?? []).map((s) => {
    let text = `${s.n_entities.toLocaleString()} sequences embedded`;
    if (s.n_dropped_empty > 0) {
      const reason = s.feature === "Fv" ? "incomplete pair" : "no sequence";
      text += ` · ${s.n_dropped_empty.toLocaleString()} dropped (${reason})`;
    }
    return { key: s.name, region: scopeLabel(s.name), text };
  }),
);

// Truncation caps an over-long sequence; the row is still embedded — so it's a
// quality caveat, not a drop. Surface only when it actually happened.
const totalTruncated = computed(() =>
  (stats.value?.scopes ?? []).reduce((acc, s) => acc + (s.n_truncated ?? 0), 0),
);
</script>

<template>
  <PlBlockPage>
    <template #title>Sequence Embeddings</template>

    <!-- Settings live on the page: this block has no output table to occupy the
         canvas, so a slide-out panel would just leave the page empty. -->
    <PlDropdownRef
      :model-value="app.model.data.inputAnchor"
      :options="app.model.outputs.inputOptions"
      label="Input dataset"
      clearable
      required
      @update:model-value="setInput"
    >
      <template #tooltip>
        Select the output from a clonotyping / profiling block (Peptide Profiling, MiXCR
        Clonotyping, Import V(D)J Data, etc.).
      </template>
    </PlDropdownRef>

    <PlDropdownMulti
      :model-value="selectedScopeIds"
      :options="scopeOptions"
      label="Sequences to embed"
      required
      :disabled="!hasInput"
      @update:model-value="onScopesChange"
    >
      <template #tooltip>
        Which sequence regions to embed. Paired Fv stands for embedding VH and VL chains together.
      </template>
    </PlDropdownMulti>

    <PlAccordionSection label="Advanced Settings">
      <PlBtnGroup
        v-model="app.model.data.fidelity"
        :options="fidelityOptions"
        label="Model fidelity"
      >
        <template #tooltip>
          High uses ESM-2 650M (best quality but slower); Standard uses ESM-2 150M (faster but lower
          quality). Auto picks 650M when a GPU is available and 150M otherwise. As a rough guide for
          10k sequences — High: ~1.5 min on GPU, ~70 min on CPU. Standard: ~40 s on GPU, ~15 min on
          CPU.
        </template>
      </PlBtnGroup>

      <PlSectionSeparator>Resource Allocation</PlSectionSeparator>
      <PlNumberField
        v-model="app.model.data.mem"
        label="Memory (GiB)"
        :minValue="1"
        :step="1"
        :maxValue="1012"
      >
        <template #tooltip> Host memory for the embedding step. Default: 32 GiB. </template>
      </PlNumberField>
      <PlNumberField
        v-model="app.model.data.cpu"
        label="CPU (cores)"
        :minValue="1"
        :step="1"
        :maxValue="128"
      >
        <template #tooltip> CPU cores for the embedding step. Default: 16. </template>
      </PlNumberField>
    </PlAccordionSection>

    <!-- Results are out of date: settings changed since the last completed run.
         Suppressed while a run is in progress (the running indicator covers it). -->
    <PlAlert v-if="resultsStale && !isRunning" type="info">
      Settings changed — press <strong>Run</strong> to update the results.
    </PlAlert>

    <!-- Running: live status + access to the streaming progress log, so a long
         CPU run can be watched mid-flight (the log isn't reachable otherwise). -->
    <template v-if="isRunning">
      <PlSectionSeparator compact />
      <PlRow alignCenter>
        <PlLoaderCircular size="16" />
        <span>Computing embeddings…</span>
        <PlSpacer />
        <PlBtnGhost @click.stop="() => (logOpen = true)">
          Logs
          <template #append>
            <PlMaskIcon24 name="file-logs" />
          </template>
        </PlBtnGhost>
      </PlRow>
    </template>

    <!-- Run report. -->
    <template v-if="showResults">
      <!-- Separator bar above the heading; Logs sits on the heading row. -->
      <PlSectionSeparator compact />
      <PlRow alignCenter>
        <h3 class="results-title">Results</h3>
        <PlSpacer />
        <PlBtnGhost @click.stop="() => (logOpen = true)">
          Logs
          <template #append>
            <PlMaskIcon24 name="file-logs" />
          </template>
        </PlBtnGhost>
      </PlRow>

      <!-- Per-scope lines kept as one tight block: the SDK's 24px vertical gap
           would space these out too much. -->
      <div class="results">
        <template v-if="reportRows.length > 0">
          <div v-for="row in reportRows" :key="row.key">
            <strong>{{ row.region }}</strong> — {{ row.text }}
          </div>
        </template>
        <div v-else>No sequences were embedded — check the processing log.</div>
      </div>

      <PlAlert v-if="totalTruncated > 0" type="warn">
        {{ totalTruncated.toLocaleString() }} sequence(s) exceeded the model's length limit and were
        truncated before embedding.
      </PlAlert>
    </template>
  </PlBlockPage>

  <PlSlideModal v-model="logOpen" width="80%">
    <template #title>Processing Log</template>
    <PlLogView :log-handle="app.model.outputs.processingLog" />
  </PlSlideModal>
</template>

<style scoped>
.results {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.results-title {
  margin: 0;
}
</style>
