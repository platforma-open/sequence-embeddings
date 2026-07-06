<script setup lang="ts">
import type { ScopeConfig } from "@platforma-open/milaboratories.sequence-embeddings.model";
import type { PlRef } from "@platforma-sdk/model";
import {
  PlAccordionSection,
  PlAlert,
  PlBlockPage,
  PlBtnGhost,
  PlDropdownRef,
  PlMaskIcon24,
  PlNumberField,
  PlSectionSeparator,
  PlSlideModal,
} from "@platforma-sdk/ui-vue";
import { computed, ref, watch } from "vue";
import { useApp } from "../app";
import EmbeddingSelector from "./EmbeddingSelector.vue";
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
  // Set the subtitle label here
  const match = ref
    ? (app.model.outputs.inputOptions ?? []).find(
        (o) => o.ref?.blockId === ref.blockId && o.ref?.name === ref.name,
      )
    : undefined;
  app.model.data.defaultBlockLabel = match?.label ?? "";
}

const config = computed<ScopeConfig | undefined>(() => app.model.outputs.availableScopes);

// GPU-heavy models (CurrAb, ESM-2 650M via High fidelity) run slowly on a CPU-only
// backend. gpuAvailable comes from the prerun; undefined while it resolves, so
// only warn on an explicit `false`.
const slowOnCpu = computed(() => {
  const sel = app.model.data.embedding;
  return (
    app.model.outputs.gpuAvailable === false &&
    (sel.model === "currab" || (sel.model === "esm2" && (sel.fidelity ?? "standard") === "high"))
  );
});
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

    <!-- Sequence and Model dropdowns -->
    <EmbeddingSelector v-model="app.model.data.embedding" :config="config" />

    <PlAlert v-if="slowOnCpu" type="warn">
      <strong>The selected model runs best on a GPU — this machine has none.</strong>
      It will run on the CPU, which will be substantially slower.
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
