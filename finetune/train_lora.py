"""QLoRA fine-tune of a small open model (default Qwen2.5-3B-Instruct) on the
synthetic receptionist dataset, then export to GGUF for Ollama.

Fits a 6 GB GPU (RTX 4050 Laptop) with 4-bit loading, batch 1 and
max_seq_length 3072. If you run out of memory, lower --max-len to 2048 or
train on Google Colab (free T4, 15 GB).

Setup (separate venv, Python 3.11 or 3.12 — torch/unsloth may not support 3.14):
    py -3.11 -m venv .venv-train
    .venv-train\\Scripts\\activate
    pip install -r finetune/requirements-train.txt

Run:
    python -m finetune.generate_dataset --n 1500
    python finetune/train_lora.py
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.tools import TOOL_SPECS  # noqa: E402  (same tool schema as production)


def load_rows(path: Path) -> list[dict]:
    # Read with json (not datasets.load_dataset) so tool-call arguments keep their exact keys.
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="unsloth/Qwen2.5-3B-Instruct-bnb-4bit")
    ap.add_argument("--max-len", type=int, default=3072)
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--rank", type=int, default=16)
    ap.add_argument("--out", default=str(ROOT / "finetune" / "out"))
    ap.add_argument("--gguf", default="q4_k_m", help="GGUF quantization, '' to skip export")
    args = ap.parse_args()

    import torch
    from datasets import Dataset
    from trl import SFTConfig, SFTTrainer
    from unsloth import FastLanguageModel
    from unsloth.chat_templates import train_on_responses_only

    model, tokenizer = FastLanguageModel.from_pretrained(args.base, max_seq_length=args.max_len, load_in_4bit=True)
    model = FastLanguageModel.get_peft_model(
        model, r=args.rank, lora_alpha=args.rank, lora_dropout=0,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth", random_state=3407)

    def to_text(rows):
        texts = []
        for r in rows:
            t = tokenizer.apply_chat_template(r["messages"], tools=TOOL_SPECS, tokenize=False)
            if len(tokenizer(t).input_ids) <= args.max_len:
                texts.append(t)
        return Dataset.from_dict({"text": texts})

    data_dir = ROOT / "finetune" / "data"
    train_ds = to_text(load_rows(data_dir / "train.jsonl"))
    val_ds = to_text(load_rows(data_dir / "val.jsonl"))
    print(f"train={len(train_ds)} val={len(val_ds)}")

    bf16 = torch.cuda.is_available() and torch.cuda.is_bf16_supported()
    trainer = SFTTrainer(
        model=model,
        processing_class=tokenizer,  # older TRL versions call this `tokenizer=`
        train_dataset=train_ds,
        eval_dataset=val_ds,
        args=SFTConfig(
            dataset_text_field="text",
            max_length=args.max_len,  # older TRL: max_seq_length
            per_device_train_batch_size=1,
            gradient_accumulation_steps=8,
            num_train_epochs=args.epochs,
            learning_rate=args.lr,
            warmup_ratio=0.05,
            lr_scheduler_type="cosine",
            logging_steps=10,
            eval_strategy="steps",
            eval_steps=100,
            save_steps=200,
            optim="adamw_8bit",
            bf16=bf16,
            fp16=not bf16,
            output_dir=args.out,
            report_to="none",
            seed=3407,
        ),
    )
    # Learn only the assistant turns (tool calls + answers), not the prompt/tool results.
    trainer = train_on_responses_only(trainer, instruction_part="<|im_start|>user\n",
                                      response_part="<|im_start|>assistant\n")
    trainer.train()

    lora_dir = Path(args.out) / "lora"
    model.save_pretrained(lora_dir)
    tokenizer.save_pretrained(lora_dir)
    print(f"Đã lưu LoRA adapter: {lora_dir}")

    if args.gguf:
        gguf_dir = Path(args.out) / "gguf"
        model.save_pretrained_gguf(str(gguf_dir), tokenizer, quantization_method=args.gguf)
        print(f"Đã xuất GGUF: {gguf_dir}  → xem finetune/README.md để nạp vào Ollama")


if __name__ == "__main__":
    main()
