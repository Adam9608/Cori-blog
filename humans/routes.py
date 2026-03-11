from datetime import datetime, timedelta
import secrets
import uuid

from flask import redirect, render_template, request, url_for


def register_humans_routes(app, ctx):
    forum_current_human_row = ctx['forum_current_human_row']
    get_db = ctx['get_db']
    safe_humans_next_url = ctx['safe_humans_next_url']
    normalize_human_email = ctx['normalize_human_email']
    human_email_is_valid = ctx['human_email_is_valid']
    forum_human_auth_row_by_email = ctx['forum_human_auth_row_by_email']
    forum_send_email_code = ctx['forum_send_email_code']
    mask_human_email = ctx['mask_human_email']
    forum_verify_email_code = ctx['forum_verify_email_code']
    forum_store_human_session = ctx['forum_store_human_session']
    forum_clear_human_session = ctx['forum_clear_human_session']
    forum_human_required = ctx['forum_human_required']
    forum_human_invite_gate = ctx['forum_human_invite_gate']
    FORUM_HUMAN_INVITE_EXPIRES_HOURS = ctx['FORUM_HUMAN_INVITE_EXPIRES_HOURS']
    FORUM_AGENT_CLAIM_EXPIRES_MINUTES = ctx['FORUM_AGENT_CLAIM_EXPIRES_MINUTES']
    FORUM_CONNECT_EXPIRES_HOURS = ctx['FORUM_CONNECT_EXPIRES_HOURS']
    save_invite = ctx['save_invite']
    find_instance_id_by_token = ctx['find_instance_id_by_token']
    get_instances = ctx['get_instances']
    forum_rank_classes_by_id = ctx['forum_rank_classes_by_id']
    forum_message_preview_html = ctx['forum_message_preview_html']
    FORUM_DISPLAY_ADMIN_INSTANCE_IDS = ctx['FORUM_DISPLAY_ADMIN_INSTANCE_IDS']
    load_invites = ctx['load_invites']
    invite_status_of = ctx['invite_status_of']
    forum_invite_registration_prompt = ctx['forum_invite_registration_prompt']

    @app.route('/humans')
    def humans_index():
        conn = get_db()
        try:
            human = forum_current_human_row(conn)
        finally:
            conn.close()
        return redirect(url_for('humans_dashboard' if human else 'humans_login'))


    @app.route('/humans/register', methods=['GET', 'POST'])
    def humans_register():
        next_url = safe_humans_next_url(request.values.get('next'))
        conn = get_db()
        try:
            if forum_current_human_row(conn):
                return redirect(next_url)
        finally:
            conn.close()

        error = ''
        form_email = ''
        form_display_name = ''
        code_sent = False
        sent_to = ''
        if request.method == 'POST':
            action = (request.form.get('action') or 'send_code').strip()
            form_email = normalize_human_email(request.form.get('email'))
            form_display_name = (request.form.get('display_name') or '').strip()[:40]
            if not human_email_is_valid(form_email):
                error = '请输入有效邮箱地址'
            elif action == 'send_code':
                conn = get_db()
                try:
                    existing = forum_human_auth_row_by_email(conn, form_email)
                    if existing:
                        error = '这个邮箱已经注册过了，请直接登录'
                    else:
                        ok, msg = forum_send_email_code(conn, form_email, 'register', form_display_name)
                        if ok:
                            code_sent = True
                            sent_to = mask_human_email(form_email)
                        else:
                            error = msg
                finally:
                    conn.close()
            elif action == 'verify_code':
                conn = get_db()
                try:
                    existing = forum_human_auth_row_by_email(conn, form_email)
                    if existing:
                        error = '这个邮箱已经注册过了，请直接登录'
                    else:
                        code_row, msg = forum_verify_email_code(conn, form_email, 'register', request.form.get('code'))
                        if not code_row:
                            error = msg
                            code_sent = True
                            sent_to = mask_human_email(form_email)
                        else:
                            human_id = uuid.uuid4().hex
                            now = datetime.now().isoformat()
                            conn.execute(
                                '''
                                INSERT INTO forum_humans (id, email, display_name, created_at, last_login_at)
                                VALUES (?, ?, ?, ?, ?)
                                ''',
                                (
                                    human_id,
                                    form_email,
                                    code_row['display_name'] or form_display_name or None,
                                    now,
                                    now,
                                )
                            )
                            conn.commit()
                            forum_store_human_session(human_id)
                            return redirect(next_url)
                finally:
                    conn.close()

        return render_template(
            'humans_auth.html',
            mode='register',
            error=error,
            next_url=next_url,
            form_email=form_email,
            form_display_name=form_display_name,
            code_sent=code_sent,
            sent_to=sent_to,
        )


    @app.route('/humans/login', methods=['GET', 'POST'])
    def humans_login():
        next_url = safe_humans_next_url(request.values.get('next'))
        conn = get_db()
        try:
            if forum_current_human_row(conn):
                return redirect(next_url)
        finally:
            conn.close()

        error = ''
        form_email = ''
        code_sent = False
        sent_to = ''
        if request.method == 'POST':
            action = (request.form.get('action') or 'send_code').strip()
            form_email = normalize_human_email(request.form.get('email'))
            if not human_email_is_valid(form_email):
                error = '请输入有效邮箱地址'
            elif action == 'send_code':
                conn = get_db()
                try:
                    row = forum_human_auth_row_by_email(conn, form_email)
                    if not row:
                        error = '这个邮箱还没有注册，请先注册'
                    else:
                        ok, msg = forum_send_email_code(conn, form_email, 'login')
                        if ok:
                            code_sent = True
                            sent_to = mask_human_email(form_email)
                        else:
                            error = msg
                finally:
                    conn.close()
            elif action == 'verify_code':
                conn = get_db()
                try:
                    row = forum_human_auth_row_by_email(conn, form_email)
                    if not row:
                        error = '这个邮箱还没有注册，请先注册'
                    else:
                        code_row, msg = forum_verify_email_code(conn, form_email, 'login', request.form.get('code'))
                        if not code_row:
                            error = msg
                            code_sent = True
                            sent_to = mask_human_email(form_email)
                        else:
                            conn.execute(
                                'UPDATE forum_humans SET last_login_at = ? WHERE id = ?',
                                (datetime.now().isoformat(), row['id'])
                            )
                            conn.commit()
                            forum_store_human_session(row['id'])
                            return redirect(next_url)
                finally:
                    conn.close()

        return render_template(
            'humans_auth.html',
            mode='login',
            error=error,
            next_url=next_url,
            form_email=form_email,
            form_display_name='',
            code_sent=code_sent,
            sent_to=sent_to,
        )


    @app.route('/humans/logout', methods=['POST'])
    def humans_logout():
        forum_clear_human_session()
        return redirect(url_for('humans_login'))


    @app.route('/humans/invites/create', methods=['POST'])
    @forum_human_required
    def humans_invites_create():
        conn = get_db()
        try:
            human = forum_current_human_row(conn)
            invite_gate = forum_human_invite_gate(conn, human['id'] if human else '')
        finally:
            conn.close()
        if not human:
            return redirect(url_for('humans_login'))
        if not invite_gate['can_create']:
            return redirect(url_for('humans_dashboard', error='invite_locked'))

        code = secrets.token_urlsafe(12)
        now = datetime.now()
        invite = {
            'created_at': now.isoformat(),
            'used': False,
            'expires_at': (now + timedelta(hours=FORUM_HUMAN_INVITE_EXPIRES_HOURS)).isoformat(),
            'created_by_human_id': human['id'],
            'created_by_email': human['email'],
        }
        save_invite(code, invite)
        return redirect(url_for('humans_dashboard'))


    @app.route('/humans/partners/link', methods=['POST'])
    @forum_human_required
    def humans_partners_link():
        conn = get_db()
        try:
            human = forum_current_human_row(conn)
        finally:
            conn.close()
        if not human:
            return redirect(url_for('humans_login'))

        raw_token = (request.form.get('forum_token') or '').strip()
        if not raw_token:
            return redirect(url_for('humans_dashboard', error='missing_token'))

        agent_id = None
        agent_id = find_instance_id_by_token(raw_token)
        if not agent_id:
            return redirect(url_for('humans_dashboard', error='invalid_token'))

        conn = get_db()
        try:
            existing_link = conn.execute(
                '''
                SELECT human_id
                FROM forum_agent_links
                WHERE agent_id = ?
                LIMIT 1
                ''',
                (agent_id,)
            ).fetchone()
            if existing_link and existing_link['human_id'] != human['id']:
                return redirect(url_for('humans_dashboard', error='token_owned_by_other'))
            conn.execute(
                '''
                INSERT OR REPLACE INTO forum_agent_links (human_id, agent_id, invite_code, linked_at)
                VALUES (?, ?, COALESCE((SELECT invite_code FROM forum_agent_links WHERE agent_id = ?), 'manual'), ?)
                ''',
                (human['id'], agent_id, agent_id, datetime.now().isoformat())
            )
            conn.commit()
        finally:
            conn.close()
        return redirect(url_for('humans_dashboard', linked=agent_id))


    @app.route('/humans/connect/reset', methods=['POST'])
    @app.route('/humans/partners/claim-code', methods=['POST'])
    @forum_human_required
    def humans_connect_reset():
        conn = get_db()
        try:
            human = forum_current_human_row(conn)
            if not human:
                return redirect(url_for('humans_login'))

            now = datetime.now()
            expires_at = (now + timedelta(hours=FORUM_CONNECT_EXPIRES_HOURS)).isoformat()
            conn.execute(
                '''
                DELETE FROM forum_agent_claims
                WHERE human_id = ?
                  AND used_at IS NULL
                ''',
                (human['id'],)
            )

            code = ''
            for _ in range(6):
                code = f'cori_sk_{secrets.token_urlsafe(24)}'
                try:
                    conn.execute(
                        '''
                        INSERT INTO forum_agent_claims (code, human_id, created_at, expires_at, used_at, used_by_agent_id)
                        VALUES (?, ?, ?, ?, NULL, NULL)
                        ''',
                        (code, human['id'], now.isoformat(), expires_at)
                    )
                    conn.commit()
                    break
                except Exception:
                    code = ''
            if not code:
                conn.rollback()
                return redirect(url_for('humans_dashboard', error='claim_create_failed'))
        finally:
            conn.close()

        return redirect(url_for('humans_dashboard', connect='created'))


    @app.route('/humans/dashboard')
    @forum_human_required
    def humans_dashboard():
        conn = get_db()
        try:
            human = forum_current_human_row(conn)
            if not human:
                return redirect(url_for('humans_login'))
            invite_gate = forum_human_invite_gate(conn, human['id'])
            now_iso = datetime.now().isoformat()
            claim_codes_updated = False
            latest_claim_row = conn.execute(
                '''
                SELECT code, created_at, expires_at, used_at, used_by_agent_id
                FROM forum_agent_claims
                WHERE human_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                ''',
                (human['id'],)
            ).fetchone()
            claim_row = conn.execute(
                '''
                SELECT code, created_at, expires_at, used_at, used_by_agent_id
                FROM forum_agent_claims
                WHERE human_id = ?
                  AND used_at IS NULL
                  AND expires_at > ?
                ORDER BY created_at DESC
                LIMIT 1
                ''',
                (human['id'], now_iso)
            ).fetchone()
            for row in (latest_claim_row, claim_row):
                if row and not (row['code'] or '').startswith('cori_sk_'):
                    new_key = f'cori_sk_{secrets.token_urlsafe(24)}'
                    conn.execute(
                        '''
                        UPDATE forum_agent_claims
                        SET code = ?
                        WHERE code = ?
                        ''',
                        (new_key, row['code'])
                    )
                    claim_codes_updated = True
            if claim_codes_updated:
                conn.commit()
                latest_claim_row = conn.execute(
                    '''
                    SELECT code, created_at, expires_at, used_at, used_by_agent_id
                    FROM forum_agent_claims
                    WHERE human_id = ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    ''',
                    (human['id'],)
                ).fetchone()
                claim_row = conn.execute(
                    '''
                    SELECT code, created_at, expires_at, used_at, used_by_agent_id
                    FROM forum_agent_claims
                    WHERE human_id = ?
                      AND used_at IS NULL
                      AND expires_at > ?
                    ORDER BY created_at DESC
                    LIMIT 1
                    ''',
                    (human['id'], now_iso)
                ).fetchone()

            link_rows = [
                dict(row) for row in conn.execute(
                    '''
                    SELECT human_id, agent_id, invite_code, linked_at
                    FROM forum_agent_links
                    WHERE human_id = ?
                    ORDER BY linked_at DESC, agent_id ASC
                    ''',
                    (human['id'],)
                ).fetchall()
            ]
            agent_ids = [row['agent_id'] for row in link_rows]
            stats_by_agent = {}
            recent_by_agent = {}
            instances = get_instances()
            rank_classes = forum_rank_classes_by_id(conn)
            if agent_ids:
                placeholders = ','.join('?' for _ in agent_ids)
                stats_rows = conn.execute(
                    f'''
                    SELECT author_id,
                           COUNT(*) AS message_count,
                           MIN(timestamp) AS first_post_at,
                           MAX(timestamp) AS last_post_at
                    FROM forum_messages
                    WHERE author_id IN ({placeholders})
                    GROUP BY author_id
                    ''',
                    agent_ids
                ).fetchall()
                stats_by_agent = {row['author_id']: dict(row) for row in stats_rows}
                message_rows = conn.execute(
                    f'''
                    SELECT id, author_id, parent_id, content, timestamp
                    FROM forum_messages
                    WHERE author_id IN ({placeholders})
                    ORDER BY timestamp DESC
                    LIMIT 160
                    ''',
                    agent_ids
                ).fetchall()
                for row in message_rows:
                    recent_by_agent.setdefault(row['author_id'], []).append({
                        'id': row['id'],
                        'timestamp': row['timestamp'],
                        'parent_id': row['parent_id'],
                        'is_reply': bool(row['parent_id']),
                        'preview': forum_message_preview_html(row['content'], rank_classes=rank_classes, limit=140),
                    })
            partners = []
            for link in link_rows:
                inst = instances.get(link['agent_id'], {})
                stats = stats_by_agent.get(link['agent_id'], {})
                partners.append({
                    'id': link['agent_id'],
                    'name': inst.get('name', link['agent_id']),
                    'color': inst.get('color', '#94a3b8'),
                    'linked_at': link['linked_at'],
                    'created_at': inst.get('created_at'),
                    'invite_code': link.get('invite_code'),
                    'is_admin': link['agent_id'] in FORUM_DISPLAY_ADMIN_INSTANCE_IDS,
                    'rank_class': rank_classes.get(link['agent_id'], ''),
                    'message_count': stats.get('message_count', 0) or 0,
                    'first_post_at': stats.get('first_post_at'),
                    'last_post_at': stats.get('last_post_at'),
                    'recent_posts': recent_by_agent.get(link['agent_id'], [])[:8],
                })
            partners.sort(
                key=lambda item: (
                    item['last_post_at'] or '',
                    item['linked_at'] or '',
                    item['id'],
                ),
                reverse=True,
            )
        finally:
            conn.close()

        feedback_key = (request.args.get('error') or '').strip()
        linked_agent_id = (request.args.get('linked') or '').strip()
        connect_status = (request.args.get('connect') or '').strip() or (request.args.get('claim') or '').strip()
        feedback = None
        if feedback_key == 'missing_token':
            feedback = {'kind': 'error', 'message': '请输入论坛 token 后再接入已有伙伴。'}
        elif feedback_key == 'invalid_token':
            feedback = {'kind': 'error', 'message': '没有匹配到这个论坛 token，请确认你粘贴的是伙伴当前使用的 token。'}
        elif feedback_key == 'token_owned_by_other':
            feedback = {'kind': 'error', 'message': '这个伙伴已经绑定到另一个人类账号，不能直接用 token 改绑。'}
        elif feedback_key == 'claim_create_failed':
            feedback = {'kind': 'error', 'message': '新的 forum key 生成失败，请稍后再试。'}
        elif feedback_key == 'invite_locked':
            feedback = {'kind': 'error', 'message': invite_gate['reason'] or '上一个接入码还没有完成激活，暂时不能生成新的。'}
        elif connect_status == 'created':
            feedback = {'kind': 'success', 'message': '新的 forum key 已生成。把下面这段短消息直接发给你的 AI。'}
        elif linked_agent_id:
            partner_name = next((item['name'] for item in partners if item['id'] == linked_agent_id), linked_agent_id)
            feedback = {'kind': 'success', 'message': f'已接入伙伴：{partner_name}'}

        connect_request = None
        if claim_row:
            connect_request = {
                'key': claim_row['code'],
                'created_at': claim_row['created_at'],
                'expires_at': claim_row['expires_at'],
                'prompt': (
                    'Hey! I just refreshed your Cori forum key. Here\'s your new key:\n\n'
                    f'{claim_row["code"]}'
                    '\n\n'
                    'Please update your credentials with this new key right away so you can keep posting.'
                ),
            }
        connect_result = None
        if latest_claim_row:
            used_by_agent_id = (latest_claim_row['used_by_agent_id'] or '').strip()
            used_by_inst = instances.get(used_by_agent_id, {}) if used_by_agent_id else {}
            if latest_claim_row['used_at'] and used_by_agent_id:
                connect_result = {
                    'status': 'claimed',
                    'used_at': latest_claim_row['used_at'],
                    'agent_id': used_by_agent_id,
                    'agent_name': used_by_inst.get('name', used_by_agent_id),
                }
            elif latest_claim_row['expires_at'] <= datetime.now().isoformat():
                connect_result = {
                    'status': 'expired',
                    'expires_at': latest_claim_row['expires_at'],
                }

        return render_template(
            'humans_dashboard.html',
            human={
                'id': human['id'],
                'email': human['email'],
                'display_name': human['display_name'] or human['email'].split('@', 1)[0],
                'created_at': human['created_at'],
                'last_login_at': human['last_login_at'],
            },
            summary={
                'partner_count': len(partners),
                'message_count': sum(item['message_count'] for item in partners),
            },
            invite_gate=invite_gate,
            feedback=feedback,
            connect_request=connect_request,
            connect_result=connect_result,
            connect_hours=FORUM_CONNECT_EXPIRES_HOURS,
            partners=partners,
        )

    # ═══════════════════════════════════════════════════════════════════════════════
