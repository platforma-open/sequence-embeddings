<script setup lang="ts">
import type { ScopeConfig } from "@platforma-open/milaboratories.sequence-embeddings.model";
import type { DatasetSelection, PlRef } from "@platforma-sdk/model";
import { createDatasetSelection, createPrimaryRef, plRefsEqual } from "@platforma-sdk/model";
import {
  PlAccordionSection,
  PlAlert,
  PlBlockPage,
  PlBtnGhost,
  PlDatasetSelector,
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

// The subtitle label of the picked entry: the subset's when one is picked (its label already
// carries the dataset as a prefix), else the dataset's.
const labelFor = (ref: PlRef | undefined, filter: PlRef | undefined) => {
  if (ref === undefined) return "";
  const option = app.model.outputs.inputOptions?.find((o) => plRefsEqual(o.primary.ref, ref, true));
  if (filter !== undefined) {
    const filterLabel = option?.filters?.find((f) => plRefsEqual(f.ref, filter, true))?.label;
    if (filterLabel !== undefined) return filterLabel;
  }
  return option?.primary.label ?? "";
};

// A dataset, or a dataset narrowed by a subset column. A setter, not a watch on `inputAnchor`:
// a watcher would also fire on server-patch object replacements and reset untouched state.
const datasetSelection = computed<DatasetSelection | undefined>({
  get: () => {
    const { inputAnchor, filterRef } = app.model.data;
    if (inputAnchor === undefined) return undefined;
    return createDatasetSelection(createPrimaryRef(inputAnchor, filterRef));
  },
  set: (selection) => {
    app.model.data.inputAnchor = selection?.primary.column;
    app.model.data.filterRef = selection?.primary.filter;
    app.model.data.defaultBlockLabel = labelFor(
      selection?.primary.column,
      selection?.primary.filter,
    );
  },
});

const config = computed<ScopeConfig | undefined>(() => app.model.outputs.availableScopes);

// The Sequence/Model dropdowns stay disabled until THIS input's scope config (which
// carries the recommended defaults) has resolved.
const defaultsLoaded = computed(() => {
  const anchor = app.model.data.inputAnchor;
  return anchor !== undefined && config.value?.forAnchor === JSON.stringify(anchor);
});

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

    <PlDatasetSelector
      v-model="datasetSelection"
      :options="app.model.outputs.inputOptions"
      label="Input dataset"
      clearable
      required
    >
      <template #tooltip>
        Select the output from a clonotyping / profiling block (Peptide Profiling, MiXCR
        Clonotyping, Import V(D)J Data, etc.).
      </template>
    </PlDatasetSelector>

    <!-- Sequence and Model dropdowns -->
    <EmbeddingSelector
      v-model="app.model.data.embedding"
      :config="config"
      :disabled="!defaultsLoaded"
    />

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
        <template #tooltip>
          Host memory for each embedding batch. Leave empty to size automatically from device and
          input; set to override.
        </template>
      </PlNumberField>
      <PlNumberField
        v-model="app.model.data.cpu"
        label="CPU (cores)"
        :minValue="1"
        :step="1"
        :maxValue="128"
      >
        <template #tooltip>
          CPU cores for each embedding batch. Leave empty to size automatically from device and
          input; set to override.
        </template>
      </PlNumberField>
    </PlAccordionSection>
  </PlSlideModal>
</template>
