# Генерация видео из чата — через GitHub Actions

Этот репозиторий умеет генерировать видео **без локальной установки**: работа выполняется
в облачном раннере GitHub Actions, где есть полный доступ в интернет. Агент в чате только
кладёт файл-задание в `jobs/` и забирает результат из ветки `media`.

```
чат ──push jobs/<job>.json──▶ GitHub Actions ──HTTPS──▶ api.muapi.ai
                                   │
                                   └──commit mp4──▶ ветка media:generated/*.mp4
                                                        │
чат ◀────────── git fetch origin media ─────────────────┘
```

Файл воркфлоу: `.github/workflows/generate-video.yml`.

---

## 1. Разовая настройка (2 шага, ~1 минута)

1. **Включите Actions** в форке: вкладка **Actions** → если видите баннер, нажмите
   **I understand my workflows, go ahead and enable them**.
2. **Добавьте ключ MuAPI**: **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `MUAPI_API_KEY`
   - Secret: **значение** ключа с <https://muapi.ai/access-keys> (не название/лейбл ключа)

Больше ничего делать не нужно: ни Node.js, ни Docker, ни установки приложения.
Ключ хранится только в секретах GitHub и доступен только внутри раннера.

---

## 2. Как запускать

### Вариант A — просто попросить в чате

Напишите агенту, что хотите снять. Он:
1. создаст `jobs/<slug>.json` с промптом и параметрами;
2. запушит его (это атомарно триггерит воркфлоу);
3. дождётся появления ветки `media` и заберёт `.mp4` в рабочую папку;
4. покажет видео прямо в чате.

### Вариант B — вручную из UI

**Actions → Generate video → Run workflow** → заполнить поля → **Run workflow**.

### Вариант C — из терминала

```bash
gh workflow run generate-video.yml --ref <branch> \
  -f prompt="A cinematic drone shot over a foggy pine forest at sunrise" \
  -f model="seedance-2.5-text-to-video" \
  -f aspect_ratio="16:9" -f duration="5" -f resolution="720p"
```

---

## 3. Формат файла-задания (`jobs/*.json`)

Любой push, затрагивающий `jobs/*.json`, запускает генерацию.

| Поле | Обязательно | Пример | Комментарий |
|---|---|---|---|
| `prompt` | да* | `"cinematic drone shot ..."` | английский даёт лучший результат |
| `model` | нет | `seedance-2.5-text-to-video` | id модели = endpoint MuAPI |
| `aspect_ratio` | нет | `16:9`, `9:16`, `1:1`, `4:3`, `3:4`, `21:9` | пусто = дефолт модели |
| `duration` | нет | `5` (сек) | пусто = дефолт модели |
| `resolution` | нет | `720p`, `1080p`, `4k` | не у всех моделей есть |
| `image_path` | нет | `inputs/frame.png` | путь к кадру в репозитории → image-to-video |
| `generate_audio` | нет | `true` / `false` | только Kling 3.0, FLUX 3 и др. |
| `seed` | нет | `42` | для воспроизводимости |
| `params` | нет | `{"high_bitrate": true}` | произвольные доп. параметры провайдера |

\* обязателен, если не задан `image_path`.

Шаблон: `jobs/example.json.example`. Секрет и правила: одна пушка = одно задание
(если файлов несколько, берётся первый).

---

## 4. Проверенные модели (по `packages/studio/src/models.js`)

| Модель (`model`) | Длительность | Разрешение | Форматы | Особенность |
|---|---|---|---|---|
| `seedance-2.5-text-to-video` / `-image-to-video` | 4–30 с | — | до 21:9 | баланс цена/качество, `high_bitrate`, `seed` |
| `kling-v3.0-standard-text-to-video` / `-pro-` | 3–15 с | — | 16:9, 9:16, 1:1 | нативное аудио (`generate_audio`) |
| `veo3.1-text-to-video` / `veo3.1-image-to-video` | 8 с | 720p/1080p/4K | 16:9, 9:16 | самый «киношный» вид |
| `openai-sora-2-text-to-video` | 4/8/12/16/20 с | — | 16:9, 9:16 | длинные связные сцены |
| `wan3.0-text-to-video` / `-image-to-video` | 2–30 с | 480/720/1080p | 6 форматов | дёшево, длинно, для черновиков |
| `minimax-h3-text-to-video` | 5–15 с | 2K | до 21:9 | анаморфный кадр |
| `ltx-2.5-text-to-video` | 5–20 с | 720p–4K | 16:9, 9:16 | быстро и в 4K |
| `flux-3-text-to-video` | 5 с | 720/1080p | 6 форматов | нативное аудио |

Полный список — 111 text-to-video и 184 image-to-video: см. `t2vModels` / `i2vModels`
в `packages/studio/src/models.js`.

---

## 5. Схема промпта

```
[тип кадра] + [субъект и детали] + [действие] + [окружение]
+ [движение камеры] + [свет и оптика] + [стиль]
```

- одно действие на клип (5 с = один микро-сюжет);
- описывайте **движение**, а не статичное состояние;
- камера явно: `slow dolly-in`, `low-angle tracking shot`, `crane up reveal`, `locked-off`;
- свет явно: `golden hour backlight`, `hard rim light`, `neon reflections on wet asphalt`;
- текст в кадре не просите — будет кривой.

```
A cinematic low-angle tracking shot of a lone astronaut walking across black
volcanic sand, dust drifting in golden backlight, shallow depth of field,
slow dolly-in, shot on 35mm anamorphic, subtle film grain
```

---

## 6. Пайплайн «сначала кадр, потом движение»

Самый надёжный способ получить качество:

1. **Кадр.** Агент генерирует стартовый кадр своим встроенным инструментом
   (работает здесь, ключей не требует), коммитит его как `inputs/frame.png`.
2. **Оживление.** Job с `"image_path": "inputs/frame.png"` и моделью `*-image-to-video`.
3. **Сборка.** 5–8-секундные куски склеиваются в монтаже; для продолжения —
   модели `*-extend`.
4. **Финал.** 1080p/4K на Veo 3.1 / Kling 3.0 Pro / Seedance 2.5.

---

## 7. Ограничения и деньги

- Каждый прогон тратит кредиты MuAPI. Дешёвый черновик: `wan3.0-…`,
  `duration: 5`, `resolution: 480p`. Финальный рендер — отдельным заданием.
- Видео идёт 1–10 минут; раннер ждёт до 30 минут, таймаут джобы — 75 минут.
- Публичный репозиторий = бесплатные минуты Actions, но **не** бесплатные кредиты MuAPI.
- `.mp4` копится в ветке `media` → репозиторий растёт. 4K/30 s ≈ 50–150 МБ.
  Чистить: удалять файлы из `generated/` в ветке `media`.
- Прогресс и причины ошибок видны в **Actions → Generate video → последний run**.

---

## 8. Если Actions недоступны

- **Хостинг без установки:** <https://muapi.ai/open-generative-ai> — весь Video Studio
  в браузере, тот же ключ MuAPI.
- **Десктоп-приложение:** инсталляторы в Releases репозитория.
- **Локальная сборка:** `npm run setup && npm run dev` (нужен Node.js 18+).
