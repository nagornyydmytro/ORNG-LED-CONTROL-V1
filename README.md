# ORNG LED CONTROL V1

Локальный световой пульт для DJ-сетапа ORNG. Источник истины по продукту:
`.context/PROJECT_CANON.md`. План реализации: `docs/IMPLEMENTATION_PLAN.md`.
HOME acceptance: `docs/HOME_ACCEPTANCE_REPORT.md`.

## Требования

- Python **3.12**
- Node.js **18+** и npm
- Windows: PowerShell 5.1+ · macOS: bash + Homebrew

Docker и Cloudflare **не** требуются.

## Быстрый старт (Windows)

```powershell
cd C:\path\to\ORNG-LED-CONTROL-V1
.\scripts\bootstrap.ps1
.\scripts\run.ps1
# venue one-click (Art-Net + Arm + Chrome):
.\ORNG-LED-CONTROL.cmd
```

## Быстрый старт (macOS — с GitHub одной командой)

На Mac (нужны Homebrew, git, node, python3.12):

```bash
curl -fsSL https://raw.githubusercontent.com/nagornyydmytro/ORNG-LED-CONTROL-V1/main/scripts/macos-install.sh | bash
```

Скрипт клонирует репо в `~/ORNG-LED-CONTROL-V1`, ставит зависимости,
кладёт ярлык на Desktop и запускает venue-start (Blackout + Art-Net + Arm + браузер).
Дальше только сними Blackout в UI.

Если репо уже склонировано:

```bash
cd ~/ORNG-LED-CONTROL-V1
./scripts/bootstrap.sh
./scripts/venue-start.sh
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
cloud/            Cloudflare Worker control plane (Phase 2; no Art-Net)
config/
  runtime.example.yaml   template for device-local active_scene_id
  runtime.yaml           gitignored local active scene (defaults to orng-bar)
  scenes/
    orng-bar/            canonical writable ORNG BAR (app/patch/layout/controls/profiles/presets)
scripts/          bootstrap / run / run-dev / check / backup-config
.context/         PROJECT_CANON.md
docs/             IMPLEMENTATION_PLAN, cloud-architecture, scene-releases, device-pairing, …
```

Cloud (optional): `cloud/README.md`. UI panel at `/cloud`. Agent env:
`ORNG_CLOUD_DEVICE_URL` + device token file.

Конфигурация versioned (`schema_version: 1`). Живые show-данные лежат только в
`config/scenes/<scene_id>/`. Локальный `config/runtime.yaml` (gitignored) хранит
активную сцену на устройстве; без него дефолт — `orng-bar`. Подробности:
`config/README.md`.

Все provisional-профили помечены `hardware_verified: false`.

`scenes/*/layout.yaml` задаёт расстановку (нормализованные координаты,
ориентация, монтаж) и порядок кабельной цепочки DMX. Симулятор строится только
из layout активной сцены.

### Сцены и клавиатура

- Список / активация: `GET /api/scenes`, `GET /api/scenes/active`,
  `POST /api/scenes/activate` с `{ "scene_id": "..." }`.
- Раскладка клавиатуры — `controls.yaml` активной сцены.
- Linux USB keyboard (опционально): `ORNG_LINUX_KEYBOARD=1` и
  `pip install -e "backend[linux]"`; права на `/dev/input` через группу `input`,
  не root. Raspberry acceptance для адаптера ещё не сделан.

## Живые эффекты

Кроме `Blackout`, `White Hit` и `Strobe`, в UI доступны `Drop` (momentary),
`Color Hit` (контрастный RGB-акцент) и `Sweep Hit` (одиночный проход по сцене).
Все они возвращают сцену автоматически, используют monotonic-таймеры защиты и
полностью перекрываются Blackout. Физическая раскладка 16 кнопок не изменена.

## Безопасность output

На старте активен только **Mock** transport и безопасный нулевой кадр (Blackout).
Art-Net output по умолчанию выключен (`output_armed: false`). Реальная сетевая
отправка не выполняется на HOME-этапе.

## Аппаратная граница

Точные DMX-профили, адреса, калибровка и сценическая доводка — только после
физической проверки (`docs/HARDWARE_DAY_CHECKLIST.md`). Следующий этап после
HOME — `H001` в заведении с оборудованием.
