r"""Client for the developer SQL query endpoint -- db2 (Info/Feature) and db4 (Info/Logs) only.

These two databases are the developer-facing ones; the rest back the
Dinosaur-App and aren't meant to be queried/edited ad hoc (see README.md).

Plug into your own application:
    import sys
    sys.path.append(r"D:\Development\Telemetry\User-API")
    from sql_query_client import SqlQueryClient

    client = SqlQueryClient()  # or SqlQueryClient(base_url=..., password=...)
    ok, data = client.query("db4", "SELECT id, tool_name, user_name FROM info_logs LIMIT 10")
    # data == {"rows": [...], "row_count": N, "truncated": False}
"""

from base_client import BaseApiClient


class SqlQueryClient(BaseApiClient):
    PATH = "/api/sql-query/"

    def query(self, database, query):
        """Run a single, read-only SELECT statement against "db2" or "db4".

        Only SELECT is allowed -- no INSERT/UPDATE/DELETE/DROP/etc., and no
        stacked statements (no ";" inside the query). Results are capped at
        1000 rows ("truncated" is True if there were more).

        Returns (True, {"rows": [...], "row_count": N, "truncated": bool}) on
        success, (False, None) if the query was rejected or the request failed.
        """
        return self._post({"database": database, "query": query}, expected_status=200)


if __name__ == "__main__":
    client = SqlQueryClient()

    ok, data = client.query("db4", "SELECT id, tool_name, user_name FROM info_logs LIMIT 5")
    print("QUERY (db4):", ok, data)

    ok, data = client.query("db2", "SELECT COUNT(*) AS total FROM info_feature")
    print("QUERY (db2):", ok, data)

    ok, data = client.query("db1", "SELECT * FROM info_details LIMIT 1")
    print("QUERY (disallowed db, expect False, None):", ok, data)

    ok, data = client.query("db4", "DELETE FROM info_logs WHERE id = 1")
    print("QUERY (non-SELECT, expect False, None):", ok, data)
