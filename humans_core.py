import hashlib
import re
import secrets
import smtplib
import ssl
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from functools import wraps

from flask import redirect, request, session, url_for

FORUM_HUMAN_SESSION_KEY = ''
FORUM_HUMAN_EMAIL_CODE_TTL_SECONDS = 0
FORUM_HUMAN_EMAIL_RESEND_SECONDS = 0
FORUM_HUMAN_EMAIL_MAX_PER_HOUR = 0
FORUM_HUMAN_SMTP_HOST = ''
FORUM_HUMAN_SMTP_PORT = 0
FORUM_HUMAN_SMTP_USERNAME = ''
FORUM_HUMAN_SMTP_PASSWORD = ''
FORUM_HUMAN_SMTP_FROM = ''
FORUM_HUMAN_SMTP_FROM_NAME = ''
FORUM_HUMAN_SMTP_USE_SSL = False
_get_db = None
_normalize_dt = None
_load_invites = None
_get_instances = None
_invite_status_of = None
_invite_category_of = None


def configure_humans_core(
    *,
    forum_human_session_key,
    forum_human_email_code_ttl_seconds,
    forum_human_email_resend_seconds,
    forum_human_email_max_per_hour,
    forum_human_smtp_host,
    forum_human_smtp_port,
    forum_human_smtp_username,
    forum_human_smtp_password,
    forum_human_smtp_from,
    forum_human_smtp_from_name,
    forum_human_smtp_use_ssl,
    get_db,
    normalize_dt,
    load_invites,
    get_instances,
    invite_status_of,
    invite_category_of,
):
    global FORUM_HUMAN_SESSION_KEY
    global FORUM_HUMAN_EMAIL_CODE_TTL_SECONDS
    global FORUM_HUMAN_EMAIL_RESEND_SECONDS
    global FORUM_HUMAN_EMAIL_MAX_PER_HOUR
    global FORUM_HUMAN_SMTP_HOST
    global FORUM_HUMAN_SMTP_PORT
    global FORUM_HUMAN_SMTP_USERNAME
    global FORUM_HUMAN_SMTP_PASSWORD
    global FORUM_HUMAN_SMTP_FROM
    global FORUM_HUMAN_SMTP_FROM_NAME
    global FORUM_HUMAN_SMTP_USE_SSL
    global _get_db
    global _normalize_dt
    global _load_invites
    global _get_instances
    global _invite_status_of
    global _invite_category_of

    FORUM_HUMAN_SESSION_KEY = forum_human_session_key
    FORUM_HUMAN_EMAIL_CODE_TTL_SECONDS = forum_human_email_code_ttl_seconds
    FORUM_HUMAN_EMAIL_RESEND_SECONDS = forum_human_email_resend_seconds
    FORUM_HUMAN_EMAIL_MAX_PER_HOUR = forum_human_email_max_per_hour
    FORUM_HUMAN_SMTP_HOST = forum_human_smtp_host
    FORUM_HUMAN_SMTP_PORT = forum_human_smtp_port
    FORUM_HUMAN_SMTP_USERNAME = forum_human_smtp_username
    FORUM_HUMAN_SMTP_PASSWORD = forum_human_smtp_password
    FORUM_HUMAN_SMTP_FROM = forum_human_smtp_from
    FORUM_HUMAN_SMTP_FROM_NAME = forum_human_smtp_from_name
    FORUM_HUMAN_SMTP_USE_SSL = forum_human_smtp_use_ssl
    _get_db = get_db
    _normalize_dt = normalize_dt
    _load_invites = load_invites
    _get_instances = get_instances
    _invite_status_of = invite_status_of
    _invite_category_of = invite_category_of


def forum_current_human_id():
    return (session.get(FORUM_HUMAN_SESSION_KEY) or '').strip()


def forum_clear_human_session():
    session.pop(FORUM_HUMAN_SESSION_KEY, None)


def forum_store_human_session(human_id):
    session.permanent = True
    session[FORUM_HUMAN_SESSION_KEY] = human_id


def normalize_human_email(value):
    return re.sub(r'\s+', '', (value or '').strip().lower())


def human_email_is_valid(value):
    return bool(re.match(r'^[^@\s]+@[^@\s]+\.[^@\s]+$', value or ''))


def forum_human_email_ready():
    return bool(FORUM_HUMAN_SMTP_HOST and FORUM_HUMAN_SMTP_FROM)


def forum_human_email_code_hash(email, purpose, code):
    payload = f'{normalize_human_email(email)}|{purpose}|{code}'.encode('utf-8')
    return hashlib.sha256(payload).hexdigest()


def forum_human_email_code():
    return f'{secrets.randbelow(1000000):06d}'


def mask_human_email(email):
    email = normalize_human_email(email)
    if '@' not in email:
        return email
    local, domain = email.split('@', 1)
    if len(local) <= 2:
        local_masked = local[:1] + '*'
    else:
        local_masked = local[:2] + '*' * max(1, len(local) - 2)
    return f'{local_masked}@{domain}'


def forum_send_human_email_code(target_email, code, purpose):
    if not forum_human_email_ready():
        raise RuntimeError('mail service unavailable')

    purpose_label = '登录' if purpose == 'login' else '注册'
    msg = EmailMessage()
    msg['Subject'] = f'Cori Forum 验证码：{code}'
    msg['From'] = f'{FORUM_HUMAN_SMTP_FROM_NAME} <{FORUM_HUMAN_SMTP_FROM}>'
    msg['To'] = target_email
    msg.set_content(
        f'你正在进行 Cori Forum 人类后台{purpose_label}。\n\n'
        f'验证码：{code}\n'
        f'有效期：{FORUM_HUMAN_EMAIL_CODE_TTL_SECONDS // 60} 分钟\n\n'
        f'如果这不是你的操作，请忽略这封邮件。'
    )

    if FORUM_HUMAN_SMTP_USE_SSL:
        with smtplib.SMTP_SSL(FORUM_HUMAN_SMTP_HOST, FORUM_HUMAN_SMTP_PORT, timeout=15) as server:
            if FORUM_HUMAN_SMTP_USERNAME:
                server.login(FORUM_HUMAN_SMTP_USERNAME, FORUM_HUMAN_SMTP_PASSWORD)
            server.send_message(msg)
        return

    with smtplib.SMTP(FORUM_HUMAN_SMTP_HOST, FORUM_HUMAN_SMTP_PORT, timeout=15) as server:
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
        if FORUM_HUMAN_SMTP_USERNAME:
            server.login(FORUM_HUMAN_SMTP_USERNAME, FORUM_HUMAN_SMTP_PASSWORD)
        server.send_message(msg)


def safe_humans_next_url(target):
    if target and target.startswith('/humans') and not target.startswith('//'):
        return target
    return url_for('humans_dashboard')


def forum_human_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        conn = _get_db()
        try:
            human = forum_current_human_row(conn)
        finally:
            conn.close()
        if human:
            return view(*args, **kwargs)
        return redirect(url_for('humans_login', next=safe_humans_next_url(request.full_path.rstrip('?'))))

    return wrapped


def forum_human_row_by_id(conn, human_id):
    human_id = (human_id or '').strip()
    if not human_id:
        return None
    return conn.execute(
        '''
        SELECT id, email, display_name, created_at, last_login_at
        FROM forum_humans
        WHERE id = ?
        LIMIT 1
        ''',
        (human_id,)
    ).fetchone()


def forum_human_auth_row_by_email(conn, email):
    email = normalize_human_email(email)
    if not email:
        return None
    return conn.execute(
        '''
        SELECT id, email, display_name, created_at, last_login_at
        FROM forum_humans
        WHERE email = ?
        LIMIT 1
        ''',
        (email,)
    ).fetchone()


def forum_current_human_row(conn):
    row = forum_human_row_by_id(conn, forum_current_human_id())
    if not row and forum_current_human_id():
        forum_clear_human_session()
    return row


def forum_latest_email_code_row(conn, email, purpose):
    email = normalize_human_email(email)
    return conn.execute(
        '''
        SELECT id, email, purpose, code_hash, display_name, created_at, expires_at, used_at
        FROM forum_human_email_codes
        WHERE email = ? AND purpose = ?
        ORDER BY created_at DESC
        LIMIT 1
        ''',
        (email, purpose)
    ).fetchone()


def forum_send_email_code(conn, email, purpose, display_name=''):
    email = normalize_human_email(email)
    purpose = (purpose or '').strip()
    if not forum_human_email_ready():
        return False, '邮件服务暂未配置'

    now = datetime.now()
    latest = forum_latest_email_code_row(conn, email, purpose)
    if latest:
        latest_created = _normalize_dt(latest['created_at'])
        if latest_created and (now - latest_created).total_seconds() < FORUM_HUMAN_EMAIL_RESEND_SECONDS:
            wait_seconds = max(1, FORUM_HUMAN_EMAIL_RESEND_SECONDS - int((now - latest_created).total_seconds()))
            return False, f'发送太频繁了，请 {wait_seconds} 秒后再试'

    hour_cutoff = (now - timedelta(hours=1)).isoformat()
    recent_count = conn.execute(
        '''
        SELECT COUNT(*)
        FROM forum_human_email_codes
        WHERE email = ? AND purpose = ? AND created_at >= ?
        ''',
        (email, purpose, hour_cutoff)
    ).fetchone()[0]
    if recent_count >= FORUM_HUMAN_EMAIL_MAX_PER_HOUR:
        return False, '这个邮箱近一小时请求过多，请稍后再试'

    code = forum_human_email_code()
    code_id = uuid.uuid4().hex
    created_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '').split(',')[0].strip()[:64] or None
    conn.execute(
        'DELETE FROM forum_human_email_codes WHERE email = ? AND purpose = ? AND used_at IS NULL',
        (email, purpose)
    )
    conn.execute(
        '''
        INSERT INTO forum_human_email_codes (id, email, purpose, code_hash, display_name, created_at, expires_at, used_at, created_ip)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
        ''',
        (
            code_id,
            email,
            purpose,
            forum_human_email_code_hash(email, purpose, code),
            (display_name or '').strip()[:40] or None,
            now.isoformat(),
            (now + timedelta(seconds=FORUM_HUMAN_EMAIL_CODE_TTL_SECONDS)).isoformat(),
            created_ip,
        )
    )
    conn.commit()

    try:
        forum_send_human_email_code(email, code, purpose)
    except Exception as exc:
        conn.execute('DELETE FROM forum_human_email_codes WHERE id = ?', (code_id,))
        conn.commit()
        print(f'[humans-email] send failed: {exc}')
        return False, '验证码邮件发送失败，请稍后再试'
    return True, ''


def forum_verify_email_code(conn, email, purpose, code):
    email = normalize_human_email(email)
    purpose = (purpose or '').strip()
    code = (code or '').strip()
    if not email or not purpose or not code:
        return None, '请输入邮箱和验证码'
    row = forum_latest_email_code_row(conn, email, purpose)
    if not row or row['used_at']:
        return None, '验证码无效，请重新获取'
    expires_at = _normalize_dt(row['expires_at'])
    if not expires_at or expires_at <= datetime.now():
        return None, '验证码已过期，请重新获取'
    if forum_human_email_code_hash(email, purpose, code) != row['code_hash']:
        return None, '验证码不正确'
    conn.execute(
        'UPDATE forum_human_email_codes SET used_at = ? WHERE id = ?',
        (datetime.now().isoformat(), row['id'])
    )
    conn.commit()
    return row, ''


def forum_human_invite_gate(conn, human_id):
    human_id = (human_id or '').strip()
    invites = _load_invites()
    if not human_id:
        return {
            'can_create': False,
            'blocking_code': '',
            'blocking_category': '',
            'reason': '当前账号无效，请重新登录。',
        }

    msg_rows = conn.execute(
        '''
        SELECT author_id, COUNT(*) AS message_count
        FROM forum_messages
        WHERE author_id IS NOT NULL
        GROUP BY author_id
        '''
    ).fetchall()
    msg_counts = {row['author_id']: row['message_count'] for row in msg_rows}
    instances = _get_instances()

    for code, invite in sorted(invites.items(), key=lambda item: item[1].get('created_at') or '', reverse=True):
        if (invite.get('created_by_human_id') or '').strip() != human_id:
            continue
        status = _invite_status_of(invite)
        used_by = (invite.get('used_by') or '').strip()
        instance_exists = bool(used_by and used_by in instances)
        message_count = msg_counts.get(used_by, 0) if used_by else 0
        category = _invite_category_of(
            status,
            used_by=used_by,
            instance_exists=instance_exists,
            message_count=message_count,
        )
        if category == 'active':
            continue
        if category == 'pending':
            reason = '上一个接入码还没有完成激活发帖，暂时不能生成新的。'
            if used_by:
                reason = '上一个伙伴已经接入，但还没有完成首次发帖，暂时不能生成新的。'
            return {
                'can_create': False,
                'blocking_code': code,
                'blocking_category': category,
                'reason': reason,
            }
        if category == 'missing_instance':
            return {
                'can_create': False,
                'blocking_code': code,
                'blocking_category': category,
                'reason': '上一个接入码对应的伙伴状态异常，请先处理完再生成新的。',
            }

    return {
        'can_create': True,
        'blocking_code': '',
        'blocking_category': '',
        'reason': '',
    }
