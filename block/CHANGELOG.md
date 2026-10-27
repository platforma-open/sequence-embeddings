# @platforma-open/milaboratories.sequence-embeddings

## 1.6.0

### Minor Changes

- 9ce9100: Update the SDK and accept a subset filter (e.g. a Repertoire Labeling label or Lead Selection picks) with the input dataset. The dataset list is built with buildDatasetOptions (requireEnrichments: false). Only the subset's entities are embedded and counted in the run report. Outputs of a subset run carry pl7.app/inputSubset in their domain and a trace rooted in the subset, so their labels name it, and the subtitle names the subset. Unfiltered runs are unchanged.

## 1.5.0

### Minor Changes

- e7c0a24: - ESM-2 can now embed any amino-acid region the input exports, including frameworks (FR1–FR4), CDR1/CDR2 and custom regions. Each region gets its own output column.
  - The sequence dropdown lists the typical choices first. When the input exports framework, CDR1/CDR2 or custom regions, they follow in a separate "ESM-2 only" section, since only ESM-2 can embed them.
  - After an ESM-2 run, the run report adds "Too short" and "Min trained length" columns when any embedded sequence is shorter than 11 amino acids. ESM-2 was not trained on sequences that short, so their embeddings may be unreliable.
  - When the embedding step fails, the run report shows "Failed" in the "Successfully embedded" column instead of a count. Previously the counts, which are computed separately, made a failed run look successful.
  - The README and block description now cover the extra regions and their caveats with ESM-2.

## 1.4.3

### Patch Changes

- 5f59014: Raise the SDK catalog to latest.

## 1.4.2

### Patch Changes

- c2fffd2: Add the block's `kind` component

  Every block must now declare a kind — a fourth component next to model, workflow
  and ui. It carries the block's identity and its init-params contract, and
  `block-tools structure check` fails without it.

  This block's contract is `{ inputAnchor?: PlRef; embedding?: EmbeddingSelection }`.
  A project template can therefore seed a new instance with both the dataset to
  embed and the (scope, model) combination to embed it with. Everything else in
  the block's data still defaults: `embeddingInitializedForAnchor` is the UI's
  re-seed guard, `mem` and `cpu` are opt-in overrides the workflow otherwise sizes
  itself, and `defaultBlockLabel` is written by the UI from the chosen input.

  The two fields travel as a pair. A scope's `columns` are anchored ids that
  resolve against `inputAnchor`, so an `embedding` seeded without the matching
  `inputAnchor` points at nothing. Both stay optional, and the args projection —
  which already refuses an incomplete selection — is what catches the mismatch
  when the block runs.

  The model's `init` now reads those params, and a new `templateParams` projection
  hands the same two fields back out, so seeding and exporting are inverses. A
  block created without params starts exactly as before.

  The types the contract is built from (`EmbeddingSelection`, `SelectedScope`,
  `EmbeddingModelId`, `Fidelity`, `ScopeFeature`, `ScopeReceptor`,
  `WorkflowReceptor`) moved into the kind package, which owns the contract. The
  model re-exports them, so every existing import path keeps working.

- 0a3a196: Offer antibody and TCR models for VDJ amplicon-profiling input (MILAB-6668).

  `synthetic-repertoire-profiler` tags its whole-variant amino-acid sequence with the
  `amplicon-sequence` feature in both of its modalities, and this block mapped that
  feature onto the `peptide` scope unconditionally. Since model compatibility is gated
  on the scope feature, a DMS **VDJ** run was offered only ESM-2 and PeptideCLM-2, with
  the peptide specialist as the default — no antibody or TCR model was reachable.

  The block now reads the producer's `pl7.app/modality` declaration off the entity
  axis. On a `vdj` run the whole-variant column becomes a `VDJRegion` scope and the
  profiler's CDR3 region column becomes a `CDR3` scope (a new selector discovers the
  region columns, which are keyed by region name in `pl7.app/feature` rather than on
  `pl7.app/vdj/sequence`). That puts CurrAb, VHHBERT, H3BERTa and TCR-BERT in reach.
  Amplicon runs, projects predating the declaration, and peptide-extraction input are
  unaffected — all keep the peptide scope and the PeptideCLM-2 default.

  Receptor handling: the profiler declares VDJ data but emits neither
  `pl7.app/vdj/receptor` nor `pl7.app/vdj/chain`, because germline auto-detection
  builds a custom reference from the user's own parent sequences and has no library
  locus to read. `ScopeReceptor` gains an explicit `"unknown"` value for that case, and
  model filtering relaxes its receptor and heavy-chain gates rather than defaulting to
  `IG` — which would have silently hidden the TCR specialists from TCR data. The trade
  is deliberate: the dropdown can now offer a TCR model for antibody input, so the user
  picks the model that matches their library. With the receptor unknown the default
  model is the universal one (ESM-2) rather than the highest-priority specialist, so no
  receptor-specific model is chosen on the user's behalf from data that does not state
  a receptor.

  AbLang2 is the one model withheld there. It encodes a chain by SLOT — `[seq, ""]` for
  heavy, `["", seq]` for light, told apart by position rather than content — so running
  it needs the chain role the input does not carry, and the wrong slot encodes the
  sequence in the other chain's context with no error and no warning. Every other model
  reads the heavy flag only to unlock itself, so relaxing that gate merely widens the
  choice; here it would be a coin flip on the result. The new `requiresChainRole` flag
  on the model spec marks that class, and unknown-receptor scopes do not offer it.

  Inputs that do carry a receptor or chain key are unchanged, as are inputs with no
  modality declaration — both keep the historical `IG` fallback and full gating.

## 1.4.1

### Patch Changes

- 7305e43: Recognize synthetic-repertoire-profiler variant sequences: the peptide-family sequence selector now also matches `pl7.app/sequence` columns with the `amplicon-sequence` feature (in addition to `peptide`), so profiler outputs become embeddable via the peptide scope.

## 1.4.0

### Minor Changes

- 622bac2: workflow-tengo": 6.8.0

## 1.3.0

### Minor Changes

- ed3fcfb: Optimize embedding compute-resource requests via the fluent `exec.formula` API (workflow-tengo 6.7.x) and size the GPU batch from the allocated VRAM.

  - **Workflow** — the embedding exec now declares resources with `.resources({ onCPU | onGPU })` instead of a flat 16 CPU / 32 GiB / 16 GiB-VRAM request:
    - GPU path: host CPU/RAM cut to 2 cores / 8 GiB (single streaming process; the accelerator does the work, per-chunk CPU work is interleaved, and the heavy PColumn→TSV shaping is already upstream CPU execs), and VRAM is model-tiered — 6 GiB for the wider checkpoints (ESM-2 650M, CurrAb, PeptideCLM-2), 3 GiB otherwise. This fits the cheapest fractional-L4 tiers (gpu-3g `g6f.xlarge` / gpu-6g `g6f.2xlarge`) instead of forcing the 4×L40S `g6e.12xlarge` the old 16 CPU / 32 GiB / 16 GiB-VRAM request required.
    - CPU path: cores scale with the batch's token volume (`size("batch")`, 4–16) and RAM tracks the core count (2 GiB/core, 8–32 GiB), with a `.staticFallback` equal to the old fixed 16 / 32 GiB for backends that cannot evaluate resource formulas. Advanced-settings overrides still win per dimension.
    - The run-report counting pass now sizes its RAM from the source-TSV size (it full-loads the TSV), replacing the flat 4 GiB that could OOM on large inputs.
  - **Software** — on CUDA the per-forward token budget is now auto-sized from the allocated VRAM (`PLATFORMA_GPU_MEMORY`), mirroring how `--max-memory-gb` sizes the host-RAM path: a larger VRAM request yields larger batches (higher throughput), a smaller one stays safe. An explicit `--token-budget` still wins, and the halve-on-OOM retry remains the backstop.
  - **Model / UI** — the `mem` / `cpu` Advanced-Settings fields are now opt-in: a new block leaves them unset so the workflow's automatic sizing applies, and a user value overrides it per dimension. (Existing projects keep any value they already had.)

## 1.2.0

### Minor Changes

- 11d7d1f: Add new models

## 1.1.1

### Patch Changes

- Updated dependencies [dd136a0]
  - @platforma-open/milaboratories.sequence-embeddings.model@1.1.1
  - @platforma-open/milaboratories.sequence-embeddings.ui@1.1.1
  - @platforma-open/milaboratories.sequence-embeddings.workflow@1.1.1

## 1.1.0

### Minor Changes

- 04e9415: First release

### Patch Changes

- Updated dependencies [04e9415]
  - @platforma-open/milaboratories.sequence-embeddings.workflow@1.1.0
  - @platforma-open/milaboratories.sequence-embeddings.model@1.1.0
  - @platforma-open/milaboratories.sequence-embeddings.ui@1.1.0
