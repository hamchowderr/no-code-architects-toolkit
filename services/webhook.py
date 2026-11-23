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

"""
Sends a finished job's result to its webhook_url.

The requests docs say every request should carry a timeout, or it can wait forever; a
receiver that never answers used to hold the worker (and every job behind it) for good.
Brief failures (no connection, 429, 502, 503, 504) are tried again with backoff through
urllib3's Retry, the mechanism requests itself uses for retries. A receiver that took the
POST but answered too slowly is not posted to again, so it does not get the result twice.

Settings:
  WEBHOOK_TIMEOUT   Seconds to wait for the receiver to connect and to answer. Default 30.
  WEBHOOK_RETRIES   Extra tries after a brief failure. Default 3.
"""

import logging
import os

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

RETRY_STATUSES = (429, 502, 503, 504)


def _number(name, default):
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)
    return value if value >= 0 else float(default)


def webhook_timeout():
    return _number("WEBHOOK_TIMEOUT", 30)


def webhook_retries():
    return int(_number("WEBHOOK_RETRIES", 3))


def _session():
    retry = Retry(
        total=webhook_retries(),
        connect=webhook_retries(),
        read=0,  # the receiver may already have the result; do not post it again on a slow answer
        status=webhook_retries(),
        status_forcelist=RETRY_STATUSES,
        allowed_methods=frozenset({"POST"}),
        backoff_factor=1,
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


def send_webhook(webhook_url, data):
    """POSTs the job's result to its webhook URL. Never raises: a failure is logged."""
    job_id = data.get("job_id") if isinstance(data, dict) else None
    try:
        from services.file_management import url_problem
        problem = url_problem(webhook_url)
        if problem:
            raise ValueError(problem)
        with _session() as session:
            response = session.post(
                webhook_url, json=data, timeout=webhook_timeout(), allow_redirects=False
            )
        response.raise_for_status()
        logger.info(f"Webhook sent for job {job_id} ({response.status_code})")
    except (requests.RequestException, ValueError) as e:
        logger.error(f"Webhook failed for job {job_id}: {e}")
