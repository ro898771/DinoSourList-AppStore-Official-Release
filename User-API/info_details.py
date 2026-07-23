r"""Convenience wrapper around InfoDetailsClient that auto-fills user_name/ip_address
from the local machine (see local_identity.py) -- for reporting tool check-ins
(e.g. a successful download/update) without the caller wiring up LocalIdentity itself.

Plug into your own application:
    import sys
    sys.path.append(r"D:\Development\Telemetry\User-API")
    from info_details import report_tool_checkin

    ok, data = report_tool_checkin(tool_name="QuickMi2e", version="9.0.0.0")
"""

from info_details_client import InfoDetailsClient
from local_identity import LocalIdentity


def report_tool_checkin(tool_name, version, base_url=None, password=None):
    """Record a tool check-in via POST /api/info/details/.

    user_name and ip_address are resolved locally via LocalIdentity -- callers
    only need to supply what's being checked in (tool_name, version).
    Returns (True, {...}) on success, (False, None) otherwise.
    """
    identity = LocalIdentity()
    client = InfoDetailsClient(base_url=base_url, password=password)
    return client.create(
        tool_name=tool_name,
        version=version,
        user_name=identity.get_current_username(),
        ip_address=identity.get_local_ip(),
    )


if __name__ == "__main__":
    ok, data = report_tool_checkin(tool_name="QuickMi2e", version="9.0.0.0")
    print("CHECK-IN:", ok, data)
