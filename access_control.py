"""Persistent allowlist for additional Business accounts."""

import time


class AccessManager:
    def __init__(self, db, routes, account_labels, protected_ids):
        self.db = db
        self.routes = routes
        self.account_labels = account_labels
        self.protected_ids = set(protected_ids)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS invited_accounts ("
            "user_id INTEGER PRIMARY KEY, label TEXT NOT NULL, granted_at INTEGER NOT NULL)"
        )
        self.db.commit()
        for row in self.list():
            if row["user_id"] not in self.protected_ids:
                self.routes[row["user_id"]] = row["user_id"]
                self.account_labels[row["user_id"]] = row["label"]

    def list(self):
        return self.db.execute(
            "SELECT user_id,label,granted_at FROM invited_accounts ORDER BY granted_at,user_id"
        ).fetchall()

    def grant(self, user_id, label=None):
        if user_id <= 0:
            raise ValueError("Нужен положительный числовой Telegram ID")
        if user_id in self.protected_ids:
            raise ValueError("Этот аккаунт уже настроен в .env")
        label = (label or str(user_id)).strip()
        if not label or len(label) > 50:
            raise ValueError("Имя должно содержать от 1 до 50 символов")
        self.db.execute(
            "INSERT INTO invited_accounts(user_id,label,granted_at) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET label=excluded.label",
            (user_id, label, int(time.time())),
        )
        self.db.commit()
        self.routes[user_id] = user_id
        self.account_labels[user_id] = label

    def revoke(self, user_id):
        if user_id in self.protected_ids:
            raise ValueError("Аккаунт из .env нельзя отключить этой командой")
        cursor = self.db.execute("DELETE FROM invited_accounts WHERE user_id=?", (user_id,))
        self.db.commit()
        if cursor.rowcount:
            self.routes.pop(user_id, None)
            self.account_labels.pop(user_id, None)
        return bool(cursor.rowcount)
