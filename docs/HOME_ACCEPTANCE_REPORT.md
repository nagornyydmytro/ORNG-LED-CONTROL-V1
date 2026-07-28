# ORNG LED CONTROL — HOME Acceptance Report

Дата: **2026-07-28**  
Ветка: `main`  
Режим: **HOME / Mock only**

Этот отчёт фиксирует завершение домашнего программного scope (`L001`–`L013`).
Он **не** подтверждает физическое оборудование.

## 1. Итоговая архитектура

```text
Vue 3 UI → FastAPI (REST + WebSocket) → Engine (30 FPS) → MockTransport
                                         ↓
                                   frontend/dist SPA
```

- Один процесс runtime, один engine loop.
- Пресеты YAML → semantic intents → profiles → DMX patch → 512-frame.
- Art-Net адаптер существует на уровне пакета, но HOME runtime не вооружает
  сеть и не создаёт реальный UDP socket.

## 2. Ledger L001–L013

Полная таблица hash ведётся в `docs/IMPLEMENTATION_PLAN.md`. Финальный ledger
commit после L013 дополняет hash L013 и строку `HOME PLAN COMPLETE`.

Зафиксировано на момент L013:

| ID | Commit (short) | Статус |
|---|---|---|
| L001 | `1807357` | DONE |
| L002 | `df98ab6` | DONE |
| L003 | `fbaf08a` | DONE |
| L004 | `1c01252` | DONE |
| L005 | `f1cb34a` | DONE |
| L006 | `ea3fae8` | DONE |
| L007 | `7493e01` | DONE |
| L008 | `48687b0` | DONE |
| L009 | `02f414c` | DONE |
| L010 | `9151dbf` | DONE |
| L011 | `15f8053` | DONE |
| L012 | `b10f4e0` | DONE |
| Corrective HOME | `34bb2ba` | DONE (не этап Lxxx) |
| L013 | `c2d06e9` | DONE |

## 3. Команды установки и запуска (Windows)

```powershell
cd <repo>
.\scripts\bootstrap.ps1
.\scripts\run.ps1
# UI: http://127.0.0.1:8000/
# Health: http://127.0.0.1:8000/api/health
.\scripts\check.ps1
.\scripts\backup-config.ps1
```

Dev:

```powershell
.\scripts\run-dev.ps1
# UI: http://127.0.0.1:5173/
```

## 4. Проверки HOME

См. блок `L013 — Результаты проверок` в `docs/IMPLEMENTATION_PLAN.md` и финальный
ledger commit. Минимальный набор:

- backend pytest / ruff check / ruff format --check;
- frontend vitest / typecheck / lint / production build;
- SPA `/`, `/presets`, `/setup`, `/settings`;
- Mock + disarmed + startup blackout / zero frame;
- P01–P10 в Mock preview;
- отсутствие реального UDP/Art-Net.

## 5. Что проверено в симуляторе

- Декод того же 512-кадра, что публикует backend.
- Симулятор на Пульт / Пресети / Налаштування.
- Beam Left слева, Beam Right справа (по `side`).
- Identify / Raw tester / Blackout видимы при снятом Blackout.
- Переходы `cut` / `fade` / `soft` влияют на rendering.
- Semantic groups: dimensional AND / within-dimension OR.

Симулятор **не** подтверждает реальный цвет, яркость, геометрию Beam или
соответствие физических DMX-профилей.

## 6. PENDING HARDWARE

- реальные режимы/карты каналов PAR, Bar, Beam, Face PAR;
- сегменты Bars и физическая ориентация;
- pan/tilt home, invert, пределы и скорость Beam;
- Art-Net target IP / universe / поведение при потере пакетов;
- `hardware_verified` остаётся `false`;
- `hardware_tuned` остаётся `false` для всех пресетов;
- художественная доводка на реальной сцене;
- этапы `H001+` по `docs/HARDWARE_DAY_CHECKLIST.md`.

## 7. Ограничения

- GPIO / Raspberry — stub / будущий блок.
- Cloudflare / публичный deploy / auth — вне V1 HOME.
- Реальный Art-Net не включался и не проверялся в сети.

## 8. Заключение HOME software

Домашний программный контур принят. Реальный Art-Net не включался.
`hardware_verified` и `hardware_tuned` остаются `false`. Следующий разрешённый
этап — **H001** только в заведении с оборудованием.

**HOME PLAN COMPLETE**
