"""Load settings in isolated processes so production guards are actually exercised."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

from django.test import SimpleTestCase


class ProductionSettingsTests(SimpleTestCase):
    def load_settings(self, **overrides):
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(('DJANGO_', 'RENDER_')) and key not in ('DATABASE_URL', 'DB_SSL_REQUIRE')}
        env.update(overrides)
        code = '''
import json
from businesspulse import settings as s
print(json.dumps({
    'debug': s.DEBUG, 'engine': s.DATABASES['default']['ENGINE'],
    'options': s.DATABASES['default'].get('OPTIONS', {}),
    'hosts': s.ALLOWED_HOSTS, 'origins': s.CSRF_TRUSTED_ORIGINS,
    'secure_session': getattr(s, 'SESSION_COOKIE_SECURE', False),
    'secure_csrf': getattr(s, 'CSRF_COOKIE_SECURE', False),
    'redirect': getattr(s, 'SECURE_SSL_REDIRECT', False),
    'proxy': getattr(s, 'SECURE_PROXY_SSL_HEADER', None),
}))
'''
        return subprocess.run([sys.executable, '-c', code], env=env,
                              cwd=Path(__file__).resolve().parents[2],
                              capture_output=True, text=True, timeout=15)

    def production_env(self):
        return {'DJANGO_DEBUG': '0', 'DJANGO_SECRET_KEY': secrets.token_urlsafe(64),
                'DJANGO_ALLOWED_HOSTS': 'staging.example.invalid',
                'DATABASE_URL': 'postgresql://synthetic:unused@localhost/businesspulse'}

    def test_sqlite_development_still_works(self):
        result = self.load_settings()
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = json.loads(result.stdout)
        self.assertTrue(settings['debug'])
        self.assertEqual(settings['engine'], 'django.db.backends.sqlite3')

    def test_production_requires_secret(self):
        env = self.production_env()
        del env['DJANGO_SECRET_KEY']
        result = self.load_settings(**env)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('DJANGO_SECRET_KEY is mandatory', result.stderr)

    def test_production_rejects_weak_or_example_secret(self):
        for secret in ['short', 'x' * 80, 'replace-this-with-a-secure-random-string-for-production']:
            with self.subTest(secret_type=len(secret)):
                env = self.production_env()
                env['DJANGO_SECRET_KEY'] = secret
                result = self.load_settings(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('strong, independently generated', result.stderr)

    def test_production_requires_postgresql(self):
        for url in ['', 'sqlite:///ignored.sqlite3']:
            with self.subTest(url=url):
                env = self.production_env()
                env['DATABASE_URL'] = url
                result = self.load_settings(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Production requires a PostgreSQL', result.stderr)

    def test_production_requires_explicit_hosts(self):
        for hosts in ['', '*', ' , ']:
            with self.subTest(hosts=hosts):
                env = self.production_env()
                env['DJANGO_ALLOWED_HOSTS'] = hosts
                result = self.load_settings(**env)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('explicit production allowed hosts', result.stderr)

    def test_render_configuration_enables_security_and_database_tls(self):
        env = self.production_env()
        del env['DJANGO_ALLOWED_HOSTS']
        env.update(RENDER_EXTERNAL_HOSTNAME='staging.example.invalid', DB_SSL_REQUIRE='1')
        result = self.load_settings(**env)
        self.assertEqual(result.returncode, 0, result.stderr)
        settings = json.loads(result.stdout)
        self.assertFalse(settings['debug'])
        self.assertIn('staging.example.invalid', settings['hosts'])
        self.assertIn('https://staging.example.invalid', settings['origins'])
        self.assertTrue(settings['secure_session'])
        self.assertTrue(settings['secure_csrf'])
        self.assertTrue(settings['redirect'])
        self.assertEqual(settings['proxy'], ['HTTP_X_FORWARDED_PROTO', 'https'])
        self.assertEqual(settings['options']['sslmode'], 'require')
