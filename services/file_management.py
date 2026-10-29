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
import uuid
import requests
from urllib.parse import urlparse, parse_qs
import mimetypes
from services import nextcloud

def get_extension_from_url(url):
    """Extract file extension from URL or content type.
    
    Args:
        url (str): The URL to extract the extension from
        
    Returns:
        str: The file extension including the dot (e.g., '.jpg')
        
    Raises:
        ValueError: If no valid extension can be determined from the URL or content type
    """
    # First try to get extension from URL
    parsed_url = urlparse(url)
    path = parsed_url.path
    if path:
        ext = os.path.splitext(path)[1].lower()
        if ext:
            return ext

    # If no extension in URL, try to determine from content type
    try:
        response = requests.head(url, allow_redirects=True)
        content_type = response.headers.get('content-type', '').split(';')[0]
        ext = mimetypes.guess_extension(content_type)
        if ext:
            return ext.lower()
    except:
        pass

    # If we can't determine the extension, raise an error
    raise ValueError(f"Could not determine file extension from URL: {url}")

def _download_nextcloud(url, storage_path, file_id):
    """Download a Nextcloud share link or private WebDAV path.

    Share links have no file extension in the URL, so the extension comes from
    the filename Nextcloud sends with the file.
    """
    fetch_url, kwargs = nextcloud.prepare_request(url)
    response = requests.get(fetch_url, stream=True, timeout=nextcloud.REQUEST_TIMEOUT, **kwargs)
    local_filename = None
    try:
        content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
        if response.status_code >= 400 or content_type == 'text/html':
            nextcloud.explain_failure(response, url)

        name = nextcloud.filename_from_response(response) or urlparse(fetch_url).path
        extension = os.path.splitext(name)[1].lower() or mimetypes.guess_extension(content_type) or ''
        if not extension:
            raise ValueError(f"Could not determine file extension from Nextcloud link: {url}")
        local_filename = os.path.join(storage_path, f"{file_id}{extension}")

        with open(local_filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return local_filename
    except Exception:
        if local_filename and os.path.exists(local_filename):
            os.remove(local_filename)
        raise
    finally:
        response.close()


def download_file(url, storage_path="/tmp/"):
    """Download a file from URL to local storage."""
    # Create storage directory if it doesn't exist
    os.makedirs(storage_path, exist_ok=True)

    file_id = str(uuid.uuid4())

    if nextcloud.is_nextcloud_url(url):
        return _download_nextcloud(url, storage_path, file_id)

    extension = get_extension_from_url(url)
    local_filename = os.path.join(storage_path, f"{file_id}{extension}")

    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()

        with open(local_filename, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)

        return local_filename
    except Exception as e:
        if os.path.exists(local_filename):
            os.remove(local_filename)
        raise e

