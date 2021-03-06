#!/usr/bin/env python3

# Copyright (C) 2017-2020 The btclib developers
#
# This file is part of btclib. It is subject to the license terms in the
# LICENSE file found in the top-level directory of this distribution.
#
# No part of btclib including this file, may be copied, modified, propagated,
# or distributed except according to the terms contained in the LICENSE file.

"""Elliptic Curve Schnorr Signature Algorithm (ECSSA).

Implementation according to bip-schnorr:

https://github.com/sipa/bips/blob/bip-schnorr/bip-schnorr.mediawiki.
"""

import heapq
import random
from typing import List, Optional, Sequence, Tuple
from hashlib import sha256

from .curve import Curve, Point, _JacPoint
from .curves import secp256k1
from .curvemult import (_double_mult, _jac_from_aff, _mult_jac,
                        _multi_mult, double_mult, mult)
from .numbertheory import legendre_symbol, mod_inv
from .rfc6979 import rfc6979
from .utils import HashF, int_from_bits, octets_from_int, octets_from_point

ECSS = Tuple[int, int]  # Tuple[field element, scalar]


def _ensure_msg_size(msg: bytes, hf: HashF = sha256) -> None:
    if len(msg) != hf().digest_size:
        errmsg = f'message of wrong size: {len(msg)}'
        errmsg += f' instead of {hf().digest_size} bytes'
        raise ValueError(errmsg)


def _e(r: int, P: Point, mhd: bytes, ec: Curve = secp256k1, hf: HashF = sha256) -> int:
    # Let e = int(hf(bytes(x(R)) || bytes(dG) || mhd)) mod n.
    h = hf()
    h.update(octets_from_int(r, ec.psize))
    h.update(octets_from_point(P, True, ec))
    h.update(mhd)
    e = int_from_bits(h.digest(), ec)
    return e


def sign(mhd: bytes, d: int, k: Optional[int] = None,
         ec: Curve = secp256k1, hf: HashF = sha256) -> ECSS:
    """ECSSA signing operation according to bip-schnorr.

    This signature scheme supports only 32-byte messages.
    Differently from ECDSA, the 32-byte message can be a
    digest of other messages, but it does not need to.
    """

    # the bitcoin proposed standard is only valid for curves
    # whose prime p = 3 % 4
    if not ec.pIsThreeModFour:
        errmsg = 'curve prime p must be equal to 3 (mod 4)'
        raise ValueError(errmsg)

    # The message mhd: a 32-byte array
    _ensure_msg_size(mhd, hf)

    # The secret key d: an integer in the range 1..n-1.
    if not 0 < d < ec.n:
        raise ValueError(f"private key {hex(d)} not in [1, n-1]")
    P = mult(d, ec.G, ec)

    # Fail if k' = 0.
    if k is None:
        k = rfc6979(mhd, d, ec, hf)
    if not 0 < k < ec.n:
        raise ValueError(f"ephemeral key {hex(k)} not in [1, n-1]")

    # Let R = k'G.
    RJ = _mult_jac(k, ec.GJ, ec)

    # break the simmetry: any criteria might have been used,
    # jacobi is the proposed bitcoin standard
    # Let k = k' if jacobi(y(R)) = 1, otherwise let k = n - k'.
    if legendre_symbol(RJ[1]*RJ[2] % ec._p, ec._p) != 1:
        k = ec.n - k

    Z2 = RJ[2]*RJ[2]
    r = (RJ[0]*mod_inv(Z2, ec._p)) % ec._p

    # Let e = int(hf(bytes(x(R)) || bytes(dG) || mhd)) mod n.
    e = _e(r, P, mhd, ec, hf)

    s = (k + e*d) % ec.n  # s=0 is ok: in verification there is no inverse of s
    # The signature is bytes(x(R) || bytes((k + ed) mod n)).
    return r, s


def verify(mhd: bytes, P: Point, sig: ECSS,
           ec: Curve = secp256k1, hf: HashF = sha256) -> bool:
    """ECSSA signature verification according to bip-schnorr."""

    # try/except wrapper for the Errors raised by _verify
    try:
        return _verify(mhd, P, sig, ec, hf)
    except Exception:
        return False


def _verify(mhd: bytes, P: Point, sig: ECSS,
            ec: Curve = secp256k1, hf: HashF = sha256) -> bool:
    # Private function for test/dev purposes
    # It raises Errors, while verify should always return True or False

    # the bitcoin proposed standard is only valid for curves
    # whose prime p = 3 % 4
    if not ec.pIsThreeModFour:
        errmsg = 'curve prime p must be equal to 3 (mod 4)'
        raise ValueError(errmsg)

    # Let r = int(sig[ 0:32]).
    # Let s = int(sig[32:64]); fail if s is not [0, n-1].
    _check_sig(sig, ec)

    # The message mhd: a 32-byte array
    _ensure_msg_size(mhd, hf)

    # Let P = point(pk); fail if point(pk) fails.
    ec.require_on_curve(P)
    if P[1] == 0:
        raise ValueError("public key is infinite")

    # Let e = int(hf(bytes(r) || bytes(P) || mhd)) mod n.
    e = _e(sig[0], P, mhd, ec, hf)

    # Let R = sG - eP.
    # in Jacobian coordinates
    R = _double_mult(-e, (P[0], P[1], 1), sig[1], ec.GJ, ec)

    # Fail if infinite(R).
    if R[2] == 0:
        raise ValueError("sG - eP is infinite")

    # Fail if jacobi(R.y) ≠ 1.
    if legendre_symbol(R[1]*R[2] % ec._p, ec._p) != 1:
        raise ValueError("(sG - eP).y is not a quadratic residue")

    # Fail if R.x ≠ r.
    return R[0] == (R[2]*R[2]*sig[0] % ec._p)


def _pubkey_recovery(e: int, sig: ECSS,
                     ec: Curve = secp256k1, hf: HashF = sha256) -> Point:
    # Private function provided for testing purposes only.
    # TODO: use _double_mult instead of double_mult

    _check_sig(sig, ec)

    K = sig[0], ec.y_quadratic_residue(sig[0], True)
    # FIXME: y_quadratic_residue in Jacobian coordinates?

    if e == 0:
        raise ValueError("invalid (zero) challenge e")
    e1 = mod_inv(e, ec.n)
    P = double_mult(-e1, K, e1*sig[1], ec.G, ec)
    assert P[1] != 0, "how did you do that?!?"
    return P


def _check_sig(sig: ECSS, ec: Curve = secp256k1) -> None:
    # check that the SSA signature is correct
    # and return the signature itself

    # A signature sig: a 64-byte array.
    if len(sig) != 2:
        mhd = f"invalid length {len(sig)} for ECSSA signature"
        raise TypeError(mhd)

    # Let r = int(sig[ 0:32]).
    if not 0 <= sig[0] < 2**256-1:
        raise ValueError(f"r ({hex(sig[0])}) not in [0, 2**256-1]")

    # Let s = int(sig[32:64]); fail if s is not [0, n-1].
    if not 0 <= sig[1] < ec.n:
        raise ValueError(f"s ({hex(sig[1])}) not in [0, n-1]")



def batch_verify(ms: Sequence[bytes], P: Sequence[Point], sig: Sequence[ECSS],
                 ec: Curve = secp256k1, hf: HashF = sha256) -> bool:
    """ECSSA batch signature verification according to bip-schnorr."""

    # try/except wrapper for the Errors raised by _batch_verify
    try:
        return _batch_verify(ms, P, sig, ec, hf)
    except Exception:
        return False


def _batch_verify(ms: Sequence[bytes], P: Sequence[Point], sig: Sequence[ECSS],
                  ec: Curve = secp256k1, hf: HashF = sha256) -> bool:

    # the bitcoin proposed standard is only valid for curves
    # whose prime p = 3 % 4
    if not ec.pIsThreeModFour:
        errmsg = 'curve prime p must be equal to 3 (mod 4)'
        raise ValueError(errmsg)

    batch_size = len(P)
    if len(ms) != batch_size:
        errMsg = f"mismatch between number of pubkeys ({batch_size}) "
        errMsg += f"and number of messages ({len(ms)})"
        raise ValueError(errMsg)
    if len(sig) != batch_size:
        errMsg = f"mismatch between number of pubkeys ({batch_size}) "
        errMsg += f"and number of signatures ({len(sig)})"
        raise ValueError(errMsg)

    if batch_size == 1:
        return _verify(ms[0], P[0], sig[0], ec, hf)

    t = 0
    scalars: List[int] = list()
    points: List[_JacPoint] = list()
    for i in range(batch_size):
        _check_sig(sig[i], ec)
        r, s = sig[i]
        _ensure_msg_size(ms[i], hf)
        ec.require_on_curve(P[i])
        e = _e(r, P[i], ms[i], ec, hf)
        # raises an error if y does not exist
        # no need to check for quadratic residue
        y = ec.y(r)

        # a in [1, n-1]
        # deterministically generated using a CSPRNG seeded by a
        # cryptographic hash (e.g., SHA256) of all inputs of the
        # algorithm, or randomly generated independently for each
        # run of the batch verification algorithm
        a = (1 if i == 0 else (1+random.getrandbits(ec.nlen)) % ec.n)
        scalars.append(a)
        points.append(_jac_from_aff((r, y)))
        scalars.append(a * e % ec.n)
        points.append(_jac_from_aff(P[i]))
        t += a * s

    TJ = _mult_jac(t, ec.GJ, ec)
    RHSJ = _multi_mult(scalars, points, ec)

    # return T == RHS, checked in Jacobian coordinates
    RHSZ2 = RHSJ[2] * RHSJ[2]
    TZ2 = TJ[2] * TJ[2]
    if (TJ[0] * RHSZ2) % ec._p != (RHSJ[0] * TZ2) % ec._p:
        return False

    return (TJ[1] * RHSZ2 * RHSJ[2]) % ec._p == (RHSJ[1] * TZ2 * TJ[2]) % ec._p
