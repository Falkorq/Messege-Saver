# Ревью `business-message-watcher`

**Охват:** `app.py`, `access_control.py`, `business_notifications.py`, `business_tools/*`, `profile_monitor/*`, `tests/*`, конфигурация (`.env.example`, `requirements.txt`, `.gitignore`, README/USER_GUIDE). Сверка — по чек-листу скила `telegram-bot-builder` (Authentication, Error Handling, Keyboards, Security Checklist, Deployment).

**Проверки:** тесты `unittest` — **45 passed, exit 0**. Примечание: в `.venv` не было `Telethon`, заявленного в `requirements.txt` (5 из 6 файлов тестов падали с `ModuleNotFoundError`); после установки пина из `requirements.txt` набор зелёный.

---

## Что сделано хорошо (по чек-листу скила)

- **Токен и секреты** — `BOT_TOKEN` только в `.env`, есть заглушка-проверка `app.py:37-39`; `.gitignore` покрывает `.env`, `*.sqlite3`, `.venv/`, `profile_data/`; `TELEGRAM_SESSION`/`API_HASH` не логируются и не эхо-ятся в чат.
- **Авторизация на всех входах** — `admin_ids` + проверка личного чата в `/grant|/revoke|/access` (`app.py:336`), общий `authorized_message`/`authorized_callback` в обоих модулях, причём callback дополнительно требует `chat.id == from_user.id` и отвечает alert-ом отказавшим. Owner-скоуп в SQL — по `owner_id`, не из payload.
- **SQL-инъекций нет** — всё через параметры; единственный f-string SQL (`business_tools/repository.py:195`) построен из литералов, `sort` — по whitelist.
- **HTML-эскейп** — почти везде `html.escape`; `truncate_html` закрывает незакрытые теги. Есть 2 пропуска — см. п. 5.
- **`allowed_updates` ограничен** нужными типами (`app.py:842`) — пункт чек-листа выполнен.
- **Очередь уведомлений** — отдельный `outbox` с exponential backoff, уважение `retry_after` при 429, permanent-fail на 400/401/403/404, `supervise_outbox` с перезапуском (`app.py:804-833`). Ретраи не дублируют уже отправленные медиа/части (`media_sent`, `text_parts_sent`).
- **Выбор polling** для личного бота на Pterodactyl — корректен (скил: polling для dev/небольших ботов, webhook — для high traffic). Webhook не требуется.
- **Фолбэк на plain-text**, если Telegram отверг кастомные эмодзи (`app.py:665-676`), валидация `username` перед вставкой в `t.me/...` ссылку (`app.py:589-591`, `business_notifications.py:12`).

---

## Высокие

1. **Кнопки зависают в вечном «loading»** — `answer_callback_query` вызывается *после* рендера во всех callback-хендлерах: `business_tools/ui.py:49-80` (4 шт.) и все `pm:*` в `profile_monitor/ui.py:93-188`. Любое исключение (400 «message to edit not found», битый `int()` в callback_data, ошибка БД) обрывает хендлер до ответа; telebot лишь логирует исключение. Скил прямо требует: *"Always call `answerCallbackQuery` to dismiss the loading indicator"*.
   **Fix:** `try/finally: answer_callback_query(...)`, permanent-ошибки — `show_alert=True` с подсказкой «откройте /menu заново».

2. **`/mute` и другие команды молча исчезают при незавершённом «Добавлении профиля»** — `profile_monitor/ui.py:67-72`: catch-all хендлер на `pending_add` срабатывает на *любой* текст, сначала `pop()` снимает состояние, потом `return` на `/...`. Состояние потеряно, и telebot останавливается на первом совпадении — `business_tools/ui.py:43` до `/mute` не доходит (регистрация модулей: `app.py:478` раньше `482`).
   **Fix:** проверять `startswith("/")` до `pop()` и не съедать сообщение (пропускать к следующим хендлерам), либо поднять группу хендлеров команд.

3. **Мониторинг профилей может умереть навсегда без лога** — `profile_monitor/scheduler.py:19-23`: `run()` без try/except; исключение из `repo.due()` (например `database is locked`) или из `set_error` внутри `except` убивает задачу, созданную в `module.py:63`. В `app.py` для outbox уже есть `supervise_outbox` — для scheduler-а пара отсутствует.
   **Fix:** `while: try/except Exception: log + sleep(30)` или `add_done_callback` с рестартом.

4. **Владелец watchlist без строки настроек никогда не проверяется** — `profile_monitor/repository.py:124-129` делает INNER JOIN `profile_settings`, а `migrate()` (`repository.py:10-59`) не бэкфиллит строки для уже существующих `profile_watchers`. Новые `add()` строку создают через `ensure_settings` (`repository.py:81`), так что проявляется только на БД, созданной старой версией. Фича выглядит «включённой», но цикл не запускается ни разу.
   **Fix:** `INSERT OR IGNORE INTO profile_settings(owner_id) SELECT DISTINCT owner_id FROM profile_watchers` в `migrate()`, либо `LEFT JOIN` + `COALESCE`.

---

## Средние

5. **Незэкранированный `username` в HTML** — `profile_monitor/ui.py:212` и `ui.py:78`: `f"@{snapshot.username ...}"` уходит в `send_html` без `escape`, при этом `telegram_client.py:77-78` кладёт в `username` сырой ввод `target.lstrip("@")`. `<`/`&` → 400 «can't parse entities» → `/watch` не отвечает, UI перестаёт обновляться.
6. **Callback data не валидируется** — `int(call.data.split(":")[2])` и распаковки без проверки длины/формата: `business_tools/ui.py:52,58,67,75-77`, `profile_monitor/ui.py:115,121,128,142,162,173`. Форж-данные дают необработанное исключение (и снова п. 1); `render_stat` (`business_tools/ui.py:191`) не ограничивает `page` → можно собрать `callback_data` > 64 байт → 400. Плюс `pm:set:` (`profile_monitor/ui.py:181-186`) принимает `interval_seconds=0` → `due()` вернёт всё на каждом 30-секундном цикле → флуд-самоблокировка Telegram.
7. **`/mutelist` не ограничен по длине** — `business_tools/ui.py:101-102,204-210` склеивает все активные муты (с `last_error` до 300 символов) в одну строку; ~100 мутов > 4096 → 400, и ответа не будет вовсе (см. п. 1). Отправка вне `try` и без обработки 429 (`ui.py:136`).
8. **`/mute 99999999999999h` роняет команду молча** — `business_tools/service.py:35,93-95`: переполнение INTEGER не ловится `except ValueError` (`service.py:107`), исключение уходит в `module.py:48-50`, пользователь не получает ни ответа, ни удаления команды. **Fix:** кап в `parse_duration` (напр. 10 лет) + `db.rollback()` в исключении.
9. **Открытая транзакция при ошибке в `record_message`** — `business_tools/repository.py:86-139`: если упал любой из upsert-ов, `rollback()` нигде нет; следующий случайный `commit()` сохранит строку дедупликации без статистики → сообщение навсегда «увидено», но не посчитано. Backfill защищён SAVEPOINT (`:158-168`), live-путь — нет.
10. **`FloodWait` режется до 300 с и цикл продолжает остальные строки** — `profile_monitor/scheduler.py:34-37`: каждая оставшаяся строка снова жжёт 300 с и пишет `last_error`, глобального cooldown нет.
11. **`/watch <numeric_id>` неизвестного юзера грузит все диалоги** — `profile_monitor/telegram_client.py:104` (`get_dialogs(limit=None)`) — самый флудоопасный вызов Telethon, доступен без валидации формата аргумента и без троттлинга (`ui.py:74,209`).
12. **Опциональный модуль валит весь бот** — `profile_monitor/module.py:59-64` / `telegram_client.py:32-35`: невалидный `TELEGRAM_SESSION` кидает `RuntimeError` до `infinity_polling` (`app.py:836-844`), хотя README позиционирует модуль как изолированный и опциональный.
13. **Дубли уведомлений при параллельной проверке** — `profile_monitor/service.py:61-81`: чтение старого snapshot → `await` сеть → `save_check`; двойной тап «Проверить сейчас» (`ui.py:146-157`) = два `batch_id`, два уведомления.
14. **Синхронный SQLite + fsync-`commit()` прямо в event loop** — на каждое сообщение: SELECT + INSERT + UPSERT + upsert на каждое уникальное слово (`business_tools/repository.py:129-136`) + `commit()`; `backfill()` (`:141-175`) — полный скан при старте; `active_mutes` пишет и коммитит на *чтении* (`:271-272`). Блокирует Telegram-I/O для всего процесса. Отдельно: ежечасная очистка `DELETE FROM messages WHERE created_at < ?` (`app.py:768`) — полный скан без индекса по `created_at`; добавить `CREATE INDEX`.
15. **`business_stats_seen` растёт без ограничений** — `business_tools/repository.py:69-75,88-93` никогда не чистится (в отличие от `messages` через `RETENTION_DAYS` и `business_self_deletions`).

---

## Низкие

16. **`setMyCommands` нигде не вызывается** — меню команд держится на ручной настройке в BotFather (README:76-98). Скил рекомендует регистрацию через API, можно со scope/языком — одна строка в `main()`.
17. **Мёртвый код:** `send_text` (`app.py:611`) не используется ни в проекте, ни в модулях.
18. **Таймзона разъехалась:** `profile_monitor/ui.py:35` рендерит `datetime.fromtimestamp(value)` в локальной зоне сервера, остальной бот — MSK (`app.py:585-586`). На UTC-хосте «Последняя проверка» будет на 3 ч меньше.
19. **`truncate_html(text, 900)` считает code points, а Telegram — UTF-16** (`business_tools/ui.py:214`, `profile_monitor/style.py:174-184`): тексты, близкие к лимиту с эмодзи, могут дать 1024+ UTF-16 единиц → 400 → снова «зависшая кнопка».
20. **Логи на каждое падение удаления** — `business_tools/module.py:46-50` пишет полный traceback на каждом сообщении в чат, где бот уже потерял права (дедуп уведомления есть, дедуп лога — нет).
21. **`muted` сообщения попадают в статистику** — `record_message` вызывается до проверки мута (`business_tools/service.py:61`), т.е. два хранилища расходятся по смыслу.
22. **Артефакты в корне:** `bot_update.zip`, `bot_update_business_fix.zip`, `friend_access_update.zip`, `business_tools.zip`, `profile_monitor.zip` — старые слепки кода рядом с живым; легко перепутать при деплое по инструкции README:45. Проект не является git-репозиторием — истории и отката нет. Рекомендуется `git init` и удаление архивов.
23. **`messages.sqlite3` лежит в корне проекта** (тексты личных сообщений) — README предупреждает, но для локальной разработки лучше выносить за пределы дерева.
24. **Потенциальный дубль после краша:** падение между ответом Telegram и `DELETE FROM outbox` (`app.py:821`) переотправит уведомление при рестарте — README:106 это признаёт; можно помечать `sent_at` до удаления или принимать как есть.
25. **Нет per-user rate limit на команды** (пункт чек-листа) — для личного бота некритично, но `/chatstats`-спам можно ограничить простым cooldown.
26. **Нет dev-зависимостей/CI**; тесты запускаются только через `unittest discover` из корня, `Telethon` приходится ставить руками. Пробелы покрытия: HTML-инъекция через враждебный `chat_name`/`bio`, «callback всегда отвечается», формат битого `callback_data`, лимит `/mutelist`, `FloodWait` в scheduler-е, отклонение `interval=0`.

---

## Итог по чек-листу скила

| Пункт | Статус |
|---|---|
| Токен в env, не в коде | ✅ |
| Валидация ID для admin-команд | ✅ |
| `allowed_updates` сужен | ✅ |
| Обработка 429 / permanent 4xx | ✅ в outbox, ⚠️ в командах/UI |
| `answerCallbackQuery` всегда | ❌ п. 1 |
| Escape пользовательского ввода в HTML | ⚠️ п. 5 |
| Валидация `callback_data` (64 байта) | ❌ п. 6 |
| Per-user rate limit | ⚠️ п. 25 |
| Webhook secret / HTTPS | n/a (polling) |
| 409 Conflict (второй инстанс) | ⚠️ не обрабатывается — на Pterodactyl следите, чтобы не запускалось два воркера |

## Приоритет исправлений

1. `try/finally` + `answerCallbackQuery` во всех callback-хендлерах (п. 1) — чинит сразу всю «вечную крутилку».
2. Разводка хендлеров `pending_add` и команд (п. 2).
3. Супервизия scheduler-а и бэкфилл `profile_settings` (п. 3, 4) — иначе фича может молча не работать.
4. `escape()` на `ui.py:212/78`, whitelist + диапазоны для `callback_data`, капы длины и длительности (п. 5-8).
5. Транзакционная дисциплина (`rollback`/SAVEPOINT в `record_message`), FloodWait-cooldown, индекс по `messages(created_at)` (п. 9, 10, 14).
