# @platforma-open/milaboratories.sequence-embeddings.kind

## 1.2.0

### Minor Changes

- 9ce9100: Update the SDK and accept a subset filter (e.g. a Repertoire Labeling label or Lead Selection picks) with the input dataset. The dataset list is built with buildDatasetOptions (requireEnrichments: false). Only the subset's entities are embedded and counted in the run report. Outputs of a subset run carry pl7.app/inputSubset in their domain and a trace rooted in the subset, so their labels name it, and the subtitle names the subset. Unfiltered runs are unchanged.

## 1.1.0

### Minor Changes

- e7c0a24: - ESM-2 can now embed any amino-acid region the input exports, including frameworks (FR1–FR4), CDR1/CDR2 and custom regions. Each region gets its own output column.
  - The sequence dropdown lists the typical choices first. When the input exports framework, CDR1/CDR2 or custom regions, they follow in a separate "ESM-2 only" section, since only ESM-2 can embed them.
  - After an ESM-2 run, the run report adds "Too short" and "Min trained length" columns when any embedded sequence is shorter than 11 amino acids. ESM-2 was not trained on sequences that short, so their embeddings may be unreliable.
  - When the embedding step fails, the run report shows "Failed" in the "Successfully embedded" column instead of a count. Previously the counts, which are computed separately, made a failed run look successful.
  - The README and block description now cover the extra regions and their caveats with ESM-2.
