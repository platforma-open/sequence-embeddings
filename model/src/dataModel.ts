import { createPlDataTableStateV2, DataModelBuilder } from "@platforma-sdk/model";
import type { BlockDataV1 } from "./types";

/**
 * Default device preference. `"auto"` lets the workflow resolve the device tier
 * at plan time via `exec.hasGpu` (GPU when the backend advertises one, else
 * CPU); the Python step then confirms at runtime (slice 01 R8/R18/R21).
 */
const DEFAULT_DEVICE = "auto" as const;

export const blockDataModel = new DataModelBuilder()
  .from<BlockDataV1>("Ver_2026_05_29")
  .init(() => ({
    device: DEFAULT_DEVICE,
    selectedScopes: [],
    // Resource defaults for the embedding step (Advanced Settings).
    mem: 32,
    cpu: 16,
    tableState: createPlDataTableStateV2(),
  }));
