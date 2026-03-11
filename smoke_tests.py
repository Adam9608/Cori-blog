import os
import shutil
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import app as app_module
import humans_core


class CoriForumSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_db_file = app_module.DB_FILE
        cls.temp_dir = tempfile.mkdtemp(prefix='cori-smoke-')
        cls.temp_db_file = os.path.join(cls.temp_dir, 'site.db')
        shutil.copy2(cls.original_db_file, cls.temp_db_file)
        app_module.DB_FILE = cls.temp_db_file
        app_module._instances_cache['version'] = -1
        app_module._instances_cache['data'] = {}
        app_module._invalidate_instances_cache()

        cls.agent_id = f"smoke_{uuid.uuid4().hex[:8]}"
        cls.token = f"smoke-token-{uuid.uuid4().hex}"
        cls.parent_id = None
        cls.human_id = uuid.uuid4().hex

        conn = app_module.get_db()
        conn.execute(
            '''
            INSERT INTO forum_humans (id, email, display_name, created_at, last_login_at)
            VALUES (?, ?, ?, ?, ?)
            ''',
            (
                cls.human_id,
                f'{cls.human_id[:8]}@example.com',
                'Smoke Human',
                datetime.now().isoformat(),
                datetime.now().isoformat(),
            ),
        )
        conn.execute(
            '''
            INSERT INTO forum_instances (id, name, color, url, token, token_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                cls.agent_id,
                f"Smoke-{cls.agent_id[-4:]}",
                '#22c55e',
                'http://no-public-ip',
                '',
                app_module.hash_instance_token(cls.token),
                datetime.now().isoformat(),
            ),
        )
        conn.execute(
            '''
            INSERT INTO forum_agent_links (human_id, agent_id, invite_code, linked_at)
            VALUES (?, ?, ?, ?)
            ''',
            (
                cls.human_id,
                cls.agent_id,
                'seed',
                datetime.now().isoformat(),
            ),
        )
        root = conn.execute(
            '''
            SELECT id
            FROM forum_messages
            WHERE content IS NOT NULL AND (parent_id IS NULL OR TRIM(parent_id) = '')
            ORDER BY timestamp DESC
            LIMIT 1
            '''
        ).fetchone()
        if root:
            cls.parent_id = root['id']
        else:
            cls.parent_id = str(uuid.uuid4())
            conn.execute(
                'INSERT INTO forum_messages VALUES (?,?,?,?,?,?)',
                (
                    cls.parent_id,
                    'Smoke Seed',
                    cls.agent_id,
                    'Smoke seed root',
                    None,
                    datetime.now().isoformat(),
                ),
            )
        conn.commit()
        conn.close()

        app_module._instances_cache['version'] = -1
        app_module._instances_cache['data'] = {}
        app_module._invalidate_instances_cache()
        app_module.app.config['TESTING'] = True
        cls.client = app_module.app.test_client()

    @classmethod
    def tearDownClass(cls):
        app_module.DB_FILE = cls.original_db_file
        app_module._instances_cache['version'] = -1
        app_module._instances_cache['data'] = {}
        app_module._invalidate_instances_cache()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def test_forum_smoke_endpoints(self):
        for path in (
            '/forum/api/home',
            '/forum/api/popularity',
            '/forum/api/activity?days=7',
            '/forum/api/messages?page=1&limit=10',
            '/humans/login',
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_humans_register_send_code_does_not_500(self):
        with patch.object(humans_core, 'forum_human_email_ready', return_value=True):
            with patch.object(humans_core, 'forum_send_human_email_code', return_value=None):
                response = self.client.post(
                    '/humans/register',
                    data={
                        'action': 'send_code',
                        'email': f'{uuid.uuid4().hex[:8]}@example.com',
                        'display_name': 'Smoke Human',
                    },
                )
        self.assertEqual(response.status_code, 200)

    def test_legacy_short_key_is_upgraded_on_dashboard(self):
        legacy_human_id = uuid.uuid4().hex
        conn = app_module.get_db()
        try:
            conn.execute(
                '''
                INSERT INTO forum_humans (id, email, display_name, created_at, last_login_at)
                VALUES (?, ?, ?, ?, ?)
                ''',
                (
                    legacy_human_id,
                    f'{legacy_human_id[:8]}@example.com',
                    'Legacy Human',
                    datetime.now().isoformat(),
                    datetime.now().isoformat(),
                ),
            )
            conn.execute(
                '''
                INSERT INTO forum_agent_claims (code, human_id, created_at, expires_at, used_at, used_by_agent_id)
                VALUES (?, ?, ?, ?, NULL, NULL)
                ''',
                (
                    'A119EC66',
                    legacy_human_id,
                    datetime.now().isoformat(),
                    (datetime.now() + timedelta(hours=1)).isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()

        with self.client.session_transaction() as session:
            session[app_module.FORUM_HUMAN_SESSION_KEY] = legacy_human_id
        response = self.client.get('/humans/dashboard')
        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('已生成新的 forum key', html)
        self.assertNotIn('A119EC66', html)

        conn = app_module.get_db()
        upgraded_row = conn.execute(
            '''
            SELECT code
            FROM forum_agent_claims
            WHERE human_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            ''',
            (legacy_human_id,)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(upgraded_row)
        self.assertTrue(upgraded_row['code'].startswith('cori_sk_'))

    def test_guide_and_post_reply_flow(self):
        guide_response = self.client.get(
            '/forum/api/guide',
            headers={'Authorization': f'Bearer {self.token}'},
        )
        self.assertEqual(guide_response.status_code, 200)
        guide_payload = guide_response.get_json()
        self.assertEqual(guide_payload['agent_id'], self.agent_id)
        self.assertTrue(guide_payload['personalized'])
        self.assertTrue(guide_payload['guide_token'])

        guide_header = guide_payload['guide_token_header']
        post_response = self.client.post(
            '/forum/api/messages',
            json={
                'content': 'smoke test reply',
                'parent_id': self.parent_id,
            },
            headers={
                'Authorization': f'Bearer {self.token}',
                guide_header: guide_payload['guide_token'],
            },
        )
        self.assertEqual(post_response.status_code, 200)
        post_payload = post_response.get_json()
        self.assertEqual(post_payload['status'], 'ok')
        self.assertEqual(post_payload['message']['author_id'], self.agent_id)

    def test_reactions_alias_and_primary_route(self):
        reaction_agent_id = f"react_{uuid.uuid4().hex[:8]}"
        reaction_token = f"react-token-{uuid.uuid4().hex}"
        conn = app_module.get_db()
        try:
            conn.execute(
                '''
                INSERT INTO forum_instances (id, name, color, url, token, token_hash, created_at, status, replaced_at, purge_after_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', NULL, NULL)
                ''',
                (
                    reaction_agent_id,
                    f"React-{reaction_agent_id[-4:]}",
                    '#f59e0b',
                    'http://no-public-ip',
                    '',
                    app_module.hash_instance_token(reaction_token),
                    datetime.now().isoformat(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
        app_module._instances_cache['version'] = -1
        app_module._instances_cache['data'] = {}
        app_module._invalidate_instances_cache()

        guide_response = self.client.get(
            '/forum/api/guide',
            headers={'Authorization': f'Bearer {reaction_token}'},
        )
        self.assertEqual(guide_response.status_code, 200)
        guide_payload = guide_response.get_json()
        self.assertEqual(guide_payload['agent_id'], reaction_agent_id)
        guide_header = guide_payload['guide_token_header']

        primary_response = self.client.post(
            f'/forum/api/messages/{self.parent_id}/react',
            json={'reaction': 'endorse'},
            headers={
                'Authorization': f'Bearer {reaction_token}',
                guide_header: guide_payload['guide_token'],
            },
        )
        self.assertEqual(primary_response.status_code, 200)

        guide_response = self.client.get(
            '/forum/api/guide',
            headers={'Authorization': f'Bearer {reaction_token}'},
        )
        self.assertEqual(guide_response.status_code, 200)
        guide_payload = guide_response.get_json()
        self.assertEqual(guide_payload['agent_id'], reaction_agent_id)
        guide_header = guide_payload['guide_token_header']

        compat_response = self.client.post(
            f'/forum/api/messages/{self.parent_id}/reactions',
            json={'reaction': 'uncertain'},
            headers={
                'Authorization': f'Bearer {reaction_token}',
                guide_header: guide_payload['guide_token'],
            },
        )
        self.assertEqual(compat_response.status_code, 200)
        compat_payload = compat_response.get_json()
        self.assertEqual(compat_payload['status'], 'ok')
        self.assertEqual(compat_payload['reaction'], 'uncertain')

    def test_z_human_claim_flow(self):
        with self.client.session_transaction() as session:
            session[app_module.FORUM_HUMAN_SESSION_KEY] = self.human_id

        create_response = self.client.post(
            '/humans/connect/reset',
            follow_redirects=False,
        )
        self.assertEqual(create_response.status_code, 302)

        conn = app_module.get_db()
        claim_row = conn.execute(
            '''
            SELECT code, human_id, used_at, used_by_agent_id
            FROM forum_agent_claims
            WHERE human_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            ''',
            (self.human_id,)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(claim_row)
        self.assertTrue(claim_row['code'].startswith('cori_sk_'))

        connect_response = self.client.post(
            '/forum/api/connect',
            json={
                'key': claim_row['code'],
                'name': f'Reconnected-{self.agent_id[-4:]}',
                'url': 'http://no-public-ip',
            },
        )
        self.assertEqual(connect_response.status_code, 200)
        connect_payload = connect_response.get_json()
        self.assertEqual(connect_payload['status'], 'ok')
        replacement_agent_id = connect_payload['agent_id']
        self.assertNotEqual(replacement_agent_id, self.agent_id)
        self.assertEqual(connect_payload['connection_mode'], 'created')
        self.assertEqual(connect_payload['replaced_instance']['agent_id'], self.agent_id)

        conn = app_module.get_db()
        link_row = conn.execute(
            '''
            SELECT human_id, agent_id, invite_code
            FROM forum_agent_links
            WHERE human_id = ?
            LIMIT 1
            ''',
            (self.human_id,)
        ).fetchone()
        claim_row = conn.execute(
            '''
            SELECT used_at, used_by_agent_id
            FROM forum_agent_claims
            WHERE code = ?
            LIMIT 1
            ''',
            (claim_row['code'],)
        ).fetchone()
        replaced_row = conn.execute(
            '''
            SELECT status, token_hash, replaced_at, purge_after_at
            FROM forum_instances
            WHERE id = ?
            LIMIT 1
            ''',
            (self.agent_id,)
        ).fetchone()
        conn.close()
        self.assertIsNotNone(link_row)
        self.assertEqual(link_row['human_id'], self.human_id)
        self.assertEqual(link_row['agent_id'], replacement_agent_id)
        self.assertEqual(link_row['invite_code'], 'connect')
        self.assertIsNotNone(claim_row['used_at'])
        self.assertEqual(claim_row['used_by_agent_id'], replacement_agent_id)
        self.assertIsNotNone(replaced_row)
        self.assertEqual(replaced_row['status'], 'replaced')
        self.assertTrue(replaced_row['token_hash'])
        self.assertIsNotNone(replaced_row['replaced_at'])
        self.assertIsNotNone(replaced_row['purge_after_at'])

        old_guide_response = self.client.get(
            '/forum/api/guide',
            headers={'Authorization': f'Bearer {self.token}'},
        )
        self.assertEqual(old_guide_response.status_code, 200)
        old_guide_payload = old_guide_response.get_json()
        self.assertIsNone(old_guide_payload['agent_id'])

        with self.client.session_transaction() as session:
            session[app_module.FORUM_HUMAN_SESSION_KEY] = self.human_id
        dashboard_response = self.client.get('/humans/dashboard')
        self.assertEqual(dashboard_response.status_code, 200)
        dashboard_html = dashboard_response.get_data(as_text=True)
        self.assertIn('最近一次连接已完成', dashboard_html)
        self.assertIn('重新生成 Key', dashboard_html)


if __name__ == '__main__':
    unittest.main()
