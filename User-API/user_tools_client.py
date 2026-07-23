r"""Client for Feature 3 (User Tools, db3): which tools a user has installed locally.

Plug into your own application:
    import sys
    sys.path.append(r"D:\Development\Telemetry\User-API")
    from user_tools_client import UserToolsClient

    client = UserToolsClient()  # or UserToolsClient(base_url=..., password=...)
    ok, data = client.create("roeyyee", {"DinosaurList": "1.4.2", "BandMaster": "2.0.1"})
    ok, data = client.update("roeyyee", {"QuickMi2e": "3.1.1"})  # merges into the existing tool_list
    ok, rows = client.list(user_name="roeyyee")
"""

from base_client import BaseApiClient


class UserToolsClient(BaseApiClient):
    PATH = "/api/user-tools/"

    def create(self, user_name, tool_list):
        """Create the user's tool list, or fully replace it if the user already exists.

        Returns (True, {...}) on success, (False, None) otherwise.
        """
        return self._post({"user_name": user_name, "tool_list": tool_list}, expected_status=(200, 201))

    def update(self, user_name, tool_list):
        """Merge tool_list into the user's existing one -- each key's latest value wins,
        tools not mentioned are left untouched, unknown users are created.

        Returns (True, {...}) on success, (False, None) otherwise.
        """
        return self._put({"user_name": user_name, "tool_list": tool_list})

    def list(self, user_name="All"):
        """Returns (True, [...]) on success, (False, None) otherwise."""
        return self._get({"user_name": user_name})

    def delete(self, id):
        """Returns (True, {"id": id}) on success, (False, None) otherwise."""
        return self._delete(id)


if __name__ == "__main__":
    client = UserToolsClient()

    ok, created = client.create(
        "ChunHao",
        {"DinosaurList": "1.4.2", "BandMaster": "2.0.1", "QuickMi2e": "3.1.0", "GUQC": "1.0.5"},
    )
    print("CREATE:", ok, created)

    ok, updated = client.update("roeyyee", {"QuickMi2e": "3.1.1", "NewTool": "0.1.0"})
    print("UPDATE (merge):", ok, updated)

    ok, rows = client.list(user_name="roeyyee")
    print("LIST:", ok, rows)
