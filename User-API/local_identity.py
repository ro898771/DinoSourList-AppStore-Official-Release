r"""Local machine identity -- current Windows username and outbound IP address.

No API calls -- pure local lookups. Meant to supply the user_name/ip_address
fields the other Telemetry clients expect (e.g. InfoLogsClient.create,
UserToolsClient.create).

Plug into your own application:
    import sys
    sys.path.append(r"D:\Development\Telemetry\User-API")
    from local_identity import LocalIdentity

    identity = LocalIdentity()
    username = identity.get_current_username()
    ip = identity.get_local_ip()
"""

import getpass
import os
import socket


class LocalIdentity:
    def get_current_username(self):
        try:
            return os.getlogin()
        except OSError:
            return getpass.getuser()

    def get_local_ip(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            try:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
            except OSError:
                return socket.gethostbyname(socket.gethostname())


if __name__ == "__main__":
    identity = LocalIdentity()
    print(f"Current user: {identity.get_current_username()}")
    print(f"Local IP: {identity.get_local_ip()}")
