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



import os
import mimetypes
import boto3
import logging
from botocore.config import Config
from urllib.parse import urlparse, quote

logger = logging.getLogger(__name__)

HOSTED_PROVIDERS = ('amazonaws.com', 'digitaloceanspaces.com')

def _clean_region(region):
    # "None"/blank is allowed in the env docs for some providers; boto3 wants a real value or None
    if not region or str(region).strip().lower() == 'none':
        return 'us-east-1'
    return region

def get_s3_client(endpoint_url, access_key, secret_key, region):
    """S3 client that works with AWS, DO Spaces and self-hosted S3 (MinIO etc.)."""
    host = (urlparse(endpoint_url).hostname or '') if endpoint_url else ''
    default_style = 'auto' if host.endswith(HOSTED_PROVIDERS) else 'path'
    addressing_style = os.getenv('S3_ADDRESSING_STYLE', default_style)
    config_kwargs = {'s3': {'addressing_style': addressing_style}}
    try:
        # boto3 >= 1.36 sends new CRC checksums by default, which many S3-compatible
        # servers and reverse proxies reject (IncompleteBody / Content-Length errors)
        config = Config(request_checksum_calculation='when_required',
                        response_checksum_validation='when_required', **config_kwargs)
    except TypeError:
        config = Config(**config_kwargs)
    session = boto3.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=_clean_region(region)
    )
    return session.client('s3', endpoint_url=endpoint_url, config=config)

def guess_content_type(filename):
    content_type, _ = mimetypes.guess_type(filename)
    return content_type or 'application/octet-stream'

def build_public_url(endpoint_url, bucket_name, key):
    """Public link for an uploaded object.

    S3_PUBLIC_URL (optional) is the full public prefix for objects, e.g.
    https://files.example.com/nca (MinIO behind a public domain) or
    https://files.example.com (R2 custom domain). Lets uploads go to an
    internal endpoint (http://minio:9000) while links stay public.
    """
    encoded_key = quote(key)
    public_base = os.getenv('S3_PUBLIC_URL', '').strip()
    if public_base:
        return f"{public_base.rstrip('/')}/{encoded_key}"
    return f"{endpoint_url.rstrip('/')}/{bucket_name}/{encoded_key}"

def upload_to_s3(file_path, s3_url, access_key, secret_key, bucket_name, region):
    client = get_s3_client(s3_url, access_key, secret_key, region)
    key = os.path.basename(file_path)
    content_type = guess_content_type(key)

    try:
        logger.info(f"Uploading {key} to bucket {bucket_name} with Content-Type {content_type}")
        with open(file_path, 'rb') as data:
            client.upload_fileobj(data, bucket_name, key,
                                  ExtraArgs={'ACL': 'public-read', 'ContentType': content_type})

        return build_public_url(s3_url, bucket_name, key)
    except Exception as e:
        logger.error(f"Error uploading file to S3: {e}")
        raise
