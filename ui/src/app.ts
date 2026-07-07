import {
  platforma,
  recommendedModel,
} from "@platforma-open/milaboratories.sequence-embeddings.model";
import { defineAppV3 } from "@platforma-sdk/ui-vue";
import { watchEffect } from "vue";
import MainPage from "./pages/MainPage.vue";

export const sdkPlugin = defineAppV3(platforma, (app) => {
  // The block embeds ONE (sequence, model) combination per run. `data.embedding` is
  // always present (init `{}`), so the dropdowns always have an object to bind to.
  // This watcher seeds it per anchor from the FIRST recommended default scope + its
  // specialist model, guarded by `embeddingInitializedForAnchor`. On server-patch /
  // panel reopen the key matches and the user's selection is preserved; on a
  // genuinely new input the key differs and the new input's default applies.
  watchEffect(() => {
    const anchor = app.model.data.inputAnchor;
    const key = anchor ? JSON.stringify(anchor) : undefined;

    // Dataset changed (or cleared) since the last seed → the selection's scope was
    // resolved against the previous anchor, so reset it to a blank selection.
    if (app.model.data.embeddingInitializedForAnchor !== key) {
      if (app.model.data.embedding.scope !== undefined) {
        app.model.data.embedding = {};
      }
      if (!anchor) {
        if (app.model.data.embeddingInitializedForAnchor !== undefined) {
          app.model.data.embeddingInitializedForAnchor = undefined;
        }
        return; // no input yet → keep the blank selection; dropdowns render empty
      }
    }

    if (app.model.data.embeddingInitializedForAnchor === key) return;

    const config = app.model.outputs.availableScopes;
    // `availableScopes` is retentive, so right after a dataset switch it may still
    // hold the PREVIOUS anchor's config. Gate on `forAnchor` so we seed this input's
    // default — not the retained stale one — and stamp the guard only once a config
    // matching this anchor has been applied. (Selection stays blank while we wait.)
    if (!config || config.forAnchor !== key) return;
    const scope = config.defaults[0]; // single selection → the first recommended scope
    if (scope) {
      app.model.data.embedding = {
        scope,
        model: recommendedModel(scope.feature, scope.isHeavy, {
          receptor: config.receptor,
          paired: config.paired,
        }),
        fidelity: "standard",
      };
    } // else: no default scope → keep the blank selection (user picks from the dropdowns).
    app.model.data.embeddingInitializedForAnchor = key;
  });

  return {
    routes: {
      "/": () => MainPage,
    },
    progress: () => app.model.outputs.isRunning,
  };
});

export const useApp = sdkPlugin.useApp;
