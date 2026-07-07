<script setup lang="ts">
import type {
  EmbeddingModelId,
  EmbeddingSelection,
  Fidelity,
  ScopeConfig,
} from "@platforma-open/milaboratories.sequence-embeddings.model";
import {
  compatibleModels,
  EMBEDDING_MODELS,
  isCompatible,
} from "@platforma-open/milaboratories.sequence-embeddings.model";
import type { ListOption } from "@platforma-sdk/ui-vue";
import { PlBtnGroup, PlDropdown } from "@platforma-sdk/ui-vue";
import { computed } from "vue";

// The selection is v-model'd; updates are immutable replacements (new object) so
// the model's deep-watch tracks them cleanly.
const selection = defineModel<EmbeddingSelection>({ required: true });
// `config` is OPTIONAL: the dropdowns render always (even before an input is
// connected or while scopes resolve), just with empty options until it arrives.
// `disabled` greys them out until the caller signals this input's defaults have loaded.
const props = defineProps<{ config?: ScopeConfig; disabled?: boolean }>();

// Every available scope for the connected input (empty until `config` resolves).
const scopeOptions = computed(() => props.config?.options ?? []);

// Models valid for the connected input: a model is offered (when no sequence is
// chosen yet) iff it can embed at least one available scope.
const inputModels = computed<EmbeddingModelId[]>(() => {
  const set = new Set<EmbeddingModelId>();
  for (const o of scopeOptions.value) {
    for (const m of compatibleModels(o.feature, o.isHeavy, o.receptor)) set.add(m);
  }
  return [...set].sort((a, b) => EMBEDDING_MODELS[b].priority - EMBEDDING_MODELS[a].priority);
});

// Sequence dropdown
const sequenceOptions = computed<ListOption<string>[]>(() =>
  scopeOptions.value.map((o) => ({ value: o.id, label: o.label })),
);

// Model dropdown — sequence-first filtering: models that can embed the chosen
// scope, or all input-valid models when no scope is chosen yet.
const modelOptions = computed<ListOption<EmbeddingModelId>[]>(() => {
  const s = selection.value.scope;
  const ids = s ? compatibleModels(s.feature, s.isHeavy, s.receptor) : inputModels.value;
  return ids.map((id) => ({ value: id, label: EMBEDDING_MODELS[id].label }));
});

const fidelityOptions: ListOption<Fidelity>[] = [
  { value: "standard", label: "Standard" },
  { value: "high", label: "High" },
];

function onSequence(id: string | undefined) {
  const scope = scopeOptions.value.find((o) => o.id === id);
  if (scope === undefined) {
    selection.value = { ...selection.value, scope: undefined };
    return;
  }
  // Keep the model if it still fits the new scope; otherwise CLEAR it rather than
  // silently swapping in a default.
  const keep =
    selection.value.model !== undefined &&
    isCompatible(scope.feature, scope.isHeavy, scope.receptor, selection.value.model);
  selection.value = {
    ...selection.value,
    scope,
    model: keep ? selection.value.model : undefined,
  };
}

function onModel(model: EmbeddingModelId | undefined) {
  selection.value = { ...selection.value, model };
}

function onFidelity(fidelity: Fidelity) {
  selection.value = { ...selection.value, fidelity };
}
</script>

<template>
  <PlDropdown
    :model-value="selection.scope?.id"
    :options="sequenceOptions"
    :disabled="disabled"
    label="Sequence to embed"
    required
    @update:model-value="onSequence"
  >
    <template #tooltip>
      Which sequence region to embed. Paired Fv embeds the VH and VL chains together.
    </template>
  </PlDropdown>

  <PlDropdown
    :model-value="selection.model"
    :options="modelOptions"
    :disabled="disabled"
    label="Model"
    required
    @update:model-value="onModel"
  >
    <template #tooltip>
      Only models that can embed the selected sequence are shown. The default is the recommended
      specialist for that sequence; ESM-2 is the universal fallback.
    </template>
  </PlDropdown>

  <PlBtnGroup
    v-if="selection.model === 'esm2'"
    :model-value="selection.fidelity ?? 'standard'"
    :options="fidelityOptions"
    label="Model fidelity"
    @update:model-value="onFidelity"
  >
    <template #tooltip>
      Standard uses ESM-2 150M (faster, standard quality); High uses ESM-2 650M (higher quality,
      slower, GPU recommended).
    </template>
  </PlBtnGroup>
</template>
