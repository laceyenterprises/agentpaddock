"""Ed25519 signature verification (RFC 8032), standard library only.

The submission API only ever verifies: every input here (public key, message,
signature) is public, so this needs no constant-time arithmetic. Signing stays
on the submitter's machine. The checks follow RFC 8032 section 5.1.7 with the
strict choices the paddock relies on:

- a public key or ``R`` that is not a canonical encoding of a curve point is
  rejected (``y`` must be below the field prime, and ``x = 0`` must not carry
  a sign bit);
- ``S`` must be below the group order ``L`` (no malleable signatures);
- registration additionally refuses small-order public keys
  (:func:`is_valid_public_key`), whose "signatures" prove nothing.

The test suite checks it against the RFC 8032 test vectors and, where the
``cryptography`` package is installed, against OpenSSL.
"""

from __future__ import annotations

import hashlib

P = 2**255 - 19
L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, P - 2, P) % P
_SQRT_M1 = pow(2, (P - 1) // 4, P)

Point = tuple[int, int, int, int]  # extended coordinates (X, Y, Z, T)
_IDENTITY: Point = (0, 1, 1, 0)


def _recover_x(y: int, sign: int) -> int | None:
    if y >= P:
        return None
    x2 = (y * y - 1) * pow(_D * y * y + 1, P - 2, P) % P
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (P + 3) // 8, P)
    if (x * x - x2) % P:
        x = x * _SQRT_M1 % P
    if (x * x - x2) % P:
        return None
    if (x & 1) != sign:
        x = P - x
    return x


def _add(a: Point, b: Point) -> Point:
    e1 = (a[1] - a[0]) * (b[1] - b[0]) % P
    e2 = (a[1] + a[0]) * (b[1] + b[0]) % P
    c = 2 * a[3] * b[3] * _D % P
    d = 2 * a[2] * b[2] % P
    e, f, g, h = e2 - e1, d - c, d + c, e2 + e1
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _mul(scalar: int, point: Point) -> Point:
    result = _IDENTITY
    while scalar > 0:
        if scalar & 1:
            result = _add(result, point)
        point = _add(point, point)
        scalar >>= 1
    return result


def _equal(a: Point, b: Point) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % P == 0 and (a[1] * b[2] - b[1] * a[2]) % P == 0


_GY = 4 * pow(5, P - 2, P) % P
_GX = _recover_x(_GY, 0)
assert _GX is not None
BASE: Point = (_GX, _GY, 1, _GX * _GY % P)


def decompress(data: bytes) -> Point | None:
    """The point a 32-byte encoding names, or None when it names none."""
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, x * y % P)


def compress(point: Point) -> bytes:
    z_inv = pow(point[2], P - 2, P)
    x = point[0] * z_inv % P
    y = point[1] * z_inv % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def is_valid_public_key(data: bytes) -> bool:
    """A canonical point encoding of a point outside the small-order subgroup."""
    point = decompress(data)
    if point is None or compress(point) != data:
        return False
    return not _equal(_mul(8, point), _IDENTITY)


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """True when ``signature`` is a valid Ed25519 signature of ``message``."""
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = decompress(public_key)
    if a is None or compress(a) != public_key:
        return False
    r_bytes = signature[:32]
    r = decompress(r_bytes)
    if r is None or compress(r) != r_bytes:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= L:
        return False
    digest = hashlib.sha512(r_bytes + public_key + message).digest()
    h = int.from_bytes(digest, "little") % L
    return _equal(_mul(s, BASE), _add(r, _mul(h, a)))
