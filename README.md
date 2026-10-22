# Sequence Embeddings

Turn antibody, TCR, and peptide sequences into vectors that place biologically similar sequences close together. This Platforma block runs protein language models over your dataset so you can compare, cluster, and map a repertoire by learned similarity — relating sequences that differ substantially in amino acids but behave alike.

Open-source analysis block for Platforma, the biologics discovery platform by MiLaboratories. For the full no-code workflow, see [platforma.bio](https://platforma.bio/).

## What it does

Sequence identity is a poor proxy for function. Two antibodies can differ at many positions and still bind the same target; two that differ at one critical residue may not. A protein language model, trained on tens of millions of natural proteins, has learned which substitutions preserve structure and function — so in the vector space it produces, proximity means something closer to biological similarity than string similarity does.

This block computes those vectors for your sequences. You choose **what to embed**: the full peptide; the CDR3 per chain; the full variable domain per chain; the paired Fv, heavy and light together (antibody only); or the complete scFv construct including its linker. Full-domain and Fv options need a fully assembled variable domain — when the input carries only CDR3, only the CDR3 option is offered.

You also choose the **model**. A universal protein language model works on any sequence type, or a specialist model tuned to one kind of sequence — antibodies, nanobodies, TCRs, or peptides — gives sharper representations on that type. The block recommends a suitable model for whatever sequence you picked and only offers compatible ones. For the universal model you additionally pick a fidelity: Standard is faster, High is better and slower.

GPUs are used automatically when available. The larger models — High-fidelity universal, and some specialists — are dramatically faster on a GPU, so on a CPU-only machine with many sequences a lighter model is usually the better choice.

The vectors feed [Embedding Clustering](https://github.com/platforma-open/embedding-clustering) for similarity grouping and [Sequence Space](https://github.com/platforma-open/clonotype-space) for 2D maps of the library.

## Inputs & outputs

* **Input:** peptide datasets from [Peptide Profiling](https://github.com/platforma-open/peptide-extraction), or antibody and TCR clonotypes from [MiXCR Clonotyping](https://github.com/platforma-open/mixcr-clonotyping) or [Import V(D)J Data](https://github.com/platforma-open/import-vdj-data), bulk or single-cell.
* **Output:** a per-sequence embedding vector for each selected region and model, consumable by Embedding Clustering and Sequence Space.

## Specifications

| | |
|---|---|
| Block title in app | Sequence Embeddings |
| Universal model | ESM-2, with Standard or High fidelity |
| Specialist models | Antibody, paired antibody, nanobody, CDR-H3, TCR, and peptide models |
| Embeddable regions | Full peptide; CDR3 per chain; full variable domain per chain; paired Fv (antibody only); full scFv construct. With ESM-2 only: framework regions, CDR1/CDR2 and custom regions (listed in the sequence dropdown under "ESM-2 only") |
| Model compatibility | Only models valid for the selected sequence are offered, with a recommendation |
| Acceleration | GPU used automatically when available |
| Modalities | Antibodies, TCRs, peptides — bulk and single-cell |

## Use cases

* **Similarity clustering beyond identity:** produce the vectors [Embedding Clustering](https://github.com/platforma-open/embedding-clustering) uses to group functionally related but sequence-divergent candidates.
* **Library maps:** feed [Sequence Space](https://github.com/platforma-open/clonotype-space) to project a whole library into two dimensions by learned similarity.
* **Paired-chain representation:** embed heavy and light together so the vector describes the Fv rather than one chain in isolation.
* **CDR3-focused comparison:** embed CDR3 alone when the binding loop is the question and framework similarity would dominate.
* **Format-specific accuracy:** use the nanobody, TCR, or peptide specialist model rather than a general-purpose one on those sequence types.
* **scFv constructs:** embed the full VH–linker–VL polypeptide as a single sequence.

## FAQ

### What is an embedding?

A fixed-length numeric vector representing a sequence, produced by a protein language model. Sequences the model considers similar end up close together in that space, which lets you cluster and visualize by learned similarity rather than by shared substrings.

### Which region should I embed?

Whatever the biological question is about. CDR3 for binding-loop comparison; the full variable domain when the whole domain matters; paired Fv when heavy–light context matters; the scFv construct when the construct itself is the unit. Note that full-domain and Fv options need a fully assembled variable domain upstream.

Framework regions, CDR1/CDR2 and any custom regions the input exports are listed in a separate **ESM-2 only** section of the sequence dropdown. Only ESM-2 can embed these, and users should be careful to note that it hasn't been trained specifically to capture their context when they are embedded on their own. ESM-2 was not trained on sequences shorter than 11 amino acids.

### Universal or specialist model?

A specialist model tuned to your sequence type generally gives sharper representations for that type. Use the universal model for mixed inputs, or when no specialist covers your sequence. The block recommends a fit for whatever you picked and hides incompatible options.

### What does model fidelity change?

Standard is faster; High is more accurate and slower. On a GPU the difference in runtime is modest; on CPU with many sequences it is substantial, so pick Standard or a lighter specialist when running without a GPU.

### What do I do with the embeddings?

Feed them to Embedding Clustering to group related sequences, or to Sequence Space to project the library into a 2D map. They are numeric feature columns, so anything that consumes per-sequence numeric features can use them.

## Citation

The universal model is **ESM-2**, developed by Meta AI Research and released under the MIT license. See [github.com/facebookresearch/esm](https://github.com/facebookresearch/esm) and cite:

> Lin, Z., Akin, H., Rao, R., Hie, B., Zhu, Z., Lu, W., Smetanin, N., Verkuil, R., Kabeli, O., Shmueli, Y., dos Santos Costa, A., Fazel-Zarandi, M., Sercu, T., Candido, S., & Rives, A. (2023). Evolutionary-scale prediction of atomic-level protein structure with a language model. *Science* **379**(6637), 1123–1130. [https://doi.org/10.1126/science.ade2574](https://doi.org/10.1126/science.ade2574)

The specialist models are third-party open-source protein language models, each under its own permissive, commercially usable license. If you use one, please also cite its authors:

> **CurrAb** (antibody) — Burbach, S. M., & Briney, B. (2025). A curriculum learning approach to training antibody language models. *PLOS Computational Biology*. [https://doi.org/10.1371/journal.pcbi.1013473](https://doi.org/10.1371/journal.pcbi.1013473)

> **AbLang2** (antibody, paired) — Olsen, T. H., Moal, I. H., & Deane, C. M. (2024). Addressing the antibody germline bias and its effect on language models for improved antibody design. *Bioinformatics* **40**(11), btae618. [https://doi.org/10.1093/bioinformatics/btae618](https://doi.org/10.1093/bioinformatics/btae618)

> **VHHBERT** (nanobody) — Tsuruta, H., Yamazaki, H., Maeda, R., Tamura, R., & Imura, A. (2024). A SARS-CoV-2 Interaction Dataset and VHH Sequence Corpus for Antibody Language Models. *NeurIPS*. [https://arxiv.org/abs/2405.18749](https://arxiv.org/abs/2405.18749)

> **H3BERTa** (antibody CDR-H3) — Rodella, C., & Lemmin, T. (2026). H3BERTa: A CDR-H3-specific language model for antibody repertoire analysis. *Patterns*, 101561. [https://doi.org/10.1016/j.patter.2026.101561](https://doi.org/10.1016/j.patter.2026.101561)

> **TCR-BERT** (TCR) — Wu, K., Yost, K. E., Daniel, B., Belk, J. A., Xia, Y., Egawa, T., Satpathy, A. T., Chang, H. Y., & Zou, J. TCR-BERT: learning the grammar of T-cell receptors for flexible antigen-binding analyses. *bioRxiv* (2021); PMLR: Machine Learning for Health (2024). [https://doi.org/10.1101/2021.11.18.469186](https://doi.org/10.1101/2021.11.18.469186)

> **PeptideCLM-2** (peptide) — Feller, A. L., Secor, M., Swanson, S., Wilke, C. O., & Deibler, K. (2026). Scaling SMILES-based chemical language models for therapeutic peptide engineering. *bioRxiv*. [https://doi.org/10.64898/2026.01.06.697994](https://doi.org/10.64898/2026.01.06.697994)

## Part of the Platforma ecosystem

This block is part of [Platforma](https://platforma.bio/) by [MiLaboratories](https://github.com/milaboratory), built on [ESM-2](https://github.com/facebookresearch/esm) and third-party specialist protein language models. Explore the other open-source blocks at [github.com/platforma-open](https://github.com/platforma-open) and the docs for antibody discovery at [docs.platforma.bio/biology-guides/antibody-discovery](https://docs.platforma.bio/biology-guides/antibody-discovery/).
