import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone


STOP_WORDS = {
    "the", "and", "for", "that", "this", "with", "are", "was", "have", "not", "you", "from",
    "they", "will", "been", "can", "its", "это", "как", "что", "но", "или", "для", "все", "так",
    "уже", "его", "она", "они", "мне", "тебе", "нас", "вас", "был", "была", "были", "есть",
    "нет", "да", "ну", "вот", "ещё", "еще", "тут", "там", "при", "без", "под", "над", "про",
    "бы", "же", "ли",
}
MOSCOW = timezone(timedelta(hours=3))
log = logging.getLogger(__name__)


class BusinessToolsRepository:
    def __init__(self, db, routes):
        self.db = db
        self.routes = routes

    def migrate(self):
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS business_mutes (
            owner_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            display_name TEXT,
            username TEXT,
            until_at INTEGER NOT NULL DEFAULT 0,
            added_at INTEGER NOT NULL,
            last_error TEXT,
            PRIMARY KEY(account_id, chat_id)
        );
        CREATE TABLE IF NOT EXISTS business_chat_stats (
            owner_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            display_name TEXT,
            username TEXT,
            total_messages INTEGER NOT NULL DEFAULT 0,
            owner_messages INTEGER NOT NULL DEFAULT 0,
            peer_messages INTEGER NOT NULL DEFAULT 0,
            owner_chars INTEGER NOT NULL DEFAULT 0,
            peer_chars INTEGER NOT NULL DEFAULT 0,
            first_at INTEGER,
            last_at INTEGER,
            media_json TEXT NOT NULL DEFAULT '{}',
            hours_json TEXT NOT NULL DEFAULT '[0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0]',
            PRIMARY KEY(account_id, chat_id)
        );
        CREATE INDEX IF NOT EXISTS business_stats_owner_last
            ON business_chat_stats(owner_id, last_at DESC);
        CREATE TABLE IF NOT EXISTS business_stats_days (
            account_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            day TEXT NOT NULL,
            PRIMARY KEY(account_id, chat_id, day)
        );
        CREATE TABLE IF NOT EXISTS business_stats_words (
            account_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            word TEXT NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY(account_id, chat_id, word)
        );
        CREATE TABLE IF NOT EXISTS business_stats_seen (
            connection_id TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            observed_at INTEGER NOT NULL,
            PRIMARY KEY(connection_id, chat_id, message_id)
        );
        CREATE TABLE IF NOT EXISTS business_self_deletions (
            connection_id TEXT NOT NULL,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            PRIMARY KEY(connection_id, chat_id, message_id)
        );
        """)
        self.db.commit()

    def record_message(self, connection_id, account_id, owner_id, message_id, record, commit=True):
        now = int(time.time())
        inserted = self.db.execute(
            "INSERT OR IGNORE INTO business_stats_seen VALUES(?,?,?,?)",
            (connection_id, record["chat_id"], message_id, now),
        ).rowcount
        if not inserted:
            return False
        chat_id = record["chat_id"]
        own = record.get("sender_id") == account_id
        display_name = record.get("chat_name") or record.get("name") or str(chat_id)
        username = record.get("chat_username") or (record.get("username") if not own else None)
        text = record.get("text") or record.get("caption") or ""
        stamp = int(record.get("sent_at") or now)
        dt = datetime.fromtimestamp(stamp, MOSCOW)
        kind = record.get("type") or "unknown"
        try:
            row = self.db.execute(
                "SELECT media_json,hours_json FROM business_chat_stats WHERE account_id=? AND chat_id=?",
                (account_id, chat_id),
            ).fetchone()
            media = json.loads(row["media_json"]) if row else {}
            hours = json.loads(row["hours_json"]) if row else [0] * 24
            if kind != "text":
                media[kind] = media.get(kind, 0) + 1
            hours[dt.hour] += 1
            self.db.execute(
                "INSERT INTO business_chat_stats(owner_id,account_id,chat_id,display_name,username,total_messages,"
                "owner_messages,peer_messages,owner_chars,peer_chars,first_at,last_at,media_json,hours_json) "
                "VALUES(?,?,?,?,?,1,?,?,?,?,?,?,?,?) ON CONFLICT(account_id,chat_id) DO UPDATE SET "
                "owner_id=excluded.owner_id,display_name=excluded.display_name,"
                "username=COALESCE(excluded.username,business_chat_stats.username),"
                "total_messages=total_messages+1,owner_messages=owner_messages+excluded.owner_messages,"
                "peer_messages=peer_messages+excluded.peer_messages,owner_chars=owner_chars+excluded.owner_chars,"
                "peer_chars=peer_chars+excluded.peer_chars,first_at=MIN(first_at,excluded.first_at),"
                "last_at=MAX(last_at,excluded.last_at),media_json=excluded.media_json,hours_json=excluded.hours_json",
                (owner_id, account_id, chat_id, display_name, username, int(own), int(not own),
                 len(text) if own else 0, len(text) if not own else 0, stamp, stamp,
                 json.dumps(media), json.dumps(hours)),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO business_stats_days VALUES(?,?,?)",
                (account_id, chat_id, dt.strftime("%Y-%m-%d")),
            )
            for word in re.findall(r"[а-яёa-z]{3,}", text.lower()):
                if word in STOP_WORDS:
                    continue
                self.db.execute(
                    "INSERT INTO business_stats_words VALUES(?,?,?,1) "
                    "ON CONFLICT(account_id,chat_id,word) DO UPDATE SET count=count+1",
                    (account_id, chat_id, word),
                )
        except Exception:
            # Без rollback строка seen осталась бы в транзакции и сохранилась бы позже
            # без статистики — сообщение считалось бы обработанным, но не посчитанным.
            self.db.rollback()
            raise
        if commit:
            self.db.commit()
        return True

    def backfill(self):
        rows = self.db.execute(
            "SELECT m.connection_id,m.chat_id,m.message_id,m.data,c.account_id FROM messages m "
            "JOIN connections c ON c.connection_id=m.connection_id"
        ).fetchall()
        added = 0
        skipped = 0
        for row in rows:
            owner_id = self.routes.get(row["account_id"])
            if owner_id is None:
                continue
            try:
                record = json.loads(row["data"])
                if not isinstance(record, dict):
                    raise ValueError("record is not an object")
                # Older bot versions stored the chat ID only in messages.chat_id.
                record["chat_id"] = row["chat_id"]
                self.db.execute("SAVEPOINT business_stats_backfill")
                try:
                    added += int(self.record_message(
                        row["connection_id"], row["account_id"], owner_id,
                        row["message_id"], record, False,
                    ))
                except Exception:
                    self.db.execute("ROLLBACK TO SAVEPOINT business_stats_backfill")
                    raise
                finally:
                    self.db.execute("RELEASE SAVEPOINT business_stats_backfill")
            except Exception:
                skipped += 1
                continue
        self.db.commit()
        if skipped:
            log.warning("Статистика: пропущено %s повреждённых старых записей", skipped)
        return added

    def chats(self, owner_id, limit=50):
        return self.db.execute(
            "SELECT * FROM business_chat_stats WHERE owner_id=? ORDER BY last_at DESC LIMIT ?",
            (owner_id, limit),
        ).fetchall()

    def stats_summary(self, owner_id):
        return self.db.execute(
            "SELECT COUNT(*) AS chats,COALESCE(SUM(total_messages),0) AS messages,"
            "COALESCE(SUM(owner_messages),0) AS own,COALESCE(SUM(peer_messages),0) AS peer "
            "FROM business_chat_stats WHERE owner_id=?",
            (owner_id,),
        ).fetchone()

    def stats_page(self, owner_id, page=0, size=6, sort="recent"):
        order = ("total_messages DESC,last_at DESC,account_id,chat_id" if sort == "messages"
                 else "last_at DESC,total_messages DESC,account_id,chat_id")
        return self.db.execute(
            f"SELECT * FROM business_chat_stats WHERE owner_id=? ORDER BY {order} LIMIT ? OFFSET ?",
            (owner_id, size, max(page, 0) * size),
        ).fetchall()

    def matching_chats(self, owner_id, target, account_id=None):
        text = str(target).strip().lstrip("@").lower()
        if text.lstrip("-").isdigit():
            match = "chat_id=?"
            value = int(text)
        else:
            match = "lower(username)=?"
            value = text
        if account_id is None:
            return self.db.execute(
                "SELECT * FROM business_chat_stats WHERE owner_id=? AND " + match +
                " ORDER BY last_at DESC", (owner_id, value),
            ).fetchall()
        return self.db.execute(
            "SELECT * FROM business_chat_stats WHERE owner_id=? AND account_id=? AND " + match +
            " ORDER BY last_at DESC", (owner_id, account_id, value),
        ).fetchall()

    def resolve_chat(self, owner_id, target):
        text = str(target).strip().lstrip("@").lower()
        rows = self.chats(owner_id, 500)
        if text.lstrip("-").isdigit():
            chat_id = int(text)
            return next((row for row in rows if row["chat_id"] == chat_id), None)
        return next((row for row in rows if (row["username"] or "").lower() == text), None)

    def resolve_mute(self, owner_id, target):
        text = str(target).strip().lstrip("@").lower()
        rows = self.active_mutes(owner_id)
        if text.lstrip("-").isdigit():
            chat_id = int(text)
            return next((row for row in rows if row["chat_id"] == chat_id), None)
        return next((row for row in rows if (row["username"] or "").lower() == text), None)

    def stat(self, owner_id, account_id, chat_id):
        row = self.db.execute(
            "SELECT * FROM business_chat_stats WHERE owner_id=? AND account_id=? AND chat_id=?",
            (owner_id, account_id, chat_id),
        ).fetchone()
        if not row:
            return None
        days = self.db.execute(
            "SELECT COUNT(*) FROM business_stats_days WHERE account_id=? AND chat_id=?",
            (account_id, chat_id),
        ).fetchone()[0]
        words = self.db.execute(
            "SELECT word,count FROM business_stats_words WHERE account_id=? AND chat_id=? "
            "ORDER BY count DESC,word LIMIT 10", (account_id, chat_id),
        ).fetchall()
        return row, days, words

    def mute(self, owner_id, row, until_at):
        self.db.execute(
            "INSERT INTO business_mutes(owner_id,account_id,chat_id,display_name,username,until_at,added_at,last_error) "
            "VALUES(?,?,?,?,?,?,?,NULL) ON CONFLICT(account_id,chat_id) DO UPDATE SET owner_id=excluded.owner_id,"
            "display_name=excluded.display_name,username=COALESCE(excluded.username,business_mutes.username),"
            "until_at=excluded.until_at,added_at=excluded.added_at,last_error=NULL",
            (owner_id, row["account_id"], row["chat_id"], row["display_name"], row["username"],
             until_at, int(time.time())),
        )
        self.db.commit()

    def unmute(self, owner_id, account_id, chat_id):
        result = self.db.execute(
            "DELETE FROM business_mutes WHERE owner_id=? AND account_id=? AND chat_id=?",
            (owner_id, account_id, chat_id),
        )
        self.db.commit()
        return bool(result.rowcount)

    def active_mutes(self, owner_id=None):
        now = int(time.time())
        self.db.execute("DELETE FROM business_mutes WHERE until_at>0 AND until_at<=?", (now,))
        self.db.commit()
        if owner_id is None:
            return self.db.execute("SELECT * FROM business_mutes ORDER BY added_at DESC").fetchall()
        return self.db.execute(
            "SELECT * FROM business_mutes WHERE owner_id=? ORDER BY added_at DESC", (owner_id,)
        ).fetchall()

    def is_muted(self, account_id, chat_id):
        now = int(time.time())
        row = self.db.execute(
            "SELECT * FROM business_mutes WHERE account_id=? AND chat_id=?", (account_id, chat_id)
        ).fetchone()
        if row and row["until_at"] and row["until_at"] <= now:
            self.unmute(row["owner_id"], account_id, chat_id)
            return None
        return row

    def set_mute_error(self, account_id, chat_id, error):
        self.db.execute(
            "UPDATE business_mutes SET last_error=? WHERE account_id=? AND chat_id=?",
            (None if error is None else str(error)[:300], account_id, chat_id),
        )
        self.db.commit()

    def mark_self_deleted(self, connection_id, chat_id, message_id):
        self.db.execute(
            "INSERT OR REPLACE INTO business_self_deletions VALUES(?,?,?,?)",
            (connection_id, chat_id, message_id, int(time.time())),
        )
        self.db.execute("DELETE FROM business_self_deletions WHERE created_at<?", (int(time.time()) - 86400,))
        self.db.commit()

    def consume_self_deleted(self, connection_id, chat_id, message_id):
        result = self.db.execute(
            "DELETE FROM business_self_deletions WHERE connection_id=? AND chat_id=? AND message_id=?",
            (connection_id, chat_id, message_id),
        )
        self.db.commit()
        return bool(result.rowcount)
