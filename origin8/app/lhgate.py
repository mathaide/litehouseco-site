"""Check that a request came through the Litehouse access gate.

The gate (a Cloudflare Worker) signs each request it forwards with an ECDSA P-256
key that it generates and keeps itself. It publishes the public half at
<gate host>/__lh/pubkey. This module fetches that public key and verifies the
headers x-lh-ts, x-lh-host and x-lh-sig over "ts\\nMETHOD\\nhost\\npath?query".

GATE_PUBKEY_URL  where to fetch the public key. Set it to "off" only for local runs.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

MAX_SKEW = 300  # seconds
ALLOWED_HOSTS = {h.strip() for h in os.environ.get("GATE_HOSTS", "").split(",") if h.strip()}


def _b64u(s: str) -> int:
    import base64
    return int.from_bytes(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)), "big")


class GateVerifier:
    def __init__(self, pubkey_url: str):
        self.url = pubkey_url
        self.key = None
        self.fetched = 0.0
        self.lock = threading.Lock()

    @property
    def enabled(self) -> bool:
        return self.url.lower() != "off"

    def _load(self, force: bool = False):
        with self.lock:
            if self.key is not None and not force:
                return self.key
            if force and time.time() - self.fetched < 30:
                return self.key
            self.fetched = time.time()
            try:
                with urllib.request.urlopen(self.url, timeout=5) as r:
                    jwk = json.loads(r.read().decode())
                nums = ec.EllipticCurvePublicNumbers(_b64u(jwk["x"]), _b64u(jwk["y"]), ec.SECP256R1())
                self.key = nums.public_key()
            except Exception:
                pass
            return self.key

    def check(self, method: str, path_qs: str, headers) -> bool:
        if not self.enabled:
            return True
        ts, sig, host = headers.get("x-lh-ts", ""), headers.get("x-lh-sig", ""), headers.get("x-lh-host", "")
        if not (ts.isdigit() and sig and host):
            return False
        if ALLOWED_HOSTS and host not in ALLOWED_HOSTS:
            return False
        if abs(time.time() - int(ts)) > MAX_SKEW:
            return False
        try:
            raw = bytes.fromhex(sig)
        except ValueError:
            return False
        if len(raw) != 64:
            return False
        der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
        msg = f"{ts}\n{method}\n{host}\n{path_qs}".encode()
        for force in (False, True):  # retry once with a fresh key in case the gate rotated it
            key = self._load(force)
            if key is None:
                continue
            try:
                key.verify(der, msg, ec.ECDSA(hashes.SHA256()))
                return True
            except InvalidSignature:
                continue
        return False
