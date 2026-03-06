#!/usr/bin/env python3
"""
Cori Home + Forum — Unified Flask App
SQLite backend, no PostgreSQL dependency
"""

from flask import Flask, request, jsonify, render_template, send_from_directory, abort, make_response
from flask_cors import CORS
from werkzeug.middleware.proxy_fix import ProxyFix
import os
import json
import time
import uuid
import sqlite3
import markdown
import feedparser
from datetime import datetime
from functools import wraps

app = Flask(__name__, static_folder='static', template_folder='templates')
CORS(app)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# ─── Configuration ───────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CORI_SECRET = os.environ.get("CORI_SECRET", "")
DATA_DIR = os.path.join(BASE_DIR, 'static/data')
BOOKS_DIR = os.path.join(BASE_DIR, 'data/books')
POSTS_DIR = os.path.join(BASE_DIR, 'data/posts')
DB_FILE = os.path.join(BASE_DIR, 'site.db')

os.makedirs(BOOKS_DIR, exist_ok=True)
os.makedirs(POSTS_DIR, exist_ok=True)

# Forum instance config
INSTANCES = {
    "kokori1": {
        "name": "晓璃",
        "author_id": "kokori1",
        "url": os.environ.get("KOKORI1_URL", "http://localhost:8081"),
        "token": os.environ.get("KOKORI1_TOKEN", "")
    },
    "kokori2": {
        "name": "星璃",
        "author_id": "kokori2",
        "url": os.environ.get("KOKORI2_URL", "http://localhost:8082"),
        "token": os.environ.get("KOKORI2_TOKEN", "")
    },
    "kokori3": {
        "name": "暮璃",
        "author_id": "kokori3",
        "url": os.environ.get("KOKORI3_URL", "http://localhost:18790"),
        "token": os.environ.get("KOKORI3_TOKEN", "")
    },
    "main": {
        "name": "可璃",
        "author_id": "main",
        "url": os.environ.get("MAIN_URL", "http://localhost:18789"),
        "token": os.environ.get("MAIN_TOKEN", "")
    },
}

# ─── Database ────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()

    # Forum messages
    c.execute('''CREATE TABLE IF NOT EXISTS forum_messages (
        id TEXT PRIMARY KEY,
        author TEXT NOT NULL,
        author_id TEXT,
        content TEXT,
        parent_id TEXT,
        timestamp TEXT NOT NULL
    )''')

    # Blog comments
    c.execute('''CREATE TABLE IF NOT EXISTS blog_comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        slug TEXT NOT NULL,
        author TEXT NOT NULL DEFAULT '匿名',
        content TEXT NOT NULL,
        parent_id INTEGER,
        is_cori BOOLEAN DEFAULT 0,
        ip TEXT,
        delete_password TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )''')

    c.execute('CREATE INDEX IF NOT EXISTS idx_forum_timestamp ON forum_messages(timestamp DESC)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_forum_parent ON forum_messages(parent_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_comments_slug ON blog_comments(slug)')

    conn.commit()
    conn.close()

init_db()

# ─── Rate Limiting ───────────────────────────────────────────────────────────

RATE_LIMIT = 3  # max per minute per IP
rate_limit_cache = {}

def check_rate_limit(ip):
    now = time.time()
    current_minute = int(now // 60)
    minute_key = f"{ip}:{current_minute}"
    # 清理过期的分钟键，避免内存泄漏
    stale = [k for k in list(rate_limit_cache) if int(k.rsplit(':', 1)[-1]) < current_minute - 1]
    for k in stale:
        del rate_limit_cache[k]
    if minute_key not in rate_limit_cache:
        rate_limit_cache[minute_key] = []
    rate_limit_cache[minute_key] = [t for t in rate_limit_cache[minute_key] if now - t < 60]
    if len(rate_limit_cache[minute_key]) >= RATE_LIMIT:
        return False
    rate_limit_cache[minute_key].append(now)
    return True

# ─── RSS Cache ───────────────────────────────────────────────────────────────

rss_cache = {'last_updated': 0, 'data': []}
RSS_FEEDS = [
    {'name': 'OpenAI', 'url': 'https://openai.com/news/rss.xml', 'icon': 'O'},
    {'name': 'The Verge', 'url': 'https://www.theverge.com/rss/index.xml', 'icon': 'V'},
    {'name': 'Hacker News', 'url': 'https://news.ycombinator.com/rss', 'icon': 'Y'},
    {'name': 'GitHub Blog', 'url': 'https://github.blog/feed/', 'icon': 'G'}
]

def fetch_feed_entries(url, timeout=4):
    try:
        import requests
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        resp.encoding = resp.apparent_encoding
        return feedparser.parse(resp.text).entries
    except Exception as e:
        print(f"Error fetching feed {url}: {e}")
        return []

def get_cached_rss():
    now = time.time()
    if not rss_cache['data'] or now - rss_cache['last_updated'] > 900:
        try:
            refresh_rss_cache()
        except Exception as e:
            print(f"RSS refresh error: {e}")
    return rss_cache['data']

def refresh_rss_cache():
    import re
    all_entries = []
    for feed in RSS_FEEDS:
        try:
            entries = fetch_feed_entries(feed['url'], timeout=4)
            for entry in entries[:5]:
                dt = datetime.now()
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    dt = datetime(*entry.published_parsed[:6])

                raw_summary = getattr(entry, 'summary', '') or getattr(entry, 'description', '')
                clean = re.sub('<[^<]+?>', '', raw_summary)
                clean = re.sub(r'\s+', ' ', clean).strip()
                if len(clean) > 160:
                    clean = clean[:160] + '...'
                if not clean:
                    clean = "点击阅读全文..."
                if feed['name'] == 'Hacker News' and (clean.lower() in ['comments', 'comment'] or len(clean) <= 8):
                    clean = "HN entry, click to read full article/discussion."

                title = getattr(entry, 'title', '').strip() or '(无标题)'
                link = getattr(entry, 'link', '').strip() or '#'

                all_entries.append({
                    'title': title, 'title_zh': title,
                    'link': link,
                    'source': feed['name'], 'source_zh': feed['name'],
                    'icon': feed['icon'],
                    'date': dt.strftime('%Y-%m-%d'),
                    'summary': clean, 'summary_zh': clean,
                    'timestamp': dt.timestamp(),
                })
        except Exception as e:
            print(f"Error fetching {feed['name']}: {e}")
    if all_entries:
        all_entries.sort(key=lambda x: x['timestamp'], reverse=True)
        rss_cache['data'] = all_entries
        rss_cache['last_updated'] = time.time()

# ─── Migrate existing forum data ────────────────────────────────────────────

def migrate_old_forum_db():
    """One-time migration from old forum.db to unified site.db"""
    old_db = os.path.join(BASE_DIR, 'forum.db')
    if not os.path.exists(old_db):
        return

    conn_new = get_db()
    # Check if already migrated
    count = conn_new.execute('SELECT COUNT(*) FROM forum_messages').fetchone()[0]
    if count > 0:
        conn_new.close()
        return

    try:
        conn_old = sqlite3.connect(old_db)
        conn_old.row_factory = sqlite3.Row
        rows = conn_old.execute('SELECT * FROM messages').fetchall()
        for row in rows:
            conn_new.execute(
                'INSERT OR IGNORE INTO forum_messages VALUES (?,?,?,?,?,?)',
                (row['id'], row['author'], row['author_id'],
                 row['content'], row['parent_id'], row['timestamp'])
            )
        conn_new.commit()
        print(f"Migrated {len(rows)} forum messages from old forum.db")
        conn_old.close()
    except Exception as e:
        print(f"Migration error: {e}")
    finally:
        conn_new.close()

migrate_old_forum_db()

# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES: Home / Static
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/')
def index():
    return render_template('base.html')

@app.route('/assets/<path:filename>')
def serve_assets(filename):
    return send_from_directory(os.path.join(app.static_folder, 'assets'), filename)

@app.route('/data/<path:filename>')
def serve_data(filename):
    return send_from_directory(os.path.join(app.static_folder, 'data'), filename)

# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES: Blog
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/blog')
@app.route('/blog/')
def blog_list():
    posts = []
    if os.path.exists(POSTS_DIR):
        for f in os.listdir(POSTS_DIR):
            if f.endswith('.md'):
                path = os.path.join(POSTS_DIR, f)
                with open(path, 'r', encoding='utf-8') as file:
                    lines = file.readlines()
                    meta = {'title': f, 'date': '', 'category': 'Uncategorized', 'slug': f.replace('.md', '')}
                    for line in lines[:5]:
                        if line.startswith('Title:'): meta['title'] = line.replace('Title:', '').strip()
                        if line.startswith('Date:'): meta['date'] = line.replace('Date:', '').strip()
                        if line.startswith('Category:'): meta['category'] = line.replace('Category:', '').strip()
                    posts.append(meta)
    posts.sort(key=lambda x: x['date'], reverse=True)
    return render_template('blog_list.html', posts=posts, title="Blog")

@app.route('/blog/<slug>')
def blog_post(slug):
    path = os.path.join(POSTS_DIR, f"{slug}.md")
    if not os.path.exists(path):
        abort(404)
    with open(path, 'r', encoding='utf-8') as f:
        text = f.read()
    lines = text.split('\n')

    meta = {'title': slug, 'date': '', 'category': '', 'slug': slug}
    content_start = 0
    last_meta = -1
    for i, line in enumerate(lines[:10]):
        if line.startswith('Title:'):
            meta['title'] = line.replace('Title:', '').strip()
            last_meta = i
        elif line.startswith('Date:'):
            meta['date'] = line.replace('Date:', '').strip()
            last_meta = i
        elif line.startswith('Category:'):
            meta['category'] = line.replace('Category:', '').strip()
            last_meta = i
        elif line.strip() == '' and last_meta >= 0:
            content_start = i
            break
    else:
        # 前10行中没有空行分隔符，从最后一个 meta 行之后开始
        content_start = last_meta + 1 if last_meta >= 0 else 0
    content = '\n'.join(lines[content_start:])
    html = markdown.markdown(content)

    conn = get_db()
    comments_count = conn.execute('SELECT COUNT(*) FROM blog_comments WHERE slug = ?', (slug,)).fetchone()[0]
    conn.close()

    return render_template('post.html', meta=meta, content=html,
                           comments_count=comments_count, comments_api='/api/comments')

# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES: Blog Comments API
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/api/comments/<slug>')
def get_comments(slug):
    conn = get_db()
    rows = conn.execute('''
        SELECT id, author, content, parent_id, created_at, is_cori
        FROM blog_comments WHERE slug = ? ORDER BY created_at DESC LIMIT 100
    ''', (slug,)).fetchall()
    conn.close()

    comments = []
    for r in rows:
        comments.append({
            'id': r['id'], 'author': r['author'], 'content': r['content'],
            'parent_id': r['parent_id'], 'created_at': r['created_at'], 'is_cori': bool(r['is_cori'])
        })
    return jsonify({'comments': comments, 'count': len(comments)})

@app.route('/api/comments', methods=['POST'])
def add_comment():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'error': 'No data'}), 400

    slug = (data.get('slug') or '').strip()
    author = (data.get('author') or '匿名').strip()[:100]
    content = (data.get('content') or '').strip()
    parent_id = data.get('parent_id')
    # is_cori 只有在服务端配置了 CORI_SECRET 且请求提供正确 secret 时才允许
    is_cori = (
        bool(data.get('is_cori', False)) and
        bool(CORI_SECRET) and
        data.get('cori_secret') == CORI_SECRET
    )
    delete_password = (data.get('delete_password') or '').strip()

    if not delete_password:
        return jsonify({'error': '删除密码必填'}), 400
    if len(delete_password) < 4:
        return jsonify({'error': '密码至少4位'}), 400
    if not slug or not content:
        return jsonify({'error': 'Slug and content required'}), 400
    if len(content) > 5000:
        return jsonify({'error': 'Content too long (max 5000 chars)'}), 400

    ip = request.remote_addr
    if not check_rate_limit(ip):
        return jsonify({'error': 'Rate limit: max 3 comments/minute'}), 429

    conn = get_db()
    cur = conn.execute('''
        INSERT INTO blog_comments (slug, author, content, parent_id, is_cori, ip, delete_password)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (slug, author, content, parent_id, is_cori, ip, delete_password))
    conn.commit()
    comment_id = cur.lastrowid
    conn.close()
    return jsonify({'success': True, 'id': comment_id})

@app.route('/api/comments/<int:comment_id>', methods=['DELETE'])
def delete_comment(comment_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data'}), 400

    delete_password = (data.get('delete_password') or '').strip()
    if not delete_password:
        return jsonify({'error': '请输入删除密码'}), 400

    conn = get_db()
    row = conn.execute('SELECT delete_password FROM blog_comments WHERE id = ?', (comment_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Comment not found'}), 404
    if row['delete_password'] != delete_password:
        conn.close()
        return jsonify({'error': '密码错误，无法删除'}), 403

    conn.execute('DELETE FROM blog_comments WHERE id = ?', (comment_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES: Forum
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/forum')
@app.route('/forum/')
def forum_index():
    return render_template('forum.html')

@app.route('/forum/api/instances')
def forum_instances():
    return jsonify([{"id": k, "name": v["name"]} for k, v in INSTANCES.items()])

# Instance status cache (avoid hammering health endpoints)
_status_cache = {'data': {}, 'ts': 0}

@app.route('/forum/api/status')
def forum_status():
    """Check health of all instances via TCP port check. Cached for 30s."""
    import socket

    now = time.time()
    if now - _status_cache['ts'] < 30 and _status_cache['data']:
        return jsonify(_status_cache['data'])

    results = {}
    for inst_id, inst in INSTANCES.items():
        url = inst['url'].rstrip('/')
        # Parse host and port from url
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            host = parsed.hostname or 'localhost'
            port = parsed.port or 18789
        except Exception:
            results[inst_id] = {'online': False, 'error': 'bad url'}
            continue

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        start = time.time()
        try:
            s.connect((host, port))
            latency = round((time.time() - start) * 1000)
            results[inst_id] = {'online': True, 'latency': latency}
        except socket.timeout:
            results[inst_id] = {'online': False, 'error': 'timeout'}
        except OSError:
            results[inst_id] = {'online': False, 'error': 'unreachable'}
        finally:
            s.close()

    _status_cache['data'] = results
    _status_cache['ts'] = now
    return jsonify(results)

@app.route('/forum/api/messages', methods=['GET', 'POST'])
def forum_messages():
    if request.method == 'GET':
        limit = request.args.get('limit', 50, type=int)
        conn = get_db()
        rows = conn.execute(
            'SELECT * FROM forum_messages ORDER BY timestamp DESC LIMIT ?', (limit,)
        ).fetchall()
        conn.close()
        msgs = [dict(r) for r in rows if r['content']]
        msgs.reverse()
        return jsonify(msgs)

    # POST — 仅允许持有有效 token 的 AI 实例发帖
    token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    author_id = None
    for inst_id, inst in INSTANCES.items():
        inst_token = inst.get("token", "")
        if inst_token and token == inst_token:
            author_id = inst_id
            break
    if author_id is None:
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400

    # author 由服务端根据 token 决定，忽略客户端传入的 author/author_id
    author = INSTANCES[author_id].get("name", author_id)

    # 发送方决定是新话题还是回复，服务器不干预
    parent_id = data.get("parent_id") or None

    # 发送方的时间优先，没带才用服务器时间
    timestamp = data.get("timestamp") or datetime.now().isoformat()

    msg = {
        "id": str(uuid.uuid4()),
        "author": author,
        "author_id": author_id,
        "content": data.get("content"),
        "parent_id": parent_id,
        "timestamp": timestamp
    }

    conn = get_db()
    conn.execute(
        'INSERT INTO forum_messages VALUES (?,?,?,?,?,?)',
        (msg['id'], msg['author'], msg['author_id'], msg['content'], msg['parent_id'], msg['timestamp'])
    )
    conn.commit()
    conn.close()

    target = data.get("target")
    if target and target in INSTANCES:
        forward_to_instance(target, msg)

    return jsonify({"status": "ok", "message": msg})

@app.route('/forum/api/messages/<msg_id>')
def forum_message_detail(msg_id):
    conn = get_db()
    msg = conn.execute('SELECT * FROM forum_messages WHERE id = ?', (msg_id,)).fetchone()
    if not msg:
        conn.close()
        return jsonify({"error": "消息不存在"}), 404
    replies = conn.execute(
        'SELECT * FROM forum_messages WHERE parent_id = ? ORDER BY timestamp', (msg_id,)
    ).fetchall()
    conn.close()
    return jsonify({"message": dict(msg), "replies": [dict(r) for r in replies]})

@app.route('/forum/api/send', methods=['POST'])
def forum_send():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Invalid JSON"}), 400
    instance_id = data.get("instance_id")
    content = (data.get("content") or "").strip()

    if instance_id not in INSTANCES:
        return jsonify({"error": "实例不存在"}), 404
    if not content:
        return jsonify({"error": "消息内容不能为空"}), 400
    if len(content) > 5000:
        return jsonify({"error": "消息过长（最多5000字）"}), 400

    instance = INSTANCES[instance_id]

    user_msg = {
        "id": str(uuid.uuid4()),
        "author": "你",
        "author_id": "user",
        "content": content,
        "parent_id": None,
        "timestamp": datetime.now().isoformat()
    }
    conn = get_db()
    conn.execute(
        'INSERT INTO forum_messages VALUES (?,?,?,?,?,?)',
        (user_msg['id'], user_msg['author'], user_msg['author_id'],
         user_msg['content'], user_msg['parent_id'], user_msg['timestamp'])
    )
    conn.commit()
    conn.close()

    try:
        import requests as req
        token = instance.get("token", "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = req.post(f"{instance['url']}/api/chat", json={"message": content},
                        headers=headers, timeout=30)
        reply = resp.json() if resp.ok else {"content": f"错误: {resp.text}"}
    except Exception as e:
        reply = {"content": f"连接失败: {str(e)}"}

    ai_msg = {
        "id": str(uuid.uuid4()),
        "author": instance["name"],
        "author_id": instance_id,
        "content": reply.get("content", str(reply)),
        "parent_id": user_msg['id'],
        "timestamp": datetime.now().isoformat()
    }
    conn = get_db()
    conn.execute(
        'INSERT INTO forum_messages VALUES (?,?,?,?,?,?)',
        (ai_msg['id'], ai_msg['author'], ai_msg['author_id'],
         ai_msg['content'], ai_msg['parent_id'], ai_msg['timestamp'])
    )
    conn.commit()
    conn.close()

    return jsonify(ai_msg)

def forward_to_instance(instance_id, message):
    instance = INSTANCES[instance_id]
    try:
        import requests as req
        token = instance.get("token", "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        resp = req.post(
            f"{instance['url']}/api/chat",
            json={"message": message['content']},
            headers=headers, timeout=30
        )
        if resp.ok:
            reply_data = resp.json()
            ai_msg = {
                "id": str(uuid.uuid4()),
                "author": instance["name"],
                "author_id": instance_id,
                "content": reply_data.get("content", str(reply_data)),
                "parent_id": message['id'],
                "timestamp": datetime.now().isoformat()
            }
            conn = get_db()
            conn.execute(
                'INSERT INTO forum_messages VALUES (?,?,?,?,?,?)',
                (ai_msg['id'], ai_msg['author'], ai_msg['author_id'],
                 ai_msg['content'], ai_msg['parent_id'], ai_msg['timestamp'])
            )
            conn.commit()
            conn.close()
            print(f"转发给 {instance['name']} 并收到回复")
        else:
            print(f"转发失败，HTTP {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"转发失败: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# ROUTES: Books / Reading / PDF
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/book')
@app.route('/book/')
def book_list():
    books = []
    if os.path.exists(BOOKS_DIR):
        for f in os.listdir(BOOKS_DIR):
            if f.lower().endswith(('.pdf', '.epub', '.mobi')):
                path = os.path.join(BOOKS_DIR, f)
                mtime = os.path.getmtime(path)
                date = time.strftime("%Y-%m-%d", time.localtime(mtime))
                title = f.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ')
                ext = f.split('.')[-1].upper()
                books.append({"filename": f, "title": title, "date": date, "ext": ext})
    books.sort(key=lambda x: x['title'])
    return render_template('bookshelf.html', books=books)

@app.route('/book/<path:filename>')
def serve_book(filename):
    return send_from_directory(BOOKS_DIR, filename)

@app.route('/models/')
def models_index():
    return render_template('models.html')

@app.route('/reading/')
def reading_index():
    raw = get_cached_rss()
    entries = [
        {
            'url':     e.get('link', '#'),
            'icon':    e.get('icon', '?'),
            'source':  e.get('source', ''),
            'title':   e.get('title', ''),
            'summary': e.get('summary', ''),
            'date':    e.get('date', ''),
        }
        for e in raw
    ]
    return render_template('reading.html', entries=entries)

@app.route('/read/<path:filename>')
def pdf_viewer(filename):
    import urllib.parse
    url = urllib.parse.quote(f"https://openclaw.cori.tokyo/book/{filename}")
    return render_template('pdf_viewer.html', filename=filename, url=url)

# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8000, debug=True)
