"""A small stand-in for a Nextcloud server, for tests.

It copies the parts of Nextcloud the toolkit talks to:
  * public share pages and /s/<token>/download (file shares, folder shares,
    password-protected shares that bounce to a login page)
  * WebDAV under /remote.php/dav/files/<user>/ (GET, PUT, MKCOL) with basic auth
  * the OCS sharing API that creates public links
  * the "Access through untrusted domain" page for hosts not in trusted_domains
"""

import io
import secrets
import threading
import zipfile
from urllib.parse import quote

from flask import Flask, Response, request, redirect, jsonify
from werkzeug.serving import make_server


class FakeNextcloud:
    def __init__(self, user='kinn', app_password='app-pass-123'):
        self.user = user
        self.app_password = app_password
        self.files = {}          # '/Auto Edit/clip.mp4' -> bytes
        self.folders = {'/'}     # existing folders
        self.shares = {}         # token -> {'path', 'password', 'folder'}
        self.trusted_hosts = {'localhost'}
        self.overwrite_url = None   # like overwritehost; defaults to the request's own address
        self.sharing_enabled = True
        self.share_limit = None  # like Nextcloud's per-user link limit (20 per 10 minutes)
        self.shares_created = 0
        self.requests = []       # (method, path) log
        self.app = self._build()
        self._server = None

    # ------------------------------------------------------------ helpers
    def add_file(self, path, data):
        self.files[path] = data
        parts = path.strip('/').split('/')[:-1]
        current = ''
        for part in parts:
            current += '/' + part
            self.folders.add(current)

    def share(self, path, password=None, token=None):
        token = token or secrets.token_hex(6)
        self.shares[token] = {'path': path, 'password': password, 'folder': path in self.folders}
        return token

    def start(self):
        self._server = make_server('127.0.0.1', 0, self.app, threaded=True)
        self.port = self._server.server_port
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return self

    def stop(self):
        if self._server:
            self._server.shutdown()

    def url(self, host='localhost'):
        return f"http://{host}:{self.port}"

    # ------------------------------------------------------------ app
    def _build(self):
        app = Flask('fake_nextcloud')
        nc = self

        @app.before_request
        def check_domain():
            nc.requests.append((request.method, request.path))
            host = request.host.split(':')[0]
            if host not in nc.trusted_hosts:
                # Nextcloud 35 puts ~13.5 KB of page setup ahead of the message
                setup = '<script nonce="x">/* page setup */</script>\n' * 300
                return Response(
                    f'<html><head>{setup}</head><body><h2>Access through untrusted domain</h2>'
                    '<p>Please contact your administrator. If you are an administrator, edit the '
                    '"trusted_domains" setting in config/config.php.</p></body></html>',
                    status=400, content_type='text/html; charset=utf-8')

        def page(body, status=200):
            # Real Nextcloud pages (404s too) carry config that mentions passwords
            config = '{"lost_password_link":null,"enforcePasswordForPublicLink":false}'
            return Response(f'<html><head><script data-config=\'{config}\'></script></head>'
                            f'<body>{body}</body></html>', status, content_type='text/html')

        def authed():
            auth = request.authorization
            return auth and auth.username == nc.user and auth.password == nc.app_password

        def file_response(path):
            name = path.rsplit('/', 1)[-1]
            ctype = 'video/mp4' if name.endswith('.mp4') else 'application/octet-stream'
            if name.endswith('.srt'):
                ctype = 'application/x-subrip'
            return Response(nc.files[path], content_type=ctype, headers={
                'Content-Disposition': f'attachment; filename*=UTF-8\'\'{quote(name)}; filename="{name}"'})

        @app.route('/s/<token>')
        @app.route('/index.php/s/<token>')
        def share_page(token):
            if token not in nc.shares:
                return page('Share not found', 404)
            return page('Share page')

        @app.route('/s/<token>/download')
        @app.route('/index.php/s/<token>/download')
        def share_download(token):
            share = nc.shares.get(token)
            if not share:
                return page('File not found', 404)
            if share['password']:
                return redirect(f'/s/{token}/authenticate/showShare')
            if share['folder']:
                wanted = request.args.get('files')
                sub = request.args.get('path', '/').strip('/')
                if wanted:
                    full = '/'.join(p for p in [share['path'].rstrip('/'), sub, wanted] if p)
                    full = '/' + full.lstrip('/')
                    if full not in nc.files:
                        return page('File not found', 404)
                    return file_response(full)
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, 'w') as z:
                    for p, d in nc.files.items():
                        if p.startswith(share['path'].rstrip('/') + '/'):
                            z.writestr(p.rsplit('/', 1)[-1], d)
                folder_name = share['path'].rstrip('/').rsplit('/', 1)[-1]
                return Response(buf.getvalue(), content_type='application/zip',
                                headers={'Content-Disposition': f'attachment; filename="{folder_name}.zip"'})
            return file_response(share['path'])

        @app.route('/s/<token>/authenticate/showShare')
        def share_login(token):
            return Response('<html><body><form>This share is password-protected '
                            '<input type="password" name="password"></form></body></html>',
                            content_type='text/html')

        @app.route('/remote.php/dav/files/<user>/', defaults={'path': ''},
                   methods=['GET', 'PUT', 'MKCOL', 'PROPFIND'])
        @app.route('/remote.php/dav/files/<user>/<path:path>', methods=['GET', 'PUT', 'MKCOL', 'PROPFIND'])
        def webdav(user, path):
            if not authed() or user != nc.user:
                return Response('<?xml version="1.0"?><d:error xmlns:d="DAV:"><s:message>'
                                'No public access to this resource.</s:message></d:error>',
                                401, content_type='application/xml',
                                headers={'WWW-Authenticate': 'Basic realm="Nextcloud"'})
            full = '/' + path.strip('/')
            parent = full.rsplit('/', 1)[0] or '/'
            if request.method == 'GET':
                if full not in nc.files:
                    return Response('', 404)
                return file_response(full)
            if request.method == 'MKCOL':
                if full in nc.folders or full in nc.files:
                    return Response('', 405)
                if parent not in nc.folders:
                    return Response('', 409)
                nc.folders.add(full)
                return Response('', 201)
            if request.method == 'PUT':
                if parent not in nc.folders:
                    return Response('', 409)
                existed = full in nc.files
                nc.files[full] = request.get_data()
                return Response('', 204 if existed else 201)
            return Response('', 207)

        @app.route('/ocs/v2.php/apps/files_sharing/api/v1/shares', methods=['POST'])
        def ocs_share():
            if request.headers.get('OCS-APIRequest', '').lower() != 'true':
                return Response('{"message":"CSRF check failed"}', 412, content_type='application/json')
            if not authed():
                return jsonify({'ocs': {'meta': {'status': 'failure', 'statuscode': 997,
                                                 'message': 'Current user is not logged in'}, 'data': []}}), 401
            if not nc.sharing_enabled:
                return jsonify({'ocs': {'meta': {'status': 'failure', 'statuscode': 403,
                                                 'message': 'Public link sharing is disabled by the administrator'},
                                        'data': []}}), 403
            path = request.form.get('path')
            if path not in nc.files and path not in nc.folders:
                return jsonify({'ocs': {'meta': {'status': 'failure', 'statuscode': 404,
                                                 'message': 'Wrong path, file/folder does not exist'},
                                        'data': []}}), 404
            if nc.share_limit is not None and nc.shares_created >= nc.share_limit:
                return jsonify({'ocs': {'meta': {'status': 'failure', 'statuscode': 429,
                                                 'message': 'Reached maximum delay'}, 'data': []}}), 429
            nc.shares_created += 1
            token = nc.share(path)
            base = nc.overwrite_url or request.host_url.rstrip('/')
            return jsonify({'ocs': {'meta': {'status': 'ok', 'statuscode': 200, 'message': 'OK'},
                                    'data': {'id': '1', 'token': token, 'url': f'{base}/s/{token}',
                                             'path': path, 'share_type': 3}}})

        return app
