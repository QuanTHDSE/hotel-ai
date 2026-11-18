# Fine-tune model lễ tân riêng (LoRA)

Mục tiêu: dạy model mở cỡ nhỏ (Qwen2.5-3B) **gọi đúng công cụ, đọc đúng ngày, trả lời đúng giọng lễ tân**.
Fine-tune **không** dạy model giá phòng hay phòng trống. Những dữ liệu đó lúc chạy vẫn luôn tra từ DB.

## 1. Sinh dữ liệu (chạy được ngay, không cần GPU)

```bash
python -m finetune.generate_dataset --n 1500
```

Mỗi mẫu là một hội thoại hoàn chỉnh: system prompt (với một "hôm nay" ngẫu nhiên), câu hỏi của khách, lời gọi tool,
**kết quả thật từ DB giả lập**, rồi câu trả lời dựng từ kết quả đó.
Nhờ vậy model học cách *trích số từ tool* thay vì tự nghĩ ra số.

Muốn dữ liệu đa dạng hơn: thêm câu hỏi mẫu vào `POLICY_Q` và `ex_availability`, hoặc thêm kịch bản mới vào `SCENARIOS`.
Khi có hội thoại thật của khách sạn, đã ẩn thông tin cá nhân, hãy trộn vào. Đây là dữ liệu giá trị nhất.

## 2. Train

**Trên máy (RTX 4050 6GB):** dùng venv riêng chạy Python 3.11/3.12.

```bash
py -3.11 -m venv .venv-train
.venv-train\Scripts\activate
pip install torch --index-url https://download.pytorch.org/whl/cu124
pip install -r finetune/requirements-train.txt
python finetune/train_lora.py
```

- Hết VRAM (CUDA out of memory) thì thêm `--max-len 2048`.
- Unsloth trên Windows đôi khi lỗi cài đặt. Khi đó dùng **WSL2 (Ubuntu)** hoặc **Google Colab**:
  upload cả thư mục project lên Colab, chọn GPU T4, rồi chạy 2 lệnh pip và `python finetune/train_lora.py`.
- 1500 mẫu × 2 epoch, model 3B: khoảng 30–60 phút trên T4.

Kết quả nằm ở `finetune/out/lora` (adapter) và `finetune/out/gguf/*.gguf` (file cho Ollama).

## 3. Nạp vào Ollama và so sánh

```bash
ollama pull qwen2.5:3b
ollama show qwen2.5:3b --modelfile > finetune/Modelfile
```

Mở `finetune/Modelfile`, sửa dòng `FROM ...` thành đường dẫn file gguf, ví dụ `FROM ./out/gguf/unsloth.Q4_K_M.gguf`.
Giữ nguyên phần `TEMPLATE`, vì Ollama cần nó để model gọi được tool.

```bash
cd finetune
ollama create hotel-receptionist -f Modelfile
cd ..
python -m eval.run_eval --model qwen2.5:3b          # trước fine-tune
python -m eval.run_eval --model hotel-receptionist  # sau fine-tune
```

Chỉ đưa model fine-tune vào dùng khi điểm eval **cao hơn** model gốc. Cuối cùng đặt `LLM_MODEL=hotel-receptionist` trong `.env`.
