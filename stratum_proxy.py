#!/usr/bin/env python3
"""
Meowcoin MeowPoW Solo Mining Stratum Proxy
===========================================

Bridge between ProgPow-capable GPU miners (WildRig Multi, T-Rex, GMiner, etc.)
and a Meowcoin full node via JSON-RPC.

The proxy:
  1. Polls the node's getblocktemplate RPC for new work
  2. Constructs a valid coinbase transaction (with community fund output)
  3. Computes the MeowPoW header hash and epoch seed hash
  4. Distributes work to connected miners via Stratum v1 (kawpow variant)
  5. On solution, assembles the full block and submits via submitblock

Requirements:
    pip install pycryptodome requests

Usage:
    python stratum_proxy.py --address <YOUR_MEWC_ADDRESS> [options]

    Options:
        --address       Meowcoin payout address (required)
        --rpc-host      Node RPC host           (default: 127.0.0.1)
        --rpc-port      Node RPC port           (default: 8332)
        --rpc-user      Node RPC username        (default: cookie auth)
        --rpc-pass      Node RPC password        (default: cookie auth)
        --cookie-dir    Data dir with .cookie    (default: auto-detect)
        --stratum-host  Stratum listen host      (default: 0.0.0.0)
        --stratum-port  Stratum listen port      (default: 3333)
        --poll-interval GBT poll interval (s)    (default: 1.0)
        --log-level     Logging level            (default: INFO)
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import struct
import sys
import time
import traceback
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    from Crypto.Hash import keccak as _keccak_module
except ImportError:
    _keccak_module = None

# =============================================================================
# Constants — Meowcoin consensus
# =============================================================================

EPOCH_LENGTH              = 7500
COMMUNITY_FUND_PCT        = 40
COMMUNITY_FUND_ADDRESS    = "MPyNGZSSZ4rbjkVJRLn3v64pMcktpEYJnU"
SUBSIDY_HALVING_INTERVAL  = 2_100_000
INITIAL_SUBSIDY_COINS     = 5000
COIN                      = 100_000_000

# Meowcoin mainnet base58 address version bytes
PUBKEY_ADDRESS_VERSION    = 50   # 'M' prefix
SCRIPT_ADDRESS_VERSION    = 122  # 'm' prefix
BECH32_HRP                = "mewc"

log = logging.getLogger("stratum-proxy")

# =============================================================================
# Utility — SHA-256d, Keccak-256
# =============================================================================

def sha256d(data: bytes) -> bytes:
    """Double-SHA-256 hash."""
    return hashlib.sha256(hashlib.sha256(data).digest()).digest()


def keccak256(data: bytes) -> bytes:
    """Keccak-256 hash (NOT NIST SHA3-256)."""
    if _keccak_module is not None:
        k = _keccak_module.new(digest_bits=256)
        k.update(data)
        return k.digest()
    # Fallback: try pysha3
    try:
        import sha3  # type: ignore
        return sha3.keccak_256(data).digest()
    except ImportError:
        raise RuntimeError(
            "No Keccak-256 implementation found.  "
            "Install pycryptodome:  pip install pycryptodome"
        )

# =============================================================================
# Utility — Byte / hex helpers
# =============================================================================

def hex_to_bytes(h: str) -> bytes:
    return bytes.fromhex(h)


def bytes_to_hex(b: bytes) -> str:
    return b.hex()


def reverse_hex(h: str) -> str:
    """Reverse the byte order of a hex string."""
    return bytes.fromhex(h)[::-1].hex()


def int32_le(v: int) -> bytes:
    return struct.pack("<i", v)


def uint32_le(v: int) -> bytes:
    return struct.pack("<I", v)


def uint64_le(v: int) -> bytes:
    return struct.pack("<Q", v)


def int64_le(v: int) -> bytes:
    return struct.pack("<q", v)


def compact_size(n: int) -> bytes:
    """Bitcoin varint (CompactSize) encoding."""
    if n < 0xFD:
        return struct.pack("<B", n)
    elif n <= 0xFFFF:
        return b"\xfd" + struct.pack("<H", n)
    elif n <= 0xFFFFFFFF:
        return b"\xfe" + struct.pack("<I", n)
    else:
        return b"\xff" + struct.pack("<Q", n)


def push_data(data: bytes) -> bytes:
    """Bitcoin script PUSH operation for arbitrary data."""
    n = len(data)
    if n < 0x4C:
        return bytes([n]) + data
    elif n <= 0xFF:
        return b"\x4c" + struct.pack("<B", n) + data
    elif n <= 0xFFFF:
        return b"\x4d" + struct.pack("<H", n) + data
    else:
        return b"\x4e" + struct.pack("<I", n) + data

# =============================================================================
# Utility — BIP34 height encoding
# =============================================================================

def encode_height(height: int) -> bytes:
    """
    Encode block height as a Bitcoin CScriptNum for BIP34 coinbase scriptSig.
    Returns the PUSH opcode + serialized height.
    """
    if height == 0:
        return b"\x01\x00"  # OP_PUSH1 0x00
    # Convert to signed little-endian
    neg = height < 0
    v = abs(height)
    result = []
    while v:
        result.append(v & 0xFF)
        v >>= 8
    # If high bit set, add zero byte (positive) or 0x80 (negative)
    if result[-1] & 0x80:
        result.append(0x80 if neg else 0x00)
    elif neg:
        result[-1] |= 0x80
    data = bytes(result)
    return bytes([len(data)]) + data

# =============================================================================
# Utility — Address decoding
# =============================================================================

BASE58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def base58_decode(s: str) -> bytes:
    """Decode base58check string → (version_byte, payload)."""
    n = 0
    for ch in s.encode("ascii"):
        n = n * 58 + BASE58_ALPHABET.index(ch)
    # Determine required length
    byte_len = (n.bit_length() + 7) // 8
    raw = n.to_bytes(max(byte_len, 1), "big")
    # Leading '1' chars → leading zero bytes
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break
    raw = b"\x00" * pad + raw
    payload, checksum = raw[:-4], raw[-4:]
    if sha256d(payload)[:4] != checksum:
        raise ValueError(f"Invalid base58check checksum for '{s}'")
    return payload  # version(1) + hash(20+)


BECH32_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def _bech32_polymod(values):
    GEN = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    chk = 1
    for v in values:
        b = chk >> 25
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            chk ^= GEN[i] if ((b >> i) & 1) else 0
    return chk


def _bech32_hrp_expand(hrp):
    return [ord(x) >> 5 for x in hrp] + [0] + [ord(x) & 31 for x in hrp]


def bech32_decode(bech: str):
    """Decode bech32/bech32m → (hrp, 5-bit data, spec)."""
    bech = bech.lower()
    pos = bech.rfind("1")
    if pos < 1 or pos + 7 > len(bech):
        return None, None, None
    hrp = bech[:pos]
    data = [BECH32_CHARSET.find(x) for x in bech[pos + 1 :]]
    if -1 in data:
        return None, None, None
    check = _bech32_polymod(_bech32_hrp_expand(hrp) + data)
    if check == 1:
        return hrp, data[:-6], "bech32"
    if check == 0x2BC830A3:
        return hrp, data[:-6], "bech32m"
    return None, None, None


def _convertbits(data, frombits, tobits, pad=True):
    acc, bits, ret, maxv = 0, 0, [], (1 << tobits) - 1
    for value in data:
        acc = (acc << frombits) | value
        bits += frombits
        while bits >= tobits:
            bits -= tobits
            ret.append((acc >> bits) & maxv)
    if pad:
        if bits:
            ret.append((acc << (tobits - bits)) & maxv)
    elif bits >= frombits or ((acc << (tobits - bits)) & maxv):
        return None
    return ret


def address_to_scriptpubkey(addr: str) -> bytes:
    """Convert a Meowcoin address to its scriptPubKey bytes."""
    # Bech32 / Bech32m
    if addr.lower().startswith(BECH32_HRP + "1"):
        hrp, data5, spec = bech32_decode(addr)
        if hrp != BECH32_HRP or data5 is None:
            raise ValueError(f"Invalid bech32 address: {addr}")
        witver = data5[0]
        witprog = bytes(_convertbits(data5[1:], 5, 8, False))
        if witver == 0 and len(witprog) == 20:
            return bytes([0x00, 0x14]) + witprog      # P2WPKH
        if witver == 0 and len(witprog) == 32:
            return bytes([0x00, 0x20]) + witprog      # P2WSH
        # Witness v1+ (taproot, etc.)
        return bytes([0x50 + witver, len(witprog)]) + witprog

    # Base58Check
    raw = base58_decode(addr)
    version = raw[0]
    pubkey_hash = raw[1:]
    if version == PUBKEY_ADDRESS_VERSION and len(pubkey_hash) == 20:
        # P2PKH: OP_DUP OP_HASH160 <20> OP_EQUALVERIFY OP_CHECKSIG
        return b"\x76\xa9\x14" + pubkey_hash + b"\x88\xac"
    if version == SCRIPT_ADDRESS_VERSION and len(pubkey_hash) == 20:
        # P2SH: OP_HASH160 <20> OP_EQUAL
        return b"\xa9\x14" + pubkey_hash + b"\x87"
    raise ValueError(f"Unsupported address type (version={version}): {addr}")

# =============================================================================
# Utility — Merkle root
# =============================================================================

def merkle_root(hashes: List[bytes]) -> bytes:
    """
    Compute the Bitcoin-style merkle root from a list of 32-byte hashes
    (in internal byte order = little-endian).
    """
    if not hashes:
        return b"\x00" * 32
    layer = list(hashes)
    while len(layer) > 1:
        if len(layer) % 2 != 0:
            layer.append(layer[-1])
        next_layer = []
        for i in range(0, len(layer), 2):
            next_layer.append(sha256d(layer[i] + layer[i + 1]))
        layer = next_layer
    return layer[0]

# =============================================================================
# Utility — Epoch seed hash
# =============================================================================

_seed_cache: Dict[int, bytes] = {}


def compute_seed_hash(epoch: int) -> bytes:
    """Compute the ethash seed hash for a given epoch number."""
    if epoch in _seed_cache:
        return _seed_cache[epoch]
    seed = b"\x00" * 32
    for i in range(epoch):
        seed = keccak256(seed)
    _seed_cache[epoch] = seed
    return seed

# =============================================================================
# Utility — Block subsidy
# =============================================================================

def get_block_subsidy(height: int) -> int:
    """Returns block subsidy in satoshis for the given height."""
    halvings = height // SUBSIDY_HALVING_INTERVAL
    if halvings >= 64:
        return 0
    return (INITIAL_SUBSIDY_COINS * COIN) >> halvings

# =============================================================================
# Utility — Coinbase transaction builder
# =============================================================================

def build_coinbase_tx(
    height: int,
    miner_value: int,
    miner_script: bytes,
    community_value: int,
    community_script: bytes,
    witness_commitment: Optional[bytes] = None,
    extra_nonce: bytes = b"",
) -> bytes:
    """
    Build a serialised coinbase transaction compatible with Meowcoin consensus.

    Outputs:
        [0] miner payout
        [1] community fund (40% of subsidy)
        [2] witness commitment OP_RETURN  (if segwit)

    Returns the full serialised transaction bytes.
    """
    # --- ScriptSig: BIP34 height + optional extra ---
    script_sig = encode_height(height) + b"\x00" + extra_nonce  # OP_0 = 0x00

    n_version = 2
    n_locktime = max(height - 1, 0)
    has_witness = witness_commitment is not None

    parts: list[bytes] = []

    # nVersion
    parts.append(uint32_le(n_version))

    if has_witness:
        # Segwit marker + flag
        parts.append(b"\x00\x01")

    # --- Inputs (1 coinbase input) ---
    parts.append(compact_size(1))
    parts.append(b"\x00" * 32)                   # prev txid (null)
    parts.append(b"\xff\xff\xff\xff")             # prev vout (0xFFFFFFFF)
    parts.append(compact_size(len(script_sig)))
    parts.append(script_sig)
    parts.append(uint32_le(0xFFFFFFFE))           # nSequence (MAX_SEQUENCE_NONFINAL)

    # --- Outputs ---
    n_outputs = 2  # miner + community
    if has_witness:
        n_outputs += 1  # witness commitment OP_RETURN
    parts.append(compact_size(n_outputs))

    # Output 0: Miner payout
    parts.append(int64_le(miner_value))
    parts.append(compact_size(len(miner_script)))
    parts.append(miner_script)

    # Output 1: Community Autonomous Fund
    parts.append(int64_le(community_value))
    parts.append(compact_size(len(community_script)))
    parts.append(community_script)

    # Output 2: Witness commitment (OP_RETURN)
    if has_witness:
        parts.append(int64_le(0))
        parts.append(compact_size(len(witness_commitment)))
        parts.append(witness_commitment)

    # --- Witness data (for coinbase input) ---
    if has_witness:
        parts.append(compact_size(1))             # 1 witness field for 1 input
        parts.append(compact_size(32))            # 32-byte witness nonce
        parts.append(b"\x00" * 32)                # all-zero nonce

    # nLockTime
    parts.append(uint32_le(n_locktime))

    return b"".join(parts)

# =============================================================================
# Node RPC client
# =============================================================================

class NodeRPC:
    """JSON-RPC client for the Meowcoin node."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8332,
        user: str = "",
        password: str = "",
        cookie_dir: str = "",
    ):
        self.url = f"http://{host}:{port}"
        self._user = user
        self._password = password
        self._cookie_dir = cookie_dir
        self._cookie_cache: Optional[Tuple[str, str]] = None
        self._req_id = 0

    # ------------------------------------------------------------------ auth

    def _get_auth(self) -> Tuple[str, str]:
        if self._user:
            return self._user, self._password

        # Cookie auth
        cookie_path = self._find_cookie()
        if cookie_path and os.path.isfile(cookie_path):
            with open(cookie_path, "r") as f:
                parts = f.read().strip().split(":")
            if len(parts) == 2:
                return parts[0], parts[1]

        if self._cookie_cache:
            return self._cookie_cache
        raise RuntimeError(
            "No RPC credentials.  Set --rpc-user/--rpc-pass or --cookie-dir."
        )

    def _find_cookie(self) -> Optional[str]:
        """Locate the .cookie file."""
        if self._cookie_dir:
            return os.path.join(self._cookie_dir, ".cookie")

        # Try common default locations
        candidates = []
        if sys.platform == "win32":
            appdata = os.environ.get("APPDATA", "")
            if appdata:
                candidates.append(os.path.join(appdata, "Meowcoin", ".cookie"))
                candidates.append(os.path.join(appdata, "Meowcoin_test", ".cookie"))
        else:
            home = os.path.expanduser("~")
            candidates.append(os.path.join(home, ".meowcoin", ".cookie"))

        for p in candidates:
            if os.path.isfile(p):
                return p
        return None

    # ------------------------------------------------------------------ call

    def call(self, method: str, params: Any = None) -> Any:
        """Make a JSON-RPC call and return the result."""
        if params is None:
            params = []
        self._req_id += 1
        payload = {
            "jsonrpc": "1.0",
            "id": self._req_id,
            "method": method,
            "params": params,
        }
        user, pw = self._get_auth()
        resp = requests.post(
            self.url,
            json=payload,
            auth=(user, pw),
            timeout=30,
        )
        # Handle HTTP-level errors before trying to parse JSON
        if resp.status_code == 401:
            raise RuntimeError(
                "RPC authentication failed (HTTP 401). "
                "If using cookie auth, leave RPC User/Password blank "
                "and set the Cookie Dir to your node's data directory."
            )
        if resp.status_code == 403:
            raise RuntimeError(
                "RPC access forbidden (HTTP 403). "
                "Check your node's rpcallowip / rpcbind settings."
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"RPC HTTP error {resp.status_code}: {resp.text[:200]}"
            )
        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(
                f"RPC returned invalid JSON (HTTP {resp.status_code}). "
                f"Response body: {resp.text[:200]!r}"
            )
        if data.get("error"):
            raise RuntimeError(f"RPC error ({method}): {data['error']}")
        return data.get("result")

    def getblocktemplate(self) -> dict:
        return self.call("getblocktemplate", [{"rules": ["segwit"]}])

    def submitblock(self, hex_data: str) -> Any:
        return self.call("submitblock", [hex_data])

# =============================================================================
# Job — represents a single mining work unit
# =============================================================================

class Job:
    """A mining job derived from a getblocktemplate response."""

    def __init__(
        self,
        job_id: str,
        version: int,
        prev_hash_internal: bytes,   # 32 bytes, internal order
        merkle_root_internal: bytes, # 32 bytes, internal order
        ntime: int,
        nbits: int,
        height: int,
        target_hex: str,             # 64-char big-endian hex from GBT
        header_hash_hex: str,        # 64-char big-endian hex
        seed_hash_hex: str,          # 64-char hex
        coinbase_raw: bytes,         # serialised coinbase tx
        tx_hex_list: List[str],      # hex-encoded non-coinbase transactions
    ):
        self.job_id = job_id
        self.version = version
        self.prev_hash_internal = prev_hash_internal
        self.merkle_root_internal = merkle_root_internal
        self.ntime = ntime
        self.nbits = nbits
        self.height = height
        self.target_hex = target_hex
        self.header_hash_hex = header_hash_hex
        self.seed_hash_hex = seed_hash_hex
        self.coinbase_raw = coinbase_raw
        self.tx_hex_list = tx_hex_list
        self.created = time.time()

    def assemble_block_hex(self, nonce64: int, mix_hash_internal: bytes) -> str:
        """
        Assemble the full serialised block as a hex string for submitblock.

        Block format (MeowPoW):
            nVersion         4B LE
            hashPrevBlock   32B internal
            hashMerkleRoot  32B internal
            nTime            4B LE
            nBits            4B LE
            nHeight          4B LE
            nNonce64         8B LE
            mix_hash        32B internal
            tx_count        varint
            transactions
        """
        parts: list[bytes] = []

        # Header
        parts.append(int32_le(self.version))
        parts.append(self.prev_hash_internal)
        parts.append(self.merkle_root_internal)
        parts.append(uint32_le(self.ntime))
        parts.append(uint32_le(self.nbits))
        parts.append(uint32_le(self.height))
        parts.append(uint64_le(nonce64))
        parts.append(mix_hash_internal)

        # Transactions
        all_tx_hex = [bytes_to_hex(self.coinbase_raw)] + self.tx_hex_list
        parts.append(compact_size(len(all_tx_hex)))
        for tx_hex in all_tx_hex:
            parts.append(hex_to_bytes(tx_hex))

        return bytes_to_hex(b"".join(parts))

# =============================================================================
# Job manager — GBT polling and job creation
# =============================================================================

class JobManager:
    """Manages block templates from the node and creates mining jobs."""

    def __init__(self, rpc: NodeRPC, mining_address: str):
        self.rpc = rpc
        self.mining_address = mining_address
        self.miner_script = address_to_scriptpubkey(mining_address)
        self.community_script = address_to_scriptpubkey(COMMUNITY_FUND_ADDRESS)
        self.jobs: Dict[str, Job] = {}
        self.current_job: Optional[Job] = None
        self._last_prev_hash: Optional[str] = None
        self._last_txs_updated: Optional[str] = None
        self._job_counter = 0

    def _next_job_id(self) -> str:
        self._job_counter += 1
        return f"{self._job_counter:08x}"

    def poll(self) -> Optional[Job]:
        """
        Poll getblocktemplate.  Returns a new Job if work changed, else None.
        """
        try:
            tpl = self.rpc.getblocktemplate()
        except Exception as e:
            log.error("GBT failed: %s", e)
            return None

        prev_hash_rpc = tpl["previousblockhash"]
        longpollid = tpl.get("longpollid", "")

        # Detect new work (new tip or significant tx update)
        if prev_hash_rpc == self._last_prev_hash and longpollid == self._last_txs_updated:
            return None

        self._last_prev_hash = prev_hash_rpc
        self._last_txs_updated = longpollid

        return self._create_job(tpl)

    def _create_job(self, tpl: dict) -> Job:
        version   = tpl["version"]
        height    = tpl["height"]
        nbits     = int(tpl["bits"], 16)
        ntime     = tpl["curtime"]
        target_hex = tpl["target"]

        # Previous block hash: GBT gives big-endian hex → internal = reversed
        prev_hash_internal = hex_to_bytes(tpl["previousblockhash"])[::-1]

        # ---- Build coinbase ----
        subsidy = get_block_subsidy(height)
        community_value = (subsidy * COMMUNITY_FUND_PCT) // 100
        coinbase_value = tpl["coinbasevalue"]  # miner's share (subsidy - fund + fees)

        witness_commitment_hex = tpl.get("default_witness_commitment")
        witness_commitment = (
            hex_to_bytes(witness_commitment_hex) if witness_commitment_hex else None
        )

        coinbase_raw = build_coinbase_tx(
            height=height,
            miner_value=coinbase_value,
            miner_script=self.miner_script,
            community_value=community_value,
            community_script=self.community_script,
            witness_commitment=witness_commitment,
        )

        # ---- Compute merkle root ----
        # txid of each transaction (SHA256d of raw tx, in internal order)
        # For segwit coinbase, the txid is the hash WITHOUT witness data.
        coinbase_txid = self._txid_from_raw(coinbase_raw)
        tx_hashes = [coinbase_txid]
        tx_hex_list = []
        for tx_obj in tpl.get("transactions", []):
            tx_hex = tx_obj["data"]
            tx_hex_list.append(tx_hex)
            # Use the provided txid (it's in big-endian/display order)
            txid_display = tx_obj["txid"]
            tx_hashes.append(hex_to_bytes(txid_display)[::-1])  # → internal

        mr = merkle_root(tx_hashes)

        # ---- Compute MeowPoW header hash ----
        # SHA256d( nVersion[4] || hashPrevBlock[32] || hashMerkleRoot[32]
        #          || nTime[4] || nBits[4] || nHeight[4] )   = 80 bytes
        header_input = (
            int32_le(version)
            + prev_hash_internal
            + mr
            + uint32_le(ntime)
            + uint32_le(nbits)
            + uint32_le(height)
        )
        assert len(header_input) == 80, f"Header input is {len(header_input)} bytes, expected 80"

        header_hash_raw = sha256d(header_input)  # 32 bytes, internal order
        # For stratum and ProgPow: display as big-endian hex (reversed)
        header_hash_hex = header_hash_raw[::-1].hex()

        # ---- Epoch and seed hash ----
        epoch = height // EPOCH_LENGTH
        seed_hash = compute_seed_hash(epoch)
        seed_hash_hex = seed_hash.hex()  # raw bytes as hex (ethash convention)

        job_id = self._next_job_id()

        job = Job(
            job_id=job_id,
            version=version,
            prev_hash_internal=prev_hash_internal,
            merkle_root_internal=mr,
            ntime=ntime,
            nbits=nbits,
            height=height,
            target_hex=target_hex,
            header_hash_hex=header_hash_hex,
            seed_hash_hex=seed_hash_hex,
            coinbase_raw=coinbase_raw,
            tx_hex_list=tx_hex_list,
        )

        self.jobs[job_id] = job
        self.current_job = job

        # Prune old jobs (keep last 10)
        if len(self.jobs) > 10:
            old_ids = sorted(self.jobs.keys())[:-10]
            for oid in old_ids:
                del self.jobs[oid]

        log.info(
            "New job %s  height=%d  epoch=%d  txs=%d  target=%s...",
            job_id, height, epoch, len(tx_hex_list), target_hex[:16],
        )

        return job

    @staticmethod
    def _txid_from_raw(raw_tx: bytes) -> bytes:
        """
        Compute the txid from a serialised transaction.
        For segwit transactions, strip the witness data first.
        """
        # Check for segwit marker
        if len(raw_tx) > 6 and raw_tx[4] == 0x00 and raw_tx[5] == 0x01:
            # Segwit tx — strip marker, flag, and witness
            # nVersion (4) + marker (1) + flag (1) + rest
            n_version = raw_tx[:4]
            rest = raw_tx[6:]  # after marker+flag

            # Read inputs
            pos = 0
            n_vin, sz = _read_varint(rest, pos)
            pos = sz
            for _ in range(n_vin):
                pos += 32 + 4  # prev_hash + prev_index
                script_len, sz = _read_varint(rest, pos)
                pos = sz + script_len + 4  # scriptSig + nSequence

            # Read outputs
            n_vout, sz = _read_varint(rest, pos)
            pos = sz
            for _ in range(n_vout):
                pos += 8  # nValue
                script_len, sz = _read_varint(rest, pos)
                pos = sz + script_len

            # Everything from here to (end - 4) is witness data
            # nLockTime is the last 4 bytes
            n_locktime = raw_tx[-4:]
            stripped = n_version + rest[:pos] + n_locktime
            return sha256d(stripped)
        else:
            return sha256d(raw_tx)

    def submit_solution(self, job_id: str, nonce_hex: str, mix_hash_hex: str) -> str:
        """
        Assemble the block from a mining solution and submit it.
        Returns the submitblock result (None = accepted, string = error).
        """
        job = self.jobs.get(job_id)
        if not job:
            return "job-not-found"

        nonce64 = int(nonce_hex, 16)
        # mix_hash from miner is big-endian hex → internal = reversed
        mix_hash_internal = hex_to_bytes(mix_hash_hex)[::-1]

        block_hex = job.assemble_block_hex(nonce64, mix_hash_internal)

        log.info(
            "Submitting block  job=%s  height=%d  nonce=%s",
            job_id, job.height, nonce_hex,
        )

        try:
            result = self.rpc.submitblock(block_hex)
        except Exception as e:
            log.error("submitblock RPC failed: %s", e)
            return str(e)

        if result is None:
            log.info("*** BLOCK ACCEPTED ***  height=%d", job.height)
        else:
            log.warning("Block rejected: %s", result)

        return result if result is not None else "accepted"


def _read_varint(data: bytes, pos: int) -> Tuple[int, int]:
    """Read a CompactSize varint from data at pos.  Returns (value, new_pos)."""
    b = data[pos]
    if b < 0xFD:
        return b, pos + 1
    elif b == 0xFD:
        return struct.unpack_from("<H", data, pos + 1)[0], pos + 3
    elif b == 0xFE:
        return struct.unpack_from("<I", data, pos + 1)[0], pos + 5
    else:
        return struct.unpack_from("<Q", data, pos + 1)[0], pos + 9

# =============================================================================
# Stratum protocol — miner session
# =============================================================================

class MinerSession:
    """Handles a single TCP miner connection."""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        job_manager: JobManager,
        on_disconnect,
    ):
        self.reader = reader
        self.writer = writer
        self.job_manager = job_manager
        self.on_disconnect = on_disconnect
        self.peer = writer.get_extra_info("peername")
        self.subscription_id = uuid.uuid4().hex[:16]
        self.authorized = False
        self.worker_name = "unknown"
        self._closed = False

    async def run(self):
        """Main read loop for this miner connection."""
        log.info("Miner connected: %s", self.peer)
        try:
            while not self._closed:
                line = await self.reader.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    log.warning("Invalid JSON from %s: %s", self.peer, line[:200])
                    continue
                await self._handle_message(msg)
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        except Exception:
            log.error("Session error (%s): %s", self.peer, traceback.format_exc())
        finally:
            await self.close()
            log.info("Miner disconnected: %s", self.peer)
            self.on_disconnect(self)

    async def _handle_message(self, msg: dict):
        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", [])

        if method == "mining.subscribe":
            await self._handle_subscribe(msg_id, params)
        elif method == "mining.authorize":
            await self._handle_authorize(msg_id, params)
        elif method == "mining.submit":
            await self._handle_submit(msg_id, params)
        elif method == "mining.extranonce.subscribe":
            await self._send_result(msg_id, True)
        else:
            log.debug("Unknown method from %s: %s", self.peer, method)
            await self._send_error(msg_id, 20, f"Unknown method: {method}")

    async def _handle_subscribe(self, msg_id, params):
        miner_agent = params[0] if params else "unknown"
        log.info("Subscribe from %s: agent=%s", self.peer, miner_agent)

        # Response: [[["mining.notify", subscription_id]], extranonce1, extranonce2_size]
        result = [
            [["mining.notify", self.subscription_id]],
            "",   # extranonce1 (empty for solo — miner controls full nonce)
            "0",  # extranonce2_size
        ]
        await self._send_result(msg_id, result)

        # Send initial target
        if self.job_manager.current_job:
            await self.send_set_target(self.job_manager.current_job.target_hex)
            await self.send_notify(self.job_manager.current_job, clean=True)

    async def _handle_authorize(self, msg_id, params):
        self.worker_name = params[0] if params else "unknown"
        self.authorized = True
        log.info("Authorized: %s (%s)", self.worker_name, self.peer)
        await self._send_result(msg_id, True)

        # Send current job after authorization
        if self.job_manager.current_job:
            await self.send_set_target(self.job_manager.current_job.target_hex)
            await self.send_notify(self.job_manager.current_job, clean=True)

    async def _handle_submit(self, msg_id, params):
        """
        Handle mining.submit from miner.
        Expected params: [worker, job_id, nonce_hex, header_hash, mix_hash]
        Some miners may omit header_hash: [worker, job_id, nonce_hex, mix_hash]
        """
        if not self.authorized:
            await self._send_error(msg_id, 24, "Not authorized")
            return

        if len(params) < 4:
            await self._send_error(msg_id, 21, "Not enough parameters")
            return

        worker = params[0]
        job_id = params[1]
        nonce_hex = params[2]

        # Detect format: 5 params = [worker, job_id, nonce, header_hash, mix_hash]
        #                4 params = [worker, job_id, nonce, mix_hash]
        if len(params) >= 5:
            mix_hash_hex = params[4]
        else:
            mix_hash_hex = params[3]

        log.info(
            "Share from %s: job=%s nonce=%s mix=%s...",
            worker, job_id, nonce_hex, mix_hash_hex[:16],
        )

        result = self.job_manager.submit_solution(job_id, nonce_hex, mix_hash_hex)

        if result == "accepted":
            await self._send_result(msg_id, True)
        elif result == "job-not-found":
            await self._send_error(msg_id, 21, "Job not found (stale)")
        else:
            await self._send_error(msg_id, 20, f"Rejected: {result}")

    async def send_set_target(self, target_hex: str):
        """Send mining.set_target to the miner."""
        await self._send_notification("mining.set_target", [target_hex])

    async def send_notify(self, job: Job, clean: bool = True):
        """
        Send mining.notify with a new job.
        Params: [job_id, header_hash, seed_hash, target, clean_jobs, height_hex, bits_hex]
        """
        params = [
            job.job_id,
            job.header_hash_hex,
            job.seed_hash_hex,
            job.target_hex,
            clean,
            format(job.height, "x"),
            format(job.nbits, "08x"),
        ]
        await self._send_notification("mining.notify", params)

    # ------------------------------------------------------------------ I/O

    async def _send_result(self, msg_id, result):
        await self._send_json({"id": msg_id, "result": result, "error": None})

    async def _send_error(self, msg_id, code: int, message: str):
        await self._send_json({"id": msg_id, "result": None, "error": [code, message, None]})

    async def _send_notification(self, method: str, params):
        await self._send_json({"id": None, "method": method, "params": params})

    async def _send_json(self, obj):
        if self._closed:
            return
        try:
            line = json.dumps(obj) + "\n"
            self.writer.write(line.encode("utf-8"))
            await self.writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            self._closed = True

    async def close(self):
        if not self._closed:
            self._closed = True
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass

# =============================================================================
# Stratum server — main loop
# =============================================================================

class StratumServer:
    """
    Async Stratum server.  Accepts miner connections and polls
    the node for new work.
    """

    def __init__(
        self,
        job_manager: JobManager,
        host: str = "0.0.0.0",
        port: int = 3333,
        poll_interval: float = 1.0,
    ):
        self.job_manager = job_manager
        self.host = host
        self.port = port
        self.poll_interval = poll_interval
        self.sessions: List[MinerSession] = []
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self):
        self._server = await asyncio.start_server(
            self._handle_client, self.host, self.port,
        )
        addrs = ", ".join(str(s.getsockname()) for s in self._server.sockets)
        log.info("Stratum server listening on %s", addrs)
        log.info("Mining address: %s", self.job_manager.mining_address)

        # Start the GBT polling loop as a background task
        asyncio.create_task(self._poll_loop())

        async with self._server:
            await self._server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        session = MinerSession(reader, writer, self.job_manager, self._on_disconnect)
        self.sessions.append(session)
        await session.run()

    def _on_disconnect(self, session: MinerSession):
        if session in self.sessions:
            self.sessions.remove(session)

    async def _poll_loop(self):
        """Periodically poll GBT and broadcast new jobs to all miners."""
        while True:
            try:
                job = await asyncio.get_event_loop().run_in_executor(
                    None, self.job_manager.poll,
                )
                if job:
                    await self._broadcast(job)
            except Exception:
                log.error("Poll error: %s", traceback.format_exc())

            await asyncio.sleep(self.poll_interval)

    async def _broadcast(self, job: Job):
        """Send a new job to all connected miners."""
        for session in list(self.sessions):
            try:
                await session.send_set_target(job.target_hex)
                await session.send_notify(job, clean=True)
            except Exception:
                pass

# =============================================================================
# Main
# =============================================================================

def parse_args():
    p = argparse.ArgumentParser(
        description="Meowcoin MeowPoW Solo Mining Stratum Proxy",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--address", required=True, help="Meowcoin payout address")
    p.add_argument("--rpc-host", default="127.0.0.1", help="Node RPC host (default: 127.0.0.1)")
    p.add_argument("--rpc-port", type=int, default=8332, help="Node RPC port (default: 8332)")
    p.add_argument("--rpc-user", default="", help="Node RPC username")
    p.add_argument("--rpc-pass", default="", help="Node RPC password")
    p.add_argument("--cookie-dir", default="", help="Data directory containing .cookie file")
    p.add_argument("--stratum-host", default="0.0.0.0", help="Stratum listen host (default: 0.0.0.0)")
    p.add_argument("--stratum-port", type=int, default=3333, help="Stratum listen port (default: 3333)")
    p.add_argument("--poll-interval", type=float, default=1.0, help="GBT poll interval in seconds (default: 1.0)")
    p.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return p.parse_args()


def main():
    args = parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s  %(name)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Validate mining address
    try:
        script = address_to_scriptpubkey(args.address)
        log.info("Mining address %s → scriptPubKey %s", args.address, script.hex())
    except Exception as e:
        log.error("Invalid mining address '%s': %s", args.address, e)
        sys.exit(1)

    rpc = NodeRPC(
        host=args.rpc_host,
        port=args.rpc_port,
        user=args.rpc_user,
        password=args.rpc_pass,
        cookie_dir=args.cookie_dir,
    )

    # Verify RPC connectivity
    try:
        info = rpc.call("getmininginfo")
        log.info(
            "Connected to node — chain=%s  height=%s  difficulty=%s",
            info.get("chain", "?"),
            info.get("blocks", "?"),
            info.get("difficulty", "?"),
        )
    except Exception as e:
        log.error("Cannot connect to node RPC: %s", e)
        sys.exit(1)

    job_mgr = JobManager(rpc, args.address)
    server = StratumServer(
        job_manager=job_mgr,
        host=args.stratum_host,
        port=args.stratum_port,
        poll_interval=args.poll_interval,
    )

    log.info(
        "Starting stratum proxy on %s:%d  →  node %s:%d",
        args.stratum_host, args.stratum_port,
        args.rpc_host, args.rpc_port,
    )

    try:
        asyncio.run(server.start())
    except KeyboardInterrupt:
        log.info("Shutting down.")


if __name__ == "__main__":
    main()
