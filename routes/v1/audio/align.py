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
from services.v1.audio import align as service

v1_audio_align_bp = Blueprint("v1_audio_align", __name__)
logger = logging.getLogger(__name__)

ENDPOINT = "/v1/audio/align"


@v1_audio_align_bp.route(ENDPOINT, methods=["POST"])
@authenticate
@validate_payload({
    "type": "object",
    "properties": {
        "reference_url": {"type": "string", "format": "uri"},
        "media_url": {"type": "string", "format": "uri"},
        "max_seconds": {"type": "number", "minimum": 10, "maximum": 7200},
        "max_shift": {"type": "number", "minimum": 1, "maximum": 3600},
        "webhook_url": {"type": "string", "format": "uri"},
        "id": {"type": "string"},
    },
    "required": ["reference_url", "media_url"],
    "additionalProperties": False,
})
@queue_task_wrapper(bypass_queue=False)
def align(job_id, data):
    work_dir = os.path.join(LOCAL_STORAGE_PATH, f"align_{job_id}")
    os.makedirs(os.path.join(work_dir, "reference"), exist_ok=True)
    os.makedirs(os.path.join(work_dir, "other"), exist_ok=True)
    try:
        reference = download_file(data["reference_url"], os.path.join(work_dir, "reference"))
        other = download_file(data["media_url"], os.path.join(work_dir, "other"))
        result = service.align(
            reference,
            other,
            max_seconds=data.get("max_seconds") or service.DEFAULT_MAX_SECONDS,
            max_shift=data.get("max_shift") or service.DEFAULT_MAX_SHIFT,
        )
        return result, ENDPOINT, 200
    except ValueError as e:
        return str(e), ENDPOINT, 400
    except Exception as e:
        logger.exception(f"Job {job_id}: lining up the recordings failed")
        return str(e), ENDPOINT, 500
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
