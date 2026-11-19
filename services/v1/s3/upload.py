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
import boto3
import logging
import requests
from urllib.parse import urlparse, unquote, quote
import uuid
import re
from services.s3_toolkit import get_s3_client as s3_toolkit_client, guess_content_type, build_public_url

logger = logging.getLogger(__name__)

def get_s3_client():
    """Create and return an S3 client using environment variables."""
    return s3_toolkit_client(
        os.getenv('S3_ENDPOINT_URL'),
        os.getenv('S3_ACCESS_KEY'),
        os.getenv('S3_SECRET_KEY'),
        os.environ.get('S3_REGION', '')
    )

# The size of every part but the last. 16 MB parts allow files up to 160 GB (S3 and R2
# take at most 10,000 parts), well past a long meeting recording.
PART_SIZE = 16 * 1024 * 1024


def _upload_part(s3_client, bucket_name, key, upload_id, parts, body):
    part_number = len(parts) + 1
    logger.info(f"Uploading part {part_number} ({len(body)} bytes)")
    part = s3_client.upload_part(
        Bucket=bucket_name,
        Key=key,
        PartNumber=part_number,
        UploadId=upload_id,
        Body=body,
    )
    parts.append({'PartNumber': part_number, 'ETag': part['ETag']})


def get_filename_from_url(url):
    """Extract filename from URL."""
    path = urlparse(url).path
    filename = os.path.basename(unquote(path))
    
    # If filename cannot be determined, generate a UUID
    if not filename or filename == '':
        filename = f"{uuid.uuid4()}"
    
    return filename

def stream_upload_to_s3(file_url, custom_filename=None, make_public=False, download_headers=None):
    """
    Stream a file from a URL directly to S3 without saving to disk.
    
    Args:
        file_url (str): URL of the file to download
        custom_filename (str, optional): Custom filename for the uploaded file
        make_public (bool, optional): Whether to make the file publicly accessible
        download_headers (dict, optional): Headers to include in the download request for authentication
    
    Returns:
        dict: Information about the uploaded file
    """
    try:
        # Get S3 configuration
        bucket_name = os.environ.get('S3_BUCKET_NAME', '')
        endpoint_url = os.getenv('S3_ENDPOINT_URL')
        
        # Get S3 client
        s3_client = get_s3_client()
        
        # Determine filename (use custom if provided, otherwise extract from URL)
        if custom_filename:
            filename = custom_filename
        else:
            filename = get_filename_from_url(file_url)
        
        # Start a multipart upload
        logger.info(f"Starting multipart upload for {filename} to bucket {bucket_name}")
        acl = 'public-read' if make_public else 'private'
        
        multipart_upload = s3_client.create_multipart_upload(
            Bucket=bucket_name,
            Key=filename,
            ACL=acl,
            ContentType=guess_content_type(filename)
        )
        
        upload_id = multipart_upload['UploadId']
        parts = []
        try:
            # Stream the file from URL. A source that stops sending is given up on.
            response = requests.get(
                file_url, stream=True, headers=download_headers, timeout=(30, 300)
            )
            response.raise_for_status()

            # Every part but the last is exactly PART_SIZE: Cloudflare R2 refuses to
            # complete an upload whose parts differ in size, and however the download
            # arrives in pieces, the parts are cut the same.
            buffer = bytearray()
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                buffer.extend(chunk)
                while len(buffer) >= PART_SIZE:
                    _upload_part(s3_client, bucket_name, filename, upload_id, parts,
                                 bytes(buffer[:PART_SIZE]))
                    del buffer[:PART_SIZE]

            # Upload any remaining data as the final part (an empty file still needs one)
            if buffer or not parts:
                _upload_part(s3_client, bucket_name, filename, upload_id, parts, bytes(buffer))
        except Exception:
            # Leave no half-made upload behind in the bucket.
            try:
                s3_client.abort_multipart_upload(
                    Bucket=bucket_name, Key=filename, UploadId=upload_id
                )
            except Exception:
                pass
            raise

        # Complete the multipart upload
        logger.info("Completing multipart upload")
        s3_client.complete_multipart_upload(
            Bucket=bucket_name,
            Key=filename,
            UploadId=upload_id,
            MultipartUpload={'Parts': parts}
        )
        
        # Generate the URL to the uploaded file
        if make_public:
            file_url = build_public_url(endpoint_url, bucket_name, filename)
        else:
            # Generate a pre-signed URL for private files
            file_url = s3_client.generate_presigned_url(
                'get_object',
                Params={'Bucket': bucket_name, 'Key': filename},
                ExpiresIn=3600  # URL expires in 1 hour
            )
        
        return {
            'file_url': file_url,
            'filename': filename,  # Return the original filename
            'bucket': bucket_name,
            'public': make_public
        }
        
    except Exception as e:
        logger.error(f"Error streaming file to S3: {e}")
        raise