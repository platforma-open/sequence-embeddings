# @platforma-open/milaboratories.sequence-embeddings

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
