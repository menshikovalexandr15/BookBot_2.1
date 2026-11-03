import sqlite3
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

class Storage:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, language TEXT NOT NULL DEFAULT 'ru');
                CREATE TABLE IF NOT EXISTS favorites (user_id INTEGER NOT NULL, book_id INTEGER NOT NULL, title TEXT NOT NULL, author TEXT NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY(user_id, book_id));
                CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, query TEXT NOT NULL, searched_at TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS history_user ON history(user_id, id DESC);
            ''')

    @contextmanager
    def connect(self):
        # sqlite3.Connection's context manager commits/rolls back but does not
        # close the connection. Close explicitly to avoid leaking one per action.
        db = sqlite3.connect(self.path, timeout=10)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def language(self, user_id: int) -> str:
        with self.connect() as db:
            row = db.execute('SELECT language FROM users WHERE user_id=?', (user_id,)).fetchone()
            return row[0] if row else 'ru'

    def set_language(self, user_id: int, language: str):
        if language not in ('ru', 'en'):
            raise ValueError('Invalid language')
        with self.connect() as db:
            db.execute('INSERT INTO users(user_id,language) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET language=excluded.language', (user_id, language))

    def add_history(self, user_id: int, query: str):
        with self.connect() as db:
            db.execute('INSERT INTO history(user_id,query,searched_at) VALUES(?,?,?)', (user_id, query, datetime.now(timezone.utc).isoformat()))
            db.execute('DELETE FROM history WHERE user_id=? AND id NOT IN (SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT 30)', (user_id, user_id))

    def history(self, user_id: int):
        with self.connect() as db:
            return db.execute('SELECT query,searched_at FROM history WHERE user_id=? ORDER BY id DESC LIMIT 10', (user_id,)).fetchall()

    def toggle_favorite(self, user_id: int, book):
        with self.connect() as db:
            exists = db.execute('SELECT 1 FROM favorites WHERE user_id=? AND book_id=?', (user_id, book.id)).fetchone()
            if exists:
                db.execute('DELETE FROM favorites WHERE user_id=? AND book_id=?', (user_id, book.id))
                return False
            db.execute('INSERT INTO favorites VALUES(?,?,?,?,?)', (user_id, book.id, book.title, book.author, datetime.now(timezone.utc).isoformat()))
            return True

    def favorites(self, user_id: int):
        with self.connect() as db:
            return db.execute('SELECT book_id,title,author FROM favorites WHERE user_id=? ORDER BY added_at DESC LIMIT 30', (user_id,)).fetchall()
