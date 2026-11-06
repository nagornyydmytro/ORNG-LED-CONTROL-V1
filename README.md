# ORNG LED CONTROL

Локальный световой пульт ORNG. Один движок считает кадр. Windows-пульт, упакованный релиз и Raspberry отличаются способом запуска и тем, кто держит Art-Net.

Актуальная карта документов: `docs/README.md`. Границы для агента: `AGENTS.md`. Исторический замысел июля 2026 лежит в `.context/PROJECT_CANON.md` и не перекрывает код.

Версия приложения: файл `VERSION` (сейчас смотри сам файл). Каталог Auto: `len(COMPOSITIONS)` в `backend/orng_led/audio/library.py`.

## Безопасный запуск дома

Нужны Python 3.12, Node.js 18+ и npm. Docker не нужен.

```powershell
.\scripts\bootstrap.ps1
.\scripts\run-dev.ps1
```

- API: `http://127.0.0.1:8000/api/health`
- Интерфейс Vite: `http://127.0.0.1:5173/` (прокси `/api` на backend)

`.\scripts\run.ps1` собирает frontend и открывает тот же API вместе со production-сборкой на порту 8000.

На старте транспорт — **Mock**, выход **disarmed**, кадр тёмный. Это не включает свет и не открывает Art-Net.

Не использовать дома `ORNG-LED-CONTROL.cmd` и `scripts/venue-start.ps1`. Они ждут USB-Ethernet контроллера, активируют Art-Net и вооружают выход при включённом blackout.

Не задавать `ORNG_OUTPUT_POLICY=venue_ready` (также `venue`, `always_arm`), `ORNG_KEEP_OUTPUT_ARMED=1` и `ORNG_CLOUD_DEVICE_MODE=raspberry`, если цель — локальная проверка без площадки. `ORNG_CLOUD_DEVICE_URL` подключает процесс к облаку.

Маршруты: `/` пульт, `/presets` пресеты, `/setup` настройки, `/channels` каналы, `/advanced` сервис облачной сборки. `/cloud` и `/settings` только перенаправляют.

Превью композиции (`/presets`, клип `GET /api/auto/preview-clip`) рисуется отдельно и не является кадром, который уходит в выход.

## Проверки

```powershell
.\scripts\check.ps1
```

| Область | Команда |
|---|---|
| Backend | `.\.venv\Scripts\python.exe -m pytest -q` из `backend\` |
| Backend lint | `.\.venv\Scripts\python.exe -m ruff check backend` из корня |
| Frontend | `npm run typecheck`, `npm run test`, `npm run build` из `frontend\` |
| Cloud unit | `npm run test:unit` из `cloud\` |
| Cloud workers | `npm run test:workers` из `cloud\` |

Pytest копирует `config/` во временный корень и не пишет в рабочую сцену ORNG BAR. Облачные тесты локальные: не `wrangler deploy` и не удалённая миграция D1.

Сборки, которые пишут в один и тот же каталог (`frontend/dist`, `cloud/admin-dist`), не запускать параллельно.

## Кто чем владеет

| Вопрос | Владелец |
|---|---|
| Запуск checkout | `scripts/run.ps1`, `scripts/run-dev.ps1` |
| Запуск заведения с ноутбука | `scripts/venue-start.ps1` — вооружает выход |
| Упакованный пульт | `scripts/orng-product-launch.ps1` → helper → uvicorn релиза |
| Raspberry | systemd запускает `bootstrap/updater_helper.py`, не uvicorn напрямую |
| Захват и темп | `BpmService` → процесс `audio/tempo_worker.py` |
| Принятый BPM и секция | `TempoFollower`, `SectionTracker` внутри `BpmService` |
| Пресет-BPM | тот же темп масштабирует эпизод пресета |
| Auto | `EpisodePlanner` подменяет базу композицией; каталог `library.COMPOSITIONS` |
| Выходной кадр | `Engine.render_at`, затем `OutputController` |
| Активная сцена | `config/runtime.yaml` → `active_scene_id` |
| Локация и исполнитель | облако (D1), не файл на диске пульта |
| Черновик сцены | `drafts/<scene_id>/`, не живой движок |
| Обновление приложения | приложение пишет запрос, helper вне релиза меняет `current.json` |

Пресет-BPM и Auto делят захват и анализатор. В пресете темп ведёт уже выбранный пресет. В Auto планировщик ставит композицию; скорость ползунка программы не разгоняет рисунок до строба. Потеря lock переходит в freewheel без подставной сетки. Редкое затемнение — событие паузы с кулдауном, не каждый тихий такт. Поле статуса ритма для интерфейса — `sync`, не внутреннее `hearing`.

Сцена — каталог `config/scenes/<scene_id>/` (patch, layout, пресеты, профили, `controls.yaml`). Установка — один paired instance. Исполнитель — та установка локации, которая держит Art-Net. Пульт с ролью raspberry показывает состояние исполнителя и пересылает команды; сам Art-Net не включает.

Клавиатура и макропад читают `controls.yaml` активной сцены. Удержания strobe / sweep живут, пока клавиша или lease не отпущены. Blackout перекрывает кадр последним.

## Сборка и откат

Подпись релиза: `scripts/build-app-release.py`. Секрет подписи только в окружении `ORNG_APP_SIGNING_KEY`, не в дереве. Публичная карта: `config/app-trusted-keys.production.json` и встроенные ключи кода.

Stage кладёт релиз рядом с установкой и не переключает его. Apply и rollback делает helper после blackout и disarm, затем проверяет `/api/health`. Откат возвращает `previous.json`. Это не откат сцены и не перезагрузка ОС.

`cloud/admin-dist` — результат `npm run build:cloud` из `frontend/`. В git его нет.

## Что не подтверждено железом

Профили сцены помечены `hardware_verified: false`. Адреса, карты каналов и калибровка голов остаются предварительными, пока их не проверили на приборах. Движение голов в Auto — программный путь; отдельная аппаратная приёмка траекторий не зафиксирована. Приёмка Raspberry по `docs/raspberry-acceptance-checklist.md` не закрыта этим репозиторием. Установленная на площадке сборка может отличаться от текущего коммита: её версия видна в health исполнителя, не в имени папки.

Локальный `config/runtime.yaml`, `config/instance.json` и `config/desk-role.json` принадлежат этому компьютеру и в git не входят.
