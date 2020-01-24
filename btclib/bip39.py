#!/usr/bin/env python3

# Copyright (C) 2017-2020 The btclib developers
#
# This file is part of btclib. It is subject to the license terms in the
# LICENSE file found in the top-level directory of this distribution.
#
# No part of btclib including this file, may be copied, modified, propagated,
# or distributed except according to the terms contained in the LICENSE file.

"""BIP39 entropy / mnemonic / seed functions.

https://github.com/bitcoin/bips/blob/master/bip-0039.mediawiki.

Checksummed entropy (**ENT+CS**) is converted from/to mnemonic.

* bits per word = bpw = 11
* **ENT** = raw entropy
* **CS** = checksum = **ENT** / 32
* **MS** = words in the mnemonic sentence = (**ENT+CS**) / bpw

+-----+----+--------+----+
| ENT | CS | ENT+CS | MS |
+=====+====+========+====+
| 128 |  4 |    132 | 12 |
+-----+----+--------+----+
| 160 |  5 |    165 | 15 |
+-----+----+--------+----+
| 192 |  6 |    198 | 18 |
+-----+----+--------+----+
| 224 |  7 |    231 | 21 |
+-----+----+--------+----+
| 256 |  8 |    264 | 24 |
+-----+----+--------+----+

"""


from hashlib import sha256, pbkdf2_hmac

from .entropy import Entropy, GenericEntropy, _bytes_from_entropy, \
    str_from_entropy
from .mnemonic import _indexes_from_entropy, _mnemonic_from_indexes, \
    _indexes_from_mnemonic, _entropy_from_indexes, Mnemonic
from . import bip32


_bits = 128, 160, 192, 224, 256


def _entropy_checksum(entropy: GenericEntropy) -> Entropy:

    entropy = _bytes_from_entropy(entropy, _bits)
    # 256-bit checksum
    byteschecksum = sha256(entropy).digest()
    # integer checksum (leading zeros are lost)
    intchecksum = int.from_bytes(byteschecksum, 'big')
    # convert checksum to binary '01' string
    checksum = bin(intchecksum)[2:]  # remove '0b'
    checksum = checksum.zfill(256)   # pad with leading lost zeros
    # leftmost bits
    checksum_bits = len(entropy) // 4
    return checksum[:checksum_bits]


def cs_entropy_from_entropy(entropy: GenericEntropy) -> Entropy:
    """Convert input entropy to checksummed BIP39 entropy.

    Input entropy (*GenericEntropy*) can be expressed as
    binary 0/1 string, bytes-like, or integer;
    it must be 128, 160, 192, 224, or 256 bits.

    In the case of binary 0/1 string and bytes-like,
    leading zeros are not considered redundant padding.
    In the case of integer, where leading zeros cannot be represented,
    if the bit length is not an allowed value, then the binary 0/1
    string is padded with leading zeros up to the next allowed bit
    length; if the integer bit length is longer than the maximum
    length, then only the leftmost bits are retained.
    """

    entropy = str_from_entropy(entropy, _bits)
    checksum = _entropy_checksum(entropy)
    return entropy + checksum


def mnemonic_from_entropy(entropy: GenericEntropy, lang: str = "en") -> Mnemonic:
    """Convert input entropy to checksummed BIP39 mnemonic sentence.

    Input entropy (*GenericEntropy*) can be expressed as
    binary 0/1 string, bytes-like, or integer;
    it must be 128, 160, 192, 224, or 256 bits.

    In the case of binary 0/1 string and bytes-like,
    leading zeros are not considered redundant padding.
    In the case of integer, where leading zeros cannot be represented,
    if the bit length is not an allowed value, then the binary 0/1
    string is padded with leading zeros up to the next allowed bit
    length; if the integer bit length is longer than the maximum
    length, then only the leftmost bits are retained.
    """

    cs_entropy = cs_entropy_from_entropy(entropy)
    indexes = _indexes_from_entropy(cs_entropy, lang)
    mnemonic = _mnemonic_from_indexes(indexes, lang)
    return mnemonic


def entropy_from_mnemonic(mnemonic: Mnemonic, lang: str = "en") -> Entropy:
    """Convert mnemonic sentence to entropy, verifying checksum."""

    words = len(mnemonic.split())
    allowed = (b // 32 * 3 for b in _bits)
    if words not in allowed:
        msg = f"mnemonic with wrong number of words ({words}); "
        msg += f"expected: {allowed}"
        raise ValueError(msg)

    indexes = _indexes_from_mnemonic(mnemonic, lang)
    cs_entropy = _entropy_from_indexes(indexes, lang)

    # entropy is only the first part of cs_entropy
    bits = int(len(cs_entropy)*32/33)
    entropy = cs_entropy[:bits]

    # the second part being the checksum, to be verified
    checksum = _entropy_checksum(entropy)
    if cs_entropy[bits:] != checksum:
        m = f"invalid mnemonic checksum ({cs_entropy[bits:]}); "
        m += f"expected: {checksum}"
        raise ValueError(m)

    return entropy


def seed_from_mnemonic(mnemonic: Mnemonic, passphrase: str,
                       verify_checksum = True) -> bytes:
    """Return seed from mnemonic according to BIP39 standard.

    The verification of the mnemonic (implicit entropy) checksum
    can be skipped if needed.
    """

    if verify_checksum:
        entropy_from_mnemonic(mnemonic)
    hf_name = 'sha512'
    password = mnemonic.encode()
    salt = ('mnemonic' + passphrase).encode()
    iterations = 2048
    dksize = 64
    return pbkdf2_hmac(hf_name, password, salt, iterations, dksize)


def rootxprv_from_mnemonic(mnemonic: Mnemonic,
                           passphrase: str,
                           xversion: bytes) -> bytes:
    """Return BIP32 root master extended private key from BIP39 mnemonic."""

    seed = seed_from_mnemonic(mnemonic, passphrase)
    return bip32.rootxprv_from_seed(seed, xversion)
