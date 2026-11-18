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

import logging

from flask import Blueprint

from app_utils import queue_task_wrapper
from config import LOCAL_STORAGE_PATH
from services import health
from services.authentication import authenticate

v1_toolkit_health_bp = Blueprint("v1_toolkit_health", __name__)
logger = logging.getLogger(__name__)


@v1_toolkit_health_bp.route("/v1/toolkit/health", methods=["GET"])
@authenticate
@queue_task_wrapper(bypass_queue=True)
def toolkit_health(job_id, data):
    """
    How busy this toolkit is: CPU and memory in use (the container's own when it runs in
    one), disk space for work files, the job queue and its workers, the jobs of the last
    hour, and the build number. Answers at once; it is never queued.
    """
    endpoint = "/v1/toolkit/health"
    try:
        return health.snapshot(LOCAL_STORAGE_PATH, job_id=job_id), endpoint, 200
    except Exception as e:
        logger.error(f"Could not read the toolkit's health: {e}")
        return {"error": f"Could not read the toolkit's health: {e}"}, endpoint, 500
