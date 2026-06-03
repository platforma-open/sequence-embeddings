<script setup lang="ts">
import type {
  AvailableScope,
  DeviceMode,
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
  PlLogView,
  PlMaskIcon24,
  PlNumberField,
  PlSectionSeparator,
  PlSlideModal,
} from "@platforma-sdk/ui-vue";
import { computed, ref, watch } from "vue";
import { useApp } from "../app";

const app = useApp();

const logOpen = ref(false);
const settingsOpen = ref(app.model.data.inputAnchor === undefined);

watch(
  () => app.model.outputs.isRunning,
  (isRunning) => {
    if (isRunning) settingsOpen.value = false;
  },
);

// Setter, not a watch on `inputAnchor` — see sequence-properties for the
// reasoning. A watcher would fire on server-patch object replacements (other
// client edits, app reopen) and reset state the user did not touch.
function setInput(ref?: PlRef) {
  app.model.data.inputAnchor = ref;
}

const deviceOptions: { value: DeviceMode; label: string }[] = [
  { value: "auto", label: "Auto" },
  { value: "cpu", label: "CPU" },
  { value: "gpu", label: "GPU" },
];

// Scope multi-select. Options come from the model's input-shape detection
// (availableScopes); labels match clonotype-clustering for the sequence scopes,
// plus this block's "Paired Fv". The picker tracks scope ids; on change we
// snapshot the full SelectedScope (incl. the column ids) into data so the args
// lambda stays data-only.
const scopeOptions = computed(() =>
  (app.model.outputs.availableScopes?.options ?? []).map((o) => ({ value: o.id, label: o.label })),
);
const selectedScopeIds = computed(() => app.model.data.selectedScopes.map((s) => s.id));

function onScopesChange(ids: string[]) {
  const opts = app.model.outputs.availableScopes?.options ?? [];
  app.model.data.selectedScopes = ids
    .map((id) => opts.find((o) => o.id === id))
    .filter((o): o is AvailableScope => o !== undefined)
    .map((o) => ({ id: o.id, feature: o.feature, chain: o.chain, columns: o.columns }));
}

// Stats summary — read after the workflow has produced `stats.json`. Used by
// the status panel to render "what was computed" without any client-side
// computation beyond shaping the strings.
const stats = computed<WorkflowStats | undefined>(() => app.model.outputs.stats);

const deviceUsedLabel = computed(() => {
  const used = stats.value?.device_used;
  if (used === "gpu") return "GPU";
  if (used === "cpu") return "CPU";
  return undefined;
});

const computedScopeSummaries = computed(() => {
  if (!stats.value) return [];
  return stats.value.scopes
    .filter((s) => s.n_entities > 0)
    .map((s) => ({
      key: s.name,
      // Plain narrative line per scope — keeps the panel readable without
      // grouping into a sub-table.
      text: `${s.name}: ${s.n_entities} sequences × ${s.embedding_dim} dims (${s.model})`,
    }));
});
</script>

<template>
  <PlBlockPage>
    <template #title>Sequence Embeddings</template>
    <template #append>
      <PlBtnGhost @click.stop="() => (logOpen = true)">
        Logs
        <template #append>
          <PlMaskIcon24 name="file-logs" />
        </template>
      </PlBtnGhost>
      <PlBtnGhost @click.stop="() => (settingsOpen = true)">
        Settings
        <template #append>
          <PlMaskIcon24 name="settings" />
        </template>
      </PlBtnGhost>
    </template>

    <!-- Per-scope errors surfaced from the Python step's stats.json.
         Empty when everything succeeded; one PlAlert per failure otherwise. -->
    <PlAlert v-for="err in stats?.errors ?? []" :key="err.scope" type="warn">
      <strong>{{ err.scope }}</strong
      >: {{ err.error }}
    </PlAlert>

    <!-- "What was computed" summary panel, rendered once the workflow has
         produced stats.json. Empty placeholder otherwise. -->
    <PlAlert v-if="stats" type="info">
      <p>
        Computed on <strong>{{ deviceUsedLabel }}</strong
        >. Mode: <strong>{{ stats.mode }}</strong
        >.
      </p>
      <ul v-if="computedScopeSummaries.length > 0">
        <li v-for="s in computedScopeSummaries" :key="s.key">{{ s.text }}</li>
      </ul>
      <p v-else>No scopes were computed — check the errors panel and the processing log.</p>
    </PlAlert>

    <PlAlert v-if="!app.model.data.inputAnchor" type="info">
      Select an input dataset in Settings to compute embeddings.
    </PlAlert>
  </PlBlockPage>

  <PlSlideModal v-model="settingsOpen" close-on-outside-click shadow>
    <template #title>Settings</template>

    <PlDropdownRef
      :model-value="app.model.data.inputAnchor"
      :options="app.model.outputs.inputOptions"
      label="Input dataset"
      clearable
      required
      @update:model-value="setInput"
    >
      <template #tooltip>
        Select the output from a valid read/count processing block (i.e. Peptide Profiling, MiXCR
        clonotyping, etc.).
      </template>
    </PlDropdownRef>

    <PlDropdownMulti
      :model-value="selectedScopeIds"
      :options="scopeOptions"
      label="Sequences to embed"
      required
      :disabled="app.model.data.inputAnchor === undefined"
      @update:model-value="onScopesChange"
    >
      <template #tooltip>
        Which sequence regions to embed. Paired Fv stands for embedding VH and VL chains together.
      </template>
    </PlDropdownMulti>

    <PlAccordionSection label="Advanced Settings">
      <PlBtnGroup v-model="app.model.data.device" :options="deviceOptions" label="Compute device">
        <template #tooltip>
          Auto detects a CUDA-capable GPU at workflow time and falls back to CPU if none is found.
          GPU mode uses ESM-2 650M; CPU mode uses ESM-2 150M to keep inference time reasonable.
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
  </PlSlideModal>

  <PlSlideModal v-model="logOpen" width="80%">
    <template #title>Processing Log</template>
    <PlLogView :log-handle="app.model.outputs.processingLog" />
  </PlSlideModal>
</template>
