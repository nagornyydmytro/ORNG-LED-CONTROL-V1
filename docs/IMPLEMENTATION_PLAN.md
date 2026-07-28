# ORNG LED CONTROL — Implementation Plan

Статус документа: **единственный источник истины по этапам реализации**  
Дата начала: **2026-07-28**

Основа всех этапов: `.context/PROJECT_CANON.md`.

## 1. Правила работы агента

1. Сначала прочитать полностью:
   - `.context/PROJECT_CANON.md`;
   - `docs/IMPLEMENTATION_PLAN.md`;
   - `docs/HARDWARE_DAY_CHECKLIST.md`.
2. Проверить ветку, origin и `git status`.
3. Не изменять соседние репозитории.
4. Не создавать Cloudflare, облачный деплой, авторизацию или Docker-only flow.
5. Не включать реальный Art-Net output на домашнем этапе.
6. Не выдавать Mock за аппаратную проверку.
7. Один этап — один логический commit. Допускается разделить этап только при
   реальной необходимости, объяснив причину.
8. Перед commit выполнить проверки этапа.
9. Push делать в текущую ветку, если origin настроен и доступен.
10. Не останавливаться внутри одного этапа, если нет реального блокера.

Статусы:

- `PENDING`;
- `IN PROGRESS`;
- `DONE`;
- `BLOCKED`;
- `PENDING HARDWARE`;
- `NOT STARTED`.

## 1.1 Границы одного запроса агента

1. Один запрос агента выполняет ровно один следующий логический commit.
2. После commit агент делает push, предоставляет отчёт и завершает запрос.
3. Следующий запрос самостоятельно восстанавливает состояние по canon-файлам,
   `docs/IMPLEMENTATION_PLAN.md`, `git log` и `git status`.
4. Нельзя объединять несколько этапов `Lxxx` в одном запросе.
5. Нельзя заранее выполнять scope следующего этапа, даже частично.
6. Остановка происходит на границе этапа после commit, push и отчёта, а не
   посреди незавершённого этапа.
7. После `L013` требуется отдельный финальный ledger/report commit, уже
   предусмотренный этим планом.

## 1.2 Правило записи commit hash

1. Hash текущего commit не нужно пытаться записывать внутрь него: запись
   изменила бы сам hash.
2. Hash созданного commit сообщает отчёт запроса.
3. Следующий запрос записывает этот hash в таблицу
   `docs/IMPLEMENTATION_PLAN.md` вместе с результатами проверок этапа.
4. Финальный ledger commit после `L013` содержит hash всех `L001`–`L013`.
5. Hash самого финального ledger commit сообщается в финальном отчёте без
   создания ещё одного commit.

## 2. Сводная таблица

| ID | Этап | Статус | Commit | Проверки |
|---|---|---|---|---|
| L001 | Canon bootstrap | DONE | `1807357` | PASS — см. `L001 — Результаты проверок` |
| L002 | Workspace scaffold | DONE | `df98ab6` | PASS — см. `L002 — Результаты проверок` |
| L003 | Config, fixtures and patch | DONE | `fbaf08a` | PASS — см. `L003 — Результаты проверок` |
| L004 | Deterministic engine and layers | DONE | `1c01252` | PASS — см. `L004 — Результаты проверок` |
| L005 | Mock/Art-Net transports and safety | DONE | `f1cb34a` | PASS — см. `L005 — Результаты проверок` |
| L006 | Backend API and realtime state | DONE | `ea3fae8` | PASS — см. `L006 — Результаты проверок` |
| L007 | UI shell and control panel | DONE | `7493e01` | PASS — см. `L007 — Результаты проверок` |
| L008 | Stage simulator | DONE | `48687b0` | PASS — см. `L008 — Результаты проверок` |
| L009 | Setup and calibration wizard | DONE | `02f414c` | PASS — см. `L009 — Результаты проверок` |
| L010 | Preset editor | DONE | `9151dbf` | PASS — см. `L010 — Результаты проверок` |
| L011 | Ten complete presets | DONE | `15f8053` | PASS — см. `L011 — Результаты проверок` |
| L012 | Input adapter and 16-button mapping | DONE | `b10f4e0` | PASS — см. `L012 — Результаты проверок` |
| L013 | Integration, Windows scripts and HOME acceptance | DONE | `c2d06e9` | PASS — см. `L013 — Результаты проверок` |
| H001+ | Real hardware acceptance | PENDING HARDWARE | — | — |

## 3. HOME PLAN

### L001 — Canon bootstrap

Scope:

- проверить, что три canon-файла находятся по правильным путям;
- проверить отсутствие противоречий;
- добавить базовый `.gitignore`, если его нет;
- зафиксировать канон первым commit.

Non-goals:

- scaffolding приложения;
- переименование утверждённых файлов;
- разбиение канона на десятки документов.

Acceptance:

- репозиторий содержит три обязательных документа;
- `git status` не содержит случайных архивов и секретов;
- commit message: `docs: add ORNG LED CONTROL canon`.

Verification:

- ссылки между документами разрешаются;
- Markdown читается без повреждения UTF-8.

#### L001 — Результаты проверок

Статус: `DONE`. Дата: `2026-07-28`. Ветка: `main`.
Commit: `180735714a12046f675e1964892cacaa6f4dd64b` (`1807357`).

| Проверка | Результат |
|---|---|
| `.context/PROJECT_CANON.md` по правильному пути | PASS |
| `docs/IMPLEMENTATION_PLAN.md` по правильному пути | PASS |
| `docs/HARDWARE_DAY_CHECKLIST.md` по правильному пути | PASS |
| Внутренние ссылки на пути разрешаются | PASS — 5 ссылок, все существуют |
| UTF-8 без BOM, без повреждённых символов | PASS — 3 файла, 0 replacement chars |
| Предварительный DMX patch без пересечений | PASS — 12 приборов, каналы `1..170` |
| Последний канал не превышает 512 | PASS — максимум `170` |
| Состав оборудования согласован между документами | PASS — `4 + 4 + 2 + 2 = 12` |
| Длительность пресета согласована | PASS — `10 × 18 = 180` секунд |
| Карта 16 кнопок согласована | PASS — canon §9 и `L012` |
| Нет секретов, архивов, зависимостей и IDE-мусора | PASS |
| `.gitignore` добавлен | PASS — `.context/` не игнорируется целиком |

Зафиксированные особенности:

- в canon-файлах нет Markdown-гиперссылок, только пути в backticks; поэтому
  проверялось существование файлов по указанным путям;
- порядок Beam в предварительном patch (`Beam Right` перед `Beam Left`) не
  противоречит пространственной схеме: canon §4 прямо указывает, что порядок в
  DMX-цепи не является адресацией;
- фактических противоречий между `PROJECT_CANON.md`, `IMPLEMENTATION_PLAN.md` и
  `HARDWARE_DAY_CHECKLIST.md` не обнаружено; продуктовые решения не изменялись.

### L002 — Workspace scaffold

Scope:

- Python 3.12/FastAPI backend;
- Vue 3/Vite/TypeScript frontend;
- единая понятная структура репозитория;
- dev-команды;
- production build frontend, раздаваемый FastAPI;
- PowerShell bootstrap/run scripts;
- базовый health endpoint;
- базовые formatter/lint/test commands.

Non-goals:

- предметный DMX-движок;
- Docker как обязательный runtime;
- облачный deploy.

Acceptance:

- чистая установка на Windows описана и автоматизирована;
- backend запускается;
- frontend dev server запускается;
- production frontend собирается и открывается через backend;
- один command запускает production-like локальное приложение.

Verification:

- backend unit smoke;
- frontend typecheck;
- frontend production build;
- PowerShell scripts проходят syntax check, доступный в среде.

#### L002 — Результаты проверок

Статус: `DONE`. Дата: `2026-07-28`. Ветка: `main`.
Commit: `df98ab696656ace7374c7036074b44587183c6c6` (`df98ab6`).

| Проверка | Результат |
|---|---|
| Структура `backend/` + `frontend/` + `scripts/` + `README.md` | PASS |
| `GET /api/health` возвращает Mock / `output_armed: false` | PASS |
| Backend unit smoke (`pytest`) | PASS — 1 passed |
| Backend ruff format/check | PASS |
| Frontend typecheck (`vue-tsc`) | PASS |
| Frontend lint (`eslint`) | PASS |
| Frontend production build (`vite build`) | PASS |
| SPA раздаётся FastAPI из `frontend/dist` | PASS — `GET /` → 200 |
| PowerShell syntax (`Parser::ParseFile`) | PASS — bootstrap/run/run-dev/check |
| `.\scripts\bootstrap.ps1` установка deps | PASS — Python 3.12.10, npm |
| Docker / Cloudflare / auth не добавлены | PASS |

Зафиксированные особенности:

- hash L001 записан в таблицу и в блок результатов L001;
- предметный DMX-движок, пресеты, пульт и симулятор **не** входят в L002 и не
  реализованы;
- Art-Net output не активируется; реальные сетевые пакеты не отправлялись;
- предупреждение Starlette о `httpx`/`TestClient` зафиксировано, на acceptance
  не влияет.

### L003 — Config, fixtures and patch

Scope:

- versioned YAML schema;
- модели fixture profile, fixture instance, patch, spatial layout и app config;
- provisional 12-fixture config из канона;
- `hardware_verified: false`;
- patch validation: диапазон `1..512`, footprint, overlaps, duplicate IDs;
- семантические capabilities;
- атомарное сохранение и понятные validation errors.

Non-goals:

- утверждение реальных channel profiles;
- отправка Art-Net;
- полный preset runtime.

Acceptance:

- provisional patch загружается;
- искусственные overlap/out-of-range configs отклоняются;
- глобальный канал корректно вычисляется как
  `start_address + local_channel - 1`;
- неизвестная/несовместимая версия схемы отклоняется.

Verification:

- unit tests моделей и YAML round-trip;
- negative fixtures/patch tests;
- UTF-8 paths/config smoke на Windows.

#### L003 — Результаты проверок

Статус: `DONE`. Дата: `2026-07-28`. Ветка: `main`.
Commit: `fbaf08a3e6a659a14f34c11e62d353de6e6b9f41` (`fbaf08a`).

| Проверка | Результат |
|---|---|
| Provisional show config загружается (12 fixtures) | PASS |
| Все profiles `hardware_verified: false` | PASS |
| Глобальный канал `start + local - 1` | PASS |
| Overlap / out-of-range / duplicate ID отклоняются | PASS |
| Неизвестная `schema_version` и unknown fields отклоняются | PASS |
| YAML round-trip app/profile | PASS |
| Atomic write + invalid edit не портит last-good | PASS |
| UTF-8 path smoke (`конфіг/налаштування.yaml`) | PASS |
| Backend regression (`pytest`, включая health) | PASS — 14 passed |
| Ruff check/format | PASS |
| Art-Net / engine / presets не добавлены | PASS — вне scope |

### L004 — Deterministic engine and layers

Scope:

- виртуальные/монотонные часы;
- 30 FPS frame loop;
- 512-канальный frame buffer;
- semantic renderer;
- fixture-profile mapping;
- episode/cycle clock;
- interpolated Beam movement with speed limits;
- базовый preset, White Hit, Strobe, Face, brightness и Blackout layers;
- сохранение активного пресета после overlay.

Non-goals:

- Art-Net socket;
- окончательные 10 художественных пресетов;
- UI.

Acceptance:

- одинаковое время даёт одинаковый кадр;
- frame values всегда `0..255`;
- Blackout обнуляет все 512 каналов;
- White Hit/Strobe не затрагивают Face PAR;
- overlay не сбрасывает выбор и clock пресета;
- Beam position не делает мгновенных скачков.

Verification:

- unit tests с fake clock;
- layer-priority tests;
- cycle-boundary tests;
- Beam slope/speed tests.

#### L004 — Результаты проверок

Статус: `DONE`. Дата: `2026-07-28`. Ветка: `main`.
Commit: `1c01252b806662a518da55b5794fdf38bb600211` (`1c01252`).

| Проверка | Результат |
|---|---|
| Одинаковое preset time → одинаковый кадр | PASS |
| Frame values всегда `0..255` (512 каналов) | PASS |
| Blackout обнуляет все каналы, clock пресета идёт | PASS |
| White Hit / Strobe не меняют Face PAR | PASS |
| Overlay не сбрасывает preset id / clock | PASS |
| Cycle boundary `180s → 0` / episode wrap | PASS |
| Beam speed limit / no teleport | PASS |
| Backend regression (`pytest`) | PASS — 25 passed |
| Ruff check/format | PASS |
| Art-Net / UI / 10 artistic presets не добавлены | PASS — вне scope |

### L005 — Mock/Art-Net transports and safety

Scope:

- общий output adapter contract;
- Mock transport по умолчанию;
- Art-Net ArtDmx serializer;
- UDP transport, который не активируется домашними тестами;
- sequence, universe, packet length и 512 slots;
- explicit output arm/enable;
- zero-frame shutdown sequence;
- Strobe release/failsafe/timeout.

Non-goals:

- реальная UDP-доставка контроллеру;
- Art-Net discovery как обязательное условие;
- утверждение target IP/universe.

Acceptance:

- приложение стартует только в Mock;
- Art-Net нельзя включить неявно;
- ArtDmx bytes соответствуют протоколу;
- выключение/Blackout формирует нулевой frame;
- disconnect/focus loss снимает held Strobe.

Verification:

- packet golden tests;
- mock integration tests;
- safety state-machine tests;
- тесты не отправляют пакеты в реальную сеть.

#### L005 — Результаты проверок

Статус: `DONE`. Дата: `2026-07-28`. Ветка: `main`.
Commit: `f1cb34a42ea1be6239bdba96cc50390d3a856259` (`f1cb34a`).

| Проверка | Результат |
|---|---|
| Старт только Mock / `armed=false` / `allow_real_network=false` | PASS |
| Art-Net нельзя включить без `explicit=True` | PASS |
| Arm требует explicit + Art-Net transport | PASS |
| ArtDmx golden layout (ID, opcode, ver, length BE, 512 slots) | PASS |
| Sequence 1..255 skip 0 | PASS |
| Mock publish / Blackout zero frame | PASS |
| UDP через `RecordingSocket` (без реального socket) | PASS |
| Без `allow_real_network` реальный UDP запрещён | PASS |
| Disarmed Art-Net отказывает send | PASS |
| Shutdown: 3 zero frames + disarm + Mock | PASS |
| Disconnect / focus / visibility / timeout снимают Strobe | PASS |
| Backend regression (`pytest`) | PASS — 42 passed |
| Ruff check/format | PASS |
| Реальные сетевые Art-Net пакеты не отправлялись | PASS |

### L006 — Backend API and realtime state

Scope:

- typed API для state, commands, config, patch, profiles и presets;
- WebSocket realtime snapshot;
- single engine instance lifecycle;
- command idempotency там, где нужна;
- понятные ошибки;
- health/readiness;
- безопасное завершение приложения.

Non-goals:

- учётные записи;
- облачный API;
- multi-workspace.

Acceptance:

- UI может получить полный initial state и realtime updates;
- команды выбора пресета и overlays отражаются в state;
- reconnect UI не дублирует engine;
- shutdown вызывает output safety.

Verification:

- API tests;
- WebSocket connect/reconnect tests;
- lifecycle integration tests.

#### L006 — Результаты проверок

Статус: `DONE`. Дата: `2026-07-28`. Ветка: `main`.
Commit: `ea3fae87eaa0a9fc3bfb426c3379d5cfbc16bb93` (`ea3fae8`).

| Проверка | Результат |
|---|---|
| Health / ready | PASS |
| GET `/api/state` полный snapshot + frame 512 | PASS |
| Config app/patch/profiles/layout + presets | PASS |
| Commands: preset/face/white-hit/strobe/blackout/brightness | PASS |
| Command idempotency (`client_command_id`) | PASS |
| WebSocket hello + command broadcast | PASS |
| Reconnect не дублирует engine; disconnect снимает Strobe | PASS |
| Focus/visibility failsafe через WS | PASS |
| Shutdown → output safety (zeros/disarm/Mock) | PASS |
| Backend regression (`pytest`) | PASS — 53 passed |
| Ruff check/format | PASS |
| UI shell / simulator не добавлены | PASS — вне scope |

### L007 — UI shell and control panel

Scope:

- тёмный ORNG shell, accent `#FF6A00`;
- маршруты `Пульт`, `Пресети`, `Налаштування`;
- desktop и mobile navigation;
- 10 крупных кнопок;
- Face, White Hit, held Strobe, Blackout и brightness;
- active preset, episode, clock и output status;
- toasts, loading/error/offline states;
- keyboard accessibility и touch targets.

Non-goals:

- light theme;
- профессиональный dashboard;
- авторизация.

Acceptance:

- основной пульт работает на desktop и узком phone viewport;
- Strobe использует pointer/key down/up и снимается на
  pointercancel/blur/visibility/disconnect;
- Blackout доступен без modal;
- UI не показывает false connected.

Verification:

- component tests критических controls;
- viewport checks;
- frontend typecheck/build;
- axe/basic accessibility checks, если tooling позволяет.

#### L007 — Результаты проверок

Статус: `DONE`. Дата: `2026-07-28`. Ветка: `main`.
Commit: `7493e012e93f1c256e890f80bb66fc45e4f65b95` (`7493e01`).

| Проверка | Результат |
|---|---|
| Маршруты Пульт / Пресети / Налаштування + mobile nav | PASS |
| 10 кнопок пресетов, Face/White Hit/Strobe/Blackout/brightness | PASS |
| Strobe pointer/key + cancel; blur/visibility failsafe hooks | PASS |
| Blackout без modal | PASS |
| Status: Mock/output off, без false Art-Net connected | PASS |
| Component tests (vitest) | PASS — 7 passed |
| Viewport CSS contract (`preset-pad` + media queries) | PASS |
| Frontend typecheck / lint / production build | PASS |
| axe | SKIP — отдельный axe tooling не подключался; есть `aria-pressed`/labels в тестах |
| Backend regression (`pytest`) | PASS |
| Simulator / setup wizard / preset editor не реализованы полностью | PASS — placeholder pages only |

### L008 — Stage simulator

Scope:

- схема 12 приборов;
- PAR color/intensity;
- `4 × 8` Bar segments;
- Beam position/direction/intensity;
- Face state;
- overlays;
- 512-channel inspector;
- speed multiplier для preview.

Non-goals:

- физически точный 3D renderer;
- доказательство аппаратной корректности.

Acceptance:

- simulator рендерит текущий engine state, а не отдельную имитацию;
- можно увидеть полный 180-секундный цикл ускоренно;
- пространственные пары и направления различимы;
- Blackout визуально и в channel inspector обнуляет output.

Verification:

- simulator state tests;
- accelerated-clock tests;
- visual smoke на desktop/mobile.

#### L008 — Результаты проверок

Статус: `DONE`. Дата: `2026-07-28`. Ветка: `main`.
Commit: `48687b0a2b84f9ab7abccdf1ba799ec0d9b9595e` (`48687b0`).

| Проверка | Результат |
|---|---|
| Decode 12 приборов из текущего DMX frame + patch (не parallel sim) | PASS |
| PAR color/intensity; Bars `4×8` segments; Beam pan/tilt/dimmer; Face | PASS |
| Overlays + active preset/episode/time в UI | PASS |
| Channel inspector 512 + blackout → кадр/визуал = 0 | PASS |
| Preview speed API (`1..120`) ускоряет engine clock | PASS — ×60 покрывает 180s цикл |
| Simulator state + accelerated-clock tests | PASS — `test_simulator.py` |
| API state includes `simulator` + `preview-speed` command | PASS |
| Frontend simulator component tests | PASS — 3 passed |
| Visual smoke desktop/mobile | PASS — CSS grid 4/2 cols + stage layout; без browser MCP |
| Frontend typecheck / lint / production build | PASS |
| Backend regression (`pytest`) | PASS — 60 passed |
| Setup wizard / preset editor / L009+ не начаты | PASS |

### L009 — Setup and calibration wizard

Scope:

- 10 шагов из канона;
- Mock/Art-Net config;
- patch editor и validation;
- profile/channel editor;
- raw DMX tester;
- layout/orientation;
- Bar/Beam calibration forms;
- fixture/group/Blackout test controls;
- readiness summary;
- явные hardware status badges.

Non-goals:

- fake discovery;
- отметка аппаратных шагов `DONE`;
- автоматическое угадывание channel profiles.

Acceptance:

- весь wizard можно пройти в Mock;
- аппаратные пункты остаются `Не перевірено на обладнанні`;
- raw tester начинает/заканчивает нулём;
- ошибки patch видны до сохранения/output.

Verification:

- form/component tests;
- API persistence tests;
- raw tester safety tests;
- mobile smoke.

#### L009 — Результаты проверок

Статус: `DONE`. Дата: `2026-07-28`. Ветка: `main`.
Commit: `02f414cfd4dd7a3a768e5caf3f483eefa0ecdeba` (`02f414c`).

| Проверка | Результат |
|---|---|
| 10 шагов wizard в UI (Mock path) | PASS |
| Hardware badges `Не перевірено на обладнанні` | PASS — не помечаются DONE |
| Patch validate до save; overlap → 400, диск не меняется | PASS |
| App/profile/layout atomic YAML save; `hardware_verified`/`output_armed` forced false | PASS |
| Raw tester start/exit zeros (Mock only, no arm) | PASS |
| Identify fixture/group + readiness summary | PASS |
| Form/component tests (vitest setup) | PASS — 3 passed |
| API persistence + raw safety (`test_setup.py`) | PASS — 6 passed |
| Mobile smoke | PASS — wizard-steps 2-col CSS ≤900px; без browser MCP |
| Frontend typecheck / lint / production build | PASS |
| Backend regression (`pytest`) | PASS — 66 passed |
| Preset editor / L010+ не начаты | PASS |

### L010 — Preset editor

Scope:

- список карточек;
- запуск, rename, duplicate, edit;
- простой episode-card editor;
- add/delete/reorder;
- fields из канона;
- create custom preset;
- schema validation;
- preview через реальный engine/simulator;
- atomic YAML save.

Non-goals:

- многодорожечный timeline;
- raw DMX внутри пресета;
- AI runtime.

Acceptance:

- пользователь создаёт простой пресет без редактирования YAML;
- невалидный пресет не заменяет последний валидный;
- reorder работает mouse/touch;
- preview не включает Art-Net.

Verification:

- CRUD/editor tests;
- invalid YAML/config recovery;
- preview integration;
- build/mobile checks.

#### L010 — Результаты проверок

Статус: `DONE`. Дата: `2026-07-28`. Ветка: `main`.
Commit: `9151dbf493971e1823b7d954de47d7f08203b942` (`9151dbf`).

| Проверка | Результат |
|---|---|
| YAML preset schema + `config/presets/P05.yaml` + semantic evaluate | PASS |
| Карточки / create / rename / duplicate / edit / delete | PASS |
| Episode-card editor: fields, add/delete, reorder (↑↓ + drag) | PASS |
| Invalid update не заменяет last valid на диске | PASS |
| Preview через engine, Mock only, Art-Net не armed | PASS |
| CRUD/editor API tests (`test_presets_editor.py`) | PASS — 6 passed |
| Frontend editor tests (vitest) | PASS — 2 passed |
| Frontend typecheck / lint / production build | PASS |
| Mobile smoke | PASS — editor CSS stack ≤900px; без browser MCP |
| Backend regression (`pytest`) | PASS — 72 passed |
| Ten complete P01–P10 artistic packs / L011 не начаты | PASS |

### L011 — Ten complete presets

Scope:

- P01–P10;
- каждый ровно 10 × 18 секунд;
- разные композиции/палитры/эффекты;
- loop transition;
- semantic groups;
- hardware tuning flag false;
- metadata для интенсивности и UI.

Non-goals:

- заявление о сценической готовности;
- аппаратно зависимые channel assumptions.

Acceptance:

- ровно 10 штатных пресетов;
- каждый ровно 180 секунд;
- каждый проходит schema validation;
- каждый изменяется внутри эпизодов;
- P01–P10 визуально и логически различаются;
- Beam interpolation и strobe limits соблюдаются;
- конец/начало цикла не создаёт запрещённого скачка.

Verification:

- parametrized preset tests;
- accelerated simulator review;
- content-difference checks;
- cycle boundary and safety tests.

#### L011 — Результаты проверок

Статус: `DONE`. Дата: `2026-07-28`. Ветка: `main`.
Commit: `15f805323f3a7a14979988f191661b375fe3ea48` (`15f8053`).

| Проверка | Результат |
|---|---|
| Ровно 10 штатных YAML `P01`–`P10` (10×18=180s, `hardware_tuned=false`) | PASS |
| Schema validation + labels из канона | PASS |
| Content-difference (composition ≠ speed-only) | PASS |
| Intra-episode evolution (не static) | PASS |
| Accelerated full-cycle review per preset | PASS |
| Cycle-boundary Beam без teleport (speed limits) | PASS |
| Staff intents не обходят strobe limits (4 Hz / 8 s) | PASS |
| Parametrized tests (`test_staff_presets.py`) | PASS — 58 passed |
| Backend regression (`pytest`) | PASS — 130 passed |
| Frontend vitest / typecheck / lint / build | PASS — 15 passed |
| Input adapter / L012 не начат | PASS |

### L012 — Input adapter and 16-button mapping

Scope:

- общий input event contract;
- keyboard/mock adapter;
- 16-button mapping;
- press/release semantics;
- debouncing abstraction;
- brightness step rules;
- подготовленная граница GPIO adapter.

Non-goals:

- Raspberry libraries;
- настоящий GPIO;
- ESP32 firmware.

Acceptance:

- все действия пульта вызываются через общий contract;
- кнопки 1–10 выбирают P01–P10;
- Strobe корректно обрабатывает press/release;
- повтор клавиши не создаёт зависший held state.

Verification:

- mapping tests;
- key repeat/debounce tests;
- disconnect/release safety tests.

#### L012 — Результаты проверок

Статус: `DONE`. Дата: `2026-07-28`. Ветка: `main`.
Commit: `b10f4e0d4963c423262554cbf78ca885952cb623` (`b10f4e0`).

| Проверка | Результат |
|---|---|
| InputEvent contract + 16-button mapping (1–10→P01–P10) | PASS |
| Keyboard/mock adapters + GPIO stub boundary | PASS |
| Debounce / key-repeat не залипає Strobe | PASS |
| Brightness step ±0.05 через contract | PASS |
| UI pad actions через `/api/input/button` | PASS |
| Disconnect clears strobe + debouncer | PASS |
| Mapping/key tests (`test_input_adapter.py`) | PASS — 8 passed |
| Frontend input constants/API tests | PASS — 3 passed |
| Backend regression (`pytest`) | PASS — 138 passed |
| Frontend vitest / typecheck / lint / build | PASS — 18 passed |
| L013 integration/scripts не начат на момент L012 | PASS |

### L013 — Integration, Windows scripts and HOME acceptance

Scope:

- full integration pass;
- clean-install documentation;
- Windows install/run scripts;
- config backup/example;
- production frontend served by backend;
- HOME acceptance report;
- заполнение commit ledger;
- фиксация всех `PENDING HARDWARE`.

Non-goals:

- реальный Art-Net;
- сценическая калибровка;
- Raspberry.

Acceptance:

- clean HOME setup воспроизводим;
- все HOME tests проходят;
- production build проходит;
- приложение работает полностью в Mock;
- все 10 пресетов просматриваются;
- `git status` clean;
- push выполнен, если origin доступен;
- финальный отчёт содержит строку `HOME PLAN COMPLETE`.

Verification:

- backend test suite;
- frontend tests/typecheck/build;
- integration/e2e smoke;
- Windows startup smoke;
- проверка всех статусов hardware boundary.

#### L013 — Результаты проверок

Статус: `DONE`. Дата: `2026-07-28`. Ветка: `main`.
Commit: `c2d06e9744db523d799dc123d54c1a3228126446` (`c2d06e9`).

Корректирующий HOME commit (не Lxxx): `34bb2ba2e35edd3f858c78cb954ce11379a814f7`
(`34bb2ba`) — `fix: complete HOME acceptance corrections`.

| Проверка | Результат |
|---|---|
| L012 hash записан (`b10f4e0`) | PASS |
| `scripts/bootstrap.ps1` / `run.ps1` / `run-dev.ps1` / `check.ps1` | PASS |
| `scripts/backup-config.ps1` + `config/examples/app.yaml.example` | PASS |
| Port-busy диагностика в `run.ps1` / `run-dev.ps1` | PASS |
| SPA assets 404 (не HTML-as-JS) | PASS |
| Deep-link `/`, `/presets`, `/setup`, `/settings` | PASS |
| Mock + disarmed + startup blackout / zero frame | PASS |
| Integration smoke (`test_l013_integration.py`) | PASS |
| Backend pytest / ruff check / format --check | PASS — 150 passed; ruff clean |
| Frontend vitest / typecheck / lint / build | PASS — 19 passed |
| Clean-flow install без чужих `.venv`/`node_modules`/`dist` | PASS — bootstrap+check+uvicorn :8013 Mock |
| `docs/HOME_ACCEPTANCE_REPORT.md` | PASS |
| Реальный Art-Net / UDP / discovery | PASS — не выполнялись |
| `hardware_verified` / `hardware_tuned` | PASS — остаются `false` |
| H001+ | PENDING HARDWARE |

Зафиксированные особенности:

- после L012 выполнен отдельный corrective commit `34bb2ba` с HOME-дефектами
  (SPA toast provide, blackout>raw, transitions, groups, preview_speed, UA UI);
- hash L012 в ledger — исторический stage-commit `b10f4e0`, не corrective;
- hash L013 — `c2d06e9`; финальный docs ledger commit фиксирует
  `HOME PLAN COMPLETE` и полный список PENDING HARDWARE.

## 4. Hardware plan

Реальные этапы H001+ выполняются только по
`docs/HARDWARE_DAY_CHECKLIST.md`.

После реального дня агент:

- обновляет фактические profiles/patch/network config;
- меняет status только для действительно проверенных пунктов;
- не объединяет аппаратные наблюдения с догадками;
- создаёт отдельные hardware commits;
- оставляет Raspberry/GPIO отдельным будущим блоком.

## 5. Финальный отчёт HOME PLAN

Дата фиксации: **2026-07-28**. Ветка: `main`.
Детали также в `docs/HOME_ACCEPTANCE_REPORT.md`.

### Итоговая архитектура

Vue 3 UI → FastAPI (REST + WebSocket + SPA) → один Engine → MockTransport.
Art-Net адаптер реализован на уровне пакета; HOME runtime не вооружает сеть.

### Таблица L001–L013 (полные hash)

| ID | Commit | Статус |
|---|---|---|
| L001 | `180735714a12046f675e1964892cacaa6f4dd64b` | DONE |
| L002 | `df98ab696656ace7374c7036074b44587183c6c6` | DONE |
| L003 | `fbaf08a3e6a659a14f34c11e62d353de6e6b9f41` | DONE |
| L004 | `1c01252b806662a518da55b5794fdf38bb600211` | DONE |
| L005 | `f1cb34a42ea1be6239bdba96cc50390d3a856259` | DONE |
| L006 | `ea3fae87eaa0a9fc3bfb426c3379d5cfbc16bb93` | DONE |
| L007 | `7493e012e93f1c256e890f80bb66fc45e4f65b95` | DONE |
| L008 | `48687b0a2b84f9ab7abccdf1ba799ec0d9b9595e` | DONE |
| L009 | `02f414cfd4dd7a3a768e5caf3f483eefa0ecdeba` | DONE |
| L010 | `9151dbf493971e1823b7d954de47d7f08203b942` | DONE |
| L011 | `15f805323f3a7a14979988f191661b375fe3ea48` | DONE |
| L012 | `b10f4e0d4963c423262554cbf78ca885952cb623` | DONE |
| Corrective | `34bb2ba2e35edd3f858c78cb954ce11379a814f7` | DONE (не Lxxx) |
| L013 | `c2d06e9744db523d799dc123d54c1a3228126446` | DONE |

### Команды Windows

```powershell
.\scripts\bootstrap.ps1
.\scripts\run.ps1
.\scripts\check.ps1
.\scripts\backup-config.ps1
```

UI: `http://127.0.0.1:8000/` · Health: `/api/health`.

### Проверки

- backend pytest: **150 passed**;
- ruff check / ruff format --check: **PASS**;
- frontend vitest: **19 passed**;
- typecheck / lint / production build: **PASS**;
- integration SPA smoke + clean-flow: **PASS**;
- Mock / disarmed / startup blackout / no real UDP: **PASS**.

### Симулятор

Просмотрены P01–P10 в Mock; кадр = backend frame; Beam Left/Right по `side`;
Identify/Raw/Blackout; transitions. Физический цвет/геометрия **не** подтверждены.

### PENDING HARDWARE

- профили каналов PAR/Bar/Beam/Face;
- ориентация Bars;
- Beam limits / home / invert;
- Art-Net IP/universe/поведение контроллера;
- `hardware_verified: false`;
- `hardware_tuned: false`;
- H001+ по `docs/HARDWARE_DAY_CHECKLIST.md`.

### Git / push / ограничения

`origin/main` синхронизирован; `.venv` / `node_modules` / `dist` / secrets не
коммитятся. GPIO/Raspberry — stub; Cloudflare/auth вне scope; реальный Art-Net
не включался.

### Заключение

HOME software acceptance завершена. Следующий разрешённый этап — `H001` только
в заведении с оборудованием.

**HOME PLAN COMPLETE**

