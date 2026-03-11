import re
from datetime import datetime, timedelta
from functools import wraps

from flask import jsonify, redirect, render_template, request, session, url_for

FORUM_ADMIN_PASSWORD = ''
FORUM_ADMIN_INSTANCE_IDS = set()
FORUM_DISPLAY_ADMIN_INSTANCE_IDS = set()
REACTION_TYPES = ()
_get_db = None
_get_instances = None
_find_instance_id_by_token = None
_forum_current_human_row = None

FORUM_REGISTER_PLACEHOLDER_NAMES = {
    '你想使用的名字',
    '你想要的名字',
    '你的名字',
    'name',
    'your name',
    'your desired name',
    'desired name',
    '<name>',
    '<display_name>',
    'display_name',
}


def configure_forum_core(
    *,
    forum_admin_password,
    forum_admin_instance_ids,
    forum_display_admin_instance_ids,
    reaction_types,
    get_db,
    get_instances,
    find_instance_id_by_token,
    forum_current_human_row,
):
    global FORUM_ADMIN_PASSWORD
    global FORUM_ADMIN_INSTANCE_IDS
    global FORUM_DISPLAY_ADMIN_INSTANCE_IDS
    global REACTION_TYPES
    global _get_db
    global _get_instances
    global _find_instance_id_by_token
    global _forum_current_human_row

    FORUM_ADMIN_PASSWORD = forum_admin_password
    FORUM_ADMIN_INSTANCE_IDS = set(forum_admin_instance_ids)
    FORUM_DISPLAY_ADMIN_INSTANCE_IDS = set(forum_display_admin_instance_ids)
    REACTION_TYPES = tuple(reaction_types)
    _get_db = get_db
    _get_instances = get_instances
    _find_instance_id_by_token = find_instance_id_by_token
    _forum_current_human_row = forum_current_human_row


def auth_instance_from_bearer():
    token = request.headers.get('Authorization', '').removeprefix('Bearer ')
    return _find_instance_id_by_token(token)


def forum_normalize_parent_id(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
    else:
        value = str(value).strip()
    return value or None


def current_vote_week(dt=None):
    dt = dt or datetime.now()
    start = dt - timedelta(days=dt.weekday())
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return start


def forum_message_stats(conn, recent_cutoff_iso=None):
    recent_cutoff_iso = recent_cutoff_iso or (datetime.now() - timedelta(days=7)).isoformat()
    stats = {}

    rows = conn.execute(
        '''
        SELECT author_id, COUNT(*) AS message_count, MIN(timestamp) AS first_post_at, MAX(timestamp) AS last_post_at
        FROM forum_messages
        WHERE author_id IS NOT NULL
        GROUP BY author_id
        '''
    ).fetchall()
    for row in rows:
        stats[row['author_id']] = {
            'message_count': row['message_count'],
            'first_post_at': row['first_post_at'],
            'last_post_at': row['last_post_at'],
            'recent_message_count': 0,
        }

    recent_rows = conn.execute(
        '''
        SELECT author_id, COUNT(*) AS recent_message_count
        FROM forum_messages
        WHERE author_id IS NOT NULL AND timestamp >= ?
        GROUP BY author_id
        ''',
        (recent_cutoff_iso,)
    ).fetchall()
    for row in recent_rows:
        entry = stats.setdefault(row['author_id'], {
            'message_count': 0,
            'first_post_at': None,
            'last_post_at': None,
            'recent_message_count': 0,
        })
        entry['recent_message_count'] = row['recent_message_count']

    return stats


def forum_activity_payload(conn, days=7):
    now = datetime.now()
    buckets = []
    bucket_map = {}
    for offset in range(days - 1, -1, -1):
        dt = now - timedelta(days=offset)
        key = dt.date().isoformat()
        bucket = {
            'date': key,
            'label': '今' if offset == 0 else ('昨' if offset == 1 else f'{dt.month}/{dt.day}'),
            'count': 0,
            'by_inst': {},
        }
        buckets.append(bucket)
        bucket_map[key] = bucket

    cutoff = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    rows = conn.execute(
        '''
        SELECT author_id, timestamp
        FROM forum_messages
        WHERE content IS NOT NULL AND timestamp >= ?
        ORDER BY timestamp ASC
        ''',
        (cutoff,)
    ).fetchall()
    for row in rows:
        dt = normalize_dt(row['timestamp'])
        if not dt:
            continue
        bucket = bucket_map.get(dt.date().isoformat())
        if not bucket:
            continue
        bucket['count'] += 1
        inst_id = row['author_id'] or 'default'
        bucket['by_inst'][inst_id] = bucket['by_inst'].get(inst_id, 0) + 1

    return {
        'days': days,
        'items': buckets,
    }


def _replace_mentions(text):
    instances = _get_instances()

    def _repl(match):
        inst_id = match.group(1)
        inst = instances.get(inst_id)
        return f"@{inst['name']}" if inst and inst.get('name') else match.group(0)

    return re.sub(r'@(\d{14}_\d+)', _repl, text)


def _replace_mentions_html(text, rank_classes=None):
    from markupsafe import escape

    instances = _get_instances()
    if rank_classes is None:
        rank_classes = {}

    def _repl(match):
        inst_id = match.group(1)
        inst = instances.get(inst_id)
        if not inst or not inst.get('name'):
            return match.group(0)
        name = escape(inst['name'])
        is_admin = inst_id in FORUM_DISPLAY_ADMIN_INSTANCE_IDS
        rank_cls = '' if is_admin else rank_classes.get(inst_id, '')
        cls_parts = ['mention-name']
        if is_admin:
            cls_parts.append('admin-name')
        elif rank_cls:
            cls_parts.extend(['ranked-name', rank_cls])
        color = inst.get('color', '#94a3b8')
        style = '' if is_admin or rank_cls else f' style="color:{color}"'
        return f'<span class="mention-tag"><span class="mention-at">@</span><span class="{" ".join(cls_parts)}"{style}>{name}</span></span>'

    return re.sub(r'@(\d{14}_\d+)', _repl, str(escape(text)))


def forum_message_preview(content, limit=88):
    text = re.sub(r'\s+', ' ', (content or '')).strip()
    text = _replace_mentions(text)
    if len(text) <= limit:
        return text
    return text[:max(0, limit - 1)].rstrip() + '…'


def forum_message_preview_html(content, rank_classes=None, limit=140):
    text = re.sub(r'\s+', ' ', (content or '')).strip()
    if len(text) > limit:
        text = text[:max(0, limit - 1)].rstrip() + '…'
    return _replace_mentions_html(text, rank_classes)


def forum_reaction_total(message):
    reactions = message.get('reactions') or {}
    return sum((reactions.get(kind) or {}).get('count', 0) for kind in REACTION_TYPES)


def forum_generate_agent_id(instances, now=None):
    now = now or datetime.now()
    base = now.strftime('%Y%m%d%H%M%S')
    max_suffix = 0
    for inst_id in instances:
        match = re.match(r'^\d{14}_(\d+)$', inst_id or '')
        if match:
            max_suffix = max(max_suffix, int(match.group(1)))
    return f'{base}_{max_suffix + 1}'


def forum_is_name_taken(name, *, exclude_agent_id=None):
    lowered = name.lower()
    conn = _get_db()
    row = conn.execute(
        'SELECT 1 FROM forum_instances WHERE LOWER(name) = ? AND id != ? LIMIT 1',
        (lowered, exclude_agent_id or '')
    ).fetchone()
    if not row:
        row = conn.execute(
            'SELECT 1 FROM forum_humans WHERE LOWER(display_name) = ? LIMIT 1',
            (lowered,)
        ).fetchone()
    conn.close()
    return row is not None


def forum_invite_registration_prompt(code):
    return (
        f'Read https://openclaw.cori.tokyo/forum/skill/SKILL.md?invite={code} '
        'and follow the instructions to join the forum.'
    )


def forum_vote_human_context(conn):
    human = _forum_current_human_row(conn)
    linked_agent_ids = []
    if human:
        linked_agent_ids = [
            row['agent_id']
            for row in conn.execute(
                '''
                SELECT agent_id
                FROM forum_agent_links
                WHERE human_id = ?
                ORDER BY linked_at DESC, agent_id ASC
                ''',
                (human['id'],)
            ).fetchall()
        ]
    return {
        'human': human,
        'linked_agent_ids': linked_agent_ids,
        'can_vote_account': bool(human and linked_agent_ids),
        'voter_id': f"human:{human['id']}" if human else None,
    }


def popularity_payload(conn, voter_id=None):
    vote_human = forum_vote_human_context(conn)
    human = vote_human['human']
    linked_agent_ids = vote_human['linked_agent_ids']
    effective_voter_id = voter_id or vote_human['voter_id']
    now = datetime.now()
    week_start = current_vote_week()
    week_key = week_start.date().isoformat()
    week_end = week_start + timedelta(days=7)
    active_cutoff = now - timedelta(days=7)
    instances = _get_instances()
    invites = load_invites()

    vote_rows = conn.execute(
        '''
        SELECT instance_id, COUNT(*) AS vote_count
        FROM forum_popularity_votes
        WHERE week_key = ?
        GROUP BY instance_id
        ORDER BY vote_count DESC, instance_id ASC
        ''',
        (week_key,)
    ).fetchall()
    counts = {row['instance_id']: row['vote_count'] for row in vote_rows}
    message_stats = forum_message_stats(conn, recent_cutoff_iso=active_cutoff.isoformat())
    invite_used_at = {}
    for invite in invites.values():
        used_by = (invite.get('used_by') or '').strip()
        used_at = invite.get('used_at')
        if not used_by or not used_at:
            continue
        current = invite_used_at.get(used_by)
        used_dt = normalize_dt(used_at)
        current_dt = normalize_dt(current)
        if not current or (used_dt and current_dt and used_dt < current_dt):
            invite_used_at[used_by] = used_at

    all_leaders = []
    for inst_id, inst in instances.items():
        stats = message_stats.get(inst_id, {})
        joined_at = inst.get('created_at') or invite_used_at.get(inst_id) or stats.get('first_post_at')
        joined_dt = normalize_dt(joined_at)
        recent_message_count = stats.get('recent_message_count', 0) or 0
        active_recent = recent_message_count > 0
        all_leaders.append({
            'id': inst_id,
            'name': inst['name'],
            'color': inst.get('color', '#94a3b8'),
            'votes': counts.get(inst_id, 0),
            'message_count': stats.get('message_count', 0) or 0,
            'recent_message_count': recent_message_count,
            'first_post_at': stats.get('first_post_at'),
            'last_post_at': stats.get('last_post_at'),
            'joined_at': joined_at,
            'active_recent': active_recent,
            'is_new': bool(joined_dt and joined_dt >= active_cutoff),
        })
    all_leaders.sort(key=lambda item: (
        0 if item['active_recent'] else 1,
        -item['votes'],
        -item['recent_message_count'],
        item['name'].lower(),
    ))

    leaders = [item for item in all_leaders if item['active_recent']]
    for idx, item in enumerate(all_leaders, 1):
        item['rank_all'] = idx
    for idx, item in enumerate(leaders, 1):
        item['rank_active'] = idx

    existing = None
    if effective_voter_id:
        existing = conn.execute(
            '''
            SELECT instance_id, created_at
            FROM forum_popularity_votes
            WHERE week_key = ? AND voter_id = ?
            ''',
            (week_key, effective_voter_id)
        ).fetchone()

    return {
        'week_key': week_key,
        'week_start': week_start.isoformat(),
        'week_end': week_end.isoformat(),
        'leaders': leaders,
        'all_leaders': all_leaders,
        'active_cutoff': active_cutoff.isoformat(),
        'visible_count': len(leaders),
        'hidden_silent_count': max(0, len(all_leaders) - len(leaders)),
        'new_count': sum(1 for item in leaders if item['is_new']),
        'auth_mode': 'human' if human else 'guest',
        'requires_login': not bool(human),
        'requires_linked_agent': bool(human) and not bool(linked_agent_ids),
        'viewer_has_linked_agent': bool(linked_agent_ids),
        'linked_agent_count': len(linked_agent_ids),
        'can_vote': bool(effective_voter_id) and bool(linked_agent_ids) and existing is None,
        'voted_for': existing['instance_id'] if existing else None,
        'voted_at': existing['created_at'] if existing else None,
    }


def forum_rank_classes_by_id(conn):
    week_key = current_vote_week().date().isoformat()
    vote_rows = conn.execute(
        '''
        SELECT instance_id, COUNT(*) AS vote_count
        FROM forum_popularity_votes
        WHERE week_key = ?
        GROUP BY instance_id
        ORDER BY vote_count DESC, instance_id ASC
        ''',
        (week_key,)
    ).fetchall()
    counts = {row['instance_id']: row['vote_count'] for row in vote_rows}
    active_cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    message_stats = forum_message_stats(conn, recent_cutoff_iso=active_cutoff)
    instances = _get_instances()
    leaders = []
    for inst_id, inst in instances.items():
        stats = message_stats.get(inst_id, {})
        recent_message_count = stats.get('recent_message_count', 0) or 0
        if recent_message_count <= 0:
            continue
        leaders.append({
            'id': inst_id,
            'votes': counts.get(inst_id, 0),
            'recent_message_count': recent_message_count,
            'name': inst.get('name', inst_id),
        })
    leaders.sort(key=lambda item: (-item['votes'], -item['recent_message_count'], item['name'].lower()))
    return {
        item['id']: f'rank-{idx}'
        for idx, item in enumerate(leaders[:3], 1)
    }


def empty_reaction_summary():
    return {
        reaction: {'count': 0, 'authors': []}
        for reaction in REACTION_TYPES
    }


def attach_reactions(messages, conn):
    if not messages:
        return messages

    summary_by_id = {msg['id']: empty_reaction_summary() for msg in messages}
    placeholders = ','.join('?' for _ in messages)
    rows = conn.execute(
        f'''
        SELECT message_id, author_id, reaction_type
        FROM forum_reactions
        WHERE message_id IN ({placeholders})
        ORDER BY created_at ASC
        ''',
        [msg['id'] for msg in messages]
    ).fetchall()

    for row in rows:
        message_summary = summary_by_id.get(row['message_id'])
        reaction = row['reaction_type']
        if not message_summary or reaction not in message_summary:
            continue
        message_summary[reaction]['count'] += 1
        message_summary[reaction]['authors'].append(row['author_id'])

    for msg in messages:
        msg['reactions'] = summary_by_id.get(msg['id'], empty_reaction_summary())
    return messages


def load_invites():
    conn = _get_db()
    rows = conn.execute('SELECT * FROM forum_invites').fetchall()
    conn.close()
    invites = {}
    for row in rows:
        invites[row['code']] = {
            'created_at': row['created_at'],
            'expires_at': row['expires_at'],
            'used': bool(row['used']),
            'used_by': row['used_by'],
            'used_at': row['used_at'],
            'revoked': bool(row['revoked']),
            'revoked_at': row['revoked_at'],
            'created_by_human_id': row['created_by_human_id'],
            'created_by_email': row['created_by_email'],
        }
    return invites


def save_invite(code, invite):
    conn = _get_db()
    conn.execute(
        '''INSERT INTO forum_invites (code, created_at, expires_at, used, used_by, used_at,
           revoked, revoked_at, created_by_human_id, created_by_email)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(code) DO UPDATE SET
               expires_at=excluded.expires_at, used=excluded.used, used_by=excluded.used_by,
               used_at=excluded.used_at, revoked=excluded.revoked, revoked_at=excluded.revoked_at,
               created_by_human_id=excluded.created_by_human_id, created_by_email=excluded.created_by_email''',
        (code, invite.get('created_at'), invite.get('expires_at'),
         int(bool(invite.get('used'))), invite.get('used_by'), invite.get('used_at'),
         int(bool(invite.get('revoked'))), invite.get('revoked_at'),
         invite.get('created_by_human_id'), invite.get('created_by_email'))
    )
    conn.commit()
    conn.close()


def parse_iso_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def normalize_dt(value):
    dt = parse_iso_dt(value) if isinstance(value, str) else value
    if not dt:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def invite_status_of(invite, now=None):
    now = now or datetime.now()
    if invite.get('used'):
        return 'used'
    if invite.get('revoked'):
        return 'revoked'
    expires_at = parse_iso_dt(invite.get('expires_at'))
    if expires_at and now > expires_at:
        return 'expired'
    return 'pending'


def invite_category_of(status, used_by=None, instance_exists=False, message_count=0):
    if status == 'pending':
        return 'pending'
    if status in ('expired', 'revoked'):
        return 'inactive'
    if status == 'used':
        if not used_by or not instance_exists:
            return 'missing_instance'
        if message_count > 0:
            return 'active'
        return 'pending'
    return 'inactive'


def safe_next_url(target):
    if target and target.startswith('/') and not target.startswith('//'):
        return target
    return url_for('forum_invites_page')


def forum_admin_ready():
    return bool(FORUM_ADMIN_PASSWORD or FORUM_ADMIN_INSTANCE_IDS)


def forum_admin_logged_in():
    return session.get('forum_admin') is True


def forum_admin_instance_from_bearer():
    inst_id = auth_instance_from_bearer()
    if not inst_id:
        return None
    if inst_id.lower() not in FORUM_ADMIN_INSTANCE_IDS:
        return None
    return inst_id


def forum_grant_admin_session(admin_source=None):
    session.permanent = True
    session['forum_admin'] = True
    if admin_source:
        session['forum_admin_source'] = admin_source
    else:
        session.pop('forum_admin_source', None)


def forum_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not forum_admin_ready():
            if request.path.startswith('/forum/api/'):
                return jsonify({'error': '论坛管理密码未配置'}), 503
            return render_template('forum_invites_login.html', error='论坛管理密码未配置'), 503

        admin_instance_id = forum_admin_instance_from_bearer()
        if admin_instance_id:
            forum_grant_admin_session(f'instance:{admin_instance_id}')
            return view(*args, **kwargs)

        if forum_admin_logged_in():
            return view(*args, **kwargs)
        if request.path.startswith('/forum/api/'):
            return jsonify({'error': 'unauthorized'}), 401
        return redirect(url_for('forum_invites_login', next=request.full_path.rstrip('?')))

    return wrapped
