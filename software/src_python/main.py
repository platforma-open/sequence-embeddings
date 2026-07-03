"""Sequence Embeddings — per-sequence embedding compute step.

Loads ONE protein language model (the exact checkpoint the workflow chose and
mounted via --model-path), embeds the sequences for each requested scope, pools
each into one fixed-length vector per sequence, and writes a long-format Parquet
per scope: ``(entity_key, embedding_dim, value)``.

Supports several model families, selected by the workflow via --model-family:
  - ``hf``        standard HuggingFace encoders (AutoModel): ESM-2 (universal),
                  CurrAb, VHHBERT, H3BERTa, TCR-BERT.
  - ``hf-custom`` HF models needing trust_remote_code (PeptideCLM-2), which expose
                  their own pooled output.
  - ``ablang2``   the ablang2 pip model (asset-shipped weights), pooled via its
                  native seqcoding API.
The embedding recipe is PER-MODEL and set by the workflow (single source of truth:
``workflow/src/models.lib.tengo``): which hidden layer to mean-pool (--emb-layer),
whether to keep boundary special tokens in the mean (--pool-special-tokens), the
per-model input transform (--input-kind), and how a paired Fv is combined
(--pair-mode). D is read from the model. Weights load offline via
``from_pretrained(local_path)`` / the mounted asset only.

I/O contract (with ``workflow/src/main.tpl.tengo``):
  --input      input.tsv   entity_key + one column per per-scope sequence
  --plan       plan.json   { device, scopes: [...] }
  --model-path DIR         mounted HF checkpoint dir / asset (the exact model to use)
  --model-name TAG         identity for stats/logs (default: directory name)
  --model-family F         loader family: hf | hf-custom | ablang2
  --input-kind K           per-model input prep: aa | aa-cdr3-trimmed | aa-spaced | smiles
  --emb-layer N            hidden layer to mean-pool (-1 last, -2 penultimate, N block)
  --pool-special-tokens    keep boundary specials (<cls>/<eos>/<sep>) in the mean (default: drop)
  --pair-mode M            paired-Fv combination: concat (VH⊕VL) | joint (native paired)
  --output-dir DIR         writes embeddings_{scope.name}.parquet per scope
  --stats      stats.json  run metadata + per-scope counts
  --workers    N           CPU inference processes (1=single, default; 0=auto,
                           sized from the CPU budget); ignored on GPU/MPS
  --chunk-size N           clonotypes per streaming chunk (0=auto from
                           --max-memory-gb); bounds peak RAM independent of N

The keyspace is processed in chunks streamed to per-scope Parquet writers, so peak
memory is bounded by the chunk size, not N. Within a chunk each needed sequence
column is embedded once and reused across the scopes that consume it (single-column
scopes write it directly; paired Fv joins its two chains on shared clonotypes).

A scope is ``{ name, feature, chain, sourceColumns }``. A two-column scope is a
paired Fv, combined per --pair-mode: ``concat`` embeds each chain independently
and concatenates the two pooled vectors (order as listed, VH then VL) → 2D-dim;
``joint`` encodes the pair natively (CurrAb, AbLang2) → D-dim. A single-column
scope produces a D-dim vector.

Long-format output → ``xsv.importFile`` builds the two-axis PColumn
``[inputAxis, pl7.app/embeddingDim]`` directly.

CPU throughput scales with ``--workers``: on CPU each chunk's sequences are
sharded across N worker processes (each loads the checkpoint once), which scales
past the point where torch's intra-op threading plateaus for transformer
inference. GPU/MPS stay single-process — a single accelerator is contended, not
sped up, by extra processes, and each would reload the model.
Resident memory scales with the worker count (one model copy per worker).
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq

# --- config defaults --------------------------------------------------------

# ESM-2's positional limit is 1026 tokens incl. specials; cap residues so a
# tokenized sequence (with <cls>/<eos>) never exceeds it. Over-long sequences
# are truncated from the C-terminus (HF default) and counted for the log.
DEFAULT_MAX_LENGTH = 1024

# Upper bound on tokens per forward pass = (batch_size × padded_length). Sizing
# by tokens (not a fixed sequence count) keeps memory bounded across very
# different sequence lengths: short peptides pack into large batches, long
# chains into small ones.
DEFAULT_TOKEN_BUDGET = 16384

# Single process by default; the GPU tier always runs single-process regardless.
# Opt into CPU fan-out with --workers N (or 0 to auto-size from the CPU budget).
DEFAULT_WORKERS = 1


def log_message(message: str, status: str = "INFO") -> None:
    """Structured, timestamped processing log. Mirrors the dimensionality-reduction
    block's ``log_message`` (``[ts] [STATUS] msg``), printed to stdout; the
    workflow captures it as the block log."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{status}] {message}", flush=True)


# --- plan / input -----------------------------------------------------------


@dataclass(frozen=True)
class ScopePlan:
    name: str                   # feature + chain -> CDR3_A | VDJRegion_A | ...
    feature: str                # peptide | CDR3 | VCJRegion | ...
    chain: str                  # "A" | "B" | "" ("" for peptide / Fv / scFv)
    source_columns: list[str]   # 1 column → single embed; 2 → paired-Fv concat
    label: str = ""             # human-readable picker label, echoed into stats for the UI report
    model: str = ""             # checkpoint tag for this scope; echoed per-row so the report
                                # shows one row per (scope, model) (stats-only pass)
    is_heavy: bool = True       # IG heavy chain? (single-cell chain A, or bulk IGHeavy).
                                # Drives AbLang2's heavy/light slot for single-chain scopes;
                                # default True so non-IG / unspecified scopes act as heavy.
    max_length: int = 0         # per-model truncation cap (tokens incl. specials) for the
                                # stats-only count; 0 → fall back to args.max_length. The
                                # embedding pass caps via --max-length (per task), not this.


@dataclass(frozen=True)
class Plan:
    device: str                 # "cpu" | "gpu" from the workflow ("auto" = standalone default)
    scopes: list[ScopePlan]


def parse_plan(plan_path: Path) -> Plan:
    raw = json.loads(plan_path.read_text())
    scopes = [
        ScopePlan(
            name=s["name"],
            feature=s["feature"],
            chain=s.get("chain", ""),
            source_columns=list(s["sourceColumns"]),
            label=s.get("label", ""),
            model=s.get("model", ""),
            is_heavy=bool(s.get("isHeavy", True)),
            max_length=int(s.get("maxLength", 0) or 0),
        )
        for s in raw["scopes"]
    ]
    return Plan(
        device=raw.get("device", "auto"),
        scopes=scopes,
    )


def read_input(input_path: Path) -> pl.DataFrame:
    # infer_schema_length=0 → every column read as Utf8. Sequences are strings;
    # the entity key round-trips as a string and is re-typed by xsv.importFile
    # against the pass-through axis spec (Long for cloneId, String otherwise).
    return pl.read_csv(input_path, separator="\t", has_header=True, infer_schema_length=0)


# --- device -----------------------------------------------------------------


def resolve_device(requested: str):
    """Resolve the torch device. The workflow has already chosen the model for
    the expected tier; here we only place tensors and pick the dtype. fp16 on
    CUDA, fp32 elsewhere."""
    import torch

    if requested == "cpu":
        return torch.device("cpu")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if requested == "gpu":
        log_message("device 'gpu' requested but CUDA is unavailable at runtime; "
                    "running the mounted checkpoint on CPU (fp32). Throughput will be lower.",
                    "WARNING")
    # Apple Silicon's Metal backend
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return torch.device("mps")

    return torch.device("cpu")


# --- parallelism sizing -----------------------------------------------------


def detect_cpu_budget() -> int:
    """Best-effort allocated-CPU count. Prefers the cgroup-aware affinity set
    (respects cpuset pinning), falling back to os.cpu_count(). NOTE: this does
    NOT see CFS quota limits, so inside a Platforma exec the workflow should pass
    the real allocation via --cpus rather than rely on this."""
    try:
        return max(1, len(os.sched_getaffinity(0)))
    except AttributeError:   # os.sched_getaffinity is Linux-only
        return os.cpu_count() or 1


def estimate_model_gb(model_path: str) -> float:
    """Rough resident footprint of one model copy, summed from the checkpoint
    weight files on disk. Used only to cap worker count against a memory budget."""
    total = 0
    p = Path(model_path)
    for pattern in ("*.safetensors", "*.bin"):
        for f in p.glob(pattern):
            try:
                total += f.stat().st_size
            except OSError:
                pass
    gb = total / 1e9
    return gb if gb > 0 else 1.0   # fall back to ~1 GB if nothing is found


def resolve_parallelism(args, device, model_path: str) -> "tuple[int, int]":
    """Decide (workers, threads_per_worker) for the run.

    GPU/MPS always run single-process — one accelerator is contended, not sped
    up, by multiple processes, and each would reload the model.

    On CPU: an explicit --workers N wins. --workers 0 means AUTO — target
    workers × threads ≈ the CPU budget, with 2 intra-op threads per worker. The
    workers sweep showed 1 thread/worker is consistently slow; a handful of
    workers each with 2-3 threads wins (fewer model copies → better cache/memory
    behaviour). Worker count is then capped so the model copies fit --max-memory-gb.
    """
    if device.type != "cpu":
        if args.workers != 1:
            log_message(f"--workers ignored on device '{device.type}'; running single-process "
                        "(the accelerator is not parallelised across processes).", "WARNING")
        return 1, 0

    budget = args.cpus if args.cpus and args.cpus > 0 else detect_cpu_budget()

    if args.workers and args.workers > 0:          # explicit worker count
        workers = args.workers
        if args.threads_per_worker > 0:
            threads = args.threads_per_worker
        elif workers > 1:
            threads = max(1, budget // workers)
        else:
            threads = 0                            # single process → torch default threads
        return workers, threads

    # auto (--workers 0): ~2 threads/worker, total ≈ budget, memory-capped.
    if budget <= 1:
        return 1, 1
    threads = args.threads_per_worker if args.threads_per_worker > 0 else 2
    workers = max(1, budget // threads)
    if args.max_memory_gb and args.max_memory_gb > 0:
        per_worker_gb = estimate_model_gb(model_path) * 1.5    # weights + runtime overhead
        workers = min(workers, max(1, int(args.max_memory_gb * 0.8 / per_worker_gb)))
    threads = max(1, budget // workers)            # refill cores if memory capped the workers
    return workers, threads


# --- input preparation ------------------------------------------------------


def prepare_sequence(seq: str, input_kind: str) -> str:
    """Model-specific transform applied to a raw AA sequence before tokenization.

    Most models take the sequence as-is (`"aa"`). The exceptions:
      - `"aa-cdr3-trimmed"` (H3BERTa): the model was trained on the bare CDR-H3
        loop, but MiXCR's CDR3 includes the conserved anchors (`C…W`/`F`). Strip a
        leading Cys and a trailing Trp/Phe — only when present, so a non-canonical
        CDR3 keeps every residue rather than silently losing one.
      - `"aa-spaced"` (TCR-BERT): its tokenizer expects residues space-separated
        (`"C A S S ..."`) so the WordPiece splitter sees one token per residue.
      - `"smiles"` (PeptideCLM-2): the model reads molecules, not residues, so the
        peptide AA string is converted to a SMILES string via RDKit. A sequence
        RDKit cannot parse (non-standard residues) yields "" → an ~zero embedding,
        rather than failing the whole batch.
    """
    if input_kind == "aa-cdr3-trimmed":
        if seq[:1] == "C":
            seq = seq[1:]
        if seq[-1:] in ("W", "F"):
            seq = seq[:-1]
        return seq
    if input_kind == "aa-spaced":
        return " ".join(seq)
    if input_kind == "smiles":
        from rdkit import Chem
        mol = Chem.MolFromSequence(seq)
        return Chem.MolToSmiles(mol) if mol is not None else ""
    return seq


# --- embedder ---------------------------------------------------------------


# Config keys that hold the embedding width, in priority order. Standard HF encoders
# use `hidden_size`; custom checkpoints name it differently (PeptideCLM-2: `embed_dim`).
_HIDDEN_DIM_KEYS = ("hidden_size", "embed_dim", "d_model", "hidden_dim", "n_embd", "dim")


def _hidden_dim_from_config(config) -> int:
    """Embedding width from a HF config (a dict from config.json, or a loaded config
    object), trying the known key names so custom checkpoints work too."""
    get = config.get if isinstance(config, dict) else (lambda k: getattr(config, k, None))
    for k in _HIDDEN_DIM_KEYS:
        v = get(k)
        if v:
            return int(v)
    raise SystemExit(f"could not determine embedding dim from config (tried {_HIDDEN_DIM_KEYS})")


class Embedder:
    """Loads one HF encoder checkpoint and embeds sequences by mean-pooling a
    per-model hidden layer (`emb_layer`, chosen by the workflow). The model is
    loaded once and reused across all scopes.

    `AutoModel` selects the architecture from the checkpoint's config: ESM-2 and
    CurrAb load as `EsmModel`, the antibody RoBERTa checkpoints (VHHBERT, H3BERTa)
    as `RobertaModel`, TCR-BERT as `BertModel`. The pooling and special-token
    handling below are architecture-generic, so one loader serves all of them.

    `trust_remote_code=True` (PeptideCLM-2) loads the checkpoint's bundled custom
    modeling code. Such custom models do not accept `add_pooling_layer`, so it is
    only passed for the standard architectures."""

    def __init__(self, model_path: str, device, max_length: int, token_budget: int,
                 threads: int = 0, input_kind: str = "aa", trust_remote_code: bool = False,
                 emb_layer: int = -2, pool_special_tokens: bool = False) -> None:
        import torch
        from transformers import AutoTokenizer, AutoModel

        # Cap intra-op threads when asked (0 = leave torch's default, which uses
        # all cores). Lets the single-process path run a controlled thread count
        # — e.g. a true 1-thread baseline in the workers sweep.
        if threads and threads > 0:
            torch.set_num_threads(int(threads))

        self.device = device
        self.max_length = max_length
        self.token_budget = token_budget
        self.input_kind = input_kind   # per-model sequence prep (see prepare_sequence)
        # Which hidden layer to mean-pool. Indexes `output_hidden_states` (index 0 =
        # embedding layer, 1..N = transformer blocks; -1 = last, -2 = penultimate,
        # a positive N = that block). The value is PER-MODEL and passed by the
        # workflow via --emb-layer; the authoritative per-model choices (and their
        # rationale) live in `workflow/src/models.lib.tengo`, the single source of
        # truth — do not restate them here (they drift). PeptideCLM-2 (custom model)
        # ignores this and pools via its own mean_pool output.
        self.emb_layer = emb_layer
        self._layer_logged = False   # one-time log of the pooled layer (see _forward_once)
        # Custom (trust_remote_code) models — PeptideCLM-2's ChemPepMTR — build their
        # own attention mask/bias and crash under fp16 SDPA on CUDA ("invalid dtype
        # for bias — should match query's dtype"). Keep them fp32 on every device;
        # standard HF encoders still use fp16 on CUDA for speed/memory.
        self.dtype = (
            torch.float16 if (device.type == "cuda" and not trust_remote_code) else torch.float32
        )

        self.tokenizer = AutoTokenizer.from_pretrained(
            model_path, trust_remote_code=trust_remote_code)
        # add_pooling_layer=False: we mean-pool ourselves; the pooler is unused
        # classification scaffolding. EsmModel/RobertaModel/BertModel all accept the
        # kwarg, but a custom (trust_remote_code) model may not — so skip it there.
        model_kwargs = {"torch_dtype": self.dtype}
        if trust_remote_code:
            model_kwargs["trust_remote_code"] = True
        else:
            model_kwargs["add_pooling_layer"] = False
        self.model = AutoModel.from_pretrained(model_path, **model_kwargs).to(device).eval()
        self.dim = _hidden_dim_from_config(self.model.config)

        # Which token positions to drop from the residue mean. Padding is always
        # excluded via the attention_mask (so it need not be listed here). The
        # boundary specials (<cls>/<eos>, <sep> for BERT) are dropped UNLESS
        # `pool_special_tokens` is set — a per-model choice matching each model's own
        # recipe: ESM-2 / TCR-BERT exclude them (TCR-BERT's authors do so explicitly,
        # ESM's example slices them off); VHHBERT / H3BERTa / PeptideCLM-2 INCLUDE
        # them (their reference code means over all non-pad tokens). <unk> is always
        # kept: a non-canonical residue tokenizes to <unk> but is still a residue
        # position we want to pool over.
        tok = self.tokenizer
        self.exclude_ids = set() if pool_special_tokens else {
            i for i in (tok.cls_token_id, tok.eos_token_id, tok.sep_token_id, tok.pad_token_id)
            if i is not None
        }

    def embed(self, sequences: list[str]) -> np.ndarray:
        """Return an (N, dim) float32 matrix, rows aligned with `sequences`.

        Sequences are length-sorted and packed into token-budget batches to
        minimise padding waste, then scattered back into input order.
        """
        # Apply the model's input transform up front, so the length-sort, batching
        # and truncation below all see the prepared (e.g. CDR3-trimmed) sequence.
        if self.input_kind != "aa":
            sequences = [prepare_sequence(s, self.input_kind) for s in sequences]
        out = np.empty((len(sequences), self.dim), dtype=np.float32)
        order = sorted(range(len(sequences)), key=lambda i: len(sequences[i]))

        batch: list[int] = []
        batch_max = 0
        for i in order:
            seq_len = min(len(sequences[i]) + 2, self.max_length)  # +2 for <cls>/<eos>
            new_max = max(batch_max, seq_len)
            if batch and (len(batch) + 1) * new_max > self.token_budget:
                self._run_batch(sequences, batch, out)
                batch, batch_max = [], 0
                new_max = seq_len
            batch.append(i)
            batch_max = new_max
        # write each result vector to its original index i (undoing the length-sort)
        self._run_batch(sequences, batch, out)
        return out

    def embed_pairs(self, pairs: list[tuple[str, str]]) -> np.ndarray:
        """Embed (heavy, light) chain pairs JOINTLY → an (N, dim) matrix. Each pair
        is joined into one sequence with the tokenizer's cls token between the chains
        — CurrAb's paired-input convention ("H<cls>L"). That separator is a
        cls_token_id, already in exclude_ids, so it drops out of the residue mean:
        the result is one D-vector per pair (a paired encoding, NOT a concat)."""
        sep = self.tokenizer.cls_token
        return self.embed([h + sep + l for h, l in pairs])

    def _run_batch(self, sequences: list[str], idxs: list[int], out: np.ndarray) -> None:
        """Embed one batch and write each vector back to its ORIGINAL row in `out`.
        `idxs` are pre-sort indices, so this undoes `embed`'s length-sort reordering."""
        if not idxs:
            return
        vecs = self._forward([sequences[i] for i in idxs])   # gather the batch's strings → (B, D)
        for j, i in enumerate(idxs):                    # vecs[j] is the result for original row i
            out[i] = vecs[j]

    def _forward(self, seqs: list[str]) -> np.ndarray:
        """Forward + mean-pool, with halve-and-retry on OOM as a safety net.

        The OOM retry is intended for GPU (CUDA) mode: a CPU out-of-memory usually
        surfaces as a MemoryError or an OS OOM-kill, neither of which this
        RuntimeError handler catches."""
        import torch

        try:
            return self._forward_once(seqs)
        except RuntimeError as exc:
            # CUDA OOM raises a catchable RuntimeError: halve the batch and recurse
            # (keeps halving until it fits). The len > 1 guard means a single
            # sequence that still OOMs propagates instead of looping forever.
            if "out of memory" in str(exc).lower() and len(seqs) > 1:
                if self.device.type == "cuda":
                    torch.cuda.empty_cache()           # release cached VRAM before the retry
                mid = len(seqs) // 2
                log_message(f"OOM on a batch of {len(seqs)}; splitting and retrying.", "WARNING")
                return np.concatenate([self._forward(seqs[:mid]), self._forward(seqs[mid:])], axis=0)
            raise

    def _forward_once(self, seqs: list[str]) -> np.ndarray:
        import torch

        # 1. Tokenize: strings → padded id tensors. truncation clips over-long
        #    sequences from the C-terminus; add_special_tokens wraps <cls>/<eos>.
        enc = self.tokenizer(
            seqs, return_tensors="pt", padding=True, truncation=True,
            max_length=self.max_length, add_special_tokens=True,
        )
        enc = {k: v.to(self.device) for k, v in enc.items()}   # ids + attention_mask → device

        # 2. Forward pass under no_grad (inference only); keep every layer's hidden states.
        with torch.no_grad():
            out = self.model(**enc, output_hidden_states=True)

        # 2b. Custom models (PeptideCLM-2) expose their OWN pooled embedding as a
        #     `mean_pool` field (a pad-masked mean over the last layer, the recipe
        #     its authors use downstream). Prefer it verbatim — it already pools, so
        #     skip our layer-select + masking entirely.
        mean_pool = getattr(out, "mean_pool", None)
        if mean_pool is not None:
            if not self._layer_logged:
                log_message("pooling: model's own mean_pool output (last layer, pad-masked)", "STEP")
                self._layer_logged = True
            return mean_pool.float().cpu().numpy()

        # 3. Pick the model's recommended hidden layer (self.emb_layer), then mask
        #    down to the pooled positions: drop padding (attention_mask) and, unless
        #    this model pools over specials, the boundary tokens by id (<unk> kept).
        #    Custom models may not expose hidden_states, and a layer index out of
        #    range falls back to the final layer's last_hidden_state.
        hs = getattr(out, "hidden_states", None)
        in_range = hs is not None and -len(hs) <= self.emb_layer < len(hs)
        if in_range:
            hidden = hs[self.emb_layer]
        else:
            hidden = out.last_hidden_state
        # Log the pooled layer once per embedder, and flag a fall-back explicitly so a
        # misconfigured --emb-layer (out of range for this model) is visible, not silent.
        if not self._layer_logged:
            n = len(hs) if hs is not None else 0
            if in_range:
                log_message(f"pooling hidden layer emb_layer={self.emb_layer} (of {n} hidden states)", "STEP")
            else:
                log_message(
                    f"emb_layer={self.emb_layer} out of range for {n} hidden states — "
                    "FALLING BACK to the last layer (last_hidden_state)", "WARNING")
            self._layer_logged = True
        ids = enc["input_ids"]
        mask = enc["attention_mask"].clone()
        for sid in self.exclude_ids:              # drop <cls>/<eos>/<pad> positions
            mask = mask.masked_fill(ids == sid, 0)

        # 4. Mean-pool over the residue positions → one D-vector per sequence (fp32).
        #    clamp(min=1) guards divide-by-zero if a row has no residue positions.
        m = mask.unsqueeze(-1).to(hidden.dtype)   # [batch, L, 1] broadcast over D
        summed = (hidden * m).sum(dim=1)          # [batch, D]  sum over length
        counts = m.sum(dim=1).clamp(min=1.0)      # [batch, 1]  residue count
        return (summed / counts).float().cpu().numpy()

    def close(self) -> None:
        """No-op; symmetry with ParallelEmbedder so callers can tear down either
        kind of embedder the same way."""
        pass


# --- multiprocess embedder (CPU fan-out) ------------------------------------

# One persistent Embedder per worker process, created in the pool initialiser so
# the checkpoint is loaded once per worker (not once per task). Spawn re-imports
# this module in each child; these globals/functions are module-level so they are
# importable and picklable.
_WORKER_EMBEDDER: "Embedder | None" = None


def _init_worker(model_path: str, device_str: str, max_length: int, token_budget: int,
                 threads: int, input_kind: str = "aa", trust_remote_code: bool = False,
                 emb_layer: int = -2, pool_special_tokens: bool = False) -> None:
    # Cap intra-op threads per worker so N workers don't oversubscribe the cores.
    # Set the env before torch is imported (it reads OMP/MKL at init), then pin
    # torch's own thread count (redundant but safe).
    if threads and threads > 0:
        os.environ["OMP_NUM_THREADS"] = str(threads)
        os.environ.setdefault("MKL_NUM_THREADS", str(threads))
    import torch
    if threads and threads > 0:
        try:
            torch.set_num_threads(int(threads))
        except Exception:
            pass
    global _WORKER_EMBEDDER
    _WORKER_EMBEDDER = Embedder(model_path, torch.device(device_str), max_length, token_budget,
                                input_kind=input_kind, trust_remote_code=trust_remote_code,
                                emb_layer=emb_layer, pool_special_tokens=pool_special_tokens)


def _worker_embed(task: "tuple[int, list[str]]") -> "tuple[int, np.ndarray]":
    """Embed one shard. Returns (start_index, matrix) so the parent can place the
    rows back at their original offset."""
    start, seqs = task
    return start, _WORKER_EMBEDDER.embed(seqs)


def _split(n: int, n_chunks: int) -> "list[tuple[int, int]]":
    """Contiguous (start, size) ranges covering [0, n), as even as possible."""
    base, rem = divmod(n, n_chunks)
    ranges, start = [], 0
    for i in range(n_chunks):
        size = base + (1 if i < rem else 0)
        if size:
            ranges.append((start, size))
            start += size
    return ranges


class ParallelEmbedder:
    """Drop-in replacement for `Embedder` that fans CPU inference out across a
    pool of worker processes. Same interface (`embed`, `dim`, `dtype`, `close`)
    so the orchestrator is unchanged. Used only on CPU — a single GPU/MPS device
    is contended, not sped up, by multiple processes, and each would reload the
    model."""

    def __init__(self, model_path: str, device, max_length: int, token_budget: int,
                 workers: int, threads_per_worker: int, input_kind: str = "aa",
                 trust_remote_code: bool = False, emb_layer: int = -2,
                 pool_special_tokens: bool = False) -> None:
        import torch

        self.max_length = max_length        # read by the orchestrator (truncation threshold)
        self.dtype = torch.float32          # CPU-only path; surfaced in stats
        self.workers = workers
        self._model_path = str(model_path)  # for embed_pairs' lazy cls-token lookup
        # Read D from config.json so the PARENT never loads the model — only the
        # workers do. Avoids a redundant model copy in the orchestrator process.
        self.dim = _hidden_dim_from_config(json.loads((Path(model_path) / "config.json").read_text()))

        # spawn (not fork): torch + fork is unsafe, and the parent has already
        # imported torch by now. spawn gives each worker a clean interpreter.
        ctx = mp.get_context("spawn")
        self.pool = ctx.Pool(
            processes=workers,
            initializer=_init_worker,
            initargs=(str(model_path), device.type, max_length, token_budget, threads_per_worker,
                      input_kind, trust_remote_code, emb_layer, pool_special_tokens),
        )

    def embed(self, sequences: list[str]) -> np.ndarray:
        n = len(sequences)
        if n == 0:
            return np.empty((0, self.dim), dtype=np.float32)
        # Oversubscribe modestly (≈4× workers) so the pool dynamically balances
        # uneven shard runtimes, but keep each shard ≥~256 sequences so per-shard
        # token-budget batching stays efficient.
        n_chunks = max(1, min(self.workers * 4, (n + 255) // 256))
        tasks = [(start, sequences[start:start + size]) for start, size in _split(n, n_chunks)]
        out = np.empty((n, self.dim), dtype=np.float32)
        for start, mat in self.pool.map(_worker_embed, tasks):
            out[start:start + mat.shape[0]] = mat
        return out

    def embed_pairs(self, pairs: list[tuple[str, str]]) -> np.ndarray:
        """Joint paired embedding (see Embedder.embed_pairs). Joins each pair with the
        checkpoint's cls token, then fans the joined sequences out like any other
        batch. The cls token is read once from the tokenizer (no model weights)."""
        if not hasattr(self, "_cls_token"):
            from transformers import AutoTokenizer
            self._cls_token = AutoTokenizer.from_pretrained(self._model_path).cls_token
        return self.embed([h + self._cls_token + l for h, l in pairs])

    def close(self) -> None:
        self.pool.close()
        self.pool.join()


# --- ablang2 embedder (pip model, weights shipped as an asset) --------------


class AbLang2Embedder:
    """AbLang2 (antibody) embedder. Unlike the HF-checkpoint models, AbLang2 ships
    its model CODE in the runenv (`ablang2` pip package) but fetches its WEIGHTS at
    runtime from Zenodo. We ship those weights as an asset and load OFFLINE by pointing
    `pretrained(model_to_use=<asset folder>)` straight at the mounted asset — AbLang2's
    loader accepts a folder path (reads hparams.json + model.pt from it) for any name
    that isn't a built-in. We do NOT copy into the package dir (read-only in the image).

    Same `embed`/`dim`/`dtype`/`max_length`/`close` interface as `Embedder`. Each
    sequence is encoded UNPAIRED as `[seq, ""]` in `seqcoding` mode → a 480-d vector;
    the orchestrator concatenates per-chain vectors for a paired Fv scope, exactly
    as it does for every other model (the block's Fv handling is concat-of-chains,
    so AbLang2 stays consistent rather than introducing joint-pair output here).

    Runs single-process: the pip model is light, and this avoids spawning workers
    that each re-provision weights and reload the package."""

    DIM = 480

    def __init__(self, model_path: str, device, max_length: int, token_budget: int,
                 input_kind: str = "aa") -> None:
        import torch
        import ablang2

        # Locate the AbLang2 weights (model.pt + hparams.json) inside the mounted asset dir;
        # we load OFFLINE from that folder rather than by model name (see model_to_use below).
        src = Path(model_path)
        weights_dir = None
        if (src / "model.pt").exists() and (src / "hparams.json").exists():
            weights_dir = src
        else:
            for p in src.rglob("model.pt"):
                if (p.parent / "hparams.json").exists():
                    weights_dir = p.parent
                    break
        if weights_dir is None:
            raise SystemExit(
                f"AbLang2 weights (model.pt + hparams.json) not found under {model_path}")

        self.device = device
        self.max_length = max_length
        self.dtype = torch.float32
        self.dim = self.DIM
        dev = "cuda" if device.type == "cuda" else "cpu"
        # Load from the local asset folder, NOT the "ablang2-paired" name (which would try to
        # download / write into site-packages). AbLang2 0.2.1 only treats model_to_use as a
        # LOCAL folder when the string contains "ABLANG-" (its load_model gate:
        # `elif "ABLANG-" in model_to_use`); a plain path like the mounted "model" dir hits its
        # `assert False, "...does not exist"`. So expose the weights under an "ABLANG-"-named
        # symlink and hand ablang2 that path (fetch_ablang2 then reads hparams.json + model.pt
        # straight from the linked folder).
        tagged = weights_dir.parent / ("ABLANG-" + weights_dir.name)
        if not (tagged.is_symlink() or tagged.exists()):
            tagged.symlink_to(weights_dir.resolve(), target_is_directory=True)
        self.model = ablang2.pretrained(
            model_to_use=str(tagged), random_init=False, ncpu=1, device=dev)

    def embed(self, sequences: list[str], is_light: bool = False) -> np.ndarray:
        if not sequences:
            return np.zeros((0, self.dim), dtype=np.float32)
        # Batch so peak memory is bounded. AbLang2 takes a [heavy, light] record and
        # tells the two chains apart by POSITION (heavy left of the internal `|`,
        # light right) — NOT by content. So an unpaired chain must go in the correct
        # slot: a heavy chain as [seq, ""], a light chain as ["", seq]. Putting a
        # light chain in the heavy slot would encode it in heavy-chain context (a
        # silently wrong vector). `is_light` is set per scope from the chain role.
        def record(s: str) -> list[str]:
            return ["", s] if is_light else [s, ""]
        out = np.empty((len(sequences), self.dim), dtype=np.float32)
        step = 256
        for i in range(0, len(sequences), step):
            chunk = sequences[i:i + step]
            vecs = self.model([record(s) for s in chunk], mode="seqcoding")
            out[i:i + len(chunk)] = np.asarray(vecs, dtype=np.float32)
        return out

    def embed_pairs(self, pairs: list[tuple[str, str]]) -> np.ndarray:
        """Native PAIRED encoding: AbLang2 jointly encodes [heavy, light] → one 480-d
        vector per pair (its intended use — the cross-chain signal a per-chain concat
        would lose)."""
        if not pairs:
            return np.zeros((0, self.dim), dtype=np.float32)
        out = np.empty((len(pairs), self.dim), dtype=np.float32)
        step = 256
        for i in range(0, len(pairs), step):
            chunk = pairs[i:i + step]
            vecs = self.model([[h, l] for h, l in chunk], mode="seqcoding")
            out[i:i + len(chunk)] = np.asarray(vecs, dtype=np.float32)
        return out

    def close(self) -> None:
        pass


# --- per-scope orchestration ------------------------------------------------


# Long-format output schema for every per-scope ParquetWriter. The key column is
# named by `key_col` — in batch mode that is the batch-key axis name (e.g.
# "pl7.app/vdj/clonotypeKey"), so the orchestrator's Xsv import keys on it; the
# standalone default is "entity_key". The value column is float32 — the model
# emits float32 and the imported PColumn's value type is "Float"
# (columns.lib.tengo); float64 would only pad noise digits. D is encoded in the
# embedding_dim VALUES (0..D-1), not the schema.
def long_schema(key_col: str) -> pa.Schema:
    return pa.schema([
        (key_col, pa.large_string()),
        ("embedding_dim", pa.int64()),
        ("value", pa.float32()),
    ])


def col_present(col: str):
    """Polars predicate: this sequence column is present (non-null, non-empty)."""
    return pl.col(col).is_not_null() & (pl.col(col) != "")


def compute_scope_counts(df: pl.DataFrame, source_columns: list[str], max_residues: int):
    """Per-scope (n_entities, n_dropped_empty, n_truncated) over the FULL keyspace,
    one vectorised pass — no embedding. Standalone twin of stream_embed's per-batch
    scope_stats, used by the --stats-only report pass.

    n_entities = rows with every source column present (non-empty); n_dropped_empty =
    the rest; n_truncated = present rows whose sequence exceeds the token limit
    (counted once per long chain, summed across columns for paired Fv)."""
    if not all(c in df.columns for c in source_columns):
        return 0, df.height, 0
    pred = col_present(source_columns[0])
    for c in source_columns[1:]:
        pred = pred & col_present(c)
    aggs = [pred.cast(pl.Int64).sum().alias("n_ent")]
    for i, c in enumerate(source_columns):
        aggs.append(
            (pred & (pl.col(c).str.len_chars() > max_residues)).cast(pl.Int64).sum().alias(f"t{i}")
        )
    row = df.select(aggs).row(0)
    n_ent = int(row[0] or 0)
    n_trunc = sum(int(x or 0) for x in row[1:])
    return n_ent, df.height - n_ent, n_trunc


def embed_unique(seqs: list[str], embedder, **embed_kwargs) -> np.ndarray:
    """Embed `seqs` (order preserved), embedding each DISTINCT string once → (N, D).
    Dedup is within this call — i.e. within a chunk — so convergent duplicates
    (e.g. a shared CDR3) cost one forward pass, not one per occurrence. Extra kwargs
    (e.g. `is_light` for AbLang2) are forwarded to the embedder's `embed`."""
    if not seqs:
        return np.zeros((0, embedder.dim), dtype=np.float32)
    uniq = list(dict.fromkeys(seqs))            # distinct, first-appearance order
    vecs = embedder.embed(uniq, **embed_kwargs)  # (U, D)
    pos = {s: i for i, s in enumerate(uniq)}
    return vecs[[pos[s] for s in seqs]]         # scatter back to input order


def embed_pairs_unique(pairs: list[tuple[str, str]], embedder) -> np.ndarray:
    """Like embed_unique, but for (heavy, light) pairs encoded JOINTLY: dedup
    identical pairs within the chunk so a recurrent pair costs one encode → (N, D)."""
    if not pairs:
        return np.zeros((0, embedder.dim), dtype=np.float32)
    uniq = list(dict.fromkeys(pairs))           # distinct pairs, first-appearance order
    vecs = embedder.embed_pairs(uniq)           # (U, D)
    pos = {p: i for i, p in enumerate(uniq)}
    return vecs[[pos[p] for p in pairs]]        # scatter back to input order


def long_table(keys: list[str], matrix: np.ndarray, key_col: str, schema: pa.Schema) -> pa.Table:
    """One chunk's long-format table: a row per (key, dim), matching `schema`.
    `matrix` is (len(keys), outDim); for paired Fv outDim = 2D and dim runs 0..2D-1.
    The key column is named `key_col` (the batch-key axis in batch mode)."""
    n, d = matrix.shape
    return pa.table(
        {
            key_col: pa.array(np.repeat(np.asarray(keys, dtype=object), d),
                              type=pa.large_string()),
            "embedding_dim": pa.array(np.tile(np.arange(d, dtype=np.int64), n), type=pa.int64()),
            "value": pa.array(matrix.reshape(-1).astype(np.float32), type=pa.float32()),
        },
        schema=schema,
    )


def resolve_chunk_size(args, dim: int, reserved_gb: float = 0.0) -> int:
    """Clonotypes per streaming chunk. Explicit --chunk-size wins; 0 = auto from
    --max-memory-gb so peak RAM is bounded independently of N. Per-chunk cost is
    ~ chunk × dim × (a few columns' float32 vectors + the long frame's int64+float32
    rows + arrow overhead); size to a fraction of the budget with a conservative
    bytes-per-(row×dim) factor, clamped to a sane range.

    `reserved_gb` is host memory already claimed by resident model copies (CPU
    fan-out / single-process). The chunk is sized against the *remaining* budget so
    the model copies and the chunk data don't both assume the whole allocation —
    they share one pool. (GPU keeps weights in VRAM → reserve nothing here.)"""
    if args.chunk_size and args.chunk_size > 0:
        return args.chunk_size
    budget_gb = args.max_memory_gb if args.max_memory_gb and args.max_memory_gb > 0 else 4.0
    avail_gb = max(0.5, budget_gb - reserved_gb)   # budget left after model copies
    bytes_per_row_dim = 64    # vectors (≤~4 cols × 4 B) + long frame (~24 B, 2D for Fv) + overhead
    chunk = int(avail_gb * 1e9 * 0.25 / (max(1, dim) * bytes_per_row_dim))
    return max(1000, min(chunk, 500_000))


def stream_embed(plan: Plan, df: pl.DataFrame, embedder, output_dir: Path,
                 chunk_size: int, model_name: str, key_col: str,
                 pair_mode: str = "concat", input_kind: str = "aa") -> list[dict]:
    """Embed every selected scope in one pass over the keyspace, streaming each
    chunk's rows to a per-scope Parquet writer so peak RAM ~ chunk size (independent
    of N). Within a chunk each needed sequence column is embedded once and reused by
    every scope that consumes it: single-column scopes write that column directly.

    A paired Fv scope is handled per `pair_mode`:
      - "concat" (default): embed each chain column separately and concatenate the
        two per-chain vectors (VH⊕VL, 2D) — for models with no paired mode (ESM-2).
      - "joint": encode (heavy, light) together → one D-vector, via the model's
        paired path (CurrAb's H<cls>L join, AbLang2's native paired encoding).
    Both chains live in the same row, so the pairing is always within-chunk.
    Returns the per-scope stats list.

    `key_col` names the entity-key column in both the input TSV and the output
    files (the batch-key axis name in batch mode, "entity_key" standalone)."""
    available = set(df.columns)
    scopes = plan.scopes
    schema = long_schema(key_col)

    def viable(sc: ScopePlan) -> bool:
        return all(c in available for c in sc.source_columns)

    def is_joint(sc: ScopePlan) -> bool:
        # A 2-column (Fv) scope whose model encodes heavy+light together in one pass
        # (CurrAb, AbLang2), as opposed to the default per-chain concat.
        return pair_mode == "joint" and len(sc.source_columns) == 2

    # Per column, embed the rows that some selected scope needs: a single-column
    # scope needs every present row; a column used ONLY by Fv needs just the paired
    # intersection (so the Fv-only default embeds no unpaired singletons).
    # Joint-Fv scopes embed their pair directly (see emit_joint) — they are excluded
    # from the per-column embedding below so their chains aren't also encoded alone.
    single_cols: set[str] = set()
    fv_pairs: list[tuple[str, str]] = []
    for sc in scopes:
        if not viable(sc) or is_joint(sc):
            continue
        if len(sc.source_columns) == 1:
            single_cols.add(sc.source_columns[0])
        else:
            fv_pairs.append((sc.source_columns[0], sc.source_columns[1]))

    col_pred: dict[str, pl.Expr] = {}
    for sc in scopes:
        if not viable(sc) or is_joint(sc):
            continue
        for c in sc.source_columns:
            if c in col_pred:
                continue
            if c in single_cols:
                col_pred[c] = col_present(c)
            else:
                expr = None
                for vh, vl in fv_pairs:
                    if c in (vh, vl):
                        pair = col_present(vh) & col_present(vl)
                        expr = pair if expr is None else (expr | pair)
                col_pred[c] = expr if expr is not None else col_present(c)

    # Single-chain columns belonging to a LIGHT chain (IG light: single-cell chain B
    # or bulk IGLight → is_heavy False). Used only by AbLang2, whose seqcoding tells
    # heavy from light by slot — a light chain must go in the light slot. Heavy and
    # paired-Fv columns are unaffected (heavy slot / native pair handling).
    light_cols = {
        sc.source_columns[0]
        for sc in scopes
        if len(sc.source_columns) == 1 and not sc.is_heavy
    }
    ablang2 = isinstance(embedder, AbLang2Embedder)

    max_residues = embedder.max_length - 2

    def scope_stats(sc: ScopePlan):
        """ how many rows will be embedded, how many are dropped, and how many will be truncated
        (n_entities, n_dropped, n_truncated) — one vectorised pass, no materialisation."""
        if not viable(sc):
            return 0, 0, 0
        pred = col_present(sc.source_columns[0])
        for c in sc.source_columns[1:]:
            pred = pred & col_present(c)
        # All aggregations in a single select: eligible-row count + one truncation
        # count per source column (a row counts once per long chain, as before).
        # eligible-row count
        aggs = [pred.cast(pl.Int64).sum().alias("n_ent")]
        # one truncation count per source column
        for i, c in enumerate(sc.source_columns):
            aggs.append(
                (pred & (pl.col(c).str.len_chars() > max_residues)).cast(pl.Int64).sum().alias(f"t{i}")
            )
        row = df.select(aggs).row(0) # (n_ent, t0[, t1])
        # Eligible rows, or 0 guards the empty-df case where sum is None
        n_ent = int(row[0] or 0)
        # sum of the per-column truncation counts. For Fv this is t0 + t1
        n_trunc = sum(int(x or 0) for x in row[1:])
        return n_ent, df.height - n_ent, n_trunc

    stat = {sc.name: scope_stats(sc) for sc in scopes}
    # Output width: a joint pair is one D-vector; otherwise D per source column
    # (single = D, concat Fv = 2D).
    out_dim = {sc.name: (embedder.dim if is_joint(sc) else embedder.dim * len(sc.source_columns))
               for sc in scopes}

    writers: dict[str, pq.ParquetWriter] = {}
    wrote: set[str] = set()

    def emit(sc: ScopePlan, col_data: dict) -> None:
        if len(sc.source_columns) == 1:
            keys, vecs, _ = col_data[sc.source_columns[0]]
            if keys:
                writers[sc.name].write_table(long_table(keys, vecs, key_col, schema))
                wrote.add(sc.name)
            return
        # Paired Fv: join the two chains on shared clonotypes (VH then VL); the
        # per-chain vectors are reused from col_data, never re-embedded.
        cH, cL = sc.source_columns
        keysH, vecsH, posH = col_data[cH]
        _keysL, vecsL, posL = col_data[cL]
        common = [k for k in keysH if k in posL]
        if common:
            mat = np.concatenate(
                [vecsH[[posH[k] for k in common]], vecsL[[posL[k] for k in common]]], axis=1)
            writers[sc.name].write_table(long_table(common, mat, key_col, schema))
            wrote.add(sc.name)

    def emit_joint(sc: ScopePlan, cd: pl.DataFrame) -> None:
        # Joint paired Fv: gather (heavy, light) over clonotypes with BOTH chains in
        # this chunk and encode each pair jointly → one D-vector (no concat). The
        # pair columns are NOT in col_data — joint scopes are embedded only here.
        cH, cL = sc.source_columns
        present = cd.filter(col_present(cH) & col_present(cL))
        keys = present[key_col].to_list()
        if not keys:
            return
        pairs = list(zip(present[cH].to_list(), present[cL].to_list()))
        writers[sc.name].write_table(
            long_table(keys, embed_pairs_unique(pairs, embedder), key_col, schema))
        wrote.add(sc.name)

    # Joint-Fv scopes drive the chunk loop too, even when col_pred is empty (a run
    # whose only scope is a joint pair embeds nothing per-column).
    joint_scopes = [sc for sc in scopes if viable(sc) and is_joint(sc)]

    try:
        # Open per-scope writers inside the try so that if one constructor fails
        # mid-way, the finally below still closes the ones already opened.
        for sc in scopes:
            writers[sc.name] = pq.ParquetWriter(
                str(output_dir / f"embeddings_{sc.name}.parquet"), schema)
        if col_pred or joint_scopes:
            n_chunks = (df.height + chunk_size - 1) // chunk_size if df.height else 0
            for ci, cd in enumerate(df.iter_slices(n_rows=chunk_size)):
                # Real size of THIS slice (the last/only chunk is smaller than the
                # configured cap — e.g. a 300-row table is one 300-clonotype chunk).
                log_message(f"chunk {ci + 1}/{n_chunks}: {cd.height} sequences", "STEP")
                try:
                    col_data = {}
                    for col, pred in col_pred.items():
                        present = cd.filter(pred)
                        keys = present[key_col].to_list()
                        raw = present[col].to_list()
                        # A per-model transform can turn a NON-empty raw sequence into
                        # "" — an unparseable SMILES (RDKit failure) or a CDR3 that
                        # trims to nothing. col_present ran on the RAW string (before
                        # the transform), so those keys are still here. Drop them
                        # rather than write a fabricated (zero / specials-only) vector;
                        # count for the log. `aa`/`aa-spaced` never empty a non-empty
                        # sequence, so this is a no-op for them.
                        if input_kind != "aa" and keys:
                            kept = [(k, s) for k, s in zip(keys, raw)
                                    if prepare_sequence(s, input_kind) != ""]
                            n_drop = len(keys) - len(kept)
                            if n_drop:
                                log_message(
                                    f"{col}: dropped {n_drop} sequence(s) that became empty under "
                                    f"'{input_kind}' (unparseable / fully trimmed) — no vector written",
                                    "WARNING")
                                keys = [k for k, _ in kept]
                                raw = [s for _, s in kept]
                        # AbLang2 needs the chain's heavy/light slot; other embedders
                        # ignore extra kwargs (none passed).
                        kw = {"is_light": col in light_cols} if ablang2 else {}
                        vecs = (embed_unique(raw, embedder, **kw) if keys
                                else np.zeros((0, embedder.dim), dtype=np.float32))
                        col_data[col] = (keys, vecs, {k: i for i, k in enumerate(keys)})
                    for sc in scopes:
                        if not viable(sc):
                            continue
                        if is_joint(sc):
                            emit_joint(sc, cd)
                        else:
                            emit(sc, col_data)
                except Exception as exc:  # noqa: BLE001 — log for the processing log, then re-raise
                    # A real embedding failure (model / CUDA / unrecoverable OOM /
                    # unexpected error) must surface as a BLOCK error, never silent or
                    # partial output. Log it, then re-raise: the non-zero exit fails
                    # the exec and the partial parquets are discarded with it.
                    log_message(f"embedding failed on chunk {ci}: {exc!r}", "ERROR")
                    raise
        # Every scope file must exist (the workflow imports each by name): write a
        # header-only table for any scope that produced no rows (empty / column absent).
        for name, w in writers.items():
            if name not in wrote:
                w.write_table(schema.empty_table())
    finally:
        for w in writers.values():
            try:
                w.close()
            except Exception:
                pass

    entries = []
    for sc in plan.scopes:
        n_ent, n_drop, n_trunc = stat[sc.name]
        entries.append({
            "name": sc.name,
            "feature": sc.feature,
            "chain": sc.chain,
            "label": sc.label,
            "model": model_name,
            "n_entities": n_ent,
            "n_dropped_empty": n_drop,
            "n_truncated": n_trunc,
        })
        # Log the human-readable picker label (e.g. "Paired Fv", "Heavy
        # VDJRegionInFrame aa") rather than the filename-safe scope name
        disp = sc.label or sc.name
        if n_ent:
            log_message(f"scope {disp}: {n_ent} embedded, dim={out_dim[sc.name]}"
                        + (f", {n_drop} dropped (empty/partial)" if n_drop else "")
                        + (f", {n_trunc} truncated >{embedder.max_length} tokens" if n_trunc else ""))
        else:
            log_message(f"scope {disp}: no sequences to embed ({n_drop} dropped); "
                        "wrote empty (header-only) output", "WARNING")
    return entries


# --- stats-only report pass -------------------------------------------------


def run_stats_only(args, plan: Plan, df: pl.DataFrame, model_name: str) -> int:
    """Compute the per-scope run-report counts over the full source and write
    stats.json. No model load, no embedding — a cheap counting pass invoked by the
    workflow alongside the batched embedding (which produces the actual vectors)."""
    scopes = []
    for sc in plan.scopes:
        # Count truncation against THIS scope's model cap (per-model, threaded from
        # the registry via the plan), so the report matches the embedding step; fall
        # back to the run's --max-length when the plan omits it.
        max_residues = (sc.max_length or args.max_length) - 2
        n_ent, n_drop, n_trunc = compute_scope_counts(df, sc.source_columns, max_residues)
        scopes.append({
            "name": sc.name,
            "feature": sc.feature,
            "chain": sc.chain,
            "label": sc.label,
            # Per-row model: the scope's own checkpoint tag, so the same scope
            # embedded by two models shows two report rows. Falls back to the
            # run's representative --model-name if the plan omitted it.
            "model": sc.model or model_name,
            # Per-scope token cap → the UI's per-row "Trimmed (>N aa)" threshold.
            "max_length": sc.max_length or args.max_length,
            "n_entities": n_ent,
            "n_dropped_empty": n_drop,
            "n_truncated": n_trunc,
        })
        log_message(f"scope {sc.label or sc.name}: {n_ent} embedded"
                    + (f", {n_drop} dropped (empty/partial)" if n_drop else "")
                    + (f", {n_trunc} truncated >{max_residues} aa" if n_trunc else ""))
    stats = {
        "device_used": plan.device,
        "model": model_name,
        "max_length": args.max_length,
        "scopes": scopes,
    }
    Path(args.stats).write_text(json.dumps(stats, indent=2))
    log_message(f"Done (stats-only). Wrote report to {args.stats}", "STEP")
    return 0


# --- main -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Sequence Embeddings runner")
    parser.add_argument("--input", required=True, help="Input TSV path")
    parser.add_argument("--key-col", default="entity_key",
                        help="Entity-key column header in the input/output TSV. In batch mode this "
                             "is the batch-key axis name (e.g. pl7.app/vdj/clonotypeKey); the output "
                             "echoes it so the orchestrator's Xsv import can key on it.")
    parser.add_argument("--plan", required=True, help="Plan JSON path")
    parser.add_argument("--stats-only", action="store_true",
                        help="Report mode: compute per-scope counts over the full source and write "
                             "--stats, then exit. No model load, no embedding (--model-path / "
                             "--output-dir not needed). Used by the workflow's run-report step.")
    parser.add_argument("--output-dir", default=None,
                        help="Directory for per-scope embeddings_{scope.name}.parquet files "
                             "(required unless --stats-only)")
    parser.add_argument("--stats", required=True, help="Stats JSON path")
    parser.add_argument("--model-path", default=None,
                        help="Mounted checkpoint dir / asset — the exact model to use, per "
                             "--model-family (required unless --stats-only)")
    parser.add_argument("--model-name", default=None,
                        help="Model identity for stats/logs (default: --model-path directory name)")
    parser.add_argument("--input-kind", default="aa",
                        help="Per-model sequence transform before tokenization: 'aa' (as-is, "
                             "default), 'aa-cdr3-trimmed' (strip CDR3's flanking C/W, H3BERTa), "
                             "'aa-spaced' (space-separate residues, TCR-BERT), or 'smiles' "
                             "(AA→SMILES, PeptideCLM-2). See prepare_sequence.")
    parser.add_argument("--model-family", default="hf", choices=["hf", "hf-custom", "ablang2"],
                        help="Loader family: 'hf' (standard AutoModel encoder, default), "
                             "'hf-custom' (AutoModel with trust_remote_code, PeptideCLM-2), or "
                             "'ablang2' (the ablang2 pip model with asset-shipped weights).")
    parser.add_argument("--pair-mode", default="concat", choices=["concat", "joint"],
                        help="How a paired Fv scope is embedded: 'concat' (default) embeds each "
                             "chain separately and concatenates (VH⊕VL); 'joint' encodes heavy+light "
                             "together → one vector, for models with a paired mode (CurrAb, AbLang2).")
    parser.add_argument("--emb-layer", type=int, default=-2,
                        help="Hidden layer to mean-pool, indexing output_hidden_states (0 = embedding "
                             "layer, -1 = last, -2 = penultimate; positive = that transformer block). "
                             "The per-model value is set by the workflow (source of truth: "
                             "models.lib.tengo). Ignored for models exposing their own pooled output "
                             "(PeptideCLM-2) or non-HF pooling (AbLang2 seqcoding).")
    parser.add_argument("--pool-special-tokens", action="store_true",
                        help="Include the boundary special tokens (<cls>/<eos>/<sep>) in the residue "
                             "mean (padding is always excluded). Off by default (ESM-2, TCR-BERT "
                             "exclude them); set for VHHBERT/H3BERTa whose reference code means over "
                             "all non-pad tokens.")
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH,
                        help="Max residue tokens incl. specials; longer sequences truncate from the C-terminus")
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET,
                        help="Max tokens (batch × padded length) per forward pass")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help="CPU inference processes. 1 = single process (default); "
                             "0 = auto (size from --cpus: ~2 threads/worker, total ≈ cpus, "
                             "memory-capped); N = N processes. Ignored on GPU/MPS.")
    parser.add_argument("--threads-per-worker", type=int, default=0,
                        help="Torch intra-op threads per worker (0 = auto: 2 in --workers 0 mode, "
                             "or cpus // workers for explicit workers>1; torch default for workers=1).")
    parser.add_argument("--cpus", type=int, default=0,
                        help="Allocated CPU budget for auto sizing (--workers 0). 0 = detect "
                             "(cgroup-aware). In a Platforma exec the workflow should pass the real "
                             "allocation — detection cannot see CFS quota limits.")
    parser.add_argument("--max-memory-gb", type=float, default=0.0,
                        help="Memory budget (GB) for auto sizing (CPU worker count + streaming chunk "
                             "size). 0 = defaults. The workflow should pass the exec's allocation.")
    parser.add_argument("--chunk-size", type=int, default=0,
                        help="Clonotypes per streaming chunk. Bounds peak RAM independently of N. "
                             "0 = auto (size from --max-memory-gb).")
    args = parser.parse_args()

    plan = parse_plan(Path(args.plan))
    df = read_input(Path(args.input))
    log_message(f"Loaded input {args.input}, shape={df.shape}")

    model_name = args.model_name or (Path(args.model_path).name if args.model_path else "esm2")

    # Report mode: count over the full source and exit (no model, no embedding).
    if args.stats_only:
        return run_stats_only(args, plan, df, model_name)

    if not args.model_path:
        raise SystemExit("--model-path is required for embedding (omit only with --stats-only)")
    if not args.output_dir:
        raise SystemExit("--output-dir is required for embedding (omit only with --stats-only)")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    device = resolve_device(plan.device)

    # Decide CPU parallelism (workers × intra-op threads). Auto sizing and the
    # GPU/MPS single-process guard live in resolve_parallelism.
    workers, threads_per_worker = resolve_parallelism(args, device, args.model_path)

    trust_remote_code = args.model_family == "hf-custom"
    if args.model_family == "ablang2":
        # The pip model runs single-process regardless of --workers (see class doc).
        embedder = AbLang2Embedder(args.model_path, device, args.max_length, args.token_budget,
                                   input_kind=args.input_kind)
        workers, threads_per_worker = 1, 0
        parallel_note = "ablang2 (single process)"
    elif trust_remote_code:
        # Custom trust_remote_code model (PeptideCLM-2): single process — spawn + the
        # dynamic module import is fragile. Load once in-process; let torch use all cores.
        embedder = Embedder(args.model_path, device, args.max_length, args.token_budget,
                            threads=0, input_kind=args.input_kind, trust_remote_code=True,
                            emb_layer=args.emb_layer, pool_special_tokens=args.pool_special_tokens)
        workers, threads_per_worker = 1, 0
        parallel_note = "hf-custom (single process)"
    elif workers > 1:
        embedder = ParallelEmbedder(args.model_path, device, args.max_length, args.token_budget,
                                    workers, threads_per_worker, input_kind=args.input_kind,
                                    trust_remote_code=trust_remote_code,
                                    emb_layer=args.emb_layer,
                                    pool_special_tokens=args.pool_special_tokens)
        parallel_note = f"workers={workers}×{threads_per_worker} threads/worker"
    else:
        embedder = Embedder(args.model_path, device, args.max_length, args.token_budget,
                            threads=threads_per_worker, input_kind=args.input_kind,
                            trust_remote_code=trust_remote_code,
                            emb_layer=args.emb_layer, pool_special_tokens=args.pool_special_tokens)
        parallel_note = (f"workers=1 ({threads_per_worker} threads)"
                         if threads_per_worker > 0
                         else "workers=1 (single process, torch default threads)")

    # Record device + checkpoint at startup so the operator sees what ran.
    log_message(f"device={device.type} dtype={embedder.dtype} model={model_name} dim={embedder.dim} "
                f"{parallel_note} (requested={plan.device}, scopes={len(plan.scopes)})",
                "STEP")

    stats: dict = {
        "device_requested": plan.device,
        "device_used": device.type,
        "model": model_name,
        "max_length": args.max_length,
        "workers": workers,
        "threads_per_worker": threads_per_worker,
        "scopes": [],
    }

    # Stream the keyspace in chunks so peak RAM is bounded independently of N. Size
    # the chunk against the budget LEFT after the model copies (CPU: `workers`
    # resident copies in host RAM; GPU: weights live in VRAM → nothing reserved),
    # so the model copies and the chunk data share one --max-memory-gb pool.
    model_host_gb = (workers * estimate_model_gb(args.model_path) * 1.5
                     if device.type == "cpu" else 0.0)
    chunk_size = resolve_chunk_size(args, embedder.dim, reserved_gb=model_host_gb)
    stats["chunk_size"] = chunk_size
    n_chunks = (df.height + chunk_size - 1) // chunk_size if df.height else 0
    log_message(f"streaming {df.height} clonotypes in {n_chunks} chunk(s) of up to "
                f"{chunk_size} (peak RAM bounded, independent of N)")

    try:
        stats["scopes"] = stream_embed(plan, df, embedder, output_dir, chunk_size, model_name,
                                       args.key_col, pair_mode=args.pair_mode,
                                       input_kind=args.input_kind)
    finally:
        embedder.close()

    Path(args.stats).write_text(json.dumps(stats, indent=2))
    log_message(f"Done. Wrote stats to {args.stats}", "STEP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
