<script setup lang="ts">
import type {
  EmbeddingCard as EmbeddingCardData,
  ScopeConfig,
} from "@platforma-open/milaboratories.sequence-embeddings.model";
import { EMBEDDING_MODELS } from "@platforma-open/milaboratories.sequence-embeddings.model";
import type { PlRef } from "@platforma-sdk/model";
import {
  PlAccordionSection,
  PlAlert,
  PlBlockPage,
  PlBtnGhost,
  PlBtnSecondary,
  PlDropdownRef,
  PlElementList,
  PlMaskIcon24,
  PlNumberField,
  PlSectionSeparator,
  PlSlideModal,
} from "@platforma-sdk/ui-vue";
import { computed, ref, watch } from "vue";
import { useApp } from "../app";
import EmbeddingCard from "./EmbeddingCard.vue";
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

const hasInput = computed(() => app.model.data.inputAnchor !== undefined);
const config = computed<ScopeConfig | undefined>(() => app.model.outputs.availableScopes);

// Scopes are "ready" once the retentive `availableScopes` resolves AND matches the
// CURRENT anchor (right after a dataset switch it can still hold the previous
// input's config). Mirrors the seed gate in app.ts (`forAnchor === key`). The card
// list and the (enabled) Add button depend on this; the section header and the
// Add button itself are always rendered (Add stays disabled until ready), so the
// Embeddings section is a permanent part of the panel — present before an input is
// even chosen, not only after the input's defaults have been seeded.
const scopesReady = computed(() => {
  const anchor = app.model.data.inputAnchor;
  if (anchor === undefined || config.value === undefined) return false;
  return config.value.forAnchor === JSON.stringify(anchor);
});

// The embedding cards (scope × model tasks). Writable computed over the model
// data — add/remove/reorder flow through the setter via PlElementList; per-card
// edits write back through each card's v-model.
const embeddings = computed<EmbeddingCardData[]>({
  get: () => app.model.data.embeddings,
  set: (v) => {
    app.model.data.embeddings = v;
  },
});

// Cards are seeded per-anchor in app.ts (specialist-first defaults, guarded by
// embeddingsInitializedForAnchor).

// Duplicate detection: a fully-specified card (scope + model) whose (scope, model,
// effective-fidelity) matches an EARLIER such card is a duplicate.
const duplicateIds = computed<Set<string>>(() => {
  const seen = new Set<string>();
  const dups = new Set<string>();
  for (const c of embeddings.value) {
    if (c.scope === undefined || c.model === undefined) continue;
    const fidelity = c.model === "esm2" ? (c.fidelity ?? "standard") : "";
    const key = `${c.scope.id}|${c.model}|${fidelity}`;
    if (seen.has(key)) dups.add(c.id);
    else seen.add(key);
  }
  return dups;
});

function addEmbedding() {
  embeddings.value = [
    ...embeddings.value,
    { id: crypto.randomUUID(), fidelity: "standard", isExpanded: true },
  ];
}

function cardTitle(card: EmbeddingCardData): string {
  if (card.scope === undefined) return "New embedding";
  const model = card.model === undefined ? "select a model" : EMBEDDING_MODELS[card.model].label;
  const base = `${card.scope.label} — ${model}`;
  // Mark duplicates in the title too, so they are identifiable while collapsed
  // (the inline alert inside the card only shows when it is expanded).
  return duplicateIds.value.has(card.id) ? `${base} (duplicate)` : base;
}

// GPU-heavy models (CurrAb, ESM-2 650M via High fidelity) run slowly on a CPU-only
// backend. gpuAvailable comes from the prerun; undefined while it resolves, so
// only warn on an explicit `false`.
const slowOnCpu = computed(
  () =>
    app.model.outputs.gpuAvailable === false &&
    embeddings.value.some(
      (c) => c.model === "currab" || (c.model === "esm2" && (c.fidelity ?? "standard") === "high"),
    ),
);
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

    <!-- Each card is one (sequence region, model) embedding. The sequence and
         model dropdowns filter each other to compatible options; add a card to
         embed another region, or the same region with a different model. -->
    <PlSectionSeparator>Embeddings</PlSectionSeparator>
    <PlElementList
      v-if="scopesReady"
      class="embeddings-list"
      v-model:items="embeddings"
      :get-item-key="(item) => item.id"
      :is-expanded="(item) => item.isExpanded === true"
      :on-expand="(item) => (item.isExpanded = !item.isExpanded)"
      :disable-dragging="true"
    >
      <template #item-title="{ item }">{{ cardTitle(item) }}</template>
      <template #item-content="{ index }">
        <!-- config is non-undefined here: the list only renders when scopesReady. -->
        <EmbeddingCard
          v-model="embeddings[index]"
          :config="config!"
          :duplicate="duplicateIds.has(embeddings[index].id)"
        />
      </template>
    </PlElementList>

    <PlBtnSecondary :disabled="!scopesReady" icon="add" @click="addEmbedding">
      Add embedding
    </PlBtnSecondary>

    <PlAlert v-if="slowOnCpu" type="warn">
      <strong>Some selected models run best on a GPU — this machine has none.</strong>
      They will run on the CPU and be substantially slower.
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

<style scoped>
/* Tighten the gap between the "Embeddings" separator and the first card. */
.embeddings-list {
  margin-top: -16px;
}
</style>
