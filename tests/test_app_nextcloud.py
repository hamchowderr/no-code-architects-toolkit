"""End to end: real toolkit routes and FFmpeg, with Nextcloud links in and Nextcloud storage out.

Whisper and PyTorch are stubbed only when they aren't installed, so these tests run on
a machine without them. None of the endpoints below use transcription.
"""

import json
import os
import shutil
import subprocess
import sys
import types

import pytest
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault('API_KEY', 'test-key')

if shutil.which('ffmpeg') is None:
    pytest.skip('ffmpeg is not installed', allow_module_level=True)

try:
    import whisper  # noqa: F401
except ImportError:
    stub = types.ModuleType('whisper')
    stub.load_model = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('whisper not installed'))
    utils = types.ModuleType('whisper.utils')
    utils.WriteSRT = utils.WriteVTT = object
    stub.utils = utils
    sys.modules['whisper'] = stub
    sys.modules['whisper.utils'] = utils

from tests.fake_nextcloud import FakeNextcloud  # noqa: E402

SRT = """1
00:00:00,000 --> 00:00:01,500
Welcome to The KINN

2
00:00:01,500 --> 00:00:03,000
Auto Edit test
"""


def make_video(path):
    subprocess.run(['ffmpeg', '-y', '-loglevel', 'error',
                    '-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=25:duration=3',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=3',
                    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', path],
                   check=True)


def probe(path):
    out = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path],
                         check=True, capture_output=True, text=True).stdout
    return json.loads(out)


@pytest.fixture(scope='module')
def stack(tmp_path_factory):
    work = tmp_path_factory.mktemp('e2e')
    video = work / 'clip.mp4'
    make_video(str(video))

    nc = FakeNextcloud().start()
    nc.add_file('/Auto Edit/Add Captions/clip.mp4', video.read_bytes())
    nc.add_file('/Auto Edit/Add Captions/clip.srt', SRT.encode())

    env = {
        'STORAGE_PROVIDER': 'nextcloud',
        'NEXTCLOUD_URL': nc.url(),
        'NEXTCLOUD_USER': nc.user,
        'NEXTCLOUD_APP_PASSWORD': nc.app_password,
        'NEXTCLOUD_OUTPUT_FOLDER': 'Auto Edit/Finished',
        'LOCAL_STORAGE_PATH': str(work),
    }
    old = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    for k in ('S3_ENDPOINT_URL', 'GCP_BUCKET_NAME'):
        os.environ.pop(k, None)

    cwd = os.getcwd()
    os.chdir(ROOT)  # routes are discovered relative to the repo
    from app import create_app
    client = create_app().test_client()

    yield nc, client, work

    os.chdir(cwd)
    nc.stop()
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


def call(client, path, payload):
    res = client.post(path, json=payload, headers={'x-api-key': os.environ['API_KEY']})
    body = res.get_json()
    assert res.status_code == 200, body
    return body['response']


def fetch(url, dest):
    data = requests.get(url, timeout=30)
    data.raise_for_status()
    dest.write_bytes(data.content)
    return str(dest)


def test_convert_to_mp3_share_link_in_nextcloud_out(stack):
    nc, client, work = stack
    token = nc.share('/Auto Edit/Add Captions/clip.mp4')
    link = call(client, '/v1/media/convert/mp3', {'media_url': f'{nc.url()}/s/{token}'})

    assert link.startswith(nc.url() + '/s/') and link.endswith('/download')
    saved = [p for p in nc.files if p.startswith('/Auto Edit/Finished/') and p.endswith('.mp3')]
    assert len(saved) == 1
    info = probe(fetch(link, work / 'out.mp3'))
    assert info['format']['format_name'] == 'mp3'
    assert 2.5 < float(info['format']['duration']) < 3.5


def test_caption_video_with_captions_from_nextcloud(stack):
    nc, client, work = stack
    video_token = nc.share('/Auto Edit/Add Captions/clip.mp4')
    srt_token = nc.share('/Auto Edit/Add Captions/clip.srt')
    result = call(client, '/v1/video/caption', {
        'video_url': f'{nc.url()}/s/{video_token}',
        'captions': f'{nc.url()}/s/{srt_token}',
        'settings': {'font_family': 'DejaVu Sans'},  # Arial ships in the Docker image, not everywhere
    })
    link = result if isinstance(result, str) else result.get('url') or result.get('file_url')
    assert link and link.endswith('/download')
    info = probe(fetch(link, work / 'captioned.mp4'))
    assert any(s['codec_type'] == 'video' for s in info['streams'])


def test_output_can_feed_the_next_step(stack):
    """A result link goes straight into another call, the way an n8n chain would use it."""
    nc, client, work = stack
    token = nc.share('/Auto Edit/Add Captions/clip.mp4')
    mp3_link = call(client, '/v1/media/convert/mp3', {'media_url': f'{nc.url()}/s/{token}'})
    again = call(client, '/v1/media/convert/mp3', {'media_url': mp3_link, 'bitrate': '64k'})
    assert again.endswith('/download') and again != mp3_link


def test_password_share_gives_clear_error(stack):
    nc, client, work = stack
    token = nc.share('/Auto Edit/Add Captions/clip.mp4', password='hunter2')
    res = client.post('/v1/media/convert/mp3', json={'media_url': f'{nc.url()}/s/{token}'},
                      headers={'x-api-key': os.environ['API_KEY']})
    assert res.status_code == 500
    assert 'password-protected' in json.dumps(res.get_json())
