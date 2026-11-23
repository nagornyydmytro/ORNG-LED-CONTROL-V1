# Документы

Вход в проект — корневой `README.md`. Этот файл только раскладывает документы, чтобы старые отчёты не читались как текущий статус.

## Сейчас

| Документ | О чём |
|---|---|
| `README.md` | Запуск дома, проверки, карта ответственности |
| `AGENTS.md` | Границы агента, Git, площадка |
| `config/README.md` | Сцена, runtime, клавиатура |
| `docs/auto-music-contract.md` | Auto, lock, fallback, затемнение, превью |
| `docs/bpm-show.md` | Пресет-BPM и эпизоды |
| `docs/raspberry-runtime-contract.md` | Что обязано быть в поставке Pi |
| `docs/raspberry-bootstrap.md` | Bootstrap Pi, по умолчанию dry-run |
| `docs/app-releases.md` | Сборка, подпись, stage/apply, откат приложения |
| `docs/scene-releases.md` | Публикация и применение сцены |
| `docs/device-pairing.md` | Привязка устройства |
| `docs/cloud-architecture.md` | Контур Cloudflare, без Art-Net в облаке |
| `cloud/README.md` | Локальный Worker |
| `docs/app-signing-keys.md` | Публичные ключи подписи |

Размер каталога Auto берётся из `backend/orng_led/audio/library.py` (`COMPOSITIONS`), не из этих файлов.

Облачный preview сохранённого Mix — упакованные кадры `frontend/src/runtime/mixCatalogClip.json` (пересборка: `scripts/dump_mix_catalog_clip.py`). Локальный пульт для превью использует движок (`AppRuntime.preview_clip`), не этот JSON.

## История

Отчёты ниже — записи прошлых прогонов. «Готово» и «проверки пройдены» в них не переносятся на текущий HEAD.

- `docs/HOME_ACCEPTANCE_REPORT.md`
- `docs/IMPLEMENTATION_PLAN.md`
- `docs/HARDWARE_DAY_CHECKLIST.md`
- `docs/raspberry-acceptance-checklist.md`
- `docs/phase-11-report.md`, `docs/phase-12-report.md`
- `docs/stage-a-report.md`, `docs/stage-b-report.md`, `docs/stage-c-report.md`
- `docs/scene-editors-report.md`
- `docs/cloud-deploy-status.md` — снимок Cloudflare от 2026-10-25, не текущий live-статус
- `docs/orng-wtf-zone-decision.md`
- `docs/r2-billing-runbook.md`
- `.context/PROJECT_CANON.md` — замысел от 2026-07-28

Аппаратная приёмка Pi и точные DMX-профили по-прежнему не закрыты: у профилей сцены `hardware_verified: false`. Отдельный ритм PAR в Auto — плавная подложка, не второй BPM. Blackout оставляет безопасные позиции осей, а не убирает их каналы.
