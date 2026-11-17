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

from botocore.exceptions import ClientError

from services.s3_toolkit import get_s3_client

MAX_LINK_SECONDS = 7 * 24 * 60 * 60  # the SigV4 limit
MAX_KEYS = 1000


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
    """Copies a file inside the bucket (the bytes stay in the bucket). Up to 5 GB."""
    _check_key(source, "source")
    _check_key(destination, "destination")
    conf = settings()
    try:
        answer = _client(conf).copy_object(
            Bucket=conf['bucket'],
            Key=destination,
            CopySource={'Bucket': conf['bucket'], 'Key': source},
        )
    except ClientError as error:
        raise _client_error(error, "copy the file")
    etag = (answer.get('CopyObjectResult', {}).get('ETag') or '').strip('"')
    return {'key': destination, 'etag': etag}


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
