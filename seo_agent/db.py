import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid4().hex


class Database:
    def __init__(self, path):
        self.path = str(path)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def init(self):
        with self.connection() as c:
            c.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS sites (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, url TEXT NOT NULL UNIQUE,
              profile TEXT NOT NULL, analysis TEXT NOT NULL DEFAULT '{}',
              credentials TEXT, connection TEXT NOT NULL DEFAULT '{}',
              demo INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS topics (
              id TEXT PRIMARY KEY, site_id TEXT NOT NULL REFERENCES sites(id),
              title TEXT NOT NULL, keyword TEXT NOT NULL, intent TEXT NOT NULL,
              rationale TEXT NOT NULL, outline TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'idea',
              UNIQUE(site_id, keyword));
            CREATE TABLE IF NOT EXISTS articles (
              id TEXT PRIMARY KEY, site_id TEXT NOT NULL REFERENCES sites(id),
              topic_id TEXT UNIQUE REFERENCES topics(id), title TEXT NOT NULL,
              keyword TEXT NOT NULL, slug TEXT NOT NULL, meta_description TEXT NOT NULL,
              content_html TEXT NOT NULL, excerpt TEXT NOT NULL, brief TEXT NOT NULL,
              sources TEXT NOT NULL, quality TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
              revision INTEGER NOT NULL DEFAULT 1, approved_revision INTEGER,
              wp_id INTEGER, wp_url TEXT, synced_revision INTEGER,
              remote_status TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, site_id TEXT NOT NULL REFERENCES sites(id),
              kind TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
              progress INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL, error TEXT,
              cancel_requested INTEGER NOT NULL DEFAULT 0,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_active_job_per_site
              ON jobs(site_id) WHERE status IN ('queued','running');
            CREATE TABLE IF NOT EXISTS events (
              id INTEGER PRIMARY KEY AUTOINCREMENT, site_id TEXT,
              message TEXT NOT NULL, created_at TEXT NOT NULL);
            """)
            # Unfinished remote writes are reconciled explicitly; never replay on restart.
            c.execute("UPDATE articles SET status='uncertain' WHERE status='syncing'")
            c.execute("UPDATE jobs SET status='failed', error=?, message=?, updated_at=? WHERE status='running'",
                      ("برنامه در حین پردازش بسته شد. وضعیت مقاله را بررسی کنید.", "پردازش متوقف شد", now()))

    def all(self, sql, args=()):
        with self.connection() as c:
            return [dict(r) for r in c.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.connection() as c:
            return c.execute(sql, args).rowcount

    def event(self, site_id, message):
        self.execute("INSERT INTO events(site_id,message,created_at) VALUES(?,?,?)", (site_id, message, now()))

    def setting(self, key, default=""):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    def set_setting(self, key, value):
        self.execute("INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def unpack(row, fields):
    if row is None:
        return None
    result = dict(row)
    for field in fields:
        result[field] = json.loads(result[field])
    return result
