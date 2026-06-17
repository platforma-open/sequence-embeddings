<script setup lang="ts">
import type {
  AvailableScope,
  Fidelity,
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
  PlMaskIcon24,
  PlNumberField,
  PlSectionSeparator,
  PlSlideModal,
} from "@platforma-sdk/ui-vue";
import { computed, ref, watch } from "vue";
import { useApp } from "../app";
import ReportTable from "./ReportTable.vue";

const app = useApp();

// Settings live in a slide-out panel (canonical layout); the page shows the run
// report table. Open by default until an input is connected.
const settingsOpen = ref(app.model.data.inputAnchor === undefined);

// Close the settings panel when a run starts, so the report is visible.
watch(
  () => app.model.outputs.isRunning,
  (isRunning) => {
    if (isRunning) settingsOpen.value = false;
  },
);

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
</script>

<template>
  <PlBlockPage>
    <template #title>Sequence Embeddings</template>
    <template #append>
      <PlBtnGhost @click.stop="() => (settingsOpen = true)">
        Settings
        <template #append>
          <PlMaskIcon24 name="settings" />
        </template>
      </PlBtnGhost>
    </template>

    <!-- The run-report table renders every state itself (not-ready guidance,
         loading animation, empty overlay, rows). Errors surface via the block
         error panel. -->
    <ReportTable />
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
        <template #tooltip> Host memory for each embedding batch. Default: 32 GiB. </template>
      </PlNumberField>
      <PlNumberField
        v-model="app.model.data.cpu"
        label="CPU (cores)"
        :minValue="1"
        :step="1"
        :maxValue="128"
      >
        <template #tooltip> CPU cores for each embedding batch. Default: 16. </template>
      </PlNumberField>
    </PlAccordionSection>
  </PlSlideModal>
</template>
