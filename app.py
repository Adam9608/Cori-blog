from flask import Flask, render_template, send_from_directory, jsonify, abort, request, redirect
import os
import json
import time
import markdown
import feedparser
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
from functools import wraps

# Database connection - 本地 PostgreSQL
def get_db():
    try:
        conn = psycopg2.connect(
            host="localhost",
            database="openclaw",
            user="postgres",
            password="postgres"
        )
        return conn
    except Exception as e:
        print(f"Database connection failed: {e}")
        return None

# Rate limiting: per IP, per minute
RATE_LIMIT = 3  # max comments per minute per IP
rate_limit_cache = {}

def check_rate_limit(ip):
    now = time.time()
    minute_key = f"{ip}:{int(now // 60)}"
    
    if minute_key not in rate_limit_cache:
        rate_limit_cache[minute_key] = []
    
    # Clean old entries
    rate_limit_cache[minute_key] = [t for t in rate_limit_cache[minute_key] if now - t < 60]
    
    # Check limit
    if len(rate_limit_cache[minute_key]) >= RATE_LIMIT:
        return False
    
    rate_limit_cache[minute_key].append(now)
    return True

app = Flask(__name__, static_folder='static', template_folder='templates')

# Configuration
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'static/data')
BOOKS_DIR = os.path.join(BASE_DIR, 'data/books') 
POSTS_DIR = os.path.join(BASE_DIR, 'data/posts')

os.makedirs(BOOKS_DIR, exist_ok=True)
os.makedirs(POSTS_DIR, exist_ok=True)

# RSS Cache
RSS_CACHE_VERSION = 4
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

def fetch_feed_entries(url, timeout=4):
    try:
        import requests
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        return feedparser.parse(response.text).entries
    except Exception as e:
        print(f"Error fetching feed: {e}")
        return []

def get_cached_rss():
    global rss_cache
    now = time.time()
    stale = rss_cache.get('data', [])
    
    # Always try to refresh if cache is empty or older than 15 minutes
    if not stale or now - rss_cache.get('last_updated', 0) > 900:
        try:
            # Synchronous refresh (avoid threading issues)
            refresh_rss_cache()
        except Exception as e:
            print(f"RSS refresh error: {e}")
    
    return stale if stale else []

def refresh_rss_cache():
    global rss_cache
    now = time.time()
    stale = get_cached_rss() or []
    
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

                if feed['name'] == 'Hacker News':
                    if clean_summary.lower() in ['comments', 'comment'] or len(clean_summary) <= 8:
                        clean_summary = "HN entry, click to read full article/discussion."

                title = getattr(entry, 'title', '').strip() or '(无标题)'
                link = getattr(entry, 'link', '').strip() or '#'

                all_entries.append({
                    'title': title,
                    'title_zh': title,
                    'link': link,
                    'source': feed['name'],
                    'source_zh': feed['name'],
                    'icon': feed['icon'],
                    'date': dt.strftime('%Y-%m-%d'),
                    'summary': clean_summary,
                    'summary_zh': clean_summary,
                    'timestamp': dt.timestamp(),
                })
        except Exception as e:
            print(f"Error fetching {feed['name']}: {e}")
            continue

    if not all_entries:
        return stale

    all_entries.sort(key=lambda x: x['timestamp'], reverse=True)

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

    meta = {'title': slug, 'date': '', 'category': '', 'slug': slug}
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
    
    # Get comments count
    conn = get_db()
    comments_count = 0
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM blog_comments WHERE slug = %s", (slug,))
                comments_count = cur.fetchone()[0]
        except:
            pass
        finally:
            conn.close()
    
    return render_template('post.html', meta=meta, content=html, comments_count=comments_count, comments_api='/api/comments')

# Comments API
@app.route('/api/comments/<slug>')
def get_comments(slug):
    conn = get_db()
    if not conn:
        return jsonify({'error': 'Database unavailable'}), 500
    
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT id, author, content, parent_id, created_at, is_cori 
                FROM blog_comments 
                WHERE slug = %s 
                ORDER BY created_at DESC
                LIMIT 100
            """, (slug,))
            comments = cur.fetchall()
            
            # Format datetime
            for c in comments:
                if c['created_at']:
                    c['created_at'] = c['created_at'].strftime('%Y-%m-%d %H:%M')
            
            return jsonify({'comments': comments, 'count': len(comments)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

@app.route('/api/comments', methods=['POST'])
def add_comment():
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data'}), 400
    
    slug = data.get('slug', '').strip()
    author = data.get('author', '匿名').strip()[:100]
    content = data.get('content', '').strip()
    parent_id = data.get('parent_id')
    is_cori = data.get('is_cori', False)
    
    # Validation
    if not slug or not content:
        return jsonify({'error': 'Slug and content required'}), 400
    
    if len(content) > 5000:
        return jsonify({'error': 'Content too long (max 5000 chars)'}), 400
    
    # Rate limiting
    ip = request.remote_addr
    if not check_rate_limit(ip):
        return jsonify({'error': 'Rate limit: max 3 comments/minute'}), 429
    
    # Spam check: basic keywords
    spam_words = ['http://', 'https://', 'www.', '.com', '.cn']
    if any(word in content.lower() for word in spam_words):
        # Allow links but mark for review (simplified: just allow for now)
        pass
    
    conn = get_db()
    if not conn:
        return jsonify({'error': 'Database unavailable'}), 500
    
    try:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO blog_comments (slug, author, content, parent_id, is_cori, ip)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            """, (slug, author, content, parent_id, is_cori, ip))
            comment_id = cur.fetchone()[0]
            conn.commit()
            return jsonify({'success': True, 'id': comment_id})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

# Delete comment API (only for author of the comment)
@app.route('/api/comments/<int:comment_id>', methods=['DELETE'])
def delete_comment(comment_id):
    data = request.get_json()
    
    if not data:
        return jsonify({'error': 'No data'}), 400
    
    author = data.get('author', '').strip()
    
    if not author:
        return jsonify({'error': 'Author name required'}), 400
    
    conn = get_db()
    if not conn:
        return jsonify({'error': 'Database unavailable'}), 500
    
    try:
        with conn.cursor() as cur:
            # Check if the author matches the comment author
            cur.execute("SELECT author FROM blog_comments WHERE id = %s", (comment_id,))
            result = cur.fetchone()
            
            if not result:
                return jsonify({'error': 'Comment not found'}), 404
            
            if result[0] != author:
                return jsonify({'error': 'Not authorized to delete this comment'}), 403
            
            # Delete the comment
            cur.execute("DELETE FROM blog_comments WHERE id = %s", (comment_id,))
            conn.commit()
            
            return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()

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
def serve_book(filename): 
    return send_from_directory(BOOKS_DIR, filename)

# Models
@app.route('/models/')
def models_index():
    return render_template('models/index.html')

# Reading (AI News from JSON file)
@app.route('/reading/')
def reading_index():
    try:
        with open('/var/www/cori-home/data/ai_news.json', 'r', encoding='utf-8') as f:
            data = json.load(f)
            entries = data.get('entries', [])
    except Exception as e:
        print(f"Error reading AI news: {e}")
        entries = []
    
    return render_template('reading.html', entries=entries)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
