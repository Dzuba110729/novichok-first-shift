import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


STEPS = (
    ("Встретиться с наставником", "Подойди к стойке выдачи и найди Марину Савич."),
    ("Получить терминал сбора данных", "Возьми заряженный терминал на стойке выдачи."),
    ("Войти в терминал", "Проверь, что тебе доступен раздел «Комплектация»."),
    ("Собрать учебный заказ", "Найди скакалку и эспандер. Покажи заказ наставнику."),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS demos (
    id TEXT PRIMARY KEY
);
CREATE TABLE IF NOT EXISTS completed_steps (
    demo_id TEXT NOT NULL REFERENCES demos(id),
    step INTEGER NOT NULL,
    PRIMARY KEY (demo_id, step)
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY,
    demo_id TEXT NOT NULL REFERENCES demos(id),
    step INTEGER NOT NULL,
    note TEXT NOT NULL,
    opened_at TEXT NOT NULL,
    answer TEXT,
    closed_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_open_request
    ON requests (demo_id, step) WHERE closed_at IS NULL;
"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ShiftStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                connection.execute("PRAGMA foreign_keys = ON")
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("PRAGMA journal_mode = WAL")
            db.executescript(SCHEMA)

    def create_demo(self):
        demo_id = uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO demos (id) VALUES (?)", (demo_id,))
        return demo_id

    def demo_exists(self, demo_id):
        with self.connect() as db:
            return db.execute("SELECT 1 FROM demos WHERE id = ?", (demo_id,)).fetchone() is not None

    def shift(self, demo_id):
        with self.connect() as db:
            db.execute("BEGIN")
            if db.execute("SELECT 1 FROM demos WHERE id = ?", (demo_id,)).fetchone() is None:
                return None
            completed = {
                row["step"]
                for row in db.execute("SELECT step FROM completed_steps WHERE demo_id = ?", (demo_id,))
            }
            requests = db.execute(
                "SELECT * FROM requests WHERE demo_id = ? ORDER BY id DESC",
                (demo_id,),
            ).fetchall()

        latest_by_step = {}
        for ticket in requests:
            latest_by_step.setdefault(ticket["step"], ticket)
        steps = [
            {
                "number": number,
                "title": title,
                "instruction": instruction,
                "done": number in completed,
                "ticket": latest_by_step.get(number),
            }
            for number, (title, instruction) in enumerate(STEPS, 1)
        ]
        return {
            "steps": steps,
            "done_count": len(completed),
            "open_requests": [ticket for ticket in requests if ticket["closed_at"] is None],
            "closed_requests": [ticket for ticket in requests if ticket["closed_at"] is not None],
        }

    def complete_step(self, demo_id, step):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._request_open(db, demo_id, step):
                return "open"
            db.execute(
                "INSERT OR IGNORE INTO completed_steps (demo_id, step) VALUES (?, ?)",
                (demo_id, step),
            )
        return None

    def request_help(self, demo_id, step, note):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM completed_steps WHERE demo_id = ? AND step = ?", (demo_id, step)
            ).fetchone():
                return "done"
            if self._request_open(db, demo_id, step):
                return "open"
            db.execute(
                "INSERT INTO requests (demo_id, step, note, opened_at) VALUES (?, ?, ?, ?)",
                (demo_id, step, note, now()),
            )
        return None

    def close_request(self, demo_id, request_id, answer):
        with self.connect() as db:
            updated = db.execute(
                """UPDATE requests SET answer = ?, closed_at = ?
                   WHERE id = ? AND demo_id = ? AND closed_at IS NULL""",
                (answer, now(), request_id, demo_id),
            )
            return updated.rowcount == 1

    @staticmethod
    def _request_open(db, demo_id, step):
        return db.execute(
            "SELECT 1 FROM requests WHERE demo_id = ? AND step = ? AND closed_at IS NULL",
            (demo_id, step),
        ).fetchone() is not None
