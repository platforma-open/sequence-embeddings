---
"@platforma-open/milaboratories.sequence-embeddings.software": minor
"@platforma-open/milaboratories.sequence-embeddings.workflow": minor
"@platforma-open/milaboratories.sequence-embeddings.model": minor
"@platforma-open/milaboratories.sequence-embeddings.kind": minor
"@platforma-open/milaboratories.sequence-embeddings.ui": minor
"@platforma-open/milaboratories.sequence-embeddings": minor
---

- ESM-2 can now embed any amino-acid region the input exports, including frameworks (FR1–FR4), CDR1/CDR2 and custom regions. Each region gets its own output column.
- The sequence dropdown lists only the typical choices by default. An "All regions" checkbox next to it adds the framework, CDR1/CDR2 and custom regions, which can only be embedded with ESM-2.
- After an ESM-2 run, a warning reports how many embedded sequences are shorter than 11 amino acids. ESM-2 was not trained on sequences that short, so their embeddings may be unreliable.
- When the embedding step fails, the run report shows "Failed" in the "Successfully embedded" column instead of a count. Previously the counts, which are computed separately, made a failed run look successful.
