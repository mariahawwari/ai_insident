import requests
import math


class PrometheusConnectionError(Exception):
    """Raised when Prometheus cannot be reached or returns invalid data."""
    pass


class PrometheusQueryError(Exception):
    """Raised when Prometheus returns an error response."""
    pass


class PrometheusClient:
    def __init__(self, base_url="http://localhost:9090", session=None):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()

    def _request(self, query, at=None):
        params = {"query": query}
        if at:
            params["time"] = at

        try:
            resp = self.session.get(
                f"{self.base_url}/api/v1/query",
                params=params,
                timeout=5,
            )
        except Exception as exc:
            raise PrometheusConnectionError(str(exc))

        # Non‑JSON response → connection error
        try:
            body = resp.json()
        except Exception:
            raise PrometheusConnectionError("Invalid JSON from Prometheus")

        # Prometheus error response
        if not resp.ok or body.get("status") == "error":
            raise PrometheusQueryError(body.get("errorType", "unknown"))

        return body

    def query(self, query, at=None):
        return self._request(query, at)

    def query_scalar(self, query, at=None):
        body = self._request(query, at)
        result = body["data"].get("result", [])

        # No data → None
        if not result:
            return None

        # Multiple series → ambiguous
        if len(result) > 1:
            raise PrometheusQueryError("multiple_series")

        _, raw_value = result[0]["value"]

        # Parse special float values
        if raw_value in ("NaN", "nan"):
            return math.nan
        if raw_value in ("+Inf", "Inf", "inf"):
            return float("inf")
        if raw_value in ("-Inf", "-inf"):
            return float("-inf")

        return float(raw_value)


# -------------------------------------------------------------------
# Legacy wrapper (your test suite requires this)
# -------------------------------------------------------------------

def prom_query(query: str, base_url="http://localhost:9090"):
    """
    Legacy wrapper that SWALLOWS errors and returns None.
    Your tests explicitly check this behavior.
    """
    try:
        client = PrometheusClient(base_url=base_url)
        return client.query_scalar(query)
    except Exception:
        return None
