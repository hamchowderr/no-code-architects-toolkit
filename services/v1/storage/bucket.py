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


"""
The toolkit's own bucket, for apps that sit in front of the toolkit (such as ncat-dash).

An app only needs the toolkit's address and API key: it asks the toolkit for signed links
and listings, and the storage keys (S3_ACCESS_KEY / S3_SECRET_KEY) never leave the toolkit.
Signed links go straight to the bucket, so file bytes never pass through the toolkit.
"""

import os
from datetime import timezone

from boto3.s3.transfer import TransferConfig
from botocore.exceptions import ClientError

from services.s3_toolkit import get_s3_client

MAX_LINK_SECONDS = 7 * 24 * 60 * 60  # the SigV4 limit
MAX_KEYS = 1000
# S3 and R2 take at most 10,000 parts per file, each part up to 5 GiB.
MAX_PARTS = 10000
# Parts signed in one request, so a browser can ask for the next batch as it goes.
MAX_SIGNED_PARTS = 1000
# A file this size or more is copied in parts: one CopyObject call stops at 5 GiB.
COPY_IN_PARTS_FROM = 4 * 1024 ** 3
COPY_PART_SIZE = 512 * 1024 ** 2


class StorageError(Exception):
    """A problem the caller can fix or should be told about. `status` is the HTTP code."""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def settings():
    """The bucket the toolkit uploads to, read from the same S3_* settings as every upload."""
    endpoint = os.getenv('S3_ENDPOINT_URL', '').strip()
    if not endpoint:
        raise StorageError("This toolkit has no S3-compatible storage set up (S3_ENDPOINT_URL).", 501)
    # Same rules as uploads, including reading the bucket out of a Spaces address.
    from services.cloud_storage import S3CompatibleProvider
    provider = S3CompatibleProvider()
    if not provider.bucket_name:
        raise StorageError("This toolkit has no bucket set (S3_BUCKET_NAME).", 501)
    return {
        'endpoint': provider.endpoint_url,
        # Where browsers reach the bucket, when the toolkit talks to it on an internal
        # address (http://minio:9000). Signed links are made for this address.
        'link_endpoint': os.getenv('S3_LINK_ENDPOINT_URL', '').strip() or provider.endpoint_url,
        'access_key': provider.access_key,
        'secret_key': provider.secret_key,
        'bucket': provider.bucket_name,
        'region': provider.region,
        'public_url': os.getenv('S3_PUBLIC_URL', '').strip().rstrip('/') or None,
    }


def _client(conf, for_links=False):
    endpoint = conf['link_endpoint'] if for_links else conf['endpoint']
    return get_s3_client(endpoint, conf['access_key'], conf['secret_key'], conf['region'])


def _check_key(key, what="key"):
    if not isinstance(key, str) or not key or len(key.encode('utf-8')) > 1024:
        raise StorageError(f"A {what} must be 1 to 1024 bytes long.")
    if key.startswith('/'):
        raise StorageError(f"A {what} cannot start with a slash.")
    return key


def _client_error(error, doing):
    code = error.response.get('Error', {}).get('Code', '')
    status = error.response.get('ResponseMetadata', {}).get('HTTPStatusCode', 502)
    if code in ('NoSuchKey', '404', 'NotFound'):
        return StorageError("No file at that key.", 404)
    if code in ('AccessDenied', '403') or status == 403:
        return StorageError(f"The bucket refused to {doing} (access denied).", 502)
    if code == 'NoSuchBucket':
        return StorageError("The bucket set on this toolkit does not exist.", 502)
    return StorageError(f"The bucket would not {doing} ({code or status}).", 502)


def info():
    """
    Which bucket this toolkit uses and whether it answers. `endpoint` and `public_url` are
    the two addresses the toolkit's own output links start with, so an app can tell which
    links point into this bucket.
    """
    conf = settings()
    try:
        _client(conf).list_objects_v2(Bucket=conf['bucket'], MaxKeys=1)
    except ClientError as error:
        raise _client_error(error, "list files")
    except Exception:
        raise StorageError("The toolkit could not reach its bucket.", 502)
    return {'provider': 's3', 'bucket': conf['bucket'], 'endpoint': conf['endpoint'].rstrip('/'),
            'public_url': conf['public_url']}


def sign(key, method='GET', expires_in=3600, download_name=None):
    """A link that lets its holder GET (view, download) or PUT (upload) one file for a while."""
    _check_key(key)
    method = (method or 'GET').upper()
    if method not in ('GET', 'PUT'):
        raise StorageError("method must be GET or PUT.")
    expires_in = int(expires_in)
    if expires_in < 1 or expires_in > MAX_LINK_SECONDS:
        raise StorageError(f"expires_in must be between 1 and {MAX_LINK_SECONDS} seconds.")
    conf = settings()
    params = {'Bucket': conf['bucket'], 'Key': key}
    if download_name and method == 'GET':
        # Makes the link download the file under this name instead of opening it.
        safe = download_name.replace('"', '').replace('\\', '').replace('\n', ' ').replace('\r', ' ')
        params['ResponseContentDisposition'] = f'attachment; filename="{safe}"'
    url = _client(conf, for_links=True).generate_presigned_url(
        'get_object' if method == 'GET' else 'put_object',
        Params=params,
        ExpiresIn=expires_in,
        HttpMethod=method,
    )
    return {'url': url, 'method': method, 'expires_in': expires_in}


def _millis(when):
    if when is None:
        return 0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return int(when.timestamp() * 1000)


def list_files(prefix='', folders=False, token=None, max_keys=MAX_KEYS):
    """
    One page of files under `prefix`. With `folders`, only the files straight inside it plus
    the folders one level down, which is how a flat bucket is read as a tree.
    """
    if prefix:
        _check_key(prefix, "prefix")
    max_keys = int(max_keys)
    if max_keys < 1 or max_keys > MAX_KEYS:
        raise StorageError(f"max_keys must be between 1 and {MAX_KEYS}.")
    conf = settings()
    params = {'Bucket': conf['bucket'], 'MaxKeys': max_keys}
    if prefix:
        params['Prefix'] = prefix
    if folders:
        params['Delimiter'] = '/'
    if token:
        params['ContinuationToken'] = token
    try:
        page = _client(conf).list_objects_v2(**params)
    except ClientError as error:
        raise _client_error(error, "list files")
    return {
        'files': [
            {
                'key': item['Key'],
                'etag': (item.get('ETag') or '').strip('"'),
                'size': int(item.get('Size') or 0),
                'last_modified': _millis(item.get('LastModified')),
            }
            for item in page.get('Contents', [])
        ],
        'folders': [item['Prefix'] for item in page.get('CommonPrefixes', [])],
        'next_token': page.get('NextContinuationToken') if page.get('IsTruncated') else None,
    }


def exists(key):
    """Whether a file is at this key or, for a key ending in "/", anything is inside that folder."""
    _check_key(key)
    conf = settings()
    client = _client(conf)
    try:
        if key.endswith('/'):
            page = client.list_objects_v2(Bucket=conf['bucket'], Prefix=key, MaxKeys=1)
            return {'exists': bool(page.get('Contents'))}
        client.head_object(Bucket=conf['bucket'], Key=key)
        return {'exists': True}
    except ClientError as error:
        status = error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        if status == 404 or error.response.get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound'):
            return {'exists': False}
        raise _client_error(error, "check the file")


def copy(source, destination):
    """
    Copies a file inside the bucket (the bytes stay in the bucket). A file of 4 GiB or more
    is copied in 512 MiB parts, side by side, since one copy call stops at 5 GiB.
    """
    _check_key(source, "source")
    _check_key(destination, "destination")
    conf = settings()
    client = _client(conf)
    try:
        size = int(client.head_object(Bucket=conf['bucket'], Key=source).get('ContentLength') or 0)
        if size >= COPY_IN_PARTS_FROM:
            client.copy(
                {'Bucket': conf['bucket'], 'Key': source},
                conf['bucket'],
                destination,
                Config=TransferConfig(
                    multipart_threshold=COPY_IN_PARTS_FROM,
                    multipart_chunksize=COPY_PART_SIZE,
                    max_concurrency=10,
                ),
            )
            etag = (client.head_object(Bucket=conf['bucket'], Key=destination).get('ETag') or '').strip('"')
            return {'key': destination, 'etag': etag}
        answer = client.copy_object(
            Bucket=conf['bucket'],
            Key=destination,
            CopySource={'Bucket': conf['bucket'], 'Key': source},
        )
    except ClientError as error:
        raise _client_error(error, "copy the file")
    etag = (answer.get('CopyObjectResult', {}).get('ETag') or '').strip('"')
    return {'key': destination, 'etag': etag}


def _check_upload_id(upload_id):
    if not isinstance(upload_id, str) or not upload_id or len(upload_id) > 1024:
        raise StorageError("upload_id must be the id that /v1/storage/upload/start gave.")
    return upload_id


def _upload_error(error, doing):
    code = error.response.get('Error', {}).get('Code', '')
    if code == 'NoSuchUpload':
        return StorageError("That upload was finished, cancelled or never started.", 404)
    if code in ('InvalidPart', 'InvalidPartOrder'):
        return StorageError("A part is missing or its etag does not match what the bucket got.", 400)
    if code == 'EntityTooSmall':
        return StorageError("Every part but the last must be at least 5 MiB.", 400)
    return _client_error(error, doing)


def start_upload(key, content_type=None):
    """Starts an upload in parts, for files too big for one PUT (over 5 GiB) or worth sending side by side."""
    _check_key(key)
    conf = settings()
    params = {'Bucket': conf['bucket'], 'Key': key}
    if content_type:
        params['ContentType'] = content_type
    try:
        answer = _client(conf).create_multipart_upload(**params)
    except ClientError as error:
        raise _upload_error(error, "start the upload")
    return {'key': key, 'upload_id': answer['UploadId']}


def sign_parts(key, upload_id, part_numbers, expires_in=3600):
    """One PUT link per part. The bucket answers each part with an ETag header, which complete needs."""
    _check_key(key)
    _check_upload_id(upload_id)
    if not isinstance(part_numbers, list) or not part_numbers or len(part_numbers) > MAX_SIGNED_PARTS:
        raise StorageError(f"part_numbers must list 1 to {MAX_SIGNED_PARTS} parts.")
    for number in part_numbers:
        if not isinstance(number, int) or isinstance(number, bool) or number < 1 or number > MAX_PARTS:
            raise StorageError(f"Part numbers go from 1 to {MAX_PARTS}.")
    expires_in = int(expires_in)
    if expires_in < 1 or expires_in > MAX_LINK_SECONDS:
        raise StorageError(f"expires_in must be between 1 and {MAX_LINK_SECONDS} seconds.")
    conf = settings()
    client = _client(conf, for_links=True)
    return {
        'parts': [
            {
                'part_number': number,
                'url': client.generate_presigned_url(
                    'upload_part',
                    Params={'Bucket': conf['bucket'], 'Key': key, 'UploadId': upload_id, 'PartNumber': number},
                    ExpiresIn=expires_in,
                    HttpMethod='PUT',
                ),
            }
            for number in part_numbers
        ],
        'expires_in': expires_in,
    }


def complete_upload(key, upload_id, parts):
    """Joins the parts into one file. `parts` lists every part's number and the ETag the bucket gave it."""
    _check_key(key)
    _check_upload_id(upload_id)
    if not isinstance(parts, list) or not parts or len(parts) > MAX_PARTS:
        raise StorageError(f"parts must list 1 to {MAX_PARTS} parts.")
    listed = []
    for part in parts:
        number = part.get('part_number') if isinstance(part, dict) else None
        etag = part.get('etag') if isinstance(part, dict) else None
        if not isinstance(number, int) or isinstance(number, bool) or number < 1 or number > MAX_PARTS:
            raise StorageError(f"Part numbers go from 1 to {MAX_PARTS}.")
        if not isinstance(etag, str) or not etag.strip('"'):
            raise StorageError(f"Part {number} has no etag.")
        listed.append({'PartNumber': number, 'ETag': '"' + etag.strip('"') + '"'})
    listed.sort(key=lambda part: part['PartNumber'])
    if len({part['PartNumber'] for part in listed}) != len(listed):
        raise StorageError("A part number is listed twice.")
    conf = settings()
    try:
        answer = _client(conf).complete_multipart_upload(
            Bucket=conf['bucket'], Key=key, UploadId=upload_id, MultipartUpload={'Parts': listed},
        )
    except ClientError as error:
        raise _upload_error(error, "finish the upload")
    return {'key': key, 'etag': (answer.get('ETag') or '').strip('"')}


def abort_upload(key, upload_id):
    """Cancels an upload in parts and drops the parts already sent. One that is already gone is fine."""
    _check_key(key)
    _check_upload_id(upload_id)
    conf = settings()
    try:
        _client(conf).abort_multipart_upload(Bucket=conf['bucket'], Key=key, UploadId=upload_id)
    except ClientError as error:
        if error.response.get('Error', {}).get('Code') != 'NoSuchUpload':
            raise _upload_error(error, "cancel the upload")
    return {'key': key, 'cancelled': True}


def delete(keys):
    """Deletes up to 1000 files. A key that is already gone counts as deleted."""
    if not isinstance(keys, list) or not keys or len(keys) > MAX_KEYS:
        raise StorageError(f"keys must list 1 to {MAX_KEYS} files.")
    for key in keys:
        _check_key(key)
    conf = settings()
    client = _client(conf)
    failed = []
    for key in keys:
        # One request per file: some S3-compatible services reject the bulk DeleteObjects call.
        try:
            client.delete_object(Bucket=conf['bucket'], Key=key)
        except ClientError as error:
            status = error.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
            if status != 404:
                failed.append(key)
    return {'deleted': [key for key in keys if key not in failed], 'failed': failed}


def make_folder(prefix):
    """An empty file whose key ends in "/": what every S3 console shows as an empty folder."""
    _check_key(prefix, "prefix")
    if not prefix.endswith('/'):
        prefix += '/'
    conf = settings()
    try:
        _client(conf).put_object(Bucket=conf['bucket'], Key=prefix, Body=b'')
    except ClientError as error:
        raise _client_error(error, "make the folder")
    return {'prefix': prefix}
