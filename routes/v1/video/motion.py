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
from services.v1.video import cutout
from services.v1.video import motion as service
from services.v1.video.motion.pieces import DOODLES, LOOKS, PIECES

v1_video_motion_bp = Blueprint("v1_video_motion", __name__)
logger = logging.getLogger(__name__)

ENDPOINT = "/v1/video/motion"

TEXT = {"type": "string", "maxLength": 200}
COLOR = {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"}
WINDOW = {"start": {"type": "number", "minimum": 0}, "duration": {"type": "number", "exclusiveMinimum": 0}}

SCHEMA = {
    "type": "object",
    "properties": {
        "video_url": {"type": "string", "format": "uri"},
        "pieces": {
            "type": "array",
            "maxItems": service.MAX_PIECES,
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": list(PIECES)},
                    "start": {"type": "number", "minimum": 0},
                    "duration": {"type": "number", "exclusiveMinimum": 0},
                    "title": TEXT,
                    "subtitle": TEXT,
                    "image": {"type": "string", "format": "uri"},
                    "sound": {"type": ["string", "null"], "enum": [*service.SOUNDS, None]},
                    "layer": {"type": "string", "enum": ["front", "behind"]},
                    "items": {"type": "array", "maxItems": 6, "items": {"type": "string", "maxLength": 60}},
                    "shape": {"type": "string", "enum": list(DOODLES)},
                    "look": {"type": "string", "enum": ["masthead", "glass"]},
                    "x": {"type": "number", "minimum": 0, "maximum": 100},
                    "y": {"type": "number", "minimum": 0, "maximum": 100},
                },
                "required": ["type", "start"],
                "additionalProperties": False,
            },
        },
        "sounds": {
            "type": "array",
            "maxItems": service.MAX_SOUNDS,
            "items": {
                "type": "object",
                "properties": {
                    "sound": {"type": "string", "enum": list(service.SOUNDS)},
                    "at": {"type": "number", "minimum": 0},
                    "volume": {"type": "number", "minimum": 0, "maximum": 2},
                },
                "required": ["sound", "at"],
                "additionalProperties": False,
            },
        },
        "style": {
            "type": "object",
            "properties": {
                "look": {"type": "string", "enum": list(LOOKS)},
                "accent": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
                "text": {"type": "string", "pattern": "^#[0-9a-fA-F]{6}$"},
                "font": {"type": "string", "pattern": "^[A-Za-z0-9 ._-]{1,60}$"},
            },
            "additionalProperties": False,
        },
        "mask_url": {"type": "string", "format": "uri"},
        "outlines": {
            "type": "array",
            "maxItems": service.MAX_OUTLINES,
            "items": {
                "type": "object",
                "properties": {
                    **WINDOW,
                    "style": {"type": "string", "enum": ["sketch", "sticker"]},
                    "color": COLOR,
                    "width": {"type": "number", "minimum": 0.3, "maximum": 4},
                },
                "additionalProperties": False,
            },
        },
        "zooms": {
            "type": "array",
            "maxItems": service.MAX_ZOOMS,
            "items": {
                "type": "object",
                "properties": {
                    **WINDOW,
                    "style": {"type": "string", "enum": list(service.ZOOM_STYLES)},
                    "scale": {"type": "number", "minimum": 1.0, "maximum": 2.5},
                    "focus_y": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["start", "duration"],
                "additionalProperties": False,
            },
        },
        "sound_volume": {"type": "number", "minimum": 0, "maximum": 2},
        "piece_sounds": {"type": "boolean"},
        "webhook_url": {"type": "string", "format": "uri"},
        "id": {"type": "string"},
    },
    "required": ["video_url"],
    "additionalProperties": False,
}


def fetch_image(url, work_dir):
    return download_file(url, work_dir)


@v1_video_motion_bp.route(ENDPOINT, methods=["POST"])
@authenticate
@validate_payload(SCHEMA)
@queue_task_wrapper(bypass_queue=False)
def motion(job_id, data):
    work_dir = os.path.join(LOCAL_STORAGE_PATH, f"motion_{job_id}")
    os.makedirs(work_dir, exist_ok=True)
    try:
        src = download_file(data["video_url"], work_dir)
        out = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_motion.mp4")
        mask = None
        made_mask = None
        if service.needs_mask(data):
            if data.get("mask_url"):
                mask = download_file(data["mask_url"], work_dir)
            else:
                # No mask kept for this video yet: cut the speaker out now and hand the mask back.
                made_mask = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_mask.mp4")
                cutout.make_mask(src, made_mask)
                mask = made_mask
        try:
            result = service.render_motion(src, out, work_dir, data, fetch_image=fetch_image, mask=mask)
            result["file_url"] = upload_file(out)
            if made_mask:
                result["mask_url"] = upload_file(made_mask)
        finally:
            for path in (out, made_mask):
                if path and os.path.exists(path):
                    os.remove(path)
        logger.info(f"Job {job_id}: motion graphics on {data['video_url']} ({result['pieces']} pieces, {result['sounds']} sounds, {result['seconds']} s)")
        return result, ENDPOINT, 200
    except (service.MotionError, cutout.CutoutError) as e:
        logger.warning(f"Job {job_id}: motion refused: {e}")
        return str(e), ENDPOINT, 400
    except Exception as e:
        logger.exception(f"Job {job_id}: motion failed")
        return str(e), ENDPOINT, 500
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@v1_video_motion_bp.route(ENDPOINT + "/catalog", methods=["GET"])
@authenticate
def catalog():
    """The piece types, looks and sounds this toolkit has, so a dashboard can check it is current."""
    return {
        "pieces": {name: {"duration": d, "sound": s, "shortest": lo, "longest": hi} for name, (d, s, lo, hi) in PIECES.items()},
        "looks": list(LOOKS),
        "layers": ["front", "behind"],
        "doodles": list(DOODLES),
        "sounds": list(service.SOUNDS),
    }, 200
