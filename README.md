# ORNG LED CONTROL V1

Локальный световой пульт для DJ-сетапа ORNG. Источник истины по продукту:
`.context/PROJECT_CANON.md`. План реализации: `docs/IMPLEMENTATION_PLAN.md`.
HOME acceptance: `docs/HOME_ACCEPTANCE_REPORT.md`.

## Требования (Windows)

- Python **3.12**
- Node.js **18+** (проверено на 24) и npm
- PowerShell 5.1+

Docker и Cloudflare **не** требуются. Путь к репозиторию может содержать
кириллицу и пробелы — скрипты используют `-LiteralPath` / абсолютные пути.

## Быстрый старт (чистая машина)

```powershell
cd C:\path\to\ORNG-LED-CONTROL-V1
.\scripts\bootstrap.ps1
.\scripts\run.ps1
```

Откройте `http://127.0.0.1:8000/` — production-сборка frontend раздаётся FastAPI.
Health: `http://127.0.0.1:8000/api/health`.

Маршруты UI:

- `/` — Пульт
- `/presets` — Пресети
- `/setup` — Налаштування (`/settings` перенаправляет сюда)

Если порт `8000` уже занят, `run.ps1` завершится с ошибкой и подсказкой —
второй конфликтующий экземпляр не запускается. Остановите старый процесс и
повторите запуск.

Резервная копия config (локально, не коммитить):

```powershell
.\scripts\backup-config.ps1
```

Пример полей app-конфига: `config/examples/app.yaml.example`.

## Режим разработки

```powershell
.\scripts\bootstrap.ps1
.\scripts\run-dev.ps1
```

- API: `http://127.0.0.1:8000/api/health`
- Vite UI: `http://127.0.0.1:5173/` (проксирует `/api` на backend)

На HOME-этапе для полного UI через backend используйте `.\scripts\run.ps1`.

## Проверки

```powershell
.\scripts\check.ps1
```

Отдельные команды:

| Область | Команда |
|---|---|
| Backend tests | `.\.venv\Scripts\python.exe -m pytest -q` из `backend\` |
| Backend lint | `.\.venv\Scripts\python.exe -m ruff check backend` |
| Backend format check | `.\.venv\Scripts\python.exe -m ruff format --check backend` |
| Frontend typecheck | `npm run typecheck` из `frontend\` |
| Frontend lint | `npm run lint` из `frontend\` |
| Frontend build | `npm run build` из `frontend\` |

## Структура

```text
backend/          FastAPI (Python 3.12)
frontend/         Vue 3 + Vite + TypeScript
config/           provisional YAML (profiles, patch, layout, app, presets)
scripts/          bootstrap / run / run-dev / check / backup-config
.context/         PROJECT_CANON.md
docs/             IMPLEMENTATION_PLAN.md, HARDWARE_DAY_CHECKLIST.md, HOME_ACCEPTANCE_REPORT.md
```

Конфигурация `config/` versioned (`schema_version: 1`). Все provisional-профили
помечены `hardware_verified: false`.

## Безопасность output

На старте активен только **Mock** transport и безопасный нулевой кадр (Blackout).
Art-Net output по умолчанию выключен (`output_armed: false`). Реальная сетевая
отправка не выполняется на HOME-этапе.

## Аппаратная граница

Точные DMX-профили, адреса, калибровка и сценическая доводка — только после
физической проверки (`docs/HARDWARE_DAY_CHECKLIST.md`). Следующий этап после
HOME — `H001` в заведении с оборудованием.
