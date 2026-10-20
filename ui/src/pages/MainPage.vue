<script setup lang="ts">
import type { ScopeConfig } from "@platforma-open/milaboratories.sequence-embeddings.model";
import { inputSizeStatus } from "@platforma-open/milaboratories.sequence-embeddings.model";
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
import { computed, onScopeDispose, ref, watch } from "vue";
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

// The Sequence/Model dropdowns stay disabled until THIS input's scope config (which
// carries the recommended defaults) has resolved.
const defaultsLoaded = computed(() => {
  const anchor = app.model.data.inputAnchor;
  return anchor !== undefined && config.value?.forAnchor === JSON.stringify(anchor);
});

// Embedded sequences shorter than the model's training minimum.
// Hidden while the shown results are stale (they no longer match the settings).
const shortInputs = computed(() => {
  if (app.model.outputs.resultsStale === true) return undefined;
  const scope = app.model.outputs.stats?.scopes.find((s) => (s.n_short ?? 0) > 0);
  if (scope?.n_short === undefined || scope.min_residues === undefined) return undefined;
  return { count: scope.n_short, minResidues: scope.min_residues, embedded: scope.n_entities };
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

// Input-size gate state (same derivation as `.args()`). Only meaningful once a
// complete selection exists; before that the args lambda reports what's missing.
const inputSize = computed(() => {
  const sel = app.model.data.embedding;
  if (
    app.model.data.inputAnchor === undefined ||
    sel.scope === undefined ||
    sel.model === undefined
  ) {
    return undefined;
  }
  return inputSizeStatus(app.model.data.inputAnchor, sel, app.model.data.inputSizeCheck);
});

// The size check is usually sub-second; announce it only if it takes longer than
// CHECKING_NOTICE_DELAY_MS so the alert doesn't flash on every selection change.
const CHECKING_NOTICE_DELAY_MS = 2000;
const showChecking = ref(false);
let checkingTimer: ReturnType<typeof setTimeout> | undefined;
watch(
  () => inputSize.value?.kind === "checking",
  (checking) => {
    clearTimeout(checkingTimer);
    showChecking.value = false;
    if (checking) {
      checkingTimer = setTimeout(() => (showChecking.value = true), CHECKING_NOTICE_DELAY_MS);
    }
  },
  { immediate: true },
);
onScopeDispose(() => clearTimeout(checkingTimer));
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

    <PlAlert v-if="shortInputs" type="warn">
      <strong>
        {{ shortInputs.count.toLocaleString() }}
        <template v-if="shortInputs.embedded !== undefined">
          of {{ shortInputs.embedded.toLocaleString() }}
        </template>
        embedded sequences are shorter than {{ shortInputs.minResidues }} amino acids.
      </strong>
      ESM-2 was not trained on such short sequences, so their embeddings may not be reliable.
      Interpret results for these sequences with care.
    </PlAlert>

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
    <EmbeddingSelector
      v-model="app.model.data.embedding"
      v-model:show-all-regions="app.model.data.showAllRegions"
      :config="config"
      :disabled="!defaultsLoaded"
    />

    <PlAlert v-if="showChecking" type="info">
      Checking input size… This waits for the input dataset to finish computing. Run becomes
      available once the size is confirmed.
    </PlAlert>
    <PlAlert v-else-if="inputSize?.kind === 'unknown'" type="warn">
      <strong>The size of this input could not be determined.</strong>
      Run is disabled because the embedding output may exceed what the platform can store.
    </PlAlert>
    <PlAlert v-else-if="inputSize?.kind === 'tooLarge'" type="warn">
      <strong>
        This input has {{ inputSize.rows.toLocaleString() }} sequences; the selected model supports
        up to {{ inputSize.max.toLocaleString() }} for now.
      </strong>
      Filter the input to fewer sequences, or choose a model with a smaller embedding.
    </PlAlert>

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
