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
from services.v1.audio import beats as service

v1_audio_beats_bp = Blueprint("v1_audio_beats", __name__)
logger = logging.getLogger(__name__)

ENDPOINT = "/v1/audio/beats"


@v1_audio_beats_bp.route(ENDPOINT, methods=["POST"])
@authenticate
@validate_payload({
    "type": "object",
    "properties": {
        "media_url": {"type": "string", "format": "uri"},
        "max_seconds": {"type": "number", "minimum": 1, "maximum": 3600},
        "beats_per_bar": {"type": "integer", "minimum": 3, "maximum": 4},
        "min_bpm": {"type": "number", "minimum": 30, "maximum": 300},
        "max_bpm": {"type": "number", "minimum": 30, "maximum": 300},
        "webhook_url": {"type": "string", "format": "uri"},
        "id": {"type": "string"},
    },
    "required": ["media_url"],
    "additionalProperties": False,
})
@queue_task_wrapper(bypass_queue=False)
def beats(job_id, data):
    low = data.get("min_bpm") or service.MIN_BPM
    high = data.get("max_bpm") or service.MAX_BPM
    if low >= high:
        return "min_bpm must be lower than max_bpm.", ENDPOINT, 400
    work_dir = os.path.join(LOCAL_STORAGE_PATH, f"beats_{job_id}")
    os.makedirs(work_dir, exist_ok=True)
    try:
        path = download_file(data["media_url"], work_dir)
        result = service.find_beats(
            path,
            max_seconds=data.get("max_seconds") or service.DEFAULT_MAX_SECONDS,
            beats_per_bar=data.get("beats_per_bar") or 4,
            min_bpm=low,
            max_bpm=high,
        )
        return result, ENDPOINT, 200
    except ValueError as e:
        return str(e), ENDPOINT, 400
    except Exception as e:
        logger.exception(f"Job {job_id}: finding beats failed")
        return str(e), ENDPOINT, 500
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
