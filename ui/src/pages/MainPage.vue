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

// Setter, not a watch on `inputAnchor`. A watcher would fire on server-patch
// object replacements (other client edits, app reopen) and reset state the user
// did not touch.
function setInput(ref?: PlRef) {
  app.model.data.inputAnchor = ref;
}

const fidelityOptions: { value: Fidelity; label: string }[] = [
  { value: "standard", label: "Standard" },
  { value: "high", label: "High" },
];

// Warn when High fidelity (ESM-2 650M) is picked on a backend without a GPU
// `gpuAvailable` comes from the prerun (exec.hasGpu); undefined while it
// resolves, so only warn on an explicit `false`.
const highFidelityNoGpu = computed(
  () => app.model.data.fidelity === "high" && app.model.outputs.gpuAvailable === false,
);

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

// A run has produced (or is producing) outputs once the processing-log handle
// exists — true throughout running, the finishing gap, and the results state, but
// not before the first run. Gates the persistent status/results header.
const hasRunOutputs = computed(() => app.model.outputs.processingLog !== undefined);

// Report is shown once a run matching the current settings has completed.
const showResults = computed(
  () =>
    hasInput.value && hasScopes.value && !isRunning.value && !!stats.value && !resultsStale.value,
);

// One line per computed scope: embedded count, plus dropped (+ reason) only when
// some clonotypes lacked the sequence for that region.
const reportRows = computed(() =>
  (stats.value?.scopes ?? []).map((s) => {
    let text = `${s.n_entities.toLocaleString()} sequences embedded`;
    if (s.n_dropped_empty > 0) {
      const reason = s.feature === "Fv" ? "incomplete pair" : "no sequence";
      text += ` · ${s.n_dropped_empty.toLocaleString()} dropped (${reason})`;
    }
    return { key: s.name, region: s.label || s.name, text };
  }),
);

// Truncation caps an over-long sequence; the row is still embedded — so it's a
// quality caveat, not a drop. Surface only when it actually happened.
const totalTruncated = computed(() =>
  (stats.value?.scopes ?? []).reduce((acc, s) => acc + (s.n_truncated ?? 0), 0),
);

// The model's length cap in amino acids = its token limit (stats.max_length) minus
// the 2 special tokens (<cls>/<eos>). Shown in the truncation warning.
const maxResidues = computed(() => (stats.value?.max_length ?? 1024) - 2);
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

    <PlBtnGroup v-model="app.model.data.fidelity" :options="fidelityOptions" label="Model fidelity">
      <template #tooltip>
        Standard uses ESM-2 150M (faster, lower quality); High uses ESM-2 650M (best quality,
        slower). As a rough guide for 10k sequences — Standard: ~40 s on GPU, ~15 min on CPU; High:
        ~1.5 min on GPU, ~70 min on CPU.
      </template>
    </PlBtnGroup>

    <!-- High fidelity needs a GPU to be fast; warn when none is available (CPU fallback is slow). -->
    <PlAlert v-if="highFidelityNoGpu" type="warn">
      <strong>High fidelity will be slow on this machine — it has no GPU.</strong>
      It will run on the CPU instead, taking roughly
      <strong>70 minutes per 10,000 sequences</strong> (Standard takes about 15 minutes). For faster
      results, switch to <strong>Standard</strong> — only slightly lower quality. Keep
      <strong>High</strong> only if you need the best accuracy and can wait, or run the block on a
      machine that has a GPU.
    </PlAlert>

    <PlAccordionSection label="Advanced Settings">
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

    <!-- Persistent status/results header. Stays mounted from run start through to
         the report so the Logs button (and its row) never unmount. -->
    <template v-if="hasRunOutputs">
      <PlSectionSeparator compact />
      <PlRow alignCenter>
        <template v-if="isRunning">
          <PlLoaderCircular size="16" />
          <span>Computing embeddings…</span>
        </template>
        <h3 v-else-if="showResults" class="results-title">Results</h3>
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
        {{ totalTruncated.toLocaleString() }} sequence(s) exceeded the model's
        {{ maxResidues.toLocaleString() }}-amino-acid limit and were truncated before embedding.
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
