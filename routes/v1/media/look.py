# Copyright (c) 2026 Otaku Solutions
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

import logging
import os
import shutil

from flask import Blueprint

from app_utils import queue_task_wrapper, validate_payload
from config import LOCAL_STORAGE_PATH
from services.authentication import authenticate
from services.file_management import download_file
from services.v1.media import look as service

v1_media_look_bp = Blueprint("v1_media_look", __name__)
logger = logging.getLogger(__name__)

ENDPOINT = "/v1/media/look"


@v1_media_look_bp.route(ENDPOINT, methods=["POST"])
@authenticate
@validate_payload({
    "type": "object",
    "properties": {
        "media_url": {"type": "string", "format": "uri"},
        "every": {"type": "number", "minimum": 0.5, "maximum": 60},
        "cuts": {"type": "boolean"},
        "frames": {"type": "boolean"},
        "image_urls": {
            "type": "array",
            "maxItems": service.MAX_IMAGES,
            "items": {"type": "string", "format": "uri"},
        },
        "texts": {
            "type": "array",
            "maxItems": service.MAX_TEXTS,
            "items": {"type": "string", "maxLength": 1000},
        },
        "webhook_url": {"type": "string", "format": "uri"},
        "id": {"type": "string"},
    },
    "anyOf": [{"required": ["media_url"]}, {"required": ["image_urls"]}, {"required": ["texts"]}],
    "additionalProperties": False,
})
@queue_task_wrapper(bypass_queue=False)
def look(job_id, data):
    work_dir = os.path.join(LOCAL_STORAGE_PATH, f"look_{job_id}")
    os.makedirs(work_dir, exist_ok=True)
    try:
        media_path = download_file(data["media_url"], work_dir) if data.get("media_url") else None
        pictures = None
        if "image_urls" in data:
            pictures = []
            for index, url in enumerate(data["image_urls"]):
                folder = os.path.join(work_dir, f"p{index}")
                os.makedirs(folder, exist_ok=True)
                try:
                    pictures.append((url, download_file(url, folder)))
                except Exception as e:  # one bad link should not lose the rest
                    logger.warning(f"Job {job_id}: could not download {url}: {e}")
                    pictures.append((url, None))
        result = service.look(
            media_path,
            pictures,
            data.get("texts"),
            data.get("every"),
            cuts=bool(data.get("cuts")),
            frames=data.get("frames", True) is not False,
        )
        logger.info(f"Job {job_id}: looked at {'a file' if media_path else 'no file'}, {len(pictures or [])} pictures, {len(data.get('texts') or [])} texts")
        return result, ENDPOINT, 200
    except service.LookError as e:
        logger.warning(f"Job {job_id}: look refused: {e}")
        return str(e), ENDPOINT, 400
    except Exception as e:
        logger.exception(f"Job {job_id}: look failed")
        return str(e), ENDPOINT, 500
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
