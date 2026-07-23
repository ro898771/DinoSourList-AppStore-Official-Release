r"""Client for Feature 4 (Info/Logs, db4): tool user logs, with a lengthy log_content field.

Plug into your own application:
    import sys
    sys.path.append(r"D:\Development\Telemetry\User-API")
    from info_logs_client import InfoLogsClient

    client = InfoLogsClient()  # or InfoLogsClient(base_url=..., password=...)
    ok, data = client.create(tool_name="QuickMi2e", version="1.0.0.1", user_name="jdoe",
                              ip_address="10.0.0.5", log_content="...")
    ok, rows = client.list(tool_name="QuickMi2e", limit=10)
    ok, rows = client.list(tool_name="QuickMi2e", version="1.0.0.1", user_name="jdoe",
                            datetime_after="2026-01-01T00:00:00+08:00", datetime_before="2026-12-31T23:59:59+08:00")
    ok, data = client.update(data["id"], log_content="...more log text appended...")
    ok, data = client.delete(data["id"])
    ok, data = client.delete_by_content(tool_name="QuickMi2e", user_name="jdoe")  # deletes every matching row
"""

from base_client import BaseApiClient


class InfoLogsClient(BaseApiClient):
    PATH = "/api/info/logs/"

    def create(self, tool_name, version, user_name, ip_address, log_content, id=None):
        """Returns (True, {...}) on success, (False, None) otherwise.

        The record's datetime is stamped by the server (its own clock), not
        sent by the caller -- callers may be in different countries/timezones.
        """
        payload = {
            "tool_name": tool_name,
            "version": version,
            "user_name": user_name,
            "ip_address": ip_address,
            "log_content": log_content,
        }
        if id is not None:
            payload["id"] = id
        return self._post(payload)

    def update(self, id, tool_name=None, version=None, user_name=None, ip_address=None, log_content=None):
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
        if log_content is not None:
            payload["log_content"] = log_content
        return self._put(payload)

    def list(self, tool_name="All", version="All", user_name="All", limit=100,
              datetime_after=None, datetime_before=None):
        """Returns (True, [...]) on success, (False, None) otherwise.

        datetime_after/datetime_before (ISO-8601 strings) filter by "datetime":
        only "after" -> from that point onward; only "before" -> up to that point;
        both -> everything in between (inclusive).
        """
        params = {"tool_name": tool_name, "version": version, "user_name": user_name, "limit": limit}
        if datetime_after is not None:
            params["datetime_after"] = datetime_after
        if datetime_before is not None:
            params["datetime_before"] = datetime_before
        return self._get(params)

    def delete(self, id):
        """Returns (True, {"id": id}) on success, (False, None) otherwise."""
        return self._delete(id)

    def delete_by_content(self, tool_name, user_name):
        """Delete every log entry matching both tool_name and user_name (not just one row).

        Returns (True, {"tool_name": ..., "user_name": ..., "deleted_count": N}) on success,
        (False, None) if nothing matched or the request failed.
        """
        return self._delete_by({"tool_name": tool_name, "user_name": user_name})


if __name__ == "__main__":
    client = InfoLogsClient()

    ok, created = client.create(
        tool_name="QuickMi2e",
        version="1.0.0.1",
        user_name="jdoe",
        ip_address="10.0.0.5",
        log_content="Step 1: started export\nStep 2: wrote 500 rows\nStep 3: done",
    )
    print("CREATE:", ok, created)

    ok, rows = client.list(tool_name="QuickMi2e", limit=10)
    print("LIST:", ok, rows)

    if created:
        ok, deleted = client.delete(created["id"])
        print("DELETE:", ok, deleted)

    ok, deleted_bulk = client.delete_by_content(tool_name="QuickMi2e", user_name="jdoe")
    print("DELETE (by content):", ok, deleted_bulk)
