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

"""/v1/video/background puts a new background (blur, colour, picture or video) behind the
speaker, cut out with their mask."""

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

v1_video_background_bp = Blueprint("v1_video_background", __name__)
logger = logging.getLogger(__name__)

BACKGROUND = "/v1/video/background"


@v1_video_background_bp.route(BACKGROUND, methods=["POST"])
@authenticate
@validate_payload({
    "type": "object",
    "properties": {
        "video_url": {"type": "string", "format": "uri"},
        "mask_url": {"type": "string", "format": "uri"},
        "background": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["blur", "color", "image", "video"]},
                "url": {"type": "string", "format": "uri"},
                "color": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
                "blur": {"type": "number", "minimum": 2, "maximum": 80},
            },
            "required": ["type"],
            "additionalProperties": False,
        },
        "webhook_url": {"type": "string", "format": "uri"},
        "id": {"type": "string"},
    },
    "required": ["video_url", "background"],
    "additionalProperties": False,
})
@queue_task_wrapper(bypass_queue=False)
def new_background(job_id, data):
    work_dir = os.path.join(LOCAL_STORAGE_PATH, f"background_{job_id}")
    os.makedirs(work_dir, exist_ok=True)
    out = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_background.mp4")
    made_mask = None
    try:
        bg = data["background"]
        kind = bg["type"]
        if kind in ("image", "video") and not bg.get("url"):
            raise service.CutoutError(f"A {kind} background needs the {kind}'s link.")
        if kind == "color" and not bg.get("color"):
            raise service.CutoutError("A colour background needs its colour, like #1b3a5c.")
        src = download_file(data["video_url"], work_dir)
        if data.get("mask_url"):
            mask = download_file(data["mask_url"], work_dir)
        else:
            made_mask = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_mask.mp4")
            service.make_mask(src, made_mask)
            mask = made_mask
        background = download_file(bg["url"], work_dir) if kind in ("image", "video") else None
        result = service.replace_background(
            src, mask, out, background=background,
            kind=f"color:{bg['color']}" if kind == "color" else kind, blur=bg.get("blur", 24),
        )
        result["file_url"] = upload_file(out)
        if made_mask:
            result["mask_url"] = upload_file(made_mask)
        logger.info(f"Job {job_id}: new {kind} background on {data['video_url']} ({result['seconds']} s)")
        return result, BACKGROUND, 200
    except service.CutoutError as e:
        logger.warning(f"Job {job_id}: background refused: {e}")
        return str(e), BACKGROUND, 400
    except Exception as e:
        logger.exception(f"Job {job_id}: background failed")
        return str(e), BACKGROUND, 500
    finally:
        for path in (out, made_mask):
            if path and os.path.exists(path):
                os.remove(path)
        shutil.rmtree(work_dir, ignore_errors=True)
