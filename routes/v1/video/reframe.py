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
from services.cloud_storage import upload_file
from services.file_management import download_file
from services.v1.video import reframe as service

v1_video_reframe_bp = Blueprint("v1_video_reframe", __name__)
logger = logging.getLogger(__name__)

ENDPOINT = "/v1/video/reframe"


@v1_video_reframe_bp.route(ENDPOINT, methods=["POST"])
@authenticate
@validate_payload({
    "type": "object",
    "properties": {
        "video_url": {"type": "string", "format": "uri"},
        "aspect": {"type": "string", "enum": list(service.ASPECTS)},
        "follow": {"type": "string", "enum": ["speaker", "largest_face", "center"]},
        "switch_after": {"type": "number", "minimum": 0, "maximum": 10},
        "min_hold": {"type": "number", "minimum": 0, "maximum": 30},
        "smoothing": {"type": "number", "minimum": 0, "maximum": 10},
        "output_width": {"type": "integer", "minimum": 144, "maximum": 4320},
        "path": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {"time": {"type": "number", "minimum": 0}, "x": {"type": "number", "minimum": 0}},
                "required": ["time", "x"],
                "additionalProperties": False,
            },
        },
        "webhook_url": {"type": "string", "format": "uri"},
        "id": {"type": "string"},
    },
    "required": ["video_url"],
    "additionalProperties": False,
})
@queue_task_wrapper(bypass_queue=False)
def reframe(job_id, data):
    work_dir = os.path.join(LOCAL_STORAGE_PATH, f"reframe_{job_id}")
    os.makedirs(work_dir, exist_ok=True)
    try:
        src = download_file(data["video_url"], work_dir)
        out = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_reframed.mp4")
        options = {k: data.get(k) for k in ("aspect", "follow", "switch_after", "min_hold", "smoothing", "output_width", "path")}
        result = service.reframe_video(src, out, work_dir, options)
        try:
            result["file_url"] = upload_file(out)
        finally:
            if os.path.exists(out):
                os.remove(out)
        logger.info(f"Job {job_id}: reframed {data['video_url']} ({result['follow']}, {result['switches']} switches)")
        return result, ENDPOINT, 200
    except service.ReframeError as e:
        logger.warning(f"Job {job_id}: reframe refused: {e}")
        return str(e), ENDPOINT, 400
    except Exception as e:
        logger.exception(f"Job {job_id}: reframe failed")
        return str(e), ENDPOINT, 500
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
