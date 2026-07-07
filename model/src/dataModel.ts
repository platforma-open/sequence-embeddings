import { DataModelBuilder } from "@platforma-sdk/model";
import type { BlockDataV1, BlockDataV2, BlockDataV3, EmbeddingCardV2 } from "./types";

/**
 * V1 (one universal model via global `fidelity` + flat `selectedScopes`) → V2
 * (per-card models). Each existing scope becomes an **ESM-2** card at the old
 * fidelity — migrations must not silently change results, so existing projects
 * keep embedding with ESM-2; the specialist defaults are seeded only for freshly
 * connected inputs (UI). V1 scopes predate `isHeavy`/`receptor`; ESM-2 is
 * universal and ignores both, so safe defaults are filled in.
 */
function migrateV1ToV2(v1: BlockDataV1): BlockDataV2 {
  return {
    inputAnchor: v1.inputAnchor,
    embeddings: v1.selectedScopes.map(
      (s): EmbeddingCardV2 => ({
        id: s.id,
        scope: { ...s, isHeavy: false, receptor: "IG" },
        model: "esm2",
        fidelity: v1.fidelity,
      }),
    ),
    embeddingsInitializedForAnchor: v1.scopesInitializedForAnchor,
    mem: v1.mem,
    cpu: v1.cpu,
    defaultBlockLabel: v1.defaultBlockLabel,
  };
}

/**
 * V2 (per-card model list) → V3 (single selection). The card list collapses to one
 * selection: take the first card (existing projects seeded at least one), dropping
 * the now-unused `id`. Empty list → blank selection (`{}`), so the UI re-seeds a
 * default on the next input connection.
 */
function migrateV2ToV3(v2: BlockDataV2): BlockDataV3 {
  const first = v2.embeddings[0];
  return {
    inputAnchor: v2.inputAnchor,
    embedding: first ? { scope: first.scope, model: first.model, fidelity: first.fidelity } : {},
    embeddingInitializedForAnchor: v2.embeddingsInitializedForAnchor,
    mem: v2.mem,
    cpu: v2.cpu,
    defaultBlockLabel: v2.defaultBlockLabel,
  };
}

export const blockDataModel = new DataModelBuilder()
  .from<BlockDataV1>("Ver_2026_05_29")
  .migrate<BlockDataV2>("Ver_2026_06_23_models", migrateV1ToV2)
  .migrate<BlockDataV3>("Ver_2026_07_03_single_selection", migrateV2ToV3)
  .init(() => ({
    // The selection is seeded by the UI on first input connection (specialist-first);
    // starts blank so the dropdowns have an object to bind to before an input exists.
    embedding: {},
    // Resource defaults for the embedding step (Advanced Settings).
    mem: 32,
    cpu: 16,
  }));
