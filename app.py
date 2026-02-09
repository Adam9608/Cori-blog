from flask import Flask, render_template, send_from_directory, jsonify, abort
import os
import json
import time
import markdown
import feedparser
from datetime import datetime, timedelta

# Usage tracking removed

app = Flask(__name__, static_folder='static', template_folder='templates')

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'static/data')
BOOKS_DIR = os.path.join(BASE_DIR, 'data/books') 
POSTS_DIR = os.path.join(BASE_DIR, 'data/posts')

os.makedirs(BOOKS_DIR, exist_ok=True)
os.makedirs(POSTS_DIR, exist_ok=True)

# RSS Cache
RSS_CACHE_VERSION = 4  # Bump version to invalidate cache
rss_cache = {
    'version': RSS_CACHE_VERSION,
    'last_updated': 0,
    'data': []
}
RSS_FEEDS = [
    {'name': 'OpenAI', 'url': 'https://openai.com/news/rss.xml', 'icon': 'O'},
    {'name': 'The Verge', 'url': 'https://www.theverge.com/rss/index.xml', 'icon': 'V'},
    {'name': 'Hacker News', 'url': 'https://news.ycombinator.com/rss', 'icon': 'Y'},
    {'name': 'GitHub Blog', 'url': 'https://github.blog/feed/', 'icon': 'G'}
]

translate_cache = {}


def translate_en_to_zh(text: str) -> str:
    """轻量方案：调用 MyMemory 免费翻译（无 Key）。失败则回退原文。"""
    t = (text or '').strip()
    if not t:
        return ''
    # 已经是中文（或含大量中文）就不翻译
    if any('\u4e00' <= ch <= '\u9fff' for ch in t):
        return t
    if t in translate_cache:
        return translate_cache[t]

    try:
        from urllib.parse import quote
        from urllib.request import urlopen, Request
        import json as _json

        q = quote(t[:400])
        url = f"https://api.mymemory.translated.net/get?q={q}&langpair=en|zh-CN"
        req = Request(url, headers={"User-Agent": "CoriSpaceRSS/1.0"})
        with urlopen(req, timeout=2) as resp:
            data = _json.loads(resp.read().decode('utf-8', errors='ignore'))
        translated = (data.get('responseData', {}) or {}).get('translatedText', '')
        translated = (translated or '').strip() or t
    except Exception:
        translated = t

    # 如果翻译失败（仍是英文），按您的偏好：不展示原文，改成中文占位提示
    if translated == t and not any('\u4e00' <= ch <= '\u9fff' for ch in translated):
        translated = "（点击卡片阅读原文）"

    translate_cache[t] = translated
    return translated


def fetch_feed_entries(url: str, timeout: int = 4):
    """给 feedparser 加上超时，避免单个源拖垮整个页面。"""
    from urllib.request import urlopen, Request
    req = Request(url, headers={"User-Agent": "CoriSpaceRSS/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        content = resp.read()
    parsed = feedparser.parse(content)
    return getattr(parsed, 'entries', []) or []


def get_cached_rss():
    global rss_cache
    now = time.time()

    # 版本变化：强制失效缓存（避免代码更新后仍展示旧内容）
    if rss_cache.get('version') != RSS_CACHE_VERSION:
        rss_cache['version'] = RSS_CACHE_VERSION
        rss_cache['last_updated'] = 0
        rss_cache['data'] = []

    # 15 分钟内直接用缓存
    if now - rss_cache['last_updated'] < 900 and rss_cache['data']:
        return rss_cache['data']

    # 先准备一个“可用的旧缓存”，防止刷新失败时直接 500
    stale = rss_cache['data'] if rss_cache['data'] else []

    all_entries = []
    import re

    for feed in RSS_FEEDS:
        try:
            entries = fetch_feed_entries(feed['url'], timeout=4)
            for entry in entries[:5]:
                dt = datetime.now()
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    dt = datetime(*entry.published_parsed[:6])

                raw_summary = ''
                if hasattr(entry, 'summary'):
                    raw_summary = entry.summary
                elif hasattr(entry, 'description'):
                    raw_summary = entry.description

                clean_summary = re.sub('<[^<]+?>', '', raw_summary)
                clean_summary = re.sub(r'\s+', ' ', clean_summary).strip()
                if len(clean_summary) > 160:
                    clean_summary = clean_summary[:160] + '...'
                if not clean_summary:
                    clean_summary = "点击阅读全文..."

                # HN 的 summary 经常只有 "Comments"，对阅读没价值
                if feed['name'] == 'Hacker News':
                    if clean_summary.lower() in ['comments', 'comment'] or len(clean_summary) <= 8:
                        clean_summary = "HN entry, click to read full article/discussion."

                title = getattr(entry, 'title', '').strip() or '(无标题)'
                link = getattr(entry, 'link', '').strip() or '#'

                all_entries.append({
                    'title': title,
                    'title_zh': title, # No translation, zh field is same as original
                    'link': link,
                    'source': feed['name'],
                    'source_zh': feed['name'], # No translation, zh field is same as original
                    'icon': feed['icon'],
                    'date': dt.strftime('%Y-%m-%d'),
                    'summary': clean_summary,
                    'summary_zh': clean_summary, # No translation, zh field is same as original
                    'timestamp': dt.timestamp(),
                })
        except Exception as e:
            print(f"Error fetching {feed['name']}: {e}")
            continue

    if not all_entries:
        # 刷新失败：直接返回旧缓存（哪怕是空）
        return stale

    all_entries.sort(key=lambda x: x['timestamp'], reverse=True)

    # No translation: assign original fields to _zh for backward compatibility with template
    for item in all_entries:
        item['summary_zh'] = item['summary']
        item['title_zh'] = item['title']

    rss_cache['data'] = all_entries
    rss_cache['last_updated'] = now
    return all_entries

@app.route('/')
def index():
    return render_template('base.html')

@app.route('/assets/<path:filename>')
def serve_assets(filename):
    return send_from_directory(os.path.join(app.static_folder, 'assets'), filename)

@app.route('/data/<path:filename>')
def serve_data(filename):
    return send_from_directory(os.path.join(app.static_folder, 'data'), filename)

# Blog
@app.route('/blog')
@app.route('/blog/')
def blog_list():
    posts = []
    for f in os.listdir(POSTS_DIR):
        if f.endswith('.md'):
            path = os.path.join(POSTS_DIR, f)
            with open(path, 'r') as file:
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
    if not os.path.exists(path): abort(404)
    with open(path, 'r') as f: text = f.read()
    lines = text.split('\n')
    meta = {'title': slug, 'date': '', 'category': ''}
    content_start = 0
    for i, line in enumerate(lines[:10]):
        if line.startswith('Title:'): meta['title'] = line.replace('Title:', '').strip()
        elif line.startswith('Date:'): meta['date'] = line.replace('Date:', '').strip()
        elif line.startswith('Category:'): meta['category'] = line.replace('Category:', '').strip()
        elif line.strip() == '': 
            content_start = i
            break
    content = '\n'.join(lines[content_start:])
    html = markdown.markdown(content)
    return render_template('post.html', meta=meta, content=html)

# Book
@app.route('/book')
@app.route('/book/')
def book_list():
    books = []
    for f in os.listdir(BOOKS_DIR):
        if f.lower().endswith(('.pdf', '.epub', '.mobi')):
            path = os.path.join(BOOKS_DIR, f)
            size_mb = os.path.getsize(path) / (1024 * 1024)
            title = f.rsplit('.', 1)[0].replace('_', ' ').replace('-', ' ')
            ext = f.split('.')[-1].upper()
            books.append({"filename": f, "title": title, "size": f"{size_mb:.1f} MB", "ext": ext})
    books.sort(key=lambda x: x['title'])
    return render_template('bookshelf.html', books=books)

@app.route('/book/<path:filename>')
def serve_book(filename): return send_from_directory(BOOKS_DIR, filename)

# Models
@app.route('/models/')
def models_index():
    return render_template('models/index.html')

# Reading (RSS)
@app.route('/reading/')
def reading_index():
    entries = get_cached_rss()
    return render_template('reading.html', entries=entries)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
