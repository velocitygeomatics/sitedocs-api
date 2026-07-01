"""
routes/health.py
Unauthenticated health check endpoint.
"""

import azure.functions as func
from shared.db import query, ok, error

bp = func.Blueprint()


@bp.route(route="health", methods=["GET"])
def health(req: func.HttpRequest) -> func.HttpResponse:
    """GET /api/health - unauthenticated lightweight check."""
    try:
        query("SELECT 1")
        return ok({"status": "ok"})
    except Exception as e:
        return error(503, f"db unreachable: {e}")
