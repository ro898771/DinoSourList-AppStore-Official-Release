r"""Client for Feature 0 (Info/Installed, db0): minimal tool installation check-in.

Plug into your own application:
    import sys
    sys.path.append(r"D:\Development\Telemetry\User-API")
    from info_installed_client import InfoInstalledClient

    client = InfoInstalledClient()  # or InfoInstalledClient(base_url=..., password=...)
    ok, data = client.create(user_name="jdoe", ip_address="10.0.0.5")
    ok, rows = client.list(user_name="jdoe", limit=10)
    ok, data = client.update(data["id"], ip_address="10.0.0.9")
    ok, data = client.delete(data["id"])
"""

from base_client import BaseApiClient


class InfoInstalledClient(BaseApiClient):
    PATH = "/api/info/installed/"

    def create(self, user_name, ip_address, id=None):
        """Returns (True, {...}) on success, (False, None) otherwise.

        The record's datetime is stamped by the server (its own clock), not
        sent by the caller -- callers may be in different countries/timezones.
        """
        payload = {"user_name": user_name, "ip_address": ip_address}
        if id is not None:
            payload["id"] = id
        return self._post(payload)

    def list(self, user_name="All", limit=100):
        """Returns (True, [...]) on success, (False, None) otherwise."""
        return self._get({"user_name": user_name, "limit": limit})

    def update(self, id, user_name=None, ip_address=None):
        """Partially update an existing record identified by "id" -- only the
        fields you pass are changed. Returns (True, {...}) on success, (False, None) otherwise.
        """
        payload = {"id": id}
        if user_name is not None:
            payload["user_name"] = user_name
        if ip_address is not None:
            payload["ip_address"] = ip_address
        return self._put(payload)

    def delete(self, id):
        """Returns (True, {"id": id}) on success, (False, None) otherwise."""
        return self._delete(id)


if __name__ == "__main__":
    client = InfoInstalledClient()

    ok, created = client.create(user_name="jdoe", ip_address="10.0.0.5")
    print("CREATE:", ok, created)

    ok, rows = client.list(user_name="jdoe", limit=10)
    print("LIST:", ok, rows)

    if created:
        ok, updated = client.update(created["id"], ip_address="10.0.0.9")
        print("UPDATE:", ok, updated)

        ok, deleted = client.delete(created["id"])
        print("DELETE:", ok, deleted)
