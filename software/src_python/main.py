"""Sequence Embeddings — per-sequence embedding compute step.

Loads ONE protein language model (the exact checkpoint the workflow chose for
the runtime device tier and mounted via --model-path), embeds the sequences for
each requested scope, mean-pools the penultimate-layer hidden states over
residue positions, and writes a long-format TSV per scope:
``(entity_key, embedding_dim, value)``.

The workflow picks the model by device tier — GPU → ESM-2 650M (fp16),
CPU → ESM-2 150M (fp32) — and mounts exactly that checkpoint, so this script is
model-agnostic: it loads whatever HuggingFace ESM-2-style checkpoint
``--model-path`` points at and reads the embedding dimension D from the model
config. Weights load via ``from_pretrained(local_path)`` only — no HuggingFace
Hub network call at runtime (slice 01 R19a).

I/O contract (with ``workflow/src/main.tpl.tengo``):
  --input      input.tsv   entity_key + one column per per-scope sequence
  --plan       plan.json   { mode, receptor, device, scopes: [...] }
  --model-path DIR         mounted HF checkpoint dir (the exact model to use)
  --model-name TAG         identity for stats/logs (default: directory name)
  --output-dir DIR         writes embeddings_{scope.name}.tsv per scope
  --stats      stats.json  run metadata + per-scope counts
  --workers    N           CPU inference processes (1=single, default; 0=auto,
                           one per core); ignored on GPU/MPS (R21)

A scope is ``{ name, feature, chain, sourceColumn | sourceColumns, receptor }``.
``sourceColumns`` (two columns) drives the paired-Fv vector-concat (slice 01
R10): each chain is embedded independently and the two pooled vectors are
concatenated (order as listed, VH then VL) → a 2D-dim vector. A single
``sourceColumn`` produces a D-dim vector.

Long-format output → ``xsv.importFile`` builds the two-axis PColumn
``[inputAxis, pl7.app/embeddingDim]`` directly (slice 01 R11; columns.lib.tengo).

CPU throughput scales with ``--workers``: on CPU the keyspace is sharded across
N worker processes (each loads the checkpoint once), which scales past the point
where torch's intra-op threading plateaus for transformer inference. GPU/MPS
stay single-process — a single accelerator is contended, not sped up, by extra
processes, and each would reload the model (slice 01 R21). Resident memory scales
with the worker count (one model copy per worker).
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

# --- config defaults --------------------------------------------------------

# ESM-2's positional limit is 1026 tokens incl. specials; cap residues so a
# tokenized sequence (with <cls>/<eos>) never exceeds it. Over-long sequences
# are truncated from the C-terminus (HF default) and counted for the log.
DEFAULT_MAX_LENGTH = 1024

# Upper bound on tokens per forward pass = (batch_size × padded_length). Sizing
# by tokens (not a fixed sequence count) keeps memory bounded across very
# different sequence lengths: short peptides pack into large batches, long
# chains into small ones (slice 01 — Batching and Resource Allocation).
DEFAULT_TOKEN_BUDGET = 16384

# Single process by default — no behaviour change vs. the original path, and the
# GPU tier always runs single-process regardless (R21). Opt into CPU fan-out
# with --workers N (or 0 for one-per-core).
DEFAULT_WORKERS = 1


def log_message(message: str, status: str = "INFO") -> None:
    """Structured, timestamped processing log. Mirrors the dimensionality-reduction
    block's ``log_message`` (``[ts] [STATUS] msg``), printed to stdout; the
    workflow captures it as the block log (R17)."""
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{status}] {message}", flush=True)


# --- plan / input -----------------------------------------------------------


@dataclass(frozen=True)
class ScopePlan:
    name: str
    feature: str
    chain: str                  # "A" | "B" | "" ("" for peptide / Fv / scFv)
    source_columns: list[str]   # 1 column → single embed; 2 → paired-Fv concat (R10)
    receptor: str


@dataclass(frozen=True)
class Plan:
    mode: str
    receptor: str
    device: str                 # "auto" | "cpu" | "gpu" (projected from BlockData)
    scopes: list[ScopePlan]


def parse_plan(plan_path: Path) -> Plan:
    raw = json.loads(plan_path.read_text())

    def columns_of(s: dict) -> list[str]:
        if s.get("sourceColumns"):
            return list(s["sourceColumns"])
        return [s["sourceColumn"]]

    scopes = [
        ScopePlan(
            name=s["name"],
            feature=s["feature"],
            chain=s.get("chain", ""),
            source_columns=columns_of(s),
            receptor=s.get("receptor", ""),
        )
        for s in raw["scopes"]
    ]
    return Plan(
        mode=raw["mode"],
        receptor=raw.get("receptor", ""),
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
    CUDA, fp32 elsewhere (slice 01 R8)."""
    import torch

    if requested == "cpu":
        return torch.device("cpu")
    if torch.cuda.is_available():
        return torch.device("cuda")
    if requested == "gpu":
        log_message("device 'gpu' requested but CUDA is unavailable at runtime; "
                    "running the mounted checkpoint on CPU (fp32). Throughput will be lower.",
                    "WARNING")
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
    up, by multiple processes, and each would reload the model (slice 01 R21).

    On CPU: an explicit --workers N wins. --workers 0 means AUTO — target
    workers × threads ≈ the CPU budget, with 2 intra-op threads per worker. The
    workers sweep showed 1 thread/worker is consistently slow; a handful of
    workers each with 2-3 threads wins (fewer model copies → better cache/memory
    behaviour). Worker count is then capped so the model copies fit --max-memory-gb.
    """
    if device.type != "cpu":
        if args.workers != 1:
            log_message(f"--workers ignored on device '{device.type}'; running single-process "
                        "(the accelerator is not parallelised across processes; R21).", "WARNING")
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
        # add_pooling_layer=False: we mean-pool ourselves (R9); the pooler is
        # unused classification scaffolding.
        self.model = (
            EsmModel.from_pretrained(model_path, add_pooling_layer=False, torch_dtype=self.dtype)
            .to(device)
            .eval()
        )
        self.dim = int(self.model.config.hidden_size)

        # Exclude <cls>/<eos>/<pad> from the residue mean (R9, Key Formulas).
        # <unk> is kept on purpose: a non-canonical residue tokenizes to <unk>
        # and is still a residue position we want to pool over.
        tok = self.tokenizer
        self.exclude_ids = {
            i for i in (tok.cls_token_id, tok.eos_token_id, tok.pad_token_id) if i is not None
        }
        self.n_truncated = 0  # reset per scope by the caller

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
        self._run_batch(sequences, batch, out)
        return out

    def _run_batch(self, sequences: list[str], idxs: list[int], out: np.ndarray) -> None:
        if not idxs:
            return
        vecs = self._forward([sequences[i] for i in idxs])
        for j, i in enumerate(idxs):
            out[i] = vecs[j]

    def _forward(self, seqs: list[str]) -> np.ndarray:
        """Forward + mean-pool, with halve-and-retry on OOM as a safety net."""
        import torch

        try:
            return self._forward_once(seqs)
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower() and len(seqs) > 1:
                if self.device.type == "cuda":
                    torch.cuda.empty_cache()
                mid = len(seqs) // 2
                log_message(f"OOM on a batch of {len(seqs)}; splitting and retrying.", "WARNING")
                return np.concatenate([self._forward(seqs[:mid]), self._forward(seqs[mid:])], axis=0)
            raise

    def _forward_once(self, seqs: list[str]) -> np.ndarray:
        import torch

        self.n_truncated += sum(1 for s in seqs if len(s) > self.max_length - 2)

        enc = self.tokenizer(
            seqs, return_tensors="pt", padding=True, truncation=True,
            max_length=self.max_length, add_special_tokens=True,
        )
        enc = {k: v.to(self.device) for k, v in enc.items()}

        with torch.no_grad():
            out = self.model(**enc, output_hidden_states=True)

        hidden = out.hidden_states[-2]            # penultimate layer (R9)
        ids = enc["input_ids"]
        mask = enc["attention_mask"].clone()
        for sid in self.exclude_ids:              # drop <cls>/<eos>/<pad> positions
            mask = mask.masked_fill(ids == sid, 0)

        m = mask.unsqueeze(-1).to(hidden.dtype)
        summed = (hidden * m).sum(dim=1)
        counts = m.sum(dim=1).clamp(min=1.0)
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
    # torch's own thread count as a belt-and-suspenders.
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


def _worker_embed(task: "tuple[int, list[str]]") -> "tuple[int, np.ndarray, int]":
    """Embed one shard. Returns (start_index, matrix, n_truncated) so the parent
    can place rows back at their original offset and sum truncation counts."""
    start, seqs = task
    _WORKER_EMBEDDER.n_truncated = 0
    mat = _WORKER_EMBEDDER.embed(seqs)
    return start, mat, _WORKER_EMBEDDER.n_truncated


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
    pool of worker processes. Same interface (`embed`, `dim`, `n_truncated`,
    `dtype`, `close`) so `embed_scope`/`main` are unchanged. Used only on CPU —
    a single GPU/MPS device is contended, not sped up, by multiple processes, and
    each would reload the model (slice 01 R21)."""

    def __init__(self, model_path: str, device, max_length: int, token_budget: int,
                 workers: int, threads_per_worker: int) -> None:
        import torch

        self.device = device
        self.max_length = max_length
        self.token_budget = token_budget
        self.dtype = torch.float32          # the parallel path is CPU-only (R8)
        self.workers = workers
        self.threads_per_worker = threads_per_worker
        # Read D from config.json so the PARENT never loads the model — only the
        # workers do. Avoids a redundant model copy in the orchestrator process.
        self.dim = int(json.loads((Path(model_path) / "config.json").read_text())["hidden_size"])
        self.n_truncated = 0                # reset per scope by the caller

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
        for start, mat, ntrunc in self.pool.map(_worker_embed, tasks):
            out[start:start + mat.shape[0]] = mat
            self.n_truncated += ntrunc
        return out

    def close(self) -> None:
        self.pool.close()
        self.pool.join()


# --- per-scope orchestration ------------------------------------------------


def embed_scope(scope: ScopePlan, df: pl.DataFrame, embedder):
    """Return (matrix, entity_keys, n_dropped). A row is kept only when every
    source column for the scope is present and non-empty (R5); paired-Fv needs
    both chains. Dropped rows are counted for the R16 annotation."""
    missing = [c for c in scope.source_columns if c not in df.columns]
    if missing:
        return np.zeros((0, 0), dtype=np.float32), [], 0

    present = pl.lit(True)
    for col in scope.source_columns:
        present = present & pl.col(col).is_not_null() & (pl.col(col) != "")
    sub = df.filter(present)

    n_dropped = df.height - sub.height
    if sub.is_empty():
        return np.zeros((0, 0), dtype=np.float32), [], n_dropped

    entity_keys = sub["entity_key"].to_list()

    # One vector per chain, concatenated in listed order (VH then VL) → paired Fv
    # vector-concat (R10). A single source column is the common single-chain case.
    parts = [embedder.embed(sub[col].to_list()) for col in scope.source_columns]
    matrix = parts[0] if len(parts) == 1 else np.concatenate(parts, axis=1)
    return matrix, entity_keys, n_dropped


def write_long_tsv(out_path: Path, matrix: np.ndarray, entity_keys: list[str]) -> None:
    """Write a long-format TSV: (entity_key, embedding_dim, value), one row per
    (entity, dim). xsv.importFile builds the two-axis PColumn from this directly
    (columns.lib.tengo). Built vectorised to stay fast at scale."""
    n, d = matrix.shape
    frame = pl.DataFrame(
        {
            "entity_key": np.repeat(np.asarray(entity_keys, dtype=object), d),
            "embedding_dim": np.tile(np.arange(d, dtype=np.int64), n),
            "value": matrix.reshape(-1).astype(np.float64),
        }
    )
    frame.write_csv(out_path, separator="\t")


# --- main -------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Sequence Embeddings runner")
    parser.add_argument("--input", required=True, help="Input TSV path")
    parser.add_argument("--plan", required=True, help="Plan JSON path")
    parser.add_argument("--output-dir", required=True,
                        help="Directory for per-scope embeddings_{scope.name}.tsv files")
    parser.add_argument("--stats", required=True, help="Stats JSON path")
    parser.add_argument("--model-path", required=True,
                        help="Mounted HF ESM-2 checkpoint directory — the exact model to use")
    parser.add_argument("--model-name", default=None,
                        help="Model identity for stats/logs (default: --model-path directory name)")
    parser.add_argument("--max-length", type=int, default=DEFAULT_MAX_LENGTH,
                        help="Max residue tokens incl. specials; longer sequences truncate from the C-terminus")
    parser.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET,
                        help="Max tokens (batch × padded length) per forward pass")
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS,
                        help="CPU inference processes. 1 = single process (default); "
                             "0 = auto (size from --cpus: ~2 threads/worker, total ≈ cpus, "
                             "memory-capped); N = N processes. Ignored on GPU/MPS (R21).")
    parser.add_argument("--threads-per-worker", type=int, default=0,
                        help="Torch intra-op threads per worker (0 = auto: 2 in --workers 0 mode, "
                             "or cpus // workers for explicit workers>1; torch default for workers=1).")
    parser.add_argument("--cpus", type=int, default=0,
                        help="Allocated CPU budget for auto sizing (--workers 0). 0 = detect "
                             "(cgroup-aware). In a Platforma exec the workflow should pass the real "
                             "allocation — detection cannot see CFS quota limits.")
    parser.add_argument("--max-memory-gb", type=float, default=0.0,
                        help="Memory budget (GB) for auto sizing; caps worker count so model copies "
                             "fit. 0 = no cap. The workflow should pass the exec's memory allocation.")
    args = parser.parse_args()

    plan = parse_plan(Path(args.plan))
    df = read_input(Path(args.input))
    log_message(f"Loaded input {args.input}, shape={df.shape}")
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    model_name = args.model_name or Path(args.model_path).name
    device = resolve_device(plan.device)

    # Decide CPU parallelism (workers × intra-op threads). Auto sizing and the
    # GPU/MPS single-process guard live in resolve_parallelism (slice 01 R21).
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

    # R17: record device + checkpoint at startup so the operator sees which mode ran.
    log_message(f"device={device.type} dtype={embedder.dtype} model={model_name} dim={embedder.dim} "
                f"{parallel_note} (requested={plan.device}, mode={plan.mode}, scopes={len(plan.scopes)})",
                "STEP")

    stats: dict = {
        "device_requested": plan.device,
        "device_used": device.type,
        "dtype": str(embedder.dtype).replace("torch.", ""),
        "model": model_name,
        "embedding_dim": embedder.dim,
        "mode": plan.mode,
        "receptor": plan.receptor,
        "max_length": args.max_length,
        "token_budget": args.token_budget,
        "workers": workers,
        "threads_per_worker": threads_per_worker,
        "scopes": [],
        "errors": [],
    }

    try:
        for scope in plan.scopes:
            embedder.n_truncated = 0
            log_message(f"Embedding scope '{scope.name}' (feature={scope.feature}"
                        + (f", chain={scope.chain}" if scope.chain else "") + ")", "STEP")
            try:
                matrix, entity_keys, n_dropped = embed_scope(scope, df, embedder)
            except Exception as exc:  # noqa: BLE001 — surface any failure to the workflow via stats
                log_message(f"scope {scope.name}: {exc!r}", "ERROR")
                stats["errors"].append({"scope": scope.name, "error": repr(exc)})
                continue

            entry = {
                "name": scope.name,
                "feature": scope.feature,
                "chain": scope.chain,
                "model": model_name,
                "n_entities": len(entity_keys),
                "embedding_dim": int(matrix.shape[1]) if len(entity_keys) else 0,
                "n_dropped_empty": int(n_dropped),
                "n_truncated": int(embedder.n_truncated),
                "tsv_file": None,
            }

            if entity_keys:
                tsv_path = output_dir / f"embeddings_{scope.name}.tsv"
                write_long_tsv(tsv_path, matrix, entity_keys)
                entry["tsv_file"] = tsv_path.name
                log_message(f"scope {scope.name}: {len(entity_keys)} embedded, dim={entry['embedding_dim']}"
                            + (f", {n_dropped} dropped (empty/partial)" if n_dropped else "")
                            + (f", {embedder.n_truncated} truncated >{args.max_length} tokens" if embedder.n_truncated else ""))
            else:
                log_message(f"scope {scope.name}: no sequences to embed ({n_dropped} dropped)", "WARNING")

            stats["scopes"].append(entry)
    finally:
        embedder.close()

    Path(args.stats).write_text(json.dumps(stats, indent=2))
    log_message(f"Done. Wrote stats to {args.stats}", "STEP")
    return 0


if __name__ == "__main__":
    sys.exit(main())
