# Overview

Turns antibody, TCR, and peptide sequences into numerical vectors — *embeddings* — that place biologically similar sequences close together, so you can compare, cluster, and visualize a repertoire or library by similarity even when the amino acid sequences differ. The vectors come from a protein language model trained on tens of millions of natural proteins, capturing structural and evolutionary patterns that plain sequence identity misses.

The block reads peptide datasets (from Peptide Profiling) or antibody/TCR clonotypes (from MiXCR Clonotyping or Import V(D)J Data, bulk or single-cell). You choose which region to embed: the **full peptide**; the **CDR3** per chain; the **full variable domain** per chain; the **paired Fv** (heavy and light chains together, antibody only); or the **scFv** construct (the full VH–linker–VL polypeptide). Full-domain and Fv options require a fully assembled variable domain. When the input carries only CDR3, only the CDR3 option is offered.

You also choose the **model** for each embedding: a **universal** protein language model that works on any sequence type, or a **specialist** model tuned to a particular kind of sequence — antibodies, nanobodies, TCRs, or peptides — for sharper representations on that type. The block recommends a suitable model for each sequence you pick and only offers models compatible with it. For the universal model you can additionally choose a **fidelity** — *Standard* (faster) or *High* (higher quality, slower). The block uses a GPU automatically when one is available; the larger models (High-fidelity universal, some specialists) are much faster on a GPU, so on a CPU-only machine with many sequences a lighter model is the best choice.

Embeddings are numeric vectors capturing each sequence's properties — used downstream for clustering and 2D sequence-space maps. In Platforma they feed directly into **Embedding Clustering** and **Sequence Space**, for similarity grouping and visualization.

The **universal** model is **ESM-2**, a protein language model developed by Meta AI Research and released under the MIT license. For more information, please see [https://github.com/facebookresearch/esm](https://github.com/facebookresearch/esm) and cite the following publication if used in your research:

> Lin Z, Akin H, Rao R, Hie B, Zhu Z, Lu W, Smetanin N, Verkuil R, Kabeli O, Shmueli Y, dos Santos Costa A, Fazel-Zarandi M, Sercu T, Candido S, Rives A. Evolutionary-scale prediction of atomic-level protein structure with a language model. _Science_ **379**(6637), 1123–1130 (2023). [https://doi.org/10.1126/science.ade2574](https://doi.org/10.1126/science.ade2574)

The **specialist** models are third-party open-source protein language models, each included under its own permissive, commercially-usable license. If you use a specialist model, please also cite its authors:

> **CurrAb** (antibody) — Burbach SM, Briney B. A curriculum learning approach to training antibody language models. _PLOS Computational Biology_ (2025). [https://doi.org/10.1371/journal.pcbi.1013473](https://doi.org/10.1371/journal.pcbi.1013473)

> **AbLang2** (antibody, paired) — Olsen TH, Moal IH, Deane CM. Addressing the antibody germline bias and its effect on language models for improved antibody design. _Bioinformatics_ **40**(11), btae618 (2024). [https://doi.org/10.1093/bioinformatics/btae618](https://doi.org/10.1093/bioinformatics/btae618)

> **VHHBERT** (nanobody) — Tsuruta H, Yamazaki H, Maeda R, Tamura R, Imura A. A SARS-CoV-2 Interaction Dataset and VHH Sequence Corpus for Antibody Language Models. _NeurIPS_ (2024). [https://arxiv.org/abs/2405.18749](https://arxiv.org/abs/2405.18749)

> **H3BERTa** (antibody CDR-H3) — Rodella C, Lemmin T. H3BERTa: A CDR-H3-specific language model for antibody repertoire analysis. _Patterns_, 101561 (2026). [https://doi.org/10.1016/j.patter.2026.101561](https://doi.org/10.1016/j.patter.2026.101561)

> **TCR-BERT** (TCR) — Wu K, Yost KE, Daniel B, Belk JA, Xia Y, Egawa T, Satpathy AT, Chang HY, Zou J. TCR-BERT: learning the grammar of T-cell receptors for flexible antigen-binding analyses. _bioRxiv_ (2021); PMLR: Machine Learning for Health (2024). [https://doi.org/10.1101/2021.11.18.469186](https://doi.org/10.1101/2021.11.18.469186)

> **PeptideCLM-2** (peptide) — Feller AL, Secor M, Swanson S, Wilke CO, Deibler K. Scaling SMILES-based chemical language models for therapeutic peptide engineering. _bioRxiv_ (2026). [https://doi.org/10.64898/2026.01.06.697994](https://doi.org/10.64898/2026.01.06.697994)
