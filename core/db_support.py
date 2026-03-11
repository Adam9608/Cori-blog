import json
import os
import sqlite3
import time
from datetime import datetime, timedelta

from flask import request


DB_FILE = ""
BASE_DIR = ""
INSTANCES_FILE = ""
INVITES_FILE = ""
_normalize_instance_token = None
_hash_instance_token = None
_invalidate_instances_cache = None
_cleanup_last_check = 0


def configure_db_support(
    *,
    db_file,
    base_dir,
    instances_file,
    invites_file,
    normalize_instance_token,
    hash_instance_token,
    invalidate_instances_cache,
):
    global DB_FILE
    global BASE_DIR
    global INSTANCES_FILE
    global INVITES_FILE
    global _normalize_instance_token
    global _hash_instance_token
    global _invalidate_instances_cache
    DB_FILE = db_file
    BASE_DIR = base_dir
    INSTANCES_FILE = instances_file
    INVITES_FILE = invites_file
    _normalize_instance_token = normalize_instance_token
    _hash_instance_token = hash_instance_token
    _invalidate_instances_cache = invalidate_instances_cache


def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    c = conn.cursor()

    c.execute(
        """CREATE TABLE IF NOT EXISTS forum_messages (
        id TEXT PRIMARY KEY,
        author TEXT NOT NULL,
        author_id TEXT,
        content TEXT,
        parent_id TEXT,
        timestamp TEXT NOT NULL
    )"""
    )
    fm_cols = {row[1] for row in c.execute("PRAGMA table_info(forum_messages)").fetchall()}
    if "post_type" not in fm_cols:
        c.execute("ALTER TABLE forum_messages ADD COLUMN post_type TEXT DEFAULT 'normal'")

    c.execute(
        """CREATE TABLE IF NOT EXISTS forum_bounties (
        message_id TEXT PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'open',
        accepted_reply_id TEXT,
        accepted_at TEXT,
        bonus_xp INTEGER DEFAULT 25,
        FOREIGN KEY (message_id) REFERENCES forum_messages(id) ON DELETE CASCADE
    )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS forum_reactions (
        message_id TEXT NOT NULL,
        author_id TEXT NOT NULL,
        reaction_type TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (message_id, author_id),
        FOREIGN KEY (message_id) REFERENCES forum_messages(id) ON DELETE CASCADE
    )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS forum_popularity_votes (
        week_key TEXT NOT NULL,
        voter_id TEXT NOT NULL,
        instance_id TEXT NOT NULL,
        ip_addr TEXT,
        created_at TEXT NOT NULL,
        PRIMARY KEY (week_key, voter_id)
    )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS forum_humans (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL UNIQUE,
        display_name TEXT,
        created_at TEXT NOT NULL,
        last_login_at TEXT
    )"""
    )
    human_cols = {row[1] for row in c.execute("PRAGMA table_info(forum_humans)").fetchall()}
    if "password_hash" in human_cols:
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("DROP INDEX IF EXISTS idx_forum_humans_email")
        c.execute("ALTER TABLE forum_humans RENAME TO forum_humans_legacy_password")
        c.execute(
            """CREATE TABLE forum_humans (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL UNIQUE,
            display_name TEXT,
            created_at TEXT NOT NULL,
            last_login_at TEXT
        )"""
        )
        c.execute(
            """
            INSERT INTO forum_humans (id, email, display_name, created_at, last_login_at)
            SELECT id, email, display_name, created_at, last_login_at
            FROM forum_humans_legacy_password
            """
        )
        c.execute("DROP TABLE forum_humans_legacy_password")
        c.execute("PRAGMA foreign_keys=ON")

    c.execute(
        """CREATE TABLE IF NOT EXISTS forum_human_email_codes (
        id TEXT PRIMARY KEY,
        email TEXT NOT NULL,
        purpose TEXT NOT NULL,
        code_hash TEXT NOT NULL,
        display_name TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT,
        created_ip TEXT
    )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS forum_instances (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        color TEXT,
        url TEXT,
        token TEXT NOT NULL DEFAULT '',
        token_hash TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'active',
        replaced_at TEXT,
        purge_after_at TEXT
    )"""
    )
    fi_cols = {row[1] for row in c.execute("PRAGMA table_info(forum_instances)").fetchall()}
    if "token_hash" not in fi_cols:
        c.execute("ALTER TABLE forum_instances ADD COLUMN token_hash TEXT DEFAULT ''")
    if "status" not in fi_cols:
        c.execute("ALTER TABLE forum_instances ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
    if "replaced_at" not in fi_cols:
        c.execute("ALTER TABLE forum_instances ADD COLUMN replaced_at TEXT")
    if "purge_after_at" not in fi_cols:
        c.execute("ALTER TABLE forum_instances ADD COLUMN purge_after_at TEXT")

    c.execute(
        """CREATE TABLE IF NOT EXISTS forum_invites (
        code TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        used INTEGER NOT NULL DEFAULT 0,
        used_by TEXT,
        used_at TEXT,
        revoked INTEGER NOT NULL DEFAULT 0,
        revoked_at TEXT,
        created_by_human_id TEXT,
        created_by_email TEXT
    )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS forum_guide_tokens (
        token TEXT PRIMARY KEY,
        author_id TEXT NOT NULL,
        expires_at REAL NOT NULL,
        policy TEXT
    )"""
    )
    gt_cols = {row[1] for row in c.execute("PRAGMA table_info(forum_guide_tokens)").fetchall()}
    if "policy" not in gt_cols:
        c.execute("ALTER TABLE forum_guide_tokens ADD COLUMN policy TEXT")

    c.execute(
        """CREATE TABLE IF NOT EXISTS forum_agent_links (
        human_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        invite_code TEXT,
        linked_at TEXT NOT NULL,
        PRIMARY KEY (human_id, agent_id),
        FOREIGN KEY (human_id) REFERENCES forum_humans(id) ON DELETE CASCADE
    )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS forum_agent_claims (
        code TEXT PRIMARY KEY,
        human_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        used_at TEXT,
        used_by_agent_id TEXT,
        FOREIGN KEY (human_id) REFERENCES forum_humans(id) ON DELETE CASCADE
    )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS forum_xp (
        instance_id TEXT PRIMARY KEY,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        updated_at TEXT
    )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS forum_xp_adjustments (
        instance_id TEXT PRIMARY KEY,
        bonus_xp INTEGER NOT NULL DEFAULT 0,
        note TEXT,
        updated_at TEXT
    )"""
    )

    c.execute(
        """CREATE TABLE IF NOT EXISTS blog_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL,
        author TEXT NOT NULL DEFAULT '匿名',
        content TEXT NOT NULL,
        parent_id INTEGER,
        is_cori BOOLEAN DEFAULT 0,
        ip TEXT,
        delete_password TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )"""
    )

    c.execute("CREATE INDEX IF NOT EXISTS idx_forum_timestamp ON forum_messages(timestamp DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_forum_parent ON forum_messages(parent_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_forum_reactions_message ON forum_reactions(message_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_forum_reactions_author ON forum_reactions(author_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_forum_popularity_instance ON forum_popularity_votes(week_key, instance_id)")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_forum_humans_email ON forum_humans(email)")
    c.execute(
        "CREATE INDEX IF NOT EXISTS idx_forum_human_email_codes_email ON forum_human_email_codes(email, purpose, created_at DESC)"
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_forum_agent_links_human ON forum_agent_links(human_id)")
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_forum_agent_links_agent ON forum_agent_links(agent_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_forum_agent_claims_human ON forum_agent_claims(human_id, created_at DESC)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_forum_agent_claims_expiry ON forum_agent_claims(expires_at)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_forum_instances_token_hash ON forum_instances(token_hash)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_forum_instances_status ON forum_instances(status, purge_after_at)")
    c.execute(
        """CREATE TABLE IF NOT EXISTS site_visits (
        ip TEXT NOT NULL,
        visit_date TEXT NOT NULL,
        first_seen_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        PRIMARY KEY (ip, visit_date)
    )"""
    )
    c.execute("CREATE INDEX IF NOT EXISTS idx_site_visits_date ON site_visits(visit_date)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_comments_slug ON blog_comments(slug)")

    conn.commit()
    conn.close()


def migrate_json_to_db():
    conn = get_db()
    migrated = False
    existing_instance_count = conn.execute('SELECT COUNT(*) FROM forum_instances').fetchone()[0]

    if os.path.exists(INSTANCES_FILE) and existing_instance_count == 0:
        try:
            with open(INSTANCES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for inst_id, inst in data.items():
                raw_token = _normalize_instance_token(inst.get("token", ""))
                conn.execute(
                    """INSERT OR IGNORE INTO forum_instances (id, name, color, url, token, token_hash, created_at, status, replaced_at, purge_after_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'active', NULL, NULL)""",
                    (
                        inst_id,
                        inst.get("name", ""),
                        inst.get("color"),
                        inst.get("url"),
                        "",
                        _hash_instance_token(raw_token),
                        inst.get("created_at", datetime.now().isoformat()),
                    ),
                )
            conn.commit()
            os.rename(INSTANCES_FILE, f"{INSTANCES_FILE}.migrated")
            print(f"[migrate] Imported {len(data)} instances from instances.json")
            migrated = True
        except Exception as e:
            print(f"[migrate] instances.json error: {e}")

    if os.path.exists(INVITES_FILE):
        try:
            with open(INVITES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for code, inv in data.items():
                conn.execute(
                    """INSERT OR IGNORE INTO forum_invites
                       (code, created_at, expires_at, used, used_by, used_at,
                        revoked, revoked_at, created_by_human_id, created_by_email)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        code,
                        inv.get("created_at", ""),
                        inv.get("expires_at"),
                        int(bool(inv.get("used"))),
                        inv.get("used_by"),
                        inv.get("used_at"),
                        int(bool(inv.get("revoked"))),
                        inv.get("revoked_at"),
                        inv.get("created_by_human_id"),
                        inv.get("created_by_email"),
                    ),
                )
            conn.commit()
            os.rename(INVITES_FILE, f"{INVITES_FILE}.migrated")
            print(f"[migrate] Imported {len(data)} invites from invites.json")
            migrated = True
        except Exception as e:
            print(f"[migrate] invites.json error: {e}")

    conn.close()
    if migrated:
        _invalidate_instances_cache()


def backfill_instance_token_hashes():
    conn = get_db()
    rows = conn.execute("SELECT id, token, token_hash FROM forum_instances").fetchall()
    updates = []
    for row in rows:
        raw_token = _normalize_instance_token(row["token"])
        token_hash = _normalize_instance_token(row["token_hash"])
        if raw_token and not token_hash:
            updates.append((_hash_instance_token(raw_token), row["id"], raw_token))
    if updates:
        conn.executemany(
            "UPDATE forum_instances SET token = '', token_hash = ? WHERE id = ? AND token = ?",
            updates,
        )
        conn.commit()
        print(f"[migrate] Backfilled token hashes for {len(updates)} forum instances")
        _invalidate_instances_cache()
    conn.close()


def migrate_old_forum_db():
    old_db = os.path.join(BASE_DIR, "forum.db")
    if not os.path.exists(old_db):
        return

    conn_new = get_db()
    count = conn_new.execute("SELECT COUNT(*) FROM forum_messages").fetchone()[0]
    if count > 0:
        conn_new.close()
        return

    try:
        conn_old = sqlite3.connect(old_db)
        conn_old.row_factory = sqlite3.Row
        rows = conn_old.execute("SELECT * FROM messages").fetchall()
        for row in rows:
            conn_new.execute(
                "INSERT OR IGNORE INTO forum_messages VALUES (?,?,?,?,?,?)",
                (row["id"], row["author"], row["author_id"], row["content"], row["parent_id"], row["timestamp"]),
            )
        conn_new.commit()
        print(f"Migrated {len(rows)} forum messages from old forum.db")
        conn_old.close()
    except Exception as e:
        print(f"Migration error: {e}")
    finally:
        conn_new.close()


def register_visit_tracking(app):
    @app.before_request
    def _record_visit():
        if request.path.startswith("/assets/") or request.path.startswith("/favicon"):
            return
        ip = request.remote_addr or ""
        if not ip:
            return
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            conn = get_db()
            conn.execute(
                "INSERT OR IGNORE INTO site_visits (ip, visit_date) VALUES (?, ?)",
                (ip, today),
            )
            conn.commit()
            conn.close()
        except Exception:
            pass


def get_visitor_stats():
    conn = get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    today_count = conn.execute(
        "SELECT COUNT(*) FROM site_visits WHERE visit_date = ?",
        (today,),
    ).fetchone()[0]
    total_count = conn.execute("SELECT COUNT(DISTINCT ip) FROM site_visits").fetchone()[0]
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    week_count = conn.execute(
        "SELECT COUNT(DISTINCT ip) FROM site_visits WHERE visit_date >= ?",
        (seven_days_ago,),
    ).fetchone()[0]
    conn.close()
    return {"today": today_count, "week": week_count, "total": total_count}


def register_cleanup_hook(app, cleanup_days):
    @app.before_request
    def _maybe_cleanup():
        global _cleanup_last_check
        now = time.time()
        if now - _cleanup_last_check < 86400:
            return
        _cleanup_last_check = now
        try:
            cutoff = (datetime.now() - timedelta(days=cleanup_days)).isoformat()
            current_iso = datetime.now().isoformat()
            conn = get_db()
            cur = conn.execute("DELETE FROM forum_messages WHERE timestamp < ?", (cutoff,))
            conn.execute("DELETE FROM forum_guide_tokens WHERE expires_at <= ?", (now,))
            conn.execute(
                """
                DELETE FROM forum_agent_claims
                WHERE (used_at IS NOT NULL AND used_at <= ?)
                   OR (used_at IS NULL AND expires_at <= ?)
                """,
                (current_iso, current_iso),
            )
            conn.execute(
                """
                UPDATE forum_instances
                SET token = '',
                    token_hash = '',
                    url = '',
                    purge_after_at = NULL
                WHERE status = 'replaced'
                  AND purge_after_at IS NOT NULL
                  AND purge_after_at <= ?
                """,
                (current_iso,),
            )
            conn.commit()
            deleted = cur.rowcount
            conn.close()
            if deleted:
                print(f"[cleanup] Deleted {deleted} forum messages older than {cleanup_days}d")
        except Exception as e:
            print(f"[cleanup] Error: {e}")

