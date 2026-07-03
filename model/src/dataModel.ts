import { DataModelBuilder } from "@platforma-sdk/model";
import type { BlockDataV1, BlockDataV2, EmbeddingCard } from "./types";

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
      (s): EmbeddingCard => ({
        id: s.id,
        scope: { ...s, isHeavy: false, receptor: "IG" },
        model: "esm2",
        fidelity: v1.fidelity,
        isExpanded: false,
      }),
    ),
    embeddingsInitializedForAnchor: v1.scopesInitializedForAnchor,
    mem: v1.mem,
    cpu: v1.cpu,
    defaultBlockLabel: v1.defaultBlockLabel,
  };
}

export const blockDataModel = new DataModelBuilder()
  .from<BlockDataV1>("Ver_2026_05_29")
  .migrate<BlockDataV2>("Ver_2026_06_23_models", migrateV1ToV2)
  .init(() => ({
    // Cards are seeded by the UI on first input connection (specialist-first).
    embeddings: [],
    // Resource defaults for the embedding step (Advanced Settings).
    mem: 32,
    cpu: 16,
  }));
