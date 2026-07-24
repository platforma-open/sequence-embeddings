---
'@platforma-open/milaboratories.sequence-embeddings.model': patch
'@platforma-open/milaboratories.sequence-embeddings.workflow': patch
'@platforma-open/milaboratories.sequence-embeddings.ui': patch
'@platforma-open/milaboratories.sequence-embeddings': patch
---

Recognize synthetic-repertoire-profiler variant sequences: the peptide-family sequence selector now also matches `pl7.app/sequence` columns with the `amplicon-sequence` feature (in addition to `peptide`), so profiler outputs become embeddable via the peptide scope.
