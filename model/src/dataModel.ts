import { DataModelBuilder } from "@platforma-sdk/model";
import type { BlockDataV1 } from "./types";

const DEFAULT_FIDELITY = "auto" as const;

export const blockDataModel = new DataModelBuilder()
  .from<BlockDataV1>("Ver_2026_05_29")
  .init(() => ({
    fidelity: DEFAULT_FIDELITY,
    selectedScopes: [],
    // Resource defaults for the embedding step (Advanced Settings).
    mem: 32,
    cpu: 16,
  }));
