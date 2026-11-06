# Phase 11 — отдельный отчёт

Дата проверки: 2026-10-27. Это сверка исходников, production и живого Raspberry. Наличие этого файла не означает, что этап закрыт.

## Что было общей архитектурой

Один `AppShell` и одни страницы: Пульт `/`, Пресети `/presets`, Налаштування `/setup`, Канали `/channels`. Облако и локальный runtime подставляют разные провайдеры (`CloudAdminApp` + `useCloudAppState` + `createCloudScenePort` против локального `useAppState`). `OperatorConsoleView` в маршрутах не смонтирован. Техническая панель только на `/advanced`. В меню оператора нет пункта «Сервіс».

## Что проверено по пунктам

| Пункт | Состояние на 2026-10-27 |
| --- | --- |
| Общий AppShell и четыре страницы | Есть в исходниках и на https://light.orng.wtf |
| Cloud и local используют одни компоненты | Да. Отдельной облачной копии пульта нет |
| Burger на телефоне | В Phase 11 открытие не было доказано скриншотом. В Phase 12 меню открывается и закрывается; см. phase-12-report |
| Схема сцены, не только сетка кнопок | Схема layout есть. Облако не получает DMX-кадр Raspberry, поэтому схема — не фактический вывод |
| Реальные пресеты и настройки сцены | Каталог A01–A03, C01, P01–P06 виден на production. Активный preset на пульте в этой сессии: A02 |
| Техническая панель только `/advanced` | Да |
| Live FX одним пальцем | Компонент `FxHoldButton` и тест есть. На установленном Pi `set_live_fx_speed` отвечает `not_allowlisted` |
| Web keyboard на выбранный Raspberry | Код есть. На установленном Pi `keyboard` отвечает `not_allowlisted`. Физическая клавиатура в этой сессии не нажималась |
| ACK и reconnect без выдуманных armed/blackout | Для уже известных команд (`select_preset`, `strobe_press/release`, `face_toggle`, `release_source`) устройство подтверждало `applied`. Новые команды до прошивки не доходят |
| Pi сохраняет venue_ready | Да, на момент чтения: armed=1, blackout=1, transport=artnet, udp_active=1 |

## Production и Pi на момент сверки

- Worker до правок Phase 12: `dd2ec6ad-6923-4fcf-903c-7eb57b70d033`.
- Установленный Pi: `dirty-ab7ea8c7bfd3-02d33ebfb86e`, сцена `orng-bar`, release version 1, content hash `5c43eea075e57605d94d24c99d78881124263b6fdc638a660df51dbcf5b4d6dd`.
- Установка `7e4457ad-c47e-4aaf-beba-87f83a1c92d0`.
- Аккаунт деплоя Events `c8415eaa4895941ba681d8ba1fe2e88e`. Notifications gateway не менялся.

## Что Phase 11 не закрыла

Прошивка на Raspberry старше контракта команд. Абсолютная скорость FX, program speed, абсолютный face и web keyboard на устройстве не применяются. Desktop по-прежнему пишет локальный YAML и не публикует каноническую ревизию. Ярлык Windows запускает git checkout, а не отдельную установку.

Blackout в ходе сверки не снимался. Commit и push не делались. Stable не публиковался.
