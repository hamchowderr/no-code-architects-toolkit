"""Nextcloud input links and output storage, tested against tests/fake_nextcloud.py."""

import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('API_KEY', 'test-key')

from tests.fake_nextcloud import FakeNextcloud  # noqa: E402
from services import nextcloud  # noqa: E402
from services.nextcloud import NextcloudError  # noqa: E402
from services.file_management import download_file  # noqa: E402

VIDEO = b'\x00\x00\x00\x18ftypmp42' + b'fake video bytes' * 100
NC_VARS = ['NEXTCLOUD_URL', 'NEXTCLOUD_PUBLIC_URL', 'NEXTCLOUD_USER', 'NEXTCLOUD_APP_PASSWORD',
           'NEXTCLOUD_OUTPUT_FOLDER', 'NEXTCLOUD_LINK_TYPE', 'STORAGE_PROVIDER',
           'S3_ENDPOINT_URL', 'S3_ACCESS_KEY', 'S3_SECRET_KEY', 'S3_BUCKET_NAME', 'S3_REGION', 'GCP_BUCKET_NAME']


@pytest.fixture
def nc(monkeypatch):
    server = FakeNextcloud().start()
    for name in NC_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('NEXTCLOUD_URL', server.url())
    monkeypatch.setenv('NEXTCLOUD_USER', server.user)
    monkeypatch.setenv('NEXTCLOUD_APP_PASSWORD', server.app_password)
    server.add_file('/Auto Edit/Add Captions/clip.mp4', VIDEO)
    yield server
    server.stop()


# ------------------------------------------------------------------ link parsing

class TestLinks:
    def test_share_link_forms(self):
        assert nextcloud.share_token('https://c.example.com/s/AbC123') == 'AbC123'
        assert nextcloud.share_token('https://c.example.com/s/AbC123/') == 'AbC123'
        assert nextcloud.share_token('https://c.example.com/s/AbC123/download') == 'AbC123'
        assert nextcloud.share_token('https://c.example.com/index.php/s/AbC123') == 'AbC123'
        assert nextcloud.share_token('https://c.example.com/nextcloud/s/AbC123') == 'AbC123'
        assert nextcloud.share_token('https://c.example.com/s/AbC123/authenticate/showShare') is None
        assert nextcloud.share_token('https://c.example.com/video.mp4') is None

    def test_download_url(self):
        assert nextcloud.to_download_url('https://c.example.com/s/AbC') == 'https://c.example.com/s/AbC/download'
        assert nextcloud.to_download_url('https://c.example.com/index.php/s/AbC/') == \
            'https://c.example.com/index.php/s/AbC/download'
        assert nextcloud.to_download_url('https://c.example.com/s/AbC/download?path=/&files=a.mp4') == \
            'https://c.example.com/s/AbC/download?path=/&files=a.mp4'

    def test_other_sites_untouched(self, monkeypatch):
        monkeypatch.setenv('NEXTCLOUD_URL', 'https://cloud.thekinn.test')
        monkeypatch.setenv('NEXTCLOUD_USER', 'kinn')
        monkeypatch.setenv('NEXTCLOUD_APP_PASSWORD', 'secret')
        other = 'https://example.com/s/abc123'
        assert not nextcloud.is_nextcloud_url(other)
        assert nextcloud.prepare_request(other) == (other, {})
        # credentials never go to another server's WebDAV path
        dav = 'https://evil.example.com/remote.php/dav/files/kinn/x.mp4'
        assert nextcloud.prepare_request(dav) == (dav, {})

    def test_no_nextcloud_configured(self, monkeypatch):
        for name in NC_VARS:
            monkeypatch.delenv(name, raising=False)
        url = 'https://cloud.thekinn.test/s/AbC'
        assert nextcloud.prepare_request(url) == (url, {})


# ------------------------------------------------------------------ downloads

class TestDownload:
    def test_plain_share_link(self, nc, tmp_path):
        token = nc.share('/Auto Edit/Add Captions/clip.mp4')
        path = download_file(f'{nc.url()}/s/{token}', str(tmp_path))
        assert path.endswith('.mp4')
        assert open(path, 'rb').read() == VIDEO

    def test_index_php_download_form(self, nc, tmp_path):
        token = nc.share('/Auto Edit/Add Captions/clip.mp4')
        path = download_file(f'{nc.url()}/index.php/s/{token}/download', str(tmp_path))
        assert open(path, 'rb').read() == VIDEO

    def test_file_in_shared_folder(self, nc, tmp_path):
        token = nc.share('/Auto Edit/Add Captions')
        path = download_file(f'{nc.url()}/s/{token}/download?path=/&files=clip.mp4', str(tmp_path))
        assert path.endswith('.mp4')
        assert open(path, 'rb').read() == VIDEO

    def test_password_share_explains(self, nc, tmp_path):
        token = nc.share('/Auto Edit/Add Captions/clip.mp4', password='hunter2')
        with pytest.raises(NextcloudError, match='password-protected'):
            download_file(f'{nc.url()}/s/{token}', str(tmp_path))
        assert os.listdir(tmp_path) == []

    def test_missing_share(self, nc, tmp_path):
        with pytest.raises(NextcloudError, match="couldn't find"):
            download_file(f'{nc.url()}/s/doesnotexist', str(tmp_path))

    def test_private_webdav_path(self, nc, tmp_path):
        url = f'{nc.url()}/remote.php/dav/files/kinn/Auto%20Edit/Add%20Captions/clip.mp4'
        path = download_file(url, str(tmp_path))
        assert open(path, 'rb').read() == VIDEO

    def test_private_webdav_wrong_password(self, nc, tmp_path, monkeypatch):
        monkeypatch.setenv('NEXTCLOUD_APP_PASSWORD', 'wrong')
        url = f'{nc.url()}/remote.php/dav/files/kinn/Auto%20Edit/Add%20Captions/clip.mp4'
        with pytest.raises(NextcloudError, match='app password'):
            download_file(url, str(tmp_path))

    def test_untrusted_domain_explains(self, nc, tmp_path, monkeypatch):
        # Nextcloud only trusts "localhost"; reaching it as 127.0.0.1 is refused
        monkeypatch.setenv('NEXTCLOUD_URL', nc.url('127.0.0.1'))
        token = nc.share('/Auto Edit/Add Captions/clip.mp4')
        with pytest.raises(NextcloudError, match='trusted_domains'):
            download_file(f'{nc.url("127.0.0.1")}/s/{token}', str(tmp_path))

    def test_public_address_fetched_internally(self, nc, tmp_path, monkeypatch):
        # People paste links with the public address, which the container can't resolve
        monkeypatch.setenv('NEXTCLOUD_PUBLIC_URL', 'https://cloud.thekinn.test')
        token = nc.share('/Auto Edit/Add Captions/clip.mp4')
        path = download_file(f'https://cloud.thekinn.test/s/{token}', str(tmp_path))
        assert open(path, 'rb').read() == VIDEO

    def test_other_servers_use_original_path(self, nc, tmp_path):
        # The same /s/<token> link on a server that isn't configured is fetched as-is,
        # exactly as before this change (no rewrite to /download).
        nc.trusted_hosts.add('127.0.0.1')
        token = nc.share('/Auto Edit/Add Captions/clip.mp4')
        path = download_file(f'{nc.url("127.0.0.1")}/s/{token}', str(tmp_path))
        assert open(path, 'rb').read() != VIDEO  # got the share page, as an unrelated site would
        assert ('GET', f'/s/{token}') in nc.requests
        assert ('GET', f'/s/{token}/download') not in nc.requests


# ------------------------------------------------------------------ uploads

class TestUpload:
    def _local(self, tmp_path, name='result.mp3', data=b'ID3fake mp3'):
        p = tmp_path / name
        p.write_bytes(data)
        return str(p)

    def test_upload_returns_working_download_link(self, nc, tmp_path, monkeypatch):
        monkeypatch.setenv('NEXTCLOUD_OUTPUT_FOLDER', 'Auto Edit/Finished')
        link = nextcloud.upload_to_nextcloud(self._local(tmp_path))
        assert '/Auto Edit/Finished/result.mp3' in nc.files
        assert link.endswith('/download')
        assert requests.get(link).content == b'ID3fake mp3'

    def test_link_type_share(self, nc, tmp_path, monkeypatch):
        monkeypatch.setenv('NEXTCLOUD_LINK_TYPE', 'share')
        link = nextcloud.upload_to_nextcloud(self._local(tmp_path))
        assert '/NCA Output/result.mp3' in nc.files  # default folder
        assert not link.endswith('/download') and '/s/' in link

    def test_share_link_uses_public_address(self, nc, tmp_path, monkeypatch):
        monkeypatch.setenv('NEXTCLOUD_PUBLIC_URL', 'https://cloud.thekinn.test')
        link = nextcloud.upload_to_nextcloud(self._local(tmp_path))
        assert link.startswith('https://cloud.thekinn.test/s/')
        # and that public link can be fed straight back in as input
        path = download_file(link, str(tmp_path / 'again'))
        assert open(path, 'rb').read() == b'ID3fake mp3'

    def test_sharing_disabled_explains(self, nc, tmp_path):
        nc.sharing_enabled = False
        with pytest.raises(NextcloudError, match='Sharing'):
            nextcloud.upload_to_nextcloud(self._local(tmp_path))

    def test_bad_login_explains(self, nc, tmp_path, monkeypatch):
        monkeypatch.setenv('NEXTCLOUD_APP_PASSWORD', 'wrong')
        with pytest.raises(NextcloudError, match='app password'):
            nextcloud.upload_to_nextcloud(self._local(tmp_path))

    def test_missing_settings(self, nc, tmp_path, monkeypatch):
        monkeypatch.delenv('NEXTCLOUD_APP_PASSWORD')
        with pytest.raises(NextcloudError, match='NEXTCLOUD_APP_PASSWORD'):
            nextcloud.upload_to_nextcloud(self._local(tmp_path))


# ------------------------------------------------------------------ provider choice

class TestProviderChoice:
    def test_nextcloud_only(self, nc):
        from services.cloud_storage import get_storage_provider, NextcloudProvider
        assert isinstance(get_storage_provider(), NextcloudProvider)

    def test_s3_still_wins_by_default(self, nc, monkeypatch):
        from services.cloud_storage import get_storage_provider, S3CompatibleProvider
        for k, v in {'S3_ENDPOINT_URL': 'http://minio:9000', 'S3_ACCESS_KEY': 'a', 'S3_SECRET_KEY': 'b',
                     'S3_BUCKET_NAME': 'c', 'S3_REGION': 'us-east-1'}.items():
            monkeypatch.setenv(k, v)
        assert isinstance(get_storage_provider(), S3CompatibleProvider)

    def test_explicit_choice(self, nc, monkeypatch):
        from services.cloud_storage import get_storage_provider, NextcloudProvider
        monkeypatch.setenv('S3_ENDPOINT_URL', 'http://minio:9000')
        monkeypatch.setenv('STORAGE_PROVIDER', 'nextcloud')
        assert isinstance(get_storage_provider(), NextcloudProvider)

    def test_unknown_choice(self, nc, monkeypatch):
        from services.cloud_storage import get_storage_provider
        monkeypatch.setenv('STORAGE_PROVIDER', 'dropbox')
        with pytest.raises(ValueError, match='Unknown STORAGE_PROVIDER'):
            get_storage_provider()
