from __future__ import annotations

import base64
import functools
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from apiflask import abort
from flask import g, request

SESSION_COOKIE = "veritaste_session"
SESSION_TTL_S = 12 * 3600

KITCHEN_COOKIE = "veritaste_kitchen"
KITCHEN_TTL_S = 24 * 3600

_SECRET = os.environ.get("VERITASTE_SECRET", "").encode() or os.urandom(32)


@dataclass(frozen=True)
class User:

    sub: str
    name: str
    principal: str
    affiliation: str
    house_key: str | None
    demo: bool = True


def _sign(payload: bytes) -> str:
    mac = hmac.new(_SECRET, payload, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(payload).decode().rstrip("=")
        + "."
        + base64.urlsafe_b64encode(mac).decode().rstrip("=")
    )


def _unpad(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_session(user: User) -> str:
    body = {
        "sub": user.sub,
        "name": user.name,
        "principal": user.principal,
        "affiliation": user.affiliation,
        "house_key": user.house_key,
        "demo": user.demo,
        "exp": int(time.time()) + SESSION_TTL_S,
    }
    return _sign(json.dumps(body, separators=(",", ":")).encode())


def read_session(token: str | None) -> User | None:
    if not token or "." not in token:
        return None
    raw, sig = token.rsplit(".", 1)
    try:
        payload = _unpad(raw)
        expected = hmac.new(_SECRET, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unpad(sig)):
            return None
        body = json.loads(payload)
    except (ValueError, json.JSONDecodeError):
        return None

    if body.get("exp", 0) < time.time():
        return None
    if "sub" not in body:
        return None

    return User(
        sub=body["sub"],
        name=body["name"],
        principal=body["principal"],
        affiliation=body["affiliation"],
        house_key=body.get("house_key"),
        demo=body.get("demo", True),
    )


def current_user() -> User | None:
    if "veritaste_user" not in g:
        g.veritaste_user = read_session(request.cookies.get(SESSION_COOKIE))
    return g.veritaste_user


def issue_kitchen() -> str:
    body = {"aud": "kitchen", "exp": int(time.time()) + KITCHEN_TTL_S}
    return _sign(json.dumps(body, separators=(",", ":")).encode())


def read_kitchen(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    raw, sig = token.rsplit(".", 1)
    try:
        payload = _unpad(raw)
        expected = hmac.new(_SECRET, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unpad(sig)):
            return False
        body = json.loads(payload)
    except (ValueError, json.JSONDecodeError):
        return False
    return body.get("aud") == "kitchen" and body.get("exp", 0) >= time.time()


def kitchen_unlocked() -> bool:
    return read_kitchen(request.cookies.get(KITCHEN_COOKIE))


def login_required(view):

    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        if current_user() is None:
            abort(401, "Sign in to contribute feedback.")
        return view(*args, **kwargs)

    return wrapper
