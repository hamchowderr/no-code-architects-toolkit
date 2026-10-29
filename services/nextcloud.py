# Copyright (c) 2025 Stephen G. Pope
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""Nextcloud support: read media from Nextcloud links and store results in Nextcloud.

Input links on the configured server (NEXTCLOUD_URL or NEXTCLOUD_PUBLIC_URL) work
anywhere a media URL is accepted:
  * Public share links:      https://cloud.example.com/s/AbC123
                             (also /index.php/s/AbC123 and the .../download form)
  * Files in a shared folder: https://cloud.example.com/s/AbC123/download?path=/&files=clip.mp4
  * Private WebDAV paths:    https://cloud.example.com/remote.php/dav/files/<user>/clip.mp4
                             (uses NEXTCLOUD_USER / NEXTCLOUD_APP_PASSWORD)

Output: when Nextcloud is the storage provider, results are uploaded over WebDAV
into NEXTCLOUD_OUTPUT_FOLDER and a public share link is returned.

Environment:
  NEXTCLOUD_URL            Base URL the toolkit uses to reach Nextcloud
                           (e.g. http://host.docker.internal:8081). Enough on its own
                           for reading public share links; output also needs the two below.
  NEXTCLOUD_USER           Nextcloud user that owns uploaded files
  NEXTCLOUD_APP_PASSWORD   App password for that user (Personal settings > Security)
  NEXTCLOUD_OUTPUT_FOLDER  Folder for results, created if missing (default: "NCA Output")
  NEXTCLOUD_PUBLIC_URL     Optional. The address people use to open Nextcloud, when it
                           differs from NEXTCLOUD_URL. Links people paste with this
                           address are fetched through NEXTCLOUD_URL, and returned
                           share links use it.
  NEXTCLOUD_LINK_TYPE      "download" (default) returns a direct-download link that can be
                           passed straight into another toolkit call; "share" returns
                           the normal Nextcloud share page.
"""

import os
import re
import base64
import logging
import mimetypes
from urllib.parse import urlparse, urlunparse, quote, unquote, parse_qs

import requests

logger = logging.getLogger(__name__)

# /s/<token> or /index.php/s/<token>, optionally followed by /download
SHARE_PATH_RE = re.compile(r'^(?P<prefix>.*?)(?P<index>/index\.php)?/s/(?P<token>[A-Za-z0-9]+)(?P<rest>/.*)?$')
WEBDAV_PATH_RE = re.compile(r'/remote\.php/(dav/files/[^/]+|webdav)(/|$)')

REQUEST_TIMEOUT = (10, 300)  # connect, read


class NextcloudError(Exception):
    """A Nextcloud problem explained in terms the person running the toolkit can act on."""


def _env(name, default=''):
    return (os.getenv(name) or default).strip()


def _base_url():
    return _env('NEXTCLOUD_URL').rstrip('/')


def _public_url():
    return (_env('NEXTCLOUD_PUBLIC_URL') or _base_url()).rstrip('/')


def _host(url):
    parsed = urlparse(url)
    return (parsed.hostname or '').lower(), parsed.port


def _same_server(url, base):
    if not base:
        return False
    return _host(url) == _host(base)


def _known_server(url):
    """True when the URL points at the configured Nextcloud (by either address)."""
    return _same_server(url, _base_url()) or _same_server(url, _env('NEXTCLOUD_PUBLIC_URL'))


def _swap_base(url, old_base, new_base):
    """Point a URL on old_base at new_base, keeping the path below each base's own path."""
    old, new, target = urlparse(old_base), urlparse(new_base), urlparse(url)
    old_path = old.path.rstrip('/')
    path = target.path
    if old_path and path.startswith(old_path):
        path = path[len(old_path):]
    new_path = new.path.rstrip('/') + path
    return urlunparse((new.scheme, new.netloc, new_path, target.params, target.query, ''))


def _internal(url):
    """Fetch links pasted with the public address through the internal one."""
    public, base = _env('NEXTCLOUD_PUBLIC_URL').rstrip('/'), _base_url()
    if public and base and public != base and _same_server(url, public):
        return _swap_base(url, public, base)
    return url


def _external(url):
    """Show share links with the address people actually use."""
    public, base = _env('NEXTCLOUD_PUBLIC_URL').rstrip('/'), _base_url()
    if public and base and public != base and _same_server(url, base):
        return _swap_base(url, base, public)
    return url


def share_token(url):
    """Return the share token if the URL is a Nextcloud public share link, else None."""
    parsed = urlparse(url)
    if parsed.scheme not in ('http', 'https'):
        return None
    match = SHARE_PATH_RE.match(parsed.path)
    if not match:
        return None
    rest = match.group('rest') or ''
    if rest not in ('', '/', '/download'):
        return None
    return match.group('token')


def is_nextcloud_url(url):
    """True for share links and WebDAV paths on the configured Nextcloud server.

    Only the server named in NEXTCLOUD_URL / NEXTCLOUD_PUBLIC_URL counts, so ordinary
    links that happen to contain /s/<something> on other sites are left alone.
    """
    if not _known_server(url):
        return False
    return share_token(url) is not None or bool(WEBDAV_PATH_RE.search(urlparse(url).path))


def to_download_url(url):
    """Turn a share link into its direct-download form (unchanged for anything else)."""
    if share_token(url) is None:
        return url
    parsed = urlparse(url)
    match = SHARE_PATH_RE.match(parsed.path)
    path = f"{match.group('prefix')}{match.group('index') or ''}/s/{match.group('token')}/download"
    return urlunparse((parsed.scheme, parsed.netloc, path, '', parsed.query, ''))


def prepare_request(url):
    """Return (url, extra requests kwargs) for fetching a media URL.

    Non-Nextcloud URLs pass through unchanged, so callers can use this for every URL.
    Credentials are only ever attached for the configured Nextcloud server.
    """
    if not is_nextcloud_url(url):
        return url, {}
    if share_token(url) is not None:
        return _internal(to_download_url(url)), {}
    if WEBDAV_PATH_RE.search(urlparse(url).path):
        user, password = _env('NEXTCLOUD_USER'), _env('NEXTCLOUD_APP_PASSWORD')
        if not user or not password:
            raise NextcloudError(
                "This is a private Nextcloud link, but NEXTCLOUD_USER and NEXTCLOUD_APP_PASSWORD "
                "are not set. Set them, or use a public share link instead.")
        return _internal(url), {'auth': (user, password)}
    return url, {}


def prepare_stream(url):
    """Return (url, extra HTTP headers) for tools that read a media URL themselves (ffprobe, ffmpeg).

    Like prepare_request, but the login goes in an Authorization header, and Nextcloud links
    are checked first so a password-protected or missing share gets a clear error rather
    than an ffmpeg one.
    """
    fetch_url, kwargs = prepare_request(url)
    if not is_nextcloud_url(url):
        return fetch_url, {}
    response = requests.get(fetch_url, stream=True, timeout=REQUEST_TIMEOUT, **kwargs)
    try:
        content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
        if response.status_code >= 400 or content_type == 'text/html':
            explain_failure(response, url)
    finally:
        response.close()
    if 'auth' not in kwargs:
        return fetch_url, {}
    token = base64.b64encode(':'.join(kwargs['auth']).encode()).decode()
    return fetch_url, {'Authorization': f'Basic {token}'}


def explain_failure(response, url):
    """Raise a NextcloudError describing a failed or unusable Nextcloud response."""
    status = response.status_code
    content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
    body = ''
    if content_type in ('text/html', 'application/xml', 'text/xml', 'application/json', 'text/plain'):
        try:
            # Nextcloud's error pages carry ~14 KB of page setup before the message
            body = response.text[:65536]
        except Exception:
            body = ''
    lowered = body.lower()

    if 'untrusted domain' in lowered or 'trusted_domains' in lowered:
        host = urlparse(response.url or url).hostname
        raise NextcloudError(
            f"Nextcloud refused the address '{host}'. Add it to trusted_domains in Nextcloud's "
            f"config.php (for example: occ config:system:set trusted_domains 10 --value={host}).")
    # Password-protected shares redirect to /s/<token>/authenticate/... The page body is no
    # guide: every Nextcloud page, 404s included, mentions "password" in its built-in config.
    if status in (401, 403) or '/authenticate/' in (response.url or ''):
        if share_token(url):
            raise NextcloudError(
                "This Nextcloud share is password-protected, so it can't be downloaded "
                "automatically. Remove the share password, or use a private link with "
                "NEXTCLOUD_USER and NEXTCLOUD_APP_PASSWORD set.")
        raise NextcloudError(
            "Nextcloud rejected the login. Check NEXTCLOUD_USER and NEXTCLOUD_APP_PASSWORD "
            "(use an app password, not the account password).")
    if status == 404:
        raise NextcloudError("Nextcloud couldn't find that file or share. The link may be wrong, expired or deleted.")
    if status >= 400:
        raise NextcloudError(f"Nextcloud returned HTTP {status} for {url}.")
    if content_type == 'text/html':
        raise NextcloudError(
            "Nextcloud returned a web page instead of the file. Check the link is a share "
            "of a single file, or add &files=<name> for a file inside a shared folder.")


def filename_from_response(response):
    """Best-effort original filename from a download response."""
    disposition = response.headers.get('content-disposition', '')
    match = re.search(r"filename\*\s*=\s*[^']*''([^;]+)", disposition, re.I)
    if match:
        return unquote(match.group(1).strip().strip('"'))
    match = re.search(r'filename\s*=\s*"?([^";]+)"?', disposition, re.I)
    if match:
        return match.group(1).strip()
    files = parse_qs(urlparse(response.url or '').query).get('files')
    if files:
        return files[0]
    return ''


# ---------------------------------------------------------------- output storage

def _check_upload_settings():
    missing = [n for n in ('NEXTCLOUD_URL', 'NEXTCLOUD_USER', 'NEXTCLOUD_APP_PASSWORD') if not _env(n)]
    if missing:
        raise NextcloudError(f"Missing environment variables for Nextcloud storage: {', '.join(missing)}")


def _dav_root():
    return f"{_base_url()}/remote.php/dav/files/{quote(_env('NEXTCLOUD_USER'))}"


def _folder_parts():
    folder = _env('NEXTCLOUD_OUTPUT_FOLDER', 'NCA Output')
    return [p for p in folder.replace('\\', '/').split('/') if p and p not in ('.', '..')]


def _ensure_folder(session, parts):
    """Create each level of the output folder. 405 means it already exists."""
    current = _dav_root()
    for part in parts:
        current = f"{current}/{quote(part)}"
        response = session.request('MKCOL', current, timeout=REQUEST_TIMEOUT)
        if response.status_code not in (201, 405):
            explain_failure(response, current)
            raise NextcloudError(f"Couldn't create the output folder '{'/'.join(parts)}' (HTTP {response.status_code}).")


def _create_share(session, path):
    response = session.post(
        f"{_base_url()}/ocs/v2.php/apps/files_sharing/api/v1/shares",
        headers={'OCS-APIRequest': 'true', 'Accept': 'application/json'},
        params={'format': 'json'},
        data={'path': path, 'shareType': 3, 'permissions': 1},
        timeout=REQUEST_TIMEOUT,
    )
    if response.status_code == 429:
        raise NextcloudError(
            f"The file was uploaded to '{path}', but Nextcloud refused to create a share link: by "
            "default it allows each user 20 new links every 10 minutes. Raise the limit for "
            "NEXTCLOUD_USER (see docker-compose.nextcloud.n8n.md) or wait and try again.")
    if response.status_code >= 400 and response.status_code != 403:
        explain_failure(response, response.url)
    try:
        payload = response.json()
        meta, data = payload['ocs']['meta'], payload['ocs']['data']
    except Exception:
        explain_failure(response, response.url)
        raise NextcloudError("Nextcloud's sharing API gave an unexpected answer.")
    if meta.get('statuscode') not in (100, 200) or not data.get('url'):
        message = meta.get('message') or 'unknown error'
        raise NextcloudError(
            f"The file was uploaded, but Nextcloud wouldn't create a share link ({message}). "
            "Check that link sharing is allowed in Administration settings > Sharing.")
    return data['url']


def upload_to_nextcloud(file_path):
    """Upload a local file and return a public link to it."""
    _check_upload_settings()
    name = os.path.basename(file_path)
    parts = _folder_parts()
    session = requests.Session()
    session.auth = (_env('NEXTCLOUD_USER'), _env('NEXTCLOUD_APP_PASSWORD'))

    _ensure_folder(session, parts)

    target = '/'.join([_dav_root()] + [quote(p) for p in parts] + [quote(name)])
    content_type = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    logger.info(f"Uploading {name} to Nextcloud folder '{'/'.join(parts) or '/'}'")
    with open(file_path, 'rb') as data:
        response = session.put(target, data=data, headers={'Content-Type': content_type}, timeout=REQUEST_TIMEOUT)
    if response.status_code not in (200, 201, 204):
        explain_failure(response, target)
        raise NextcloudError(f"Upload to Nextcloud failed (HTTP {response.status_code}).")

    share_url = _external(_create_share(session, '/' + '/'.join(parts + [name])))
    if _env('NEXTCLOUD_LINK_TYPE', 'download').lower() == 'share':
        return share_url
    return to_download_url(share_url)
