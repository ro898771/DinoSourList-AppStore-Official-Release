r"""Client for Feature 2 (Info/Feature, db2): tool feature usage.

Plug into your own application:
    import sys
    sys.path.append(r"D:\Development\Telemetry\User-API")
    from info_feature_client import InfoFeatureClient

    client = InfoFeatureClient()  # or InfoFeatureClient(base_url=..., password=...)
    ok, data = client.create(tool_name="QuickMi2e", version="1.0.0.1", feature_name="Export", user_name="jdoe",
                              ip_address="10.0.0.5")
    ok, rows = client.list(tool_name="QuickMi2e", feature_name="Export", limit=10)
    ok, data = client.update(data["id"], version="1.0.0.2")
    ok, data = client.delete(data["id"])
"""

from base_client import BaseApiClient


class InfoFeatureClient(BaseApiClient):
    PATH = "/api/info/feature/"

    def create(self, tool_name, version, feature_name, user_name, ip_address, id=None):
        """Returns (True, {...}) on success, (False, None) otherwise.

        The record's datetime is stamped by the server (its own clock), not
        sent by the caller -- callers may be in different countries/timezones.
        """
        payload = {
            "tool_name": tool_name,
            "version": version,
            "feature_name": feature_name,
            "user_name": user_name,
            "ip_address": ip_address,
        }
        if id is not None:
            payload["id"] = id
        return self._post(payload)

    def update(self, id, tool_name=None, version=None, feature_name=None, user_name=None, ip_address=None):
        """Partially update an existing record identified by "id" -- only the
        fields you pass are changed. Returns (True, {...}) on success, (False, None) otherwise.
        """
        payload = {"id": id}
        if tool_name is not None:
            payload["tool_name"] = tool_name
        if version is not None:
            payload["version"] = version
        if feature_name is not None:
            payload["feature_name"] = feature_name
        if user_name is not None:
            payload["user_name"] = user_name
        if ip_address is not None:
            payload["ip_address"] = ip_address
        return self._put(payload)

    def list(self, tool_name="All", feature_name="All", version="All", user_name="All", limit=100,
              datetime_after=None, datetime_before=None):
        """Returns (True, [...]) on success, (False, None) otherwise.

        datetime_after/datetime_before (ISO-8601 strings) filter by "datetime":
        only "after" -> from that point onward; only "before" -> up to that point;
        both -> everything in between (inclusive).
        """
        params = {
            "tool_name": tool_name,
            "feature_name": feature_name,
            "version": version,
            "user_name": user_name,
            "limit": limit,
        }
        if datetime_after is not None:
            params["datetime_after"] = datetime_after
        if datetime_before is not None:
            params["datetime_before"] = datetime_before
        return self._get(params)

    def delete(self, id):
        """Returns (True, {"id": id}) on success, (False, None) otherwise."""
        return self._delete(id)


if __name__ == "__main__":
    client = InfoFeatureClient()

    ok, created = client.create(
        tool_name="QuickMi2e",
        version="1.0.0.1",
        feature_name="Export",
        user_name="Roey",
        ip_address="10.0.0.5",
    )
    print("CREATE:", ok, created)

    ok, rows = client.list(tool_name="QuickMi2e", feature_name="Export", limit=10)
    print("LIST:", ok, rows)

    ok, rows = client.list(tool_name="QuickMi2e", version="1.0.0.1", user_name="Roey",
                            datetime_after="2026-01-01T00:00:00+08:00")
    print("LIST (filtered):", ok, rows)

    # if created:
    #     ok, deleted = client.delete(created["id"])
    #     print("DELETE:", ok, deleted)
