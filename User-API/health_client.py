r"""Client for the Telemetry API health check (GET /api/health/).

Not an envelope-style ({"success", "data"}) endpoint like the feature clients --
it just returns {"status": "ok"} -- so this checks the response directly
instead of going through BaseApiClient._get/_envelope.

Plug into your own application:
    import sys
    sys.path.append(r"D:\Development\Telemetry\User-API")
    from health_client import HealthClient

    client = HealthClient()  # or HealthClient(base_url=..., password=...)
    ok, detail = client.check()
"""

import requests

from base_client import BaseApiClient


class HealthClient(BaseApiClient):
    PATH = "/api/health/"

    def check(self):
        """Returns (True, "ok") if the API is reachable and reports healthy,
        (False, <reason>) otherwise -- reason describes the HTTP status or error.
        """
        try:
            response = requests.get(
                f"{self.base_url}{self.PATH}", headers=self._headers(), timeout=self.timeout
            )
        except requests.RequestException as exc:
            return False, str(exc)

        if response.status_code != 200:
            return False, f"HTTP {response.status_code}"

        try:
            body = response.json()
        except ValueError:
            return False, "non-JSON response"

        if body.get("status") == "ok":
            return True, "ok"
        return False, str(body)


if __name__ == "__main__":
    client = HealthClient()
    ok, detail = client.check()
    print("HEALTH:", ok, detail)
