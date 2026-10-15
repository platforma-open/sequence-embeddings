---
"@platforma-open/milaboratories.sequence-embeddings": minor
"@platforma-open/milaboratories.sequence-embeddings.kind": minor
"@platforma-open/milaboratories.sequence-embeddings.model": minor
"@platforma-open/milaboratories.sequence-embeddings.ui": minor
"@platforma-open/milaboratories.sequence-embeddings.workflow": minor
---

Update the SDK and accept a subset filter (e.g. a Repertoire Labeling label or Lead Selection picks) with the input dataset. The dataset list is built with buildDatasetOptions (requireEnrichments: false). Only the subset's entities are embedded and counted in the run report. Outputs of a subset run carry pl7.app/inputSubset in their domain and a trace rooted in the subset, so their labels name it, and the subtitle names the subset. Unfiltered runs are unchanged.
