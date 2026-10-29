# Настоящая нейро-анимация бесплатно: три пути

Коротко: **анимация с движением внутри кадра бесплатна, но не в чате.** Агент в этом
репозитории работает в песочнице без GPU (проверено: `nvidia-smi` отсутствует, 2 vCPU, 3 ГБ RAM),
поэтому модели Wan/LTX здесь физически не запускаются. Зато их можно запустить на бесплатном
GPU в браузере.

| Путь | Цена | GPU | Качество | Время на 5-секундный клип |
|---|---|---|---|---|
| **Kaggle + LTX-Video 2B** | 0 | T4/P100 16 ГБ, ~30 ч/неделю | среднее (мягче, меньше деталей) | 1.5–4 мин |
| **Kaggle + LTX-Video 13B** | 0 | тот же, с `offload_to_cpu` | заметно лучше | 10+ мин |
| **MuAPI (Veo 3.1 / Kling 3.0 / Seedance 2.5)** | ~$0.85–$8.5 за клип | чужой | лучшее в 2026 | 1–10 мин |

## Путь 1 — Kaggle, готовый ноутбук

Файл: [`notebooks/kaggle-ltx-video-reels.ipynb`](../notebooks/kaggle-ltx-video-reels.ipynb)

1. [kaggle.com](https://www.kaggle.com) → аккаунт → **подтвердить телефон** (без этого
   нельзя включить интернет на GPU-сессии).
2. **Create → Notebook** → **File → Import Notebook → GitHub** → вставить ссылку на файл:
   `https://github.com/shorzyEZ/Open-Generative-AI/blob/arena/c544e7eb-open-generative-ai/notebooks/kaggle-ltx-video-reels.ipynb`
   (в диалоге импорта Kaggle принимает URL ноутбука на GitHub).
3. Справа: **Settings → Accelerator → GPU T4 x2**, **Settings → Internet → On**.
4. **Run All**. Первый запуск скачивает веса (~9 ГБ) — 5–10 минут.

Результат: `/kaggle/working/reel-9x16.mp4` — вертикаль 704×1216, ~4 клипа, склеенные
и с русской озвучкой Edge TTS.

**Модель:** `ltxv-2b-0.9.8-distilled` (Apache-2.0). Параметры, которые важны:
разрешение кратно 32 (`1216×704`), кадров `8k+1` (`121`), промпт — про движение и камеру,
на английском, до 200 слов.

## Путь 2 — Colab / свой сервер

Тот же сценарий без ноутбука:

```bash
git clone https://github.com/Lightricks/LTX-Video.git && cd LTX-Video
pip install -e ".[inference]"

python inference.py \
  --prompt "Slow cinematic push-in, chalk dust drifting through hard light, film grain" \
  --conditioning_media_paths frame.png --conditioning_start_frames 0 \
  --height 1216 --width 704 --num_frames 121 --seed 42 \
  --pipeline_config configs/ltxv-2b-0.9.8-distilled.yaml \
  --output_path out/clip1
```

`--offload_to_cpu` + `configs/ltxv-13b-0.9.8-distilled.yaml` — качество выше, скорость ниже.
Нужен GPU от 16 ГБ. **В GitHub Actions это не заработает:** у стандартных раннеров нет GPU.

## Путь 3 — платно, но сразу и лучшим

Пайплайн уже собран в этом репозитории: `.github/workflows/generate-video.yml` + `jobs/*.json`.
Нужен один секрет `MUAPI_API_KEY` и баланс от $10.

Что даёт $10 (цены с muapi.ai, проверены):

- Seedance 2.5, 480p, 5 с — **$0.85** за клип → ~11 клипов
- 1080p — дороже, 4K — до $8.5 за 5 с
- Veo 3.1 (8 с, 4K) и Kling 3.0 — верхняя граница качества

## Что выбрать под Reels про зал

1. **Начните с Kaggle** — 0 ₽, проверите, устраивает ли качество открытых моделей.
2. Если да — доводите монтаж: склейка, субтитры (`faster-whisper` или Edge TTS таймкоды),
   музыка. Всё бесплатно.
3. Если нужен уровень «как у блогеров с 1 млн просмотров» — один клип на Veo/Kling за $1–8
   дешевле, чем месяцы попыток выжать это из 2B модели.
