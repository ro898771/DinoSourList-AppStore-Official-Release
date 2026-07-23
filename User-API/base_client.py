"""Base HTTP plumbing shared by every Telemetry API feature client.

Not meant to be used directly by application code -- subclass it (see
info_details_client.py / info_feature_client.py) for a specific feature.
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()


class BaseApiClient:
    PASSWORD_HEADER = "X-API-Password"
    PATH = None  # set by subclasses

    def __init__(self, base_url=None, password=None, timeout=5):
        self.base_url = (base_url or os.environ.get("TELEMETRY_API_BASE_URL", "http://WNPVDPE01:8000")).rstrip("/")
        self.password = password or os.environ.get("TELEMETRY_API_PASSWORD")
        self.timeout = timeout

    def _headers(self):
        return {self.PASSWORD_HEADER: self.password}

    def _envelope(self, response, expected_status):
        allowed = expected_status if isinstance(expected_status, (set, tuple, list)) else (expected_status,)
        if response.status_code not in allowed:
            return False, None
        try:
            body = response.json()
        except ValueError:
            return False, None
        return bool(body.get("success")), body.get("data")

    def _post(self, payload, expected_status=201):
        try:
            response = requests.post(
                f"{self.base_url}{self.PATH}", json=payload, headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException:
            return False, None
        return self._envelope(response, expected_status)

    def _put(self, payload, expected_status=(200, 201)):
        try:
            response = requests.put(
                f"{self.base_url}{self.PATH}", json=payload, headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException:
            return False, None
        return self._envelope(response, expected_status)

    def _get(self, params, expected_status=200):
        try:
            response = requests.get(
                f"{self.base_url}{self.PATH}", params=params, headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException:
            return False, None
        return self._envelope(response, expected_status)

    def _delete(self, record_id, expected_status=200):
        return self._delete_by({"id": record_id}, expected_status=expected_status)

    def _delete_by(self, payload, expected_status=200):
        """Delete using an arbitrary filter payload (e.g. {"tool_name": ..., "user_name": ...})
        instead of a single id. Returns (True, data) on success, (False, None) otherwise.
        """
        try:
            response = requests.delete(
                f"{self.base_url}{self.PATH}", json=payload, headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException:
            return False, None
        return self._envelope(response, expected_status)
