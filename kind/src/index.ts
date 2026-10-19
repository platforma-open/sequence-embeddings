import { assertParamsObject, defineBlockKind } from "@platforma-sdk/block-kind";
import type { PlRef, SUniversalPColumnId } from "@platforma-sdk/model";
import { isPlRef } from "@platforma-sdk/model";
import { name, version } from "../package.json" with { type: "json" };
import type {
  EmbeddingModelId,
  EmbeddingSelection,
  Fidelity,
  ScopeFeature,
  ScopeReceptor,
  SelectedScope,
} from "./types";

export * from "./types";

/**
 * This block's init-params contract — the upstream dataset a new instance embeds,
 * and the (scope, model) combination it embeds it with.
 *
 * Those two are what a creator actually chooses; everything else in the model's
 * `BlockData` always defaults. `embeddingInitializedForAnchor` is the UI's
 * re-seed guard, `mem`/`cpu` are opt-in overrides the workflow otherwise sizes
 * itself, and `defaultBlockLabel` is written by the UI from the chosen input's
 * option label.
 *
 * The two fields travel as a pair. A scope's `columns` are anchored ids that
 * resolve against `inputAnchor`, so a template carrying an `embedding` without
 * the matching `inputAnchor` seeds a selection that points at nothing. The
 * contract cannot express that pairing as a type, so both stay optional and the
 * args projection — which already refuses an incomplete selection — is what
 * catches the mismatch at run time. `filterRef` narrows `inputAnchor` to a
 * subset and likewise means nothing without it.
 *
 * Both fields are optional because the projection hands live state back
 * untouched, and a freshly created block holds `undefined` and `{}` there.
 * Requiring either would make the block export a file its own kind refuses to
 * apply, so export and apply would stop being inverses.
 */
export type BlockParams = {
  inputAnchor?: PlRef;
  /** A subset column (`pl7.app/isSubset`) of the dataset; only its entities are embedded. */
  filterRef?: PlRef;
  embedding?: EmbeddingSelection;
};

// Each closed set is declared once, as a const tuple, and its type is derived
// from it. That is what keeps the run-time check and the compile-time union from
// drifting: adding a case to the tuple widens the type, and a case added to the
// type alone does not compile.
const SCOPE_FEATURES = ["peptide", "CDR3", "VDJRegion", "Fv", "scFv"] as const;
const SCOPE_CHAINS = ["A", "B", ""] as const;
const SCOPE_RECEPTORS = ["IG", "TCRAB", "TCRGD", "unknown"] as const;
const FIDELITIES = ["high", "standard"] as const;
const EMBEDDING_MODEL_IDS = [
  "esm2",
  "ablang2",
  "currab",
  "vhhbert",
  "h3berta",
  "tcr-bert",
  "peptideclm2",
  "sceptr",
] as const;

function assertMember<T extends string>(
  key: string,
  allowed: readonly T[],
  value: unknown,
): asserts value is T {
  if (typeof value !== "string" || !(allowed as readonly string[]).includes(value)) {
    throw new Error(`'${key}' must be one of ${allowed.join(", ")}. Got: ${JSON.stringify(value)}`);
  }
}

function assertString(key: string, value: unknown): asserts value is string {
  if (typeof value !== "string") {
    throw new Error(`'${key}' must be a string. Got: ${JSON.stringify(value)}`);
  }
}

/**
 * `SUniversalPColumnId` is a branded string and the SDK ships no guard for it, so
 * the check stops at "non-empty string". Whether an id resolves is a question
 * about the anchor it is read against, not about the shape of the params.
 */
function assertColumnIds(value: unknown): asserts value is SUniversalPColumnId[] {
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error("'scope.columns' must be a non-empty array of column ids.");
  }
  for (const [i, id] of value.entries()) {
    if (typeof id !== "string" || id === "") {
      throw new Error(`'scope.columns[${i}]' must be a non-empty column id string.`);
    }
  }
}

/**
 * A scope is a snapshot the UI takes from the picker, so every one of its fields
 * is present once the scope exists at all. There is no half-written scope to be
 * lenient about — the leniency lives one level up, where `scope` itself may be
 * missing.
 */
function assertSelectedScope(value: unknown): asserts value is SelectedScope {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("'embedding.scope' must be an object.");
  }
  const scope = value as Record<string, unknown>;

  assertString("scope.id", scope.id);
  assertString("scope.label", scope.label);
  assertMember<ScopeFeature>("scope.feature", SCOPE_FEATURES, scope.feature);
  assertMember<"A" | "B" | "">("scope.chain", SCOPE_CHAINS, scope.chain);
  assertMember<ScopeReceptor>("scope.receptor", SCOPE_RECEPTORS, scope.receptor);
  assertColumnIds(scope.columns);

  if (typeof scope.isHeavy !== "boolean") {
    throw new Error("'scope.isHeavy' must be a boolean.");
  }
}

/**
 * The selection the user assembles. All three fields are optional in the model,
 * because the UI fills one dropdown at a time — so each is checked only when
 * present. Whether the resulting pair is a *compatible* (scope, model) is
 * meaning, not shape; the args projection is what decides that, and it does so
 * only when the block runs.
 */
function parseEmbedding(value: unknown): EmbeddingSelection {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error("'embedding' must be an object.");
  }
  const { scope, model, fidelity } = value as Record<string, unknown>;

  if (scope !== undefined) assertSelectedScope(scope);
  if (model !== undefined) {
    assertMember<EmbeddingModelId>("embedding.model", EMBEDDING_MODEL_IDS, model);
  }
  if (fidelity !== undefined) {
    assertMember<Fidelity>("embedding.fidelity", FIDELITIES, fidelity);
  }

  return { scope, model, fidelity };
}

/**
 * The same contract at runtime, for params arriving from a template file rather
 * than from typed code — the only point that can catch a hand-written entry
 * being wrong.
 *
 * Keys the contract does not name are dropped by not being read; refusing them
 * would mean holding a list of field names as strings that nothing keeps in step
 * with the type.
 */
function parseInitializationParams(value: unknown): BlockParams {
  assertParamsObject(value);

  const { inputAnchor, filterRef, embedding } = value;

  // A readable `{ block, name }` reference is expanded to a full `PlRef` before
  // this runs, so `isPlRef` is the only shape to accept here.
  if (inputAnchor !== undefined && !isPlRef(inputAnchor)) {
    throw new Error(
      "'inputAnchor' must be a reference to an upstream column, written as { block, name }.",
    );
  }

  if (filterRef !== undefined && !isPlRef(filterRef)) {
    throw new Error(
      "'filterRef' must be a reference to a subset column, written as { block, name }.",
    );
  }

  return {
    inputAnchor,
    filterRef,
    embedding: embedding === undefined ? undefined : parseEmbedding(embedding),
  };
}

// Identity (`name`/`version`) comes from this package's own `package.json`, so
// the on-wire `{name}@{version}` reference can never drift from what npm
// publishes; the bundler inlines the JSON import.
export const kind = defineBlockKind<BlockParams>({
  name,
  version,
  parseInitializationParams,
});
