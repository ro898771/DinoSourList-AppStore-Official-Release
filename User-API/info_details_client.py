r"""Client for Feature 1 (Info/Details, db1): tool check-in info.

Plug into your own application:
    import sys
    sys.path.append(r"D:\Development\Telemetry\User-API")
    from info_details_client import InfoDetailsClient

    client = InfoDetailsClient()  # or InfoDetailsClient(base_url=..., password=...)
    ok, data = client.create(tool_name="QuickMi2e", version="1.0.0.1", user_name="jdoe",
                              ip_address="10.0.0.5")
    ok, rows = client.list(tool_name="QuickMi2e", limit=10)
    ok, data = client.update(data["id"], version="1.0.0.2")
    ok, data = client.delete(data["id"])
"""

from base_client import BaseApiClient


class InfoDetailsClient(BaseApiClient):
    PATH = "/api/info/details/"

    def create(self, tool_name, version, user_name, ip_address, id=None):
        """Returns (True, {...}) on success, (False, None) otherwise.

        The record's datetime is stamped by the server (its own clock), not
        sent by the caller -- callers may be in different countries/timezones.
        """
        payload = {
            "tool_name": tool_name,
            "version": version,
            "user_name": user_name,
            "ip_address": ip_address,
        }
        if id is not None:
            payload["id"] = id
        return self._post(payload)

    def update(self, id, tool_name=None, version=None, user_name=None, ip_address=None):
        """Partially update an existing record identified by "id" -- only the
        fields you pass are changed. Returns (True, {...}) on success, (False, None) otherwise.
        """
        payload = {"id": id}
        if tool_name is not None:
            payload["tool_name"] = tool_name
        if version is not None:
            payload["version"] = version
        if user_name is not None:
            payload["user_name"] = user_name
        if ip_address is not None:
            payload["ip_address"] = ip_address
        return self._put(payload)

    def list(self, tool_name="All", limit=100):
        """Returns (True, [...]) on success, (False, None) otherwise."""
        return self._get({"tool_name": tool_name, "limit": limit})

    def delete(self, id):
        """Returns (True, {"id": id}) on success, (False, None) otherwise."""
        return self._delete(id)


if __name__ == "__main__":
    client = InfoDetailsClient()

    ok, created = client.create(
        tool_name="QuickMi2e",
        version="1.0.0.1",
        user_name="jdoe",
        ip_address="10.0.0.5",
    )
    print("CREATE:", ok, created)

    ok, rows = client.list(tool_name="QuickMi2e", limit=10)
    print("LIST:", ok, rows)

    # if created:
    #     ok, deleted = client.delete(created["id"])
    #     print("DELETE:", ok, deleted)
