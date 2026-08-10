import os
from datetime import datetime, timezone

import requests
from fastapi import HTTPException


ACCESS_TABLE = "access_requests"


def _supabase_settings():
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    if url.endswith("/rest/v1"):
        url = url[: -len("/rest/v1")]
    return {
        "url": url,
        "anon": os.environ.get("SUPABASE_ANON_KEY", ""),
        "service": os.environ.get("SUPABASE_SERVICE_ROLE_KEY", ""),
        "admin": os.environ.get("HITIM_ADMIN_EMAIL", "").strip().lower(),
    }


def public_auth_config():
    settings = _supabase_settings()
    configured = bool(
        settings["url"] and settings["anon"] and settings["service"] and settings["admin"]
    )
    return {
        "configured": configured,
        "supabaseUrl": settings["url"] if configured else "",
        "supabaseAnonKey": settings["anon"] if configured else "",
        "provider": "google",
    }


def _require_configuration():
    settings = _supabase_settings()
    if not all((settings["url"], settings["anon"], settings["service"], settings["admin"])):
        raise HTTPException(status_code=503, detail="Hitim authentication is not configured")
    return settings


def _bearer_token(authorization):
    value = str(authorization or "").strip()
    if not value.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Google sign-in is required")
    token = value.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Invalid session")
    return token


def verified_user(authorization):
    settings = _require_configuration()
    token = _bearer_token(authorization)
    try:
        response = requests.get(
            f"{settings['url']}/auth/v1/user",
            headers={
                "apikey": settings["anon"],
                "Authorization": f"Bearer {token}",
            },
            timeout=12,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail="Authentication service unavailable") from exc
    if not response.ok:
        raise HTTPException(status_code=401, detail="Google session is not valid")
    user = response.json()
    email = str(user.get("email") or "").strip().lower()
    if not user.get("id") or not email:
        raise HTTPException(status_code=401, detail="Google account email is unavailable")
    user["email"] = email
    return user


def _service_headers(prefer=None):
    settings = _require_configuration()
    headers = {
        "apikey": settings["service"],
        "Content-Type": "application/json",
    }
    # Supabase's new sb_secret_* keys are opaque API keys, not JWTs. Sending
    # one as a Bearer token makes the gateway reject an otherwise valid key.
    # Legacy service_role JWTs still require the Authorization header.
    if not settings["service"].startswith("sb_secret_"):
        headers["Authorization"] = f"Bearer {settings['service']}"
    if prefer:
        headers["Prefer"] = prefer
    return headers


def _service_request(method, path, **kwargs):
    settings = _require_configuration()
    try:
        response = requests.request(
            method,
            f"{settings['url']}/rest/v1/{path.lstrip('/')}",
            headers=kwargs.pop("headers", _service_headers()),
            timeout=12,
            **kwargs,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=503, detail="Access database unavailable") from exc
    if not response.ok:
        try:
            supabase_code = str(response.json().get("code") or "").strip()
        except (TypeError, ValueError, AttributeError):
            supabase_code = ""
        diagnostic = f"{response.status_code}/{supabase_code}" if supabase_code else str(response.status_code)
        raise HTTPException(
            status_code=503,
            detail=f"Access database request failed ({diagnostic})",
        )
    if not response.content:
        return None
    return response.json()


def _access_row(user_id):
    rows = _service_request(
        "GET",
        ACCESS_TABLE,
        params={"user_id": f"eq.{user_id}", "select": "*", "limit": 1},
    )
    return rows[0] if rows else None


def _normalized_access(row):
    return {
        "userId": row.get("user_id"),
        "email": row.get("email"),
        "status": row.get("status", "pending"),
        "role": row.get("role", "user"),
        "createdAt": row.get("created_at", ""),
        "updatedAt": row.get("updated_at", ""),
    }


def ensure_access_request(authorization):
    settings = _require_configuration()
    user = verified_user(authorization)
    existing = _access_row(user["id"])
    is_admin = user["email"] == settings["admin"]
    now = datetime.now(timezone.utc).isoformat()

    if existing:
        updates = {"email": user["email"], "updated_at": now}
        if is_admin:
            updates.update({"status": "approved", "role": "admin"})
        rows = _service_request(
            "PATCH",
            ACCESS_TABLE,
            params={"user_id": f"eq.{user['id']}", "select": "*"},
            json=updates,
            headers=_service_headers("return=representation"),
        )
        return _normalized_access((rows or [existing])[0])

    row = {
        "user_id": user["id"],
        "email": user["email"],
        "status": "approved" if is_admin else "pending",
        "role": "admin" if is_admin else "user",
        "created_at": now,
        "updated_at": now,
    }
    rows = _service_request(
        "POST",
        ACCESS_TABLE,
        params={"select": "*"},
        json=row,
        headers=_service_headers("return=representation"),
    )
    return _normalized_access((rows or [row])[0])


def require_access(authorization, admin=False):
    access = ensure_access_request(authorization)
    if access["status"] != "approved":
        raise HTTPException(status_code=403, detail="Access is waiting for administrator approval")
    if admin and access["role"] != "admin":
        raise HTTPException(status_code=403, detail="Administrator access is required")
    return access


def list_access_users(authorization):
    require_access(authorization, admin=True)
    rows = _service_request(
        "GET",
        ACCESS_TABLE,
        params={"select": "*", "order": "created_at.desc"},
    )
    return [_normalized_access(row) for row in (rows or [])]


def update_access_user(authorization, user_id, status):
    settings = _require_configuration()
    require_access(authorization, admin=True)
    if status not in {"pending", "approved", "blocked"}:
        raise HTTPException(status_code=400, detail="Invalid access status")
    current = _access_row(user_id)
    if not current:
        raise HTTPException(status_code=404, detail="User not found")
    if str(current.get("email") or "").lower() == settings["admin"]:
        raise HTTPException(status_code=400, detail="The Hitim administrator cannot be blocked")
    rows = _service_request(
        "PATCH",
        ACCESS_TABLE,
        params={"user_id": f"eq.{user_id}", "select": "*"},
        json={"status": status, "updated_at": datetime.now(timezone.utc).isoformat()},
        headers=_service_headers("return=representation"),
    )
    return _normalized_access(rows[0])


def remove_access_user(authorization, user_id):
    settings = _require_configuration()
    require_access(authorization, admin=True)
    current = _access_row(user_id)
    if not current:
        return {"ok": True}
    if str(current.get("email") or "").lower() == settings["admin"]:
        raise HTTPException(status_code=400, detail="The Hitim administrator cannot be removed")
    _service_request(
        "DELETE",
        ACCESS_TABLE,
        params={"user_id": f"eq.{user_id}"},
        headers=_service_headers("return=minimal"),
    )
    return {"ok": True}
