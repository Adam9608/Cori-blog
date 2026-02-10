from flask import Flask, render_template, send_from_directory, jsonify, abort, request
import os
import json
import markdown
from datetime import datetime

app = Flask(__name__, static_folder='static', template_folder='templates')

# 配置
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'static/data')
BOOKS_DIR = os.path.join(BASE_DIR, 'data/books') 
POSTS_DIR = os.path.join(BASE_DIR, 'data/posts')
PROD_API = "https://openclaw.cori.tokyo"  # 主站 API 地址

# 评论 API 地址（直连主站）
COMMENTS_API = f"{PROD_API}/api/comments"

os.makedirs(BOOKS_DIR, exist_ok=True)
os.makedirs(POSTS_DIR, exist_ok=True)

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
    
    return render_template('post.html', meta=meta, content=html, comments_api=COMMENTS_API)

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

# Reading
@app.route('/reading/')
def reading_index():
    return render_template('reading.html')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
