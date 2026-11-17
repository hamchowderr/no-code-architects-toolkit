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
Requests for the toolkit's own bucket (fork only). Each answers at once, outside the job
queue, with {"code": 200, "response": {...}, "message": "success"}.

    POST /v1/storage/info     which bucket, its public address; fails if the bucket does not answer
    POST /v1/storage/sign     {key, method: GET|PUT, expires_in, download_name}  -> {url}
    POST /v1/storage/list     {prefix, folders, next_token, max_keys}  -> {files, folders, next_token}
    POST /v1/storage/exists   {key}  -> {exists}
    POST /v1/storage/copy     {source, destination}  -> {key, etag}
    POST /v1/storage/delete   {keys: [...]}  -> {deleted, failed}
    POST /v1/storage/folder   {prefix}  -> {prefix}
"""

import logging

from flask import Blueprint, jsonify

from app_utils import validate_payload
from services.authentication import authenticate
from services.v1.storage import bucket
from services.v1.storage.bucket import StorageError

logger = logging.getLogger(__name__)
v1_storage_bp = Blueprint('v1_storage', __name__)

KEY = {"type": "string", "minLength": 1, "maxLength": 1024}


def _answer(work, *args):
    try:
        return jsonify({"code": 200, "response": work(*args), "message": "success"}), 200
    except StorageError as error:
        return jsonify({"code": error.status, "response": None, "message": str(error)}), error.status
    except Exception as error:
        logger.exception("Storage request failed")
        return jsonify({"code": 500, "response": None, "message": f"Storage request failed: {error}"}), 500


def _body():
    from flask import request
    return request.get_json(silent=True) or {}


@v1_storage_bp.route('/v1/storage/info', methods=['POST'])
@authenticate
def storage_info():
    return _answer(bucket.info)


@v1_storage_bp.route('/v1/storage/sign', methods=['POST'])
@authenticate
@validate_payload({
    "type": "object",
    "properties": {
        "key": KEY,
        "method": {"type": "string", "enum": ["GET", "PUT", "get", "put"]},
        "expires_in": {"type": "integer", "minimum": 1, "maximum": bucket.MAX_LINK_SECONDS},
        "download_name": {"type": "string", "maxLength": 255},
    },
    "required": ["key"],
    "additionalProperties": False,
})
def storage_sign():
    data = _body()
    return _answer(bucket.sign, data['key'], data.get('method', 'GET'),
                   data.get('expires_in', 3600), data.get('download_name'))


@v1_storage_bp.route('/v1/storage/list', methods=['POST'])
@authenticate
@validate_payload({
    "type": "object",
    "properties": {
        "prefix": {"type": "string", "maxLength": 1024},
        "folders": {"type": "boolean"},
        "next_token": {"type": ["string", "null"]},
        "max_keys": {"type": "integer", "minimum": 1, "maximum": bucket.MAX_KEYS},
    },
    "additionalProperties": False,
})
def storage_list():
    data = _body()
    return _answer(bucket.list_files, data.get('prefix', ''), bool(data.get('folders')),
                   data.get('next_token'), data.get('max_keys', bucket.MAX_KEYS))


@v1_storage_bp.route('/v1/storage/exists', methods=['POST'])
@authenticate
@validate_payload({
    "type": "object", "properties": {"key": KEY}, "required": ["key"], "additionalProperties": False,
})
def storage_exists():
    return _answer(bucket.exists, _body()['key'])


@v1_storage_bp.route('/v1/storage/copy', methods=['POST'])
@authenticate
@validate_payload({
    "type": "object",
    "properties": {"source": KEY, "destination": KEY},
    "required": ["source", "destination"],
    "additionalProperties": False,
})
def storage_copy():
    data = _body()
    return _answer(bucket.copy, data['source'], data['destination'])


@v1_storage_bp.route('/v1/storage/delete', methods=['POST'])
@authenticate
@validate_payload({
    "type": "object",
    "properties": {"keys": {"type": "array", "items": KEY, "minItems": 1, "maxItems": bucket.MAX_KEYS}},
    "required": ["keys"],
    "additionalProperties": False,
})
def storage_delete():
    return _answer(bucket.delete, _body()['keys'])


@v1_storage_bp.route('/v1/storage/folder', methods=['POST'])
@authenticate
@validate_payload({
    "type": "object", "properties": {"prefix": KEY}, "required": ["prefix"], "additionalProperties": False,
})
def storage_folder():
    return _answer(bucket.make_folder, _body()['prefix'])
