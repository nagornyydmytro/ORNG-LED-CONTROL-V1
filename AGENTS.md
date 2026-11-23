# AGENTS.md

Коротко для следующего агента. Поведение смотри в коде. `README.md` — вход. `.context/PROJECT_CANON.md` и `docs/IMPLEMENTATION_PLAN.md` — история июля 2026 и не перекрывают более поздний код.

## Границы

- Один локальный движок. Windows-пульт, упакованный релиз и Raspberry исполняют его по-разному, но не являются вторым продуктом.
- Время шоу, принятый BPM и выходной кадр принадлежат разным местам. Часы шоу — `Engine`. Принятый BPM и секция — `BpmService` (`TempoFollower`, `SectionTracker`). Байты наружу — `OutputController.wire_frame`.
- Сцена — каталог `config/scenes/<id>/`. Активная сцена устройства — `runtime.yaml` (`active_scene_id`), не файл сцены. Локация и исполнитель — записи облака. Установка — один paired instance с токеном. Исполнитель — установка, которая реально держит Art-Net для локации.
- Updater helper (`bootstrap/updater_helper.py`, `bootstrap/helper_lib.py`) нарочно живёт вне заменяемого релиза и не импортирует `orng_led`. Не сливать его с приложением.
- Превью каталога не является кадром зала. Каталог Auto — `library.COMPOSITIONS`. Облачный Mix-клип (`mixCatalogClip`, `preset-catalog-2`) — только при совпадении штампа; локальный preview идёт через Engine.
- Face не входит в автоматический рисунок. Бары — одно полотно; зеркало не закон. Ручные удержания в Auto разрешены так, как это делает `AppRuntime`, а не как написано в старых отчётах.
- Движение голов — расширение `audio/head_motion.py`. Не включать его на площадке отдельным действием и не откатывать флаг «для порядка» без отдельного решения.

## Безопасные проверки

Дома и в этом дереве: Mock, output disarmed. Не запускать `ORNG-LED-CONTROL.cmd` и `scripts/venue-start.ps1`: они активируют Art-Net и вооружают выход.

Не делать без отдельного согласования: deploy, `stage`/`apply`, команды света, смену роли устройства, USB-захват, перезапуск площадки, push и правку remote.

Тесты держат копию config во временном корне (`backend/tests/conftest.py`). Не писать в рабочий `config/scenes/orng-bar` и не задавать `ORNG_CONFIG_ROOT` на него.

Переменные, которые вооружают выход, если их выставить: `ORNG_OUTPUT_POLICY=venue_ready|venue|always_arm`, `ORNG_KEEP_OUTPUT_ARMED=1`, `ORNG_CLOUD_DEVICE_MODE=raspberry`. Для домашней проверки их не ставить. `ORNG_CLOUD_DEVICE_URL` поднимает облачного агента.

Проверки: `.\scripts\check.ps1`, либо по отдельности pytest из `backend/`, `npm run typecheck|test|build` из `frontend/`, `npm run test:unit` и `npm run test:workers` из `cloud/`. Сборки, которые пишут в один каталог, не запускать одновременно. Worker-тесты локальные. `wrangler deploy` и удалённые миграции — не проверка.

Spawn-worker (`audio/tempo_worker.py`) и helper вне checkout (`tests/test_helper_standalone_outside_repo.py`, `tests/test_updater_helper_subprocess.py`) должны оставаться рабочими. Эмуляция ARM не считается проверкой Pi.

## Git и площадка

Локальные коммиты — да. Push и переписывание истории — нет, пока это не попросили отдельно. Не делать `reset --hard`, `git clean`, force-push и слепой `git add` всего дерева.

Не коммитить секреты, `cloud/.dev.vars`, токен устройства, `config/runtime.yaml`, `config/instance.json`, `config/desk-role.json`, пакеты `dist-*`, `cloud/admin-dist`, записи площадки.

Установленная Raspberry и облако не обновляются из этого дерева сами. Имя каталога и старый отчёт не доказывают, какая версия сейчас на свете.
