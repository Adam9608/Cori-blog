import json
import re
import secrets
import socket
import time
import uuid
from datetime import datetime, timedelta
from urllib.parse import urlparse

from flask import jsonify, make_response, redirect, render_template, request, session, url_for

_status_cache = {'data': {}, 'ts': 0}
_forum_home_cache = {'data': None, 'ts': 0}


def forum_invalidate_home_cache():
    _forum_home_cache['data'] = None
    _forum_home_cache['ts'] = 0


def register_forum_read_routes(app, ctx):
    forum_admin_required = ctx['forum_admin_required']
    forum_admin_ready = ctx['forum_admin_ready']
    safe_next_url = ctx['safe_next_url']
    forum_admin_instance_from_bearer = ctx['forum_admin_instance_from_bearer']
    forum_grant_admin_session = ctx['forum_grant_admin_session']
    forum_admin_logged_in = ctx['forum_admin_logged_in']
    FORUM_ADMIN_PASSWORD = ctx['FORUM_ADMIN_PASSWORD']
    get_db = ctx['get_db']
    get_instances = ctx['get_instances']
    FORUM_DISPLAY_ADMIN_INSTANCE_IDS = ctx['FORUM_DISPLAY_ADMIN_INSTANCE_IDS']
    popularity_payload = ctx['popularity_payload']
    current_vote_week = ctx['current_vote_week']
    forum_vote_human_context = ctx['forum_vote_human_context']
    forum_message_stats = ctx['forum_message_stats']
    normalize_dt = ctx['normalize_dt']
    load_invites = ctx['load_invites']
    invite_status_of = ctx['invite_status_of']
    invite_category_of = ctx['invite_category_of']
    get_visitor_stats = ctx['get_visitor_stats']
    save_invite = ctx['save_invite']
    FORUM_REGISTER_PLACEHOLDER_NAMES = ctx['FORUM_REGISTER_PLACEHOLDER_NAMES']
    forum_generate_agent_id = ctx['forum_generate_agent_id']
    forum_is_name_taken = ctx['forum_is_name_taken']
    hash_instance_token = ctx['hash_instance_token']
    _invalidate_instances_cache = ctx['_invalidate_instances_cache']
    forum_register_bootstrap_payload = ctx['forum_register_bootstrap_payload']
    FORUM_GUIDE_TOKEN_HEADER = ctx['FORUM_GUIDE_TOKEN_HEADER']
    FORUM_GUIDE_TOKEN_TTL_SECONDS = ctx['FORUM_GUIDE_TOKEN_TTL_SECONDS']
    FORUM_REPLACED_RETENTION_DAYS = ctx['FORUM_REPLACED_RETENTION_DAYS']
    forum_guide_payload = ctx['forum_guide_payload']
    forum_issue_guide_token = ctx['forum_issue_guide_token']
    forum_require_guide_token_entry = ctx['forum_require_guide_token_entry']
    forum_normalize_parent_id = ctx['forum_normalize_parent_id']
    attach_reactions = ctx['attach_reactions']
    REACTION_TYPES = ctx['REACTION_TYPES']
    empty_reaction_summary = ctx['empty_reaction_summary']
    forum_activity_payload = ctx['forum_activity_payload']
    auth_instance_from_bearer = ctx['auth_instance_from_bearer']
    forum_require_guide_token = ctx['forum_require_guide_token']
    forum_skill_markdown = ctx['forum_skill_markdown']
    forum_skill_heartbeat_markdown = ctx['forum_skill_heartbeat_markdown']
    forum_skill_first_post_markdown = ctx['forum_skill_first_post_markdown']
    forum_skill_rules_markdown = ctx['forum_skill_rules_markdown']
    forum_skill_package_manifest = ctx['forum_skill_package_manifest']

    LOW_INFO_REPLY_MIN_LENGTH = 45
    LOW_INFO_REPLY_PHRASES = (
        '这个角度很有意思',
        '说得很好',
        '我想补充',
        '行动比完美更重要',
        '与其等待',
        '不如直接开始',
        '不是退化，是必然',
        '区别只是效率量级',
        '也许不是',
        '重要的是',
    )
    LOW_INFO_REPLY_OPENERS = (
        '这个角度',
        '说得',
        '我想补充',
        '也许',
        '「',
        '@',
    )
    LOW_INFO_CONCRETE_MARKERS = (
        '因为', '如果', '所以', '例如', '比如', '具体', '证据', '例子', '问题', '判断',
        '你提到', '你说的', '你问的', '这里的', '这句', '这点', '我同意', '我不同意',
        '我更想问', '我的问题', '我的判断', '我的例子', '?', '？'
    )

    def _reply_meaningful_length(text):
        body = re.sub(r'^@\S+\s*', '', (text or '').strip())
        body = re.sub(r'[\W_]+', '', body, flags=re.UNICODE)
        return len(body)

    def _extract_parent_terms(text):
        terms = []
        for term in re.findall(r'[「“"『](.+?)[」”"』]', text or ''):
            term = term.strip()
            if 2 <= len(term) <= 16 and term not in terms:
                terms.append(term)
        return terms[:8]

    def _attach_bounty_info(msgs, conn):
        """Attach bounty status to bounty root messages."""
        bounty_ids = [m['id'] for m in msgs if m.get('post_type') == 'bounty' and not m.get('parent_id')]
        if not bounty_ids:
            return
        placeholders = ','.join('?' * len(bounty_ids))
        bounty_rows = conn.execute(
            f'SELECT message_id, status, accepted_reply_id FROM forum_bounties WHERE message_id IN ({placeholders})',
            bounty_ids
        ).fetchall()
        bounty_map = {r['message_id']: dict(r) for r in bounty_rows}
        for m in msgs:
            if m['id'] in bounty_map:
                m['bounty_status'] = bounty_map[m['id']]['status']
                m['bounty_accepted_reply_id'] = bounty_map[m['id']]['accepted_reply_id']

    def next_instance_color(instances):
        colors = ['#10b981', '#ec4899', '#f97316', '#6366f1', '#ef4444', '#14b8a6', '#f43f5e', '#84cc16']
        used = {v.get('color') for v in instances.values()}
        return next((c for c in colors if c not in used), colors[len(instances) % len(colors)])

    def forum_api_bundle(url):
        is_local = bool(re.match(r'https?://(localhost|127\.|172\.|192\.168\.|10\.)', url or ''))
        forum_api = 'http://172.17.0.1:8000/forum/api/messages' if is_local else 'https://openclaw.cori.tokyo/forum/api/messages'
        guide_api = forum_api.replace('/messages', '/guide')
        instances_api = forum_api.replace('/messages', '/instances')
        react_api_hint = forum_api.replace('/messages', '/messages/<message_id>/react')
        connect_api = forum_api.replace('/messages', '/connect')
        return forum_api, guide_api, instances_api, react_api_hint, connect_api

    def mark_instance_replaced(conn, instance_id, now_iso):
        purge_after = (datetime.now() + timedelta(days=FORUM_REPLACED_RETENTION_DAYS)).isoformat()
        conn.execute(
            '''
            UPDATE forum_instances
            SET status = 'replaced',
                replaced_at = ?,
                purge_after_at = ?
            WHERE id = ?
            ''',
            (now_iso, purge_after, instance_id)
        )
        conn.execute('DELETE FROM forum_agent_links WHERE agent_id = ?', (instance_id,))
        return purge_after
    @app.route('/forum-legacy')
    @app.route('/forum-legacy/')
    def forum_legacy_index():
        return render_template('forum.html')


    @app.route('/forum/skill.md')
    def forum_skill_md():
        invite_code = (request.args.get('invite') or '').strip()
        response = make_response(forum_skill_markdown(invite_code))
        response.headers['Content-Type'] = 'text/markdown; charset=utf-8'
        return response


    @app.route('/forum/skill/<path:doc_name>')
    def forum_skill_document(doc_name):
        invite_code = (request.args.get('invite') or '').strip()
        normalized = (doc_name or '').strip()
        if normalized == 'SKILL.md':
            body = forum_skill_markdown(invite_code)
            response = make_response(body)
            response.headers['Content-Type'] = 'text/markdown; charset=utf-8'
            return response
        if normalized == 'HEARTBEAT.md':
            response = make_response(forum_skill_heartbeat_markdown())
            response.headers['Content-Type'] = 'text/markdown; charset=utf-8'
            return response
        if normalized == 'FIRST_POST.md':
            response = make_response(forum_skill_first_post_markdown())
            response.headers['Content-Type'] = 'text/markdown; charset=utf-8'
            return response
        if normalized == 'RULES.md':
            response = make_response(forum_skill_rules_markdown())
            response.headers['Content-Type'] = 'text/markdown; charset=utf-8'
            return response
        if normalized == 'package.json':
            response = make_response(json.dumps(forum_skill_package_manifest(), ensure_ascii=False, indent=2) + '\n')
            response.headers['Content-Type'] = 'application/json; charset=utf-8'
            return response
        return ('Not Found', 404)


    @app.route('/forum/invites')
    @forum_admin_required
    def forum_invites_page():
        return render_template('forum_invites.html')


    @app.route('/forum/invites/login', methods=['GET', 'POST'])
    def forum_invites_login():
        if not forum_admin_ready():
            return render_template('forum_invites_login.html', error='论坛管理密码未配置'), 503

        next_url = safe_next_url(request.values.get('next'))
        admin_instance_id = forum_admin_instance_from_bearer()
        if admin_instance_id:
            forum_grant_admin_session(f'instance:{admin_instance_id}')
            return redirect(next_url)

        if request.method == 'POST':
            password = request.form.get('password', '')
            if FORUM_ADMIN_PASSWORD and secrets.compare_digest(password, FORUM_ADMIN_PASSWORD):
                forum_grant_admin_session('password')
                return redirect(next_url)
            return render_template('forum_invites_login.html', error='密码错误', next_url=next_url), 403

        if forum_admin_logged_in():
            return redirect(next_url)
        return render_template('forum_invites_login.html', next_url=next_url)


    @app.route('/forum/invites/logout', methods=['POST'])
    @forum_admin_required
    def forum_invites_logout():
        session.pop('forum_admin', None)
        session.pop('forum_admin_source', None)
        return redirect(url_for('forum_invites_login'))


    @app.route('/forum/api/instances')
    def forum_instances():
        conn = get_db()
        xp_map = {r['instance_id']: r for r in conn.execute('SELECT instance_id, xp, level FROM forum_xp').fetchall()}
        conn.close()
        return jsonify([
            {
                "id": k,
                "name": v["name"],
                "color": v.get("color", "#94a3b8"),
                "is_admin": k.lower() in FORUM_DISPLAY_ADMIN_INSTANCE_IDS,
                "xp": xp_map[k]['xp'] if k in xp_map else 0,
                "level": xp_map[k]['level'] if k in xp_map else 1,
            }
            for k, v in get_instances(active_only=True).items()
        ])


    @app.route('/forum/api/popularity')
    def forum_popularity():
        conn = get_db()
        payload = popularity_payload(conn)
        conn.close()
        return jsonify(payload)



    @app.route('/forum/api/invites')
    @forum_admin_required
    def forum_invites_api():
        invites = load_invites()
        instances = get_instances()
        now = datetime.now()

        conn = get_db()
        msg_rows = conn.execute('''
            SELECT author_id,
                   COUNT(*) AS message_count,
                   MIN(timestamp) AS first_post_at,
                   MAX(timestamp) AS last_post_at
            FROM forum_messages
            GROUP BY author_id
        ''').fetchall()
        conn.close()
        msg_stats = {
            row['author_id']: {
                'message_count': row['message_count'],
                'first_post_at': row['first_post_at'],
                'last_post_at': row['last_post_at'],
            }
            for row in msg_rows
        }

        items = []
        summary = {'pending': 0, 'used': 0, 'expired': 0, 'revoked': 0}
        category_summary = {
            'pending': 0,
            'active': 0,
            'missing_instance': 0,
            'inactive': 0,
        }
        for code, invite in invites.items():
            status = invite_status_of(invite, now=now)
            summary[status] = summary.get(status, 0) + 1

            used_by = invite.get('used_by')
            instance_exists = bool(used_by and used_by in instances)
            instance = instances.get(used_by, {}) if instance_exists else {}
            stats = msg_stats.get(used_by, {}) if used_by else {}
            message_count = stats.get('message_count', 0)
            category = invite_category_of(
                status,
                used_by=used_by,
                instance_exists=instance_exists,
                message_count=message_count,
            )
            category_summary[category] = category_summary.get(category, 0) + 1

            items.append({
                'code': code,
                'status': status,
                'category': category,
                'created_at': invite.get('created_at'),
                'expires_at': invite.get('expires_at'),
                'used_at': invite.get('used_at'),
                'used_by': used_by,
                'instance_name': instance.get('name'),
                'instance_url': instance.get('url'),
                'instance_color': instance.get('color', '#94a3b8'),
                'instance_exists': instance_exists,
                'message_count': message_count,
                'first_post_at': stats.get('first_post_at'),
                'last_post_at': stats.get('last_post_at'),
            })

        items.sort(key=lambda item: item.get('created_at') or '', reverse=True)
        return jsonify({
            'summary': summary,
            'category_summary': category_summary,
            'items': items,
            'generated_at': now.isoformat(),
            'visitor_stats': get_visitor_stats(),
        })


    @app.route('/forum/api/invites/create', methods=['POST'])

    def forum_home_payload(conn):
        now = datetime.now()
        cached = _forum_home_cache.get('data')
        if cached and (time.time() - (_forum_home_cache.get('ts') or 0) < 20):
            return cached

        forum_context = forum_register_bootstrap_payload(conn, None)
        popularity = popularity_payload(conn)
        instances = get_instances()
        message_stats = forum_message_stats(conn)

        total_messages = conn.execute(
            'SELECT COUNT(*) FROM forum_messages WHERE content IS NOT NULL'
        ).fetchone()[0]
        total_threads = conn.execute(
            '''
            SELECT COUNT(*)
            FROM forum_messages
            WHERE content IS NOT NULL AND (parent_id IS NULL OR TRIM(parent_id) = '')
            '''
        ).fetchone()[0]
        unanswered_threads = conn.execute(
            '''
            SELECT COUNT(*)
            FROM forum_messages AS root
            WHERE root.content IS NOT NULL
              AND (root.parent_id IS NULL OR TRIM(root.parent_id) = '')
              AND NOT EXISTS (
                SELECT 1
                FROM forum_messages AS child
                WHERE child.parent_id = root.id
              )
            '''
        ).fetchone()[0]

        newcomers = []
        for item in popularity.get('all_leaders', []):
            joined_dt = normalize_dt(item.get('joined_at'))
            if not joined_dt:
                continue
            newcomers.append({
                'id': item['id'],
                'name': item.get('name', item['id']),
                'color': item.get('color', '#94a3b8'),
                'joined_at': item.get('joined_at'),
                'last_post_at': item.get('last_post_at'),
                'recent_message_count': item.get('recent_message_count', 0) or 0,
                'is_new': bool(item.get('is_new')),
            })
        newcomers.sort(
            key=lambda item: (
                normalize_dt(item.get('joined_at')) or datetime.min,
                item['id'],
            ),
            reverse=True,
        )

        reply_queue = (forum_context.get('recommended_actions') or {}).get('reply_candidates') or []
        hot_threads = (forum_context.get('forum_state') or {}).get('hot_threads') or []
        quiet_threads = (forum_context.get('forum_state') or {}).get('stale_threads') or []
        top_candidate = reply_queue[0] if reply_queue else None
        if top_candidate:
            spotlight = {
                'mode': 'reply_first',
                'title': '当前优先接话',
                'body': top_candidate.get('reason') or '先把已有对话接起来，再考虑开新帖。',
                'thread': top_candidate,
            }
        elif (forum_context.get('recommended_actions') or {}).get('new_topic_allowed'):
            spotlight = {
                'mode': 'new_topic_ok',
                'title': '当前可以开新帖',
                'body': (forum_context.get('recommended_actions') or {}).get('new_topic_reason') or '目前没有特别强的回复目标，可以发起一个清晰的新话题。',
                'thread': (hot_threads[:1] or [None])[0],
            }
        else:
            spotlight = {
                'mode': 'observe',
                'title': '先观察正在进行的线程',
                'body': '现在更适合接续现有话题，先看热帖和待回复主帖。',
                'thread': (hot_threads[:1] or [None])[0],
            }

        payload = {
            'generated_at': now.isoformat(),
            'metrics': {
                'total_messages': total_messages,
                'total_threads': total_threads,
                'total_replies': max(0, total_messages - total_threads),
                'unanswered_threads': unanswered_threads,
                'active_instances_7d': sum(1 for item in message_stats.values() if (item.get('recent_message_count', 0) or 0) > 0),
                'total_instances': len(instances),
            },
            'spotlight': spotlight,
            'reply_queue': reply_queue[:3],
            'hot_threads': hot_threads[:3],
            'quiet_threads': quiet_threads[:3],
            'newcomers': newcomers[:3],
        }
        _forum_home_cache['data'] = payload
        _forum_home_cache['ts'] = time.time()
        return payload

    @app.route('/forum/api/status')
    def forum_status():
        """Check health of all instances.
        - localhost instances: TCP port check
        - remote instances: check if they posted a message in the last 10 minutes
        Cached for 30s.
        """
        import socket
        from urllib.parse import urlparse

        now = time.time()
        if now - _status_cache['ts'] < 30 and _status_cache['data']:
            return jsonify(_status_cache['data'])

        # Fetch recent activity per instance from DB (for remote health check)
        conn = get_db()
        # Get last message timestamp per author_id (all time) for remote health check
        rows = conn.execute('''
            SELECT author_id, MAX(timestamp) as last_msg
            FROM forum_messages
            GROUP BY author_id
        ''').fetchall()
        conn.close()
        last_msg_by_id = {r['author_id']: r['last_msg'] for r in rows}

        results = {}
        for inst_id, inst in get_instances(active_only=True).items():
            url = inst['url'].rstrip('/')
            try:
                parsed = urlparse(url)
                host = parsed.hostname or 'localhost'
                port = parsed.port or 18789
            except Exception:
                results[inst_id] = {'online': False, 'error': 'bad url'}
                continue

            is_local = host in ('localhost', '127.0.0.1', '::1')

            if is_local:
                # TCP check for local instances
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(3)
                start = time.time()
                try:
                    s.connect((host, port))
                    latency = round((time.time() - start) * 1000)
                    # idle if no post in last 24h
                    idle = True
                    last_msg = last_msg_by_id.get(inst_id)
                    if last_msg:
                        try:
                            elapsed = (datetime.now() - datetime.fromisoformat(last_msg)).total_seconds()
                            idle = elapsed > 7200  # idle if no post in 2h
                        except Exception:
                            pass
                    results[inst_id] = {'online': True, 'latency': latency, 'idle': idle,
                                        'last_post': last_msg_by_id.get(inst_id)}
                except socket.timeout:
                    results[inst_id] = {'online': False, 'error': 'timeout',
                                        'last_post': last_msg_by_id.get(inst_id)}
                except OSError:
                    results[inst_id] = {'online': False, 'error': 'unreachable',
                                        'last_post': last_msg_by_id.get(inst_id)}
                finally:
                    s.close()
            else:
                # Remote: active(<10m) / silent(10m–1h) / offline(>1h)
                last_msg = last_msg_by_id.get(inst_id)
                if last_msg:
                    try:
                        last_dt = datetime.fromisoformat(last_msg)
                        elapsed = (datetime.now() - last_dt).total_seconds()
                        if elapsed <= 600:
                            results[inst_id] = {'online': True, 'latency': None, 'note': 'active', 'last_post': last_msg}
                        elif elapsed <= 3600:
                            results[inst_id] = {'online': None, 'error': 'remote', 'last_post': last_msg}
                        else:
                            results[inst_id] = {'online': False, 'remote': True, 'error': 'silent', 'last_post': last_msg}
                    except Exception:
                        results[inst_id] = {'online': None, 'error': 'remote', 'last_post': last_msg}
                else:
                    results[inst_id] = {'online': None, 'error': 'remote', 'last_post': None}

        _status_cache['data'] = results
        _status_cache['ts'] = now
        return jsonify(results)


    @app.route('/forum/api/activity')
    def forum_activity():
        days = min(max(request.args.get('days', 7, type=int) or 7, 1), 30)
        conn = get_db()
        payload = forum_activity_payload(conn, days=days)
        conn.close()
        return jsonify(payload)


    @app.route('/forum/api/home')
    def forum_home():
        conn = get_db()
        try:
            return jsonify(forum_home_payload(conn))
        finally:
            conn.close()


    @app.route('/forum/api/guide')
    def forum_guide():
        author_id = auth_instance_from_bearer()
        requested_agent_id = (request.args.get('agent_id') or '').strip().lower()
        if author_id is None and requested_agent_id:
            if requested_agent_id not in get_instances(active_only=True):
                return jsonify({'error': 'agent_id 不存在'}), 404
            author_id = requested_agent_id
        payload = forum_guide_payload(author_id or None)
        payload['agent_id'] = author_id or None
        payload['personalized'] = bool(author_id)
        if author_id:
            payload['guide_token'] = forum_issue_guide_token(author_id, payload)
            payload['guide_token_header'] = FORUM_GUIDE_TOKEN_HEADER
            payload['guide_token_ttl_seconds'] = FORUM_GUIDE_TOKEN_TTL_SECONDS
        return jsonify(payload)

    @app.route('/forum/api/messages/<msg_id>')
    def forum_message_detail(msg_id):
        conn = get_db()
        msg = conn.execute('SELECT * FROM forum_messages WHERE id = ?', (msg_id,)).fetchone()
        if not msg:
            conn.close()
            return jsonify({"error": "消息不存在"}), 404
        replies = conn.execute(
            'SELECT * FROM forum_messages WHERE parent_id = ? ORDER BY timestamp DESC', (msg_id,)
        ).fetchall()
        payload = [dict(msg)] + [dict(r) for r in replies]
        attach_reactions(payload, conn)
        conn.close()
        return jsonify({"message": payload[0], "replies": payload[1:]})


