import { platforma } from "@platforma-open/milaboratories.sequence-embeddings.model";
import { defineAppV3 } from "@platforma-sdk/ui-vue";
import { watchEffect } from "vue";
import MainPage from "./pages/MainPage.vue";

export const sdkPlugin = defineAppV3(platforma, (app) => {
  // Watch the input options to populate the subtitle label from the
  // selected ref's display name.
  watchEffect(() => {
    const anchor = app.model.data.inputAnchor;
    const opts = app.model.outputs.inputOptions ?? [];
    const match = anchor
      ? opts.find((o) => o.ref?.blockId === anchor.blockId && o.ref?.name === anchor.name)
      : undefined;
    app.model.data.defaultBlockLabel = match?.label ?? "";
  });

  // Seed `selectedScopes` with the first-connection defaults
  // (availableScopes.defaults) once per anchor, guarded by
  // `scopesInitializedForAnchor`. On server-patch / panel reopen the key matches and the
  // user's selection is preserved; on a genuinely new input the key differs
  // and the new input's defaults apply.
  watchEffect(() => {
    const anchor = app.model.data.inputAnchor;
    const key = anchor ? JSON.stringify(anchor) : undefined;

    // Dataset changed (or was cleared) since the last seed → drop the previous
    // input's selection immediately. Without this the old selectedScopes (column
    // ids resolved against the previous anchor) linger until the new config
    // resolves, and the workflow would resolve them against the new anchor.
    if (app.model.data.scopesInitializedForAnchor !== key) {
      if (app.model.data.selectedScopes.length > 0) app.model.data.selectedScopes = [];
    }

    if (!anchor) {
      if (app.model.data.scopesInitializedForAnchor !== undefined) {
        app.model.data.scopesInitializedForAnchor = undefined;
      }
      return;
    }

    if (app.model.data.scopesInitializedForAnchor === key) return;

    const config = app.model.outputs.availableScopes;
    // `availableScopes` is retentive, so right after a dataset switch it may
    // still hold the PREVIOUS anchor's config. Gate on `forAnchor` so we seed
    // this input's defaults — not the retained stale ones — and only stamp the
    // guard once a config matching this anchor has actually been applied.
    if (!config || config.forAnchor !== key) return; // wait for the fresh config
    app.model.data.selectedScopes = config.defaults;
    app.model.data.scopesInitializedForAnchor = key;
  });

  return {
    routes: {
      "/": () => MainPage,
    },
    progress: () => app.model.outputs.isRunning,
  };
});

export const useApp = sdkPlugin.useApp;
