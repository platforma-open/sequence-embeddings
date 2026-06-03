import { platforma } from "@platforma-open/milaboratories.sequence-embeddings.model";
import { defineAppV3 } from "@platforma-sdk/ui-vue";
import { watchEffect } from "vue";
import MainPage from "./pages/MainPage.vue";

export const sdkPlugin = defineAppV3(platforma, (app) => {
  // Block-label exception per `hairpin.md` — output → data write that the
  // block-label tolerates because no SDK affordance exists yet. Same pattern
  // as `sequence-properties`, `clonotype-clustering`, `cdr3-spectratype`,
  // etc. Watches the input options to populate the subtitle label from the
  // selected ref's display name.
  watchEffect(() => {
    const anchor = app.model.data.inputAnchor;
    const opts = app.model.outputs.inputOptions ?? [];
    const match = anchor
      ? opts.find((o) => o.ref?.blockId === anchor.blockId && o.ref?.name === anchor.name)
      : undefined;
    app.model.data.defaultBlockLabel = match?.label ?? "";
  });

  // Scope default + reconcile (slice 01 R6 / R6c). Seed `selectedScopes` from
  // the Default Selection Rule (availableScopes.defaults) once per anchor,
  // guarded by `scopesInitializedForAnchor`. The guard makes this a one-shot
  // init, not a continuous resync (same hairpin-tolerated output→data pattern as
  // the label watch above): on server-patch / panel reopen the key matches and
  // the user's selection is preserved; on a genuinely new input the key differs
  // and the new input's defaults apply.
  watchEffect(() => {
    const anchor = app.model.data.inputAnchor;
    if (!anchor) {
      if (app.model.data.scopesInitializedForAnchor !== undefined) {
        app.model.data.selectedScopes = [];
        app.model.data.scopesInitializedForAnchor = undefined;
      }
      return;
    }
    const key = JSON.stringify(anchor);
    if (app.model.data.scopesInitializedForAnchor === key) return;
    const config = app.model.outputs.availableScopes;
    if (!config) return; // scopes for this anchor not resolved yet — wait
    app.model.data.selectedScopes = config.defaults;
    app.model.data.scopesInitializedForAnchor = key;
  });

  return {
    routes: {
      "/": () => MainPage,
    },
  };
});

export const useApp = sdkPlugin.useApp;
