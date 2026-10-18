"""Trains the local Whisper model on your voice, with MLX on this Mac's GPU (LoRA).

Uses the dictation clips Bolo saved (save_voice = true) with the text you kept as labels. Every
fifth clip is held out and only used to score the result: the trained model replaces the stock
one in config.toml only when it makes fewer word errors on those than the stock model and any
earlier trained one. A later run builds on the earlier trained model. All saved clips are
deleted when the run finishes, whether or not the model was better.

The lower encoder layers stay as they are and their output is worked out once per clip, so each
pass trains only the top encoder layers and the decoder. About 30 to 60 min for 45 min of speech
on an M3 Pro. Quit Bolo first: training needs the memory its models use.

Run: uv run scripts/train_voice.py
     uv run scripts/train_voice.py --try    (any amount of audio, into a scratch folder; keeps
                                             the clips and config.toml as they are)
"""

import argparse
import io
import math
import random
import shutil
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")

import mlx.core as mx
from huggingface_hub import snapshot_download
import mlx.nn as nn
import mlx.optimizers as optim
import soundfile as sf
from mlx.utils import tree_flatten
from mlx_whisper.audio import N_FRAMES, N_SAMPLES, log_mel_spectrogram, pad_or_trim
from mlx_whisper.load_models import load_model
from mlx_whisper.tokenizer import get_tokenizer

from accuracy import STOCK, evaluate, providers
from flow import config
from flow.learn import Learning
from flow.learn.voice import NEEDED_MINUTES, TRAINED, Voice, held_out, training_text


class LoRALinear(nn.Module):
    """A frozen linear layer plus a small trainable correction: W + scale * (A @ B)."""

    def __init__(self, linear: nn.Linear, rank: int, alpha: float):
        super().__init__()
        self.linear = linear
        out_dims, in_dims = linear.weight.shape
        self.scale = alpha / rank
        self.lora_a = mx.random.uniform(-1, 1, (in_dims, rank)) / math.sqrt(in_dims)
        self.lora_b = mx.zeros((rank, out_dims))

    def __call__(self, x):
        extra = (x.astype(mx.float32) @ self.lora_a) @ self.lora_b
        return self.linear(x) + (self.scale * extra).astype(x.dtype)

    def merged(self) -> nn.Linear:
        weight = self.linear.weight
        self.linear.weight = (weight.astype(mx.float32) + self.scale * (self.lora_a @ self.lora_b).T).astype(weight.dtype)
        return self.linear


def _attention_layers(block):
    yield block.attn
    if block.cross_attn is not None:
        yield block.cross_attn


def add_lora(model, trained_blocks: list, rank: int, alpha: float) -> None:
    model.freeze()
    for block in trained_blocks:
        for attention in _attention_layers(block):
            for name in ("query", "value"):
                setattr(attention, name, LoRALinear(getattr(attention, name), rank, alpha))


def merge_lora(model, trained_blocks: list) -> None:
    for block in trained_blocks:
        for attention in _attention_layers(block):
            for name in ("query", "value"):
                layer = getattr(attention, name)
                if isinstance(layer, LoRALinear):
                    setattr(attention, name, layer.merged())


def lower_features(model, mel: mx.array, frozen: int) -> mx.array:
    """The encoder up to its first trained layer. Fixed during training, so worked out once."""
    encoder = model.encoder
    x = nn.gelu(encoder.conv1(mel))
    x = nn.gelu(encoder.conv2(x))
    x = x + encoder._positional_embedding
    for block in encoder.blocks[:frozen]:
        x, _, _ = block(x)
    return x


def upper_features(model, x: mx.array, frozen: int) -> mx.array:
    for block in model.encoder.blocks[frozen:]:
        x, _, _ = block(x)
    return model.encoder.ln_post(x)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--try", dest="trial", action="store_true", help="any amount of audio, change nothing")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--rank", type=int, default=8)
    parser.add_argument("--lr", type=float, default=1e-4)
    parser.add_argument("--train-layers", type=int, default=8, help="top encoder layers to train")
    args = parser.parse_args()

    cfg = config.load()
    learning = Learning(True)
    voice = Voice(learning.store, keep_days=cfg["learning"].get("voice_keep_days", 14))
    voice.prune()
    rows = learning.store.with_audio()
    minutes = sum(row["audio_seconds"] for row in rows) / 60
    if minutes < NEEDED_MINUTES and not args.trial:
        sys.exit(f"{voice.line()}. Keep dictating; run this again then (or --try to test it now).")
    train = [row for row in rows if not held_out(row)]
    test = [row for row in rows if held_out(row)]
    if not train or not test:
        sys.exit(f"Need clips both to train on and to test with; have {len(train)} and {len(test)}.")
    out = Path(tempfile.mkdtemp(prefix="whisper-me-")) if args.trial else TRAINED.with_name(TRAINED.name + "-new")
    earlier = TRAINED if (TRAINED / "config.json").exists() else None
    base = earlier or Path(snapshot_download(repo_id=STOCK))
    print(f"{len(train)} clips to train on, {len(test)} held out, {minutes:.1f} min in all")
    print(f"Starting from {'the earlier trained model' if earlier else 'the stock model'}")

    started = time.perf_counter()
    model = load_model(str(base), mx.float16)
    frozen = model.dims.n_audio_layer - args.train_layers
    tokenizer = get_tokenizer(
        model.is_multilingual, num_languages=model.num_languages, language="en", task="transcribe"
    )
    prefix = list(tokenizer.sot_sequence_including_notimestamps)

    print("Reading the clips...")
    examples = []
    for row in train:
        samples, _ = sf.read(io.BytesIO(voice.path(row).read_bytes()), dtype="float32")
        mel = log_mel_spectrogram(samples, n_mels=model.dims.n_mels, padding=N_SAMPLES)
        mel = pad_or_trim(mel, N_FRAMES, axis=-2).astype(mx.float16)[None]
        features = lower_features(model, mel, frozen)
        mx.eval(features)
        tokens = prefix + tokenizer.encode(" " + training_text(row["label"])) + [tokenizer.eot]
        examples.append((features, mx.array(tokens[: model.dims.n_text_ctx])[None]))

    trained_blocks = [*model.encoder.blocks[frozen:], *model.decoder.blocks]
    add_lora(model, trained_blocks, args.rank, alpha=2 * args.rank)
    count = sum(v.size for _, v in tree_flatten(model.trainable_parameters()))
    print(f"Training {count / 1e6:.1f} M parameters, {args.epochs} passes")

    def loss_fn(model, features, tokens):
        logits = model.decoder(tokens[:, :-1], upper_features(model, features, frozen))[0].astype(mx.float32)
        targets = tokens[:, 1:]
        # Learn the words and the end, not the fixed start tokens.
        mask = mx.arange(targets.shape[1]) >= len(prefix) - 1
        losses = nn.losses.cross_entropy(logits, targets) * mask
        return losses.sum() / mask.sum()

    step = nn.value_and_grad(model, loss_fn)
    total_steps = args.epochs * len(examples)
    schedule = optim.cosine_decay(args.lr, total_steps)
    optimizer = optim.AdamW(learning_rate=schedule, weight_decay=0.01)
    random.seed(0)
    for epoch in range(args.epochs):
        random.shuffle(examples)
        losses = []
        for features, tokens in examples:
            loss, grads = step(model, features, tokens)
            if not math.isfinite(loss.item()):
                print("  skipped a step: the loss was not a number")
                continue
            grads, _ = optim.clip_grad_norm(grads, 1.0)
            optimizer.update(model, grads)
            mx.eval(model.trainable_parameters(), optimizer.state)
            losses.append(loss.item())
        print(f"  pass {epoch + 1}: loss {sum(losses) / max(len(losses), 1):.3f} ({time.perf_counter() - started:.0f} s)")
    examples.clear()

    merge_lora(model, trained_blocks)
    if out.exists() and not args.trial:
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    mx.save_safetensors(str(out / "weights.safetensors"), dict(tree_flatten(model.parameters())))
    shutil.copy(base / "config.json", out / "config.json")
    del model, optimizer
    mx.clear_cache()
    print(f"Saved to {out} after {(time.perf_counter() - started) / 60:.1f} min. Scoring on the held-out clips...")

    vocabulary = learning.terms(cfg["dictionary"]["terms"]) + cfg["dictionary"].get("hints", [])
    models = {"new": out, **({"earlier": earlier} if earlier else {})}
    scores = evaluate(providers(cfg, trained=models), voice, test, vocabulary, quiet=True)
    best = min(scores[name] for name in ("local turbo", "earlier") if name in scores)
    if args.trial:
        print(f"\nTrial only: config.toml and the clips are unchanged. The trained model is in {out}")
        return
    if scores["new"] < best:
        if TRAINED.exists():
            shutil.rmtree(TRAINED)
        out.rename(TRAINED)
        config.save({("stt", "local", "model"): str(TRAINED)})
        print(f"\nThe new model is better ({scores['new']:.1%} vs {best:.1%} WER):")
        print(f"config.toml now uses {TRAINED} for [stt.local]. Restart Bolo.")
    else:
        shutil.rmtree(out)
        print(f"\nThe new model is not better ({scores['new']:.1%} vs {best:.1%} WER), so it was deleted;")
        print("config.toml is unchanged.")
    print(f"Deleted {voice.delete(rows)} saved clip(s): this run has used them.")


if __name__ == "__main__":
    main()
