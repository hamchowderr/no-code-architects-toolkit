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

"""/v1/video/cutout makes the speaker's mask, kept per video for every effect that needs the
person cut out."""

import logging
import os
import shutil

from flask import Blueprint

from app_utils import queue_task_wrapper, validate_payload
from config import LOCAL_STORAGE_PATH
from services.authentication import authenticate
from services.cloud_storage import upload_file
from services.file_management import download_file
from services.v1.video import cutout as service

v1_video_cutout_bp = Blueprint("v1_video_cutout", __name__)
logger = logging.getLogger(__name__)

CUTOUT = "/v1/video/cutout"


@v1_video_cutout_bp.route(CUTOUT, methods=["POST"])
@authenticate
@validate_payload({
    "type": "object",
    "properties": {
        "video_url": {"type": "string", "format": "uri"},
        "webhook_url": {"type": "string", "format": "uri"},
        "id": {"type": "string"},
    },
    "required": ["video_url"],
    "additionalProperties": False,
})
@queue_task_wrapper(bypass_queue=False)
def cut_out(job_id, data):
    work_dir = os.path.join(LOCAL_STORAGE_PATH, f"cutout_{job_id}")
    os.makedirs(work_dir, exist_ok=True)
    out = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_mask.mp4")
    try:
        src = download_file(data["video_url"], work_dir)
        result = service.make_mask(src, out)
        result["mask_url"] = upload_file(out)
        logger.info(f"Job {job_id}: speaker mask for {data['video_url']} ({result['frames']} frames, {result['seconds']} s)")
        return result, CUTOUT, 200
    except service.CutoutError as e:
        logger.warning(f"Job {job_id}: cut-out refused: {e}")
        return str(e), CUTOUT, 400
    except Exception as e:
        logger.exception(f"Job {job_id}: cut-out failed")
        return str(e), CUTOUT, 500
    finally:
        if os.path.exists(out):
            os.remove(out)
        shutil.rmtree(work_dir, ignore_errors=True)
