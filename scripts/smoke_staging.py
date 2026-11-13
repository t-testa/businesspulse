"""Exercise local Gunicorn, emulating Render's trusted HTTPS proxy."""
from http.client import HTTPConnection
import re
from time import monotonic, sleep


def request(path, *, forwarded_https=True, method='GET', body=None, headers=None):
    connection = HTTPConnection('127.0.0.1', 8001, timeout=3)
    request_headers = {'Host': 'localhost'}
    if forwarded_https:
        request_headers['X-Forwarded-Proto'] = 'https'
    request_headers.update(headers or {})
    try:
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


deadline = monotonic() + 20
while True:
    try:
        status, headers, body = request('/health/')
        assert (status, body) == (200, b'ok'), (status, body)
        break
    except OSError:
        if monotonic() >= deadline:
            raise
        sleep(0.1)

status, headers, _ = request('/health/', forwarded_https=False)
assert status == 301 and headers['Location'].startswith('https://')
status, headers, body = request('/signup/')
assert status == 200
assert 'max-age=3600' in headers['Strict-Transport-Security']
assert 'Secure' in headers['Set-Cookie']
assert headers['X-Frame-Options'] == 'DENY'
stylesheet = re.search(rb'href="(/static/tracker/styles\.[^"]+\.css)"', body).group(1).decode()
assert request(stylesheet)[0] == 200
assert request('/signup/', method='POST', body='username=csrf-test',
               headers={'Content-Type': 'application/x-www-form-urlencoded'})[0] == 403
assert request('/health/', headers={'Host': 'untrusted.invalid'})[0] == 400
print('Gunicorn smoke passed: health, HTTPS redirect, signup, secure CSRF cookie, HSTS, '
      'frame protection, hashed CSS, CSRF rejection, and host validation.')
