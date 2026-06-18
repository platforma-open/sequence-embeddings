# Overview

Turns antibody, TCR, and peptide sequences into numerical vectors — *embeddings* — that place biologically similar sequences close together, so you can compare, cluster, and visualize a repertoire or library by similarity even when the amino acid sequences differ. The vectors come from a protein language model trained on tens of millions of natural proteins, capturing structural and evolutionary patterns that plain sequence identity misses.

The block reads peptide datasets (from Peptide Profiling) or antibody/TCR clonotypes (from MiXCR Clonotyping or Import V(D)J Data, bulk or single-cell). You choose which region to embed: the **full peptide**; the **CDR3** per chain; the **full variable domain** per chain; the **paired Fv** (heavy and light chains together, antibody only); or the **scFv** construct (the full VH–linker–VL polypeptide). Full-domain and Fv options require a fully assembled variable domain. When the input carries only CDR3, only the CDR3 option is offered.

You choose the **model fidelity** — *Standard* (ESM-2 150M: faster, standard quality) or *High* (ESM-2 650M: high quality, slower). The block uses a GPU automatically when one is available; High fidelity is much faster on a GPU, so on a CPU-only machine with many sequences Standard fidelity is the best choice.

Embeddings are numeric vectors capturing each sequence's properties — used downstream for clustering and 2D sequence-space maps. In Platforma they feed directly into **Embedding Clustering** and **Sequence Space**, for similarity grouping and visualization.

The block uses **ESM-2**, a protein language model developed by Meta AI Research and released under the MIT license. For more information, please see [https://github.com/facebookresearch/esm](https://github.com/facebookresearch/esm) and cite the following publication if used in your research:

> Lin Z, Akin H, Rao R, Hie B, Zhu Z, Lu W, Smetanin N, Verkuil R, Kabeli O, Shmueli Y, dos Santos Costa A, Fazel-Zarandi M, Sercu T, Candido S, Rives A. Evolutionary-scale prediction of atomic-level protein structure with a language model. _Science_ **379**(6637), 1123–1130 (2023). [https://doi.org/10.1126/science.ade2574](https://doi.org/10.1126/science.ade2574)
