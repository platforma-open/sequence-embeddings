"""Sequence Embeddings — per-sequence embedding compute step.

Loads ONE protein language model (the exact checkpoint the workflow chose for
the runtime device tier and mounted via --model-path), embeds the sequences for
each requested scope, mean-pools the penultimate-layer hidden states over
residue positions, and writes a long-format Parquet per scope:
``(entity_key, embedding_dim, value)``.

The workflow picks the model (ESM-2 650M (fp16), ESM-2 150M (fp32)) and mounts
exactly that checkpoint, so this script is model-agnostic: it loads whatever
HuggingFace ESM-2-style checkpoint ``--model-path`` points at and reads the
embedding dimension D from the model config. Weights load via 
``from_pretrained(local_path)`` only.

I/O contract (with ``workflow/src/main.tpl.tengo``):
  --input      input.tsv   entity_key + one column per per-scope sequence
  --plan       plan.json   { device, scopes: [...] }
  --model-path DIR         mounted HF checkpoint dir (the exact model to use)
  --model-name TAG         identity for stats/logs (default: directory name)
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

A scope is ``{ name, feature, chain, sourceColumns }``. A two-column scope is
paired Fv: each chain is embedded independently and the two pooled vectors are
concatenated (order as listed, VH then VL) → a 2D-dim vector. A
single-column scope produces a D-dim vector.

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


# --- embedder ---------------------------------------------------------------


class Embedder:
    """Loads one ESM-2-style checkpoint and embeds sequences via penultimate-layer
    mean pooling. The model is loaded once and reused across all scopes."""

    def __init__(self, model_path: str, device, max_length: int, token_budget: int,
                 threads: int = 0) -> None:
        import torch
        from transformers import AutoTokenizer, EsmModel

        # Cap intra-op threads when asked (0 = leave torch's default, which uses
        # all cores). Lets the single-process path run a controlled thread count
        # — e.g. a true 1-thread baseline in the workers sweep.
        if threads and threads > 0:
            torch.set_num_threads(int(threads))

        self.device = device
        self.max_length = max_length
        self.token_budget = token_budget
        self.dtype = torch.float16 if device.type == "cuda" else torch.float32

        self.tokenizer = AutoTokenizer.from_pretrained(model_path)
        # add_pooling_layer=False: we mean-pool ourselves; the pooler is
        # unused classification scaffolding.
        self.model = (
            EsmModel.from_pretrained(model_path, add_pooling_layer=False, torch_dtype=self.dtype)
            .to(device)
            .eval()
        )
        self.dim = int(self.model.config.hidden_size)

        # Exclude <cls>/<eos>/<pad> from the residue mean.
        # <unk> is kept on purpose: a non-canonical residue tokenizes to <unk>
        # and is still a residue position we want to pool over.
        tok = self.tokenizer
        self.exclude_ids = {
            i for i in (tok.cls_token_id, tok.eos_token_id, tok.pad_token_id) if i is not None
        }

    def embed(self, sequences: list[str]) -> np.ndarray:
        """Return an (N, dim) float32 matrix, rows aligned with `sequences`.

        Sequences are length-sorted and packed into token-budget batches to
        minimise padding waste, then scattered back into input order.
        """
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

        # 3. Take the penultimate layer, then mask down to RESIDUE positions only:
        #    drop padding (attention_mask) and <cls>/<eos>/<pad> by id (<unk> kept).
        hidden = out.hidden_states[-2]            # penultimate layer  [batch, L, D]
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


def _init_worker(model_path: str, device_str: str, max_length: int, token_budget: int, threads: int) -> None:
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
    _WORKER_EMBEDDER = Embedder(model_path, torch.device(device_str), max_length, token_budget)


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
                 workers: int, threads_per_worker: int) -> None:
        import torch

        self.max_length = max_length        # read by the orchestrator (truncation threshold)
        self.dtype = torch.float32          # CPU-only path; surfaced in stats
        self.workers = workers
        # Read D from config.json so the PARENT never loads the model — only the
        # workers do. Avoids a redundant model copy in the orchestrator process.
        self.dim = int(json.loads((Path(model_path) / "config.json").read_text())["hidden_size"])

        # spawn (not fork): torch + fork is unsafe, and the parent has already
        # imported torch by now. spawn gives each worker a clean interpreter.
        ctx = mp.get_context("spawn")
        self.pool = ctx.Pool(
            processes=workers,
            initializer=_init_worker,
            initargs=(str(model_path), device.type, max_length, token_budget, threads_per_worker),
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

    def close(self) -> None:
        self.pool.close()
        self.pool.join()


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


def embed_unique(seqs: list[str], embedder) -> np.ndarray:
    """Embed `seqs` (order preserved), embedding each DISTINCT string once → (N, D).
    Dedup is within this call — i.e. within a chunk — so convergent duplicates
    (e.g. a shared CDR3) cost one forward pass, not one per occurrence."""
    if not seqs:
        return np.zeros((0, embedder.dim), dtype=np.float32)
    uniq = list(dict.fromkeys(seqs))            # distinct, first-appearance order
    vecs = embedder.embed(uniq)                 # (U, D)
    pos = {s: i for i, s in enumerate(uniq)}
    return vecs[[pos[s] for s in seqs]]         # scatter back to input order


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
                 chunk_size: int, model_name: str, key_col: str) -> list[dict]:
    """Embed every selected scope in one pass over the keyspace, streaming each
    chunk's rows to a per-scope Parquet writer so peak RAM ~ chunk size (independent
    of N). Within a chunk each needed sequence column is embedded once and reused by
    every scope that consumes it: single-column scopes write that column directly;
    paired Fv joins its two columns on shared clonotypes (both chains live in the
    same row, so the join is always within-chunk). Returns the per-scope stats list.

    `key_col` names the entity-key column in both the input TSV and the output
    files (the batch-key axis name in batch mode, "entity_key" standalone)."""
    available = set(df.columns)
    scopes = plan.scopes
    schema = long_schema(key_col)

    def viable(sc: ScopePlan) -> bool:
        return all(c in available for c in sc.source_columns)

    # Per column, embed the rows that some selected scope needs: a single-column
    # scope needs every present row; a column used ONLY by Fv needs just the paired
    # intersection (so the Fv-only default embeds no unpaired singletons).
    single_cols: set[str] = set()
    fv_pairs: list[tuple[str, str]] = []
    for sc in scopes:
        if not viable(sc):
            continue
        if len(sc.source_columns) == 1:
            single_cols.add(sc.source_columns[0])
        else:
            fv_pairs.append((sc.source_columns[0], sc.source_columns[1]))

    col_pred: dict[str, pl.Expr] = {}
    for sc in scopes:
        if not viable(sc):
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
    out_dim = {sc.name: embedder.dim * len(sc.source_columns) for sc in scopes}

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

    try:
        # Open per-scope writers inside the try so that if one constructor fails
        # mid-way, the finally below still closes the ones already opened.
        for sc in scopes:
            writers[sc.name] = pq.ParquetWriter(
                str(output_dir / f"embeddings_{sc.name}.parquet"), schema)
        if col_pred:
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
                        vecs = (embed_unique(present[col].to_list(), embedder) if keys
                                else np.zeros((0, embedder.dim), dtype=np.float32))
                        col_data[col] = (keys, vecs, {k: i for i, k in enumerate(keys)})
                    for sc in scopes:
                        if viable(sc):
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
    max_residues = args.max_length - 2
    scopes = []
    for sc in plan.scopes:
        n_ent, n_drop, n_trunc = compute_scope_counts(df, sc.source_columns, max_residues)
        scopes.append({
            "name": sc.name,
            "feature": sc.feature,
            "chain": sc.chain,
            "label": sc.label,
            "model": model_name,
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
                        help="Mounted HF ESM-2 checkpoint directory — the exact model to use "
                             "(required unless --stats-only)")
    parser.add_argument("--model-name", default=None,
                        help="Model identity for stats/logs (default: --model-path directory name)")
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

    if workers > 1:
        embedder = ParallelEmbedder(args.model_path, device, args.max_length, args.token_budget,
                                    workers, threads_per_worker)
        parallel_note = f"workers={workers}×{threads_per_worker} threads/worker"
    else:
        embedder = Embedder(args.model_path, device, args.max_length, args.token_budget,
                            threads=threads_per_worker)
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
                                       args.key_col)
    finally:
        embedder.close()

    Path(args.stats).write_text(json.dumps(stats, indent=2))
    log_message(f"Done. Wrote stats to {args.stats}", "STEP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
