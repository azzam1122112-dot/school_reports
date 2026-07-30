from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
from io import BytesIO
from dataclasses import dataclass
from typing import Any

import cbor2
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa


def b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    raw = (value or "").encode("ascii")
    raw += b"=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw)


def random_challenge() -> str:
    return b64url_encode(secrets.token_bytes(32))


def credential_hash(credential_id: bytes) -> str:
    return hashlib.sha256(credential_id).hexdigest()


def rp_id_from_request(request) -> str:
    """معرّف النطاق (RP ID) لمفاتيح البصمة.

    افتراضياً يُشتق من مضيف الطلب الحالي، لكن إذا ضُبط ``WEBAUTHN_RP_ID``
    (مثل ``tawtheeq-ksa.com``) وكان المضيف الحالي مطابقاً له أو نطاقاً فرعياً
    منه، نستخدم المعرّف الثابت. هذا يجعل البصمة المسجّلة من أي نطاق فرعي
    (apex / www / app) تعمل على بقية النطاقات الفرعية، وفق معيار WebAuthn الذي
    يسمح بأن يكون ``rp.id`` لاحقة قابلة للتسجيل من نطاق الأصل.
    """
    host = (request.get_host() or "").split(":", 1)[0].strip().lower()
    configured = (os.getenv("WEBAUTHN_RP_ID") or "").strip().lower().lstrip(".")
    if configured and (host == configured or host.endswith("." + configured)):
        return configured
    return host


def origin_from_request(request) -> str:
    return request.build_absolute_uri("/").rstrip("/")


def parse_client_data(
    *,
    client_data_json_b64: str,
    expected_type: str,
    expected_challenge: str,
    expected_origin: str,
) -> bytes:
    client_data_json = b64url_decode(client_data_json_b64)
    try:
        client_data = json.loads(client_data_json.decode("utf-8"))
    except Exception as exc:
        raise ValueError("client_data_invalid") from exc

    if client_data.get("type") != expected_type:
        raise ValueError("client_data_type_invalid")
    if client_data.get("challenge") != expected_challenge:
        raise ValueError("challenge_invalid")
    if client_data.get("origin") != expected_origin:
        raise ValueError("origin_invalid")

    return hashlib.sha256(client_data_json).digest()


@dataclass
class AuthenticatorData:
    flags: int
    sign_count: int
    credential_id: bytes | None = None
    public_key_cose: bytes | None = None


def parse_authenticator_data(
    authenticator_data: bytes,
    *,
    rp_id: str,
    require_attested_credential: bool = False,
    require_user_verification: bool = False,
) -> AuthenticatorData:
    if len(authenticator_data) < 37:
        raise ValueError("authenticator_data_too_short")

    rp_id_hash = authenticator_data[:32]
    if rp_id_hash != hashlib.sha256(rp_id.encode("utf-8")).digest():
        raise ValueError("rp_id_hash_invalid")

    flags = authenticator_data[32]
    if not flags & 0x01:
        raise ValueError("user_presence_required")
    if require_user_verification and not flags & 0x04:
        raise ValueError("user_verification_required")

    sign_count = int.from_bytes(authenticator_data[33:37], "big")

    if not flags & 0x40:
        if require_attested_credential:
            raise ValueError("attested_credential_missing")
        return AuthenticatorData(flags=flags, sign_count=sign_count)

    offset = 37 + 16
    if len(authenticator_data) < offset + 2:
        raise ValueError("credential_id_missing")

    credential_id_length = int.from_bytes(authenticator_data[offset : offset + 2], "big")
    offset += 2
    credential_id = authenticator_data[offset : offset + credential_id_length]
    offset += credential_id_length
    if not credential_id:
        raise ValueError("credential_id_empty")

    public_key_obj = cbor2.CBORDecoder(BytesIO(authenticator_data[offset:])).decode()
    public_key_cose = cbor2.dumps(public_key_obj)
    return AuthenticatorData(
        flags=flags,
        sign_count=sign_count,
        credential_id=credential_id,
        public_key_cose=public_key_cose,
    )


def public_key_from_cose(public_key_cose: bytes):
    cose = cbor2.loads(public_key_cose)
    kty = cose.get(1)
    alg = cose.get(3)

    if kty == 2 and alg == -7:
        x = int.from_bytes(cose[-2], "big")
        y = int.from_bytes(cose[-3], "big")
        numbers = ec.EllipticCurvePublicNumbers(x, y, ec.SECP256R1())
        return numbers.public_key(), "ES256"

    if kty == 3 and alg == -257:
        n = int.from_bytes(cose[-1], "big")
        e = int.from_bytes(cose[-2], "big")
        return rsa.RSAPublicNumbers(e, n).public_key(), "RS256"

    raise ValueError("unsupported_public_key")


def verify_signature(*, public_key_cose: bytes, signature_b64: str, authenticator_data_b64: str, client_data_hash: bytes) -> None:
    authenticator_data = b64url_decode(authenticator_data_b64)
    signature = b64url_decode(signature_b64)
    public_key, alg = public_key_from_cose(public_key_cose)
    signed_data = authenticator_data + client_data_hash

    try:
        if alg == "ES256":
            public_key.verify(signature, signed_data, ec.ECDSA(hashes.SHA256()))
        elif alg == "RS256":
            public_key.verify(signature, signed_data, padding.PKCS1v15(), hashes.SHA256())
        else:
            raise ValueError("unsupported_public_key")
    except InvalidSignature as exc:
        raise ValueError("signature_invalid") from exc

    return None


def json_body(request) -> dict[str, Any]:
    try:
        return json.loads((request.body or b"{}").decode("utf-8"))
    except Exception as exc:
        raise ValueError("json_invalid") from exc
