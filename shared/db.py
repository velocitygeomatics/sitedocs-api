"""
shared/db.py
Shared database connection pool, auth middleware, and response helpers.
"""
import os
import json
import logging
import psycopg2
import psycopg2.extras
from functools import wraps
from typing import Optional
import azure.functions as func

# Optional Key Vault support
_kv_client = None

def _get_kv_client():
    global _kv_client
    if _kv_client is None:
        from azure.identity import ManagedIdentityCredential
        from azure.keyvault.secrets import SecretClient
        credential = ManagedIdentityCredential()
        _kv_client = SecretClient(
            vault_url=os.environ["KEY_VAULT_URL"],
            credential=credential
        )
    return _kv_client

def _get_secret(name: str) -> str:
    """
    Get secret from Key Vault or environment.
    Key Vault secret names use dashes (e.g. POSTGRES-PASSWORD).
    When USE_KEY_VAULT=true, underscore names are converted to dashes for KV lookup.
    Certain values are app settings resolved via KV references — read from os.environ.
    """
    env_only = {"API_KEY", "POSTGRES_HOST", "POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PORT"}
    if name in env_only:
        return os.environ.get(name, "")

    use_kv = os.environ.get("USE_KEY_VAULT", "false").lower() == "true"
    if use_kv:
        kv_name = name.replace("_", "-")
        return _get_kv_client().get_secret(kv_name).value
    return os.environ.get(name, "")


def get_connection():
    """Return a new psycopg2 connection."""
    return psycopg2.connect(
        host=_get_secret("POSTGRES_HOST"),
        dbname=_get_secret("POSTGRES_DB"),
        user=_get_secret("POSTGRES_USER"),
        password=_get_secret("POSTGRES_PASSWORD"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        sslmode="require",
        connect_timeout=10,
    )


def query(sql: str, params: tuple = None) -> list[dict]:
    """Execute a SELECT and return list of dicts."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

def require_api_key(func_handler):
    """Decorator: validates X-API-Key header."""
    @wraps(func_handler)
    def wrapper(req: func.HttpRequest) -> func.HttpResponse:
        expected = _get_secret("API_KEY")
        provided = req.headers.get("X-API-Key", "")
        if not expected or provided != expected:
            return error(401, "Unauthorized")
        return func_handler(req)
    return wrapper


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------

def ok(data, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps(data, default=str),
        status_code=status_code,
        mimetype="application/json"
    )

def error(status_code: int, message: str) -> func.HttpResponse:
    return func.HttpResponse(
        body=json.dumps({"error": message}),
        status_code=status_code,
        mimetype="application/json"
    )

def not_found(entity: str = "Resource") -> func.HttpResponse:
    return error(404, f"{entity} not found")


# ---------------------------------------------------------------------------
# Pagination helper
# ---------------------------------------------------------------------------

def parse_pagination(req: func.HttpRequest) -> tuple[int, int]:
    """Returns (limit, offset) from ?page=&count= query params."""
    try:
        page = int(req.params.get("page", 0))
        count = min(int(req.params.get("count", 50)), 100)
    except ValueError:
        page, count = 0, 50
    return count, page * count


def paginated_response(data: list, total: int, page: int, count: int) -> dict:
    return {
        "data": data,
        "pagination": {
            "page": page,
            "count": count,
            "total": total,
            "pages": (total + count - 1) // count if count else 0
        }
    }
