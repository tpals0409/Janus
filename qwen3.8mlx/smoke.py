"""Qwen3.8-27B-Uncensored-MLX 스모크 체크: 텍스트 + 비전 + 속도/메모리."""
import time, glob, tempfile, os
import mlx.core as mx
from PIL import Image, ImageDraw
from mlx_vlm import load, generate
from mlx_vlm.prompt_utils import apply_chat_template

MODEL = glob.glob(
    "/Users/kimsemin/.cache/huggingface/hub/"
    "models--orcarouter--Qwen3.8-27B-Uncensored-MLX/snapshots/*/4-bit"
)[0]


def run(model, proc, cfg, prompt, image=None, max_tokens=150):
    fmt = apply_chat_template(proc, cfg, prompt, num_images=1 if image else 0)
    t0 = time.perf_counter()
    res = generate(model, proc, fmt, image=image, max_tokens=max_tokens, verbose=False)
    dt = time.perf_counter() - t0
    text = res if isinstance(res, str) else res.text
    ntok = getattr(res, "generation_tokens", None)
    return text, dt, ntok


def main():
    t0 = time.perf_counter()
    model, proc = load(MODEL)
    cfg = model.config
    print(f"load      {time.perf_counter() - t0:.1f}s  |  weights {mx.get_active_memory() / 1e9:.1f} GB")

    text, dt, ntok = run(model, proc, cfg, "Count from 1 to 20, numbers only.")
    rate = f"{ntok / dt:.1f} tok/s" if ntok else f"{dt:.1f}s"
    print(f"\n[text]   {rate}\n{text.strip()[:250]}")

    # 흰 배경에 빨간 원 — 모델이 뭘 보는지 명확히 검증되는 도형
    img = Image.new("RGB", (256, 256), "white")
    ImageDraw.Draw(img).ellipse((64, 64, 192, 192), fill="red")
    path = os.path.join(tempfile.gettempdir(), "smoke_circle.png")
    img.save(path)

    text, dt, ntok = run(model, proc, cfg, "What shape and color is in this image?", image=path)
    rate = f"{ntok / dt:.1f} tok/s" if ntok else f"{dt:.1f}s"
    print(f"\n[vision] {rate}\n{text.strip()[:250]}")
    assert "red" in text.lower(), "비전 경로 실패: 빨간색을 인식하지 못함"

    print(f"\npeak      {mx.get_peak_memory() / 1e9:.1f} GB")
    print("OK")


if __name__ == "__main__":
    main()
