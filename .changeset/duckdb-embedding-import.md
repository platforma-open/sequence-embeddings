---
'@platforma-open/milaboratories.sequence-embeddings.workflow': patch
'@platforma-open/milaboratories.sequence-embeddings': patch
---

Import the embedding column through the DuckDB engine (MILAB-7195).

The column is long-format, one row per sequence per dimension, so a large library
overruns the Polars importer's 2^32 row ceiling and the run dies at import. DuckDB
imports it in one out-of-core pass with no such limit. Needs an SDK carrying the new
engine; older SDKs ignore the field and import as before.
