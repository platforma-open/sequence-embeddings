<script setup lang="ts">
import type {
  EmbeddingCard,
  EmbeddingModelId,
  Fidelity,
  ScopeConfig,
} from "@platforma-open/milaboratories.sequence-embeddings.model";
import {
  compatibleModels,
  EMBEDDING_MODELS,
  isCompatible,
} from "@platforma-open/milaboratories.sequence-embeddings.model";
import type { ListOption } from "@platforma-sdk/ui-vue";
import { PlAlert, PlBtnGroup, PlDropdown } from "@platforma-sdk/ui-vue";
import { computed } from "vue";

// The card is v-model'd from the PlElementList item; updates are immutable
// replacements (new object) so the model's deep-watch tracks them cleanly.
const card = defineModel<EmbeddingCard>({ required: true });
// `duplicate`: this card repeats an earlier card's (sequence, model) — flagged by
// MainPage. Shown as an inline error; the args lambda also throws, blocking Run.
const props = defineProps<{ config: ScopeConfig; duplicate?: boolean }>();

// Models valid for the connected input: a model is offered (when no sequence is
// chosen yet) iff it can embed at least one available scope.
const inputModels = computed<EmbeddingModelId[]>(() => {
  const set = new Set<EmbeddingModelId>();
  for (const o of props.config.options) {
    for (const m of compatibleModels(o.feature, o.isHeavy, o.receptor)) set.add(m);
  }
  return [...set].sort((a, b) => EMBEDDING_MODELS[b].priority - EMBEDDING_MODELS[a].priority);
});

// Sequence dropdown
const sequenceOptions = computed<ListOption<string>[]>(() =>
  props.config.options.map((o) => ({ value: o.id, label: o.label })),
);

// Model dropdown — sequence-first filtering: models that can embed the chosen
// scope, or all input-valid models when no scope is chosen yet.
const modelOptions = computed<ListOption<EmbeddingModelId>[]>(() => {
  const s = card.value.scope;
  const ids = s ? compatibleModels(s.feature, s.isHeavy, s.receptor) : inputModels.value;
  return ids.map((id) => ({ value: id, label: EMBEDDING_MODELS[id].label }));
});

const fidelityOptions: ListOption<Fidelity>[] = [
  { value: "standard", label: "Standard" },
  { value: "high", label: "High" },
];

function onSequence(id: string | undefined) {
  const scope = props.config.options.find((o) => o.id === id);
  if (scope === undefined) {
    card.value = { ...card.value, scope: undefined };
    return;
  }
  // Keep the model if it still fits the new scope; otherwise CLEAR it rather than
  // silently swapping in a default.
  const keep =
    card.value.model !== undefined &&
    isCompatible(scope.feature, scope.isHeavy, scope.receptor, card.value.model);
  card.value = {
    ...card.value,
    scope,
    model: keep ? card.value.model : undefined,
  };
}

function onModel(model: EmbeddingModelId | undefined) {
  card.value = { ...card.value, model };
}

function onFidelity(fidelity: Fidelity) {
  card.value = { ...card.value, fidelity };
}
</script>

<template>
  <PlAlert v-if="duplicate" type="error">
    This sequence and model combination is already added. Please, remove this embedding choice or
    change its sequence or model.
  </PlAlert>

  <PlDropdown
    :model-value="card.scope?.id"
    :options="sequenceOptions"
    label="Sequence to embed"
    required
    @update:model-value="onSequence"
  >
    <template #tooltip>
      Which sequence region to embed. Paired Fv embeds the VH and VL chains together.
    </template>
  </PlDropdown>

  <PlDropdown
    :model-value="card.model"
    :options="modelOptions"
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
    v-if="card.model === 'esm2'"
    :model-value="card.fidelity ?? 'standard'"
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
