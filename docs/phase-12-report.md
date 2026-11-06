# Phase 12 — отчёт

Дата: 2026-10-27. Общий продукт не объявляется готовым. Ниже только то, что проверено в этой сессии.

## Что исправлено

- Телефонное меню: фон, закрытие по фону и Escape, меню поверх нижней панели. Тест `frontend/src/components/AppShell.spec.ts` проходит.
- Настройки и пульт в облачной сборке больше не называют runtime Mock и не называют схему «фактическим кадром Raspberry». Сохранение описано как публикация ревизии. Страница не обещает снятие blackout или смену arm.
- Локальный `frontend/dist` пересобран (`index-C_F1nOV-.js`), чтобы следующий запуск ярлыка из этого checkout отдал тот же интерфейс. Сам ярлык не запускался.

Не менялись адреса Art-Net, DMX patch, калибровка ORNG BAR, observed map макропада, credentials и Notifications gateway. Blackout не снимался. Signed release на Pi не редактировался. Commit, push и stable не делались.

## Windows

Ярлык `Desktop\ORNG LED CONTROL.lnk` указывает на `ORNG-LED-CONTROL.cmd` в git checkout. Команда запускает `scripts\venue-start.ps1`. Это не managed install: нужны checkout, `.venv` и уже собранный `frontend/dist`. `scripts\managed-install.ps1` не подменяет checkout и не создаёт `current.json`, пока нет подписанного релиза. Ярлык не переназначался. Программа не запускалась: `venue-start.ps1` снимает arm, заново активирует Art-Net ноутбука и снова включает arm при включённом blackout. Это прерывание локального вывода, его не делали без окна.

Установленный build_id пользовательского приложения: нет. «Installer собран» здесь не утверждается.

## macOS

На этой машине нет Mac и не выполнялся native build/run. `scripts/macos-install.sh` клонирует git и требует Homebrew, git и npm. `scripts/install-macos-launcher.sh` кладёт `.app` на рабочий стол, который стартует тот же checkout. Это не приёмка macOS. Статус: NOT TESTED.

## Raspberry

Фактическое состояние после деплоев, чтение D1 `installations` для `7e4457ad-c47e-4aaf-beba-87f83a1c92d0`:

- build_id `dirty-ab7ea8c7bfd3-02d33ebfb86e`
- scene `orng-bar`, release version 1
- content hash `5c43eea075e57605d94d24c99d78881124263b6fdc638a660df51dbcf5b4d6dd`
- armed 1, blackout 1, transport artnet, udp_active 1
- last_seen дошёл до `2026-10-27T11:48:42.391Z` после короткой паузы heartbeat на время деплоя Worker

Новый пакет на Pi не ставился. Docker daemon не запущен, arm64 runtime собрать нельзя. Команды `keyboard` и `set_live_fx_speed` на этой прошивке по-прежнему вне allowlist. Reboot, питание, сеть и USB не трогались.

В коде supervisor не вооружает выход, пока есть `apply-request.json` или update state applying/restarting/health_check/rolling_back/waiting_for_safe_state. Это покрыто `backend/tests/test_venue_ready.py`. На живом Pi цикл update/rollback в этой сессии не выполнялся.

## Сцены

Канонический цикл «правка → новая ревизия → назначение → apply → hash на устройстве» в этой сессии не прогонялся на тестовой сцене. Рабочие адреса и калибровка ORNG BAR не публиковались. Сохранение с desktop остаётся локальной копией, не общей ревизией. Офлайн-черновик desktop не помечен в UI как «ещё не общий». Это незакрытый пункт.

## App update и scene update

Механизмы разные: сцена подтверждается hash релиза, приложение — новым процессом и build_id. Автопроверка и stage/apply для установленного Windows-приложения не выполнялись, потому что установленного managed app нет. Candidate на Pi не публиковался. Stable не публиковался.

## Production

- Аккаунт Worker: Events `c8415eaa4895941ba681d8ba1fe2e88e`.
- Worker version: `772fa4f5-cd43-46f9-8650-b412f13662d2`.
- Живой домен отдаёт `https://light.orng.wtf/assets/index-BRp2_0dk.js` и `index-MoexoTBu.css`.
- Текст настроек на живой странице содержит «публікує нову ревізію» и не содержит «Runtime залишається на Mock».
- После загрузки пульта заголовок снова Online, badge схемы: «Схема сцени · не кадр Raspberry».

## Матрица

| Путь | Статус | Доказательство |
| --- | --- | --- |
| A. USB pad → Pi → Art-Net | NOT TESTED | Физическая кнопка в этой сессии не нажималась |
| B. Телефон → light.orng.wtf → Pi | NOT TESTED | Нет физического телефона. Эмуляция 390×844 показала меню и пульт |
| C. Ноутбук → light.orng.wtf → Pi | VERIFIED для UI и сессии | 1440×900, Online, BLACKOUT, A02, четыре раздела. Новые команды на Pi не применялись |
| D. USB pad ноутбука → web → Pi | NOT TESTED | |
| E. Клавиатура ноутбука → web → Pi | FAILED на устройстве | Прошивка отвечает `not_allowlisted` на `keyboard`. Физический keydown в этой сессии не снимался |
| F. OTG-клавиатура телефона | NOT TESTED | |
| G. Windows app + клавиатура | NOT TESTED | Ярлык не запускался |
| H. macOS app + клавиатура | NOT TESTED | Нет Mac |

Локальный телефонный вход на web Pi без интернета не проверялся и не назывался офлайн-доступом. `light.orng.wtf` требует интернет и сессию.

Закрытие браузера и чужие holds физического pad в этой сессии не измерялись.

## Экраны

Свежие снимки production после Worker `772fa4f5`:

- `phase12-cloud-390-burger.png` — меню открыто: Пульт, Пресети, Налаштування, Канали, Вийти
- `phase12-cloud-390-pult.png` — BLACKOUT, без горизонтального overflow (`scrollWidth` 390)
- `phase12-cloud-768-pult.png` — 768×1024, Online, нижняя навигация, overflow нет
- `phase12-cloud-1440-pult.png`, `phase12-cloud-1440-fx.png` — STROBE / SWEEP H / SWEEP V
- `phase12-cloud-1440-presets.png` — каталог шире девяти слотов, preview подписан как mock и «не надсилається у transport»
- `phase12-cloud-1440-setup.png` — Art-Net в конфиге сцены, честный текст публикации
- `phase12-cloud-1440-channels.png` — страница открывается на выборе типа, raw DMX сам не стартовал

Локальный сервер `127.0.0.1:8000` в этой сессии не поднимался, поэтому локальные скриншоты не являются доказательством Phase 12.

## Готовность

| Область | Оценка |
| --- | --- |
| 1. Raspberry + физический pad | NOT TESTED физически. Последняя телеметрия: armed, blackout, artnet, udp |
| 2. Телефонный web | NOT TESTED на устройстве. Вёрстка 390 проверена в браузере |
| 3. Desktop browser | VERIFIED для экранов cloud. Команды keyboard/FX speed на Pi — FAILED до новой прошивки |
| 4. Установленное Windows-приложение | NOT TESTED |
| 5. Установленное macOS-приложение | NOT TESTED |
| 6. Каноническая синхронизация сцен | FAILED как полный цикл. Рабочая ревизия Pi не менялась |
| 7. App updater / recovery | NOT TESTED на живых установках. Кодовый барьер supervisor/updater есть |
| 8. Визуальная приёмка cloud | VERIFIED по снимкам выше. 30-минутный soak не запускался |

Автотесты этой сессии: `AppShell.spec.ts` 1/1, `controls.spec.ts` 13/13, `vue-tsc` в обеих сборках. Полный pytest/vitest/cloud suite заново не гонялся.

## Следующее физическое действие

Нужно отдельное окно, когда свет можно перезапустить под blackout. Тогда: запустить Docker, собрать подписанный arm64 candidate, stage/apply через существующий agent, дождаться нового `build_id` и убедиться, что armed=true и blackout=true. После этого одна кнопка макропада и одна команда с телефона. Blackout снимать только отдельным решением. До этого ярлык Windows не запускать, если нельзя трогать Art-Net ноутбука.

## Архив

`C:\Users\Користувач\Documents\ORNG-LED-CONTROL-phase12-20261027.zip`

- размер и SHA-256 считаются по готовому ZIP и приводятся в сопроводительном сообщении, чтобы хеш не устаревал внутри самого архива
- 462 файла: исходники, lockfiles, миграции, тесты, скрипты сборки, оба отчёта, свежие `review-screenshots/phase12-cloud-*.png`
- нет `.git`, `node_modules`, `.venv`, `.wrangler`, `.local`, `.tmp-shots`, runtime binaries и вложенных архивов
- `scripts/raspberry/templates/device.env.example` — шаблон путей без токенов и паролей
