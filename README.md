# Meowcoin MeowPoW Solo Mining Stratum Proxy

A lightweight Python stratum proxy that bridges ProgPow-capable GPU miners
(WildRig Multi, T-Rex, GMiner, etc.) to a Meowcoin full node for **solo mining**.

## How It Works

```
  ┌──────────┐   Stratum v1    ┌──────────────┐   JSON-RPC   ┌────────────┐
  │ GPU Miner│ ◄──────────────► │ Stratum Proxy│ ◄───────────► │ Meowcoin   │
  │ (kawpow) │   TCP :3333     │  (this tool) │   HTTP :8332 │   Node     │
  └──────────┘                  └──────────────┘               └────────────┘
```

1. Polls the node's `getblocktemplate` RPC for new work
2. Builds a valid coinbase (with 40% community fund output)
3. Computes the MeowPoW header hash (SHA256d of 80-byte ProgPow input)
4. Sends jobs to connected miners via Stratum (KAWPOW variant)
5. On solution, assembles the full block and submits via `submitblock`

## Requirements

- **Python 3.10+**
- **pycryptodome** — for Keccak-256 (epoch seed hash)
- **requests** — for JSON-RPC communication

```powershell
pip install pycryptodome requests
```

## Node Setup

Start the Meowcoin node with **RPC server** enabled:

```powershell
# Using bitcoind (headless):
bitcoind.exe -datadir=C:\path\to\datadir -server

# Using bitcoin-qt (GUI):
bitcoin-qt.exe -datadir=C:\path\to\datadir -server
```

The proxy uses **cookie authentication** by default (reads the `.cookie` file
from the data directory). Alternatively, set `rpcuser`/`rpcpassword` in the
node config or pass `--rpc-user`/`--rpc-pass` to the proxy.

## Usage

```powershell
python stratum_proxy.py --address <YOUR_MEWC_ADDRESS> [options]
```

### Options

| Option             | Default       | Description                                |
|--------------------|---------------|--------------------------------------------|
| `--address`        | *(required)*  | Meowcoin payout address (P2PKH, P2SH, or bech32) |
| `--rpc-host`       | `127.0.0.1`   | Node RPC host                              |
| `--rpc-port`       | `8332`        | Node RPC port                              |
| `--rpc-user`       | *(cookie)*    | Node RPC username                          |
| `--rpc-pass`       | *(cookie)*    | Node RPC password                          |
| `--cookie-dir`     | *(auto)*      | Data directory containing `.cookie` file   |
| `--stratum-host`   | `0.0.0.0`     | Stratum listen host                        |
| `--stratum-port`   | `3333`        | Stratum listen port                        |
| `--poll-interval`  | `1.0`         | GBT poll interval in seconds               |
| `--log-level`      | `INFO`        | Logging: DEBUG, INFO, WARNING, ERROR       |

### Example

```powershell
# Start the proxy (node running locally with cookie auth):
python stratum_proxy.py ^
    --address MPyNGZSSZ4rbjkVJRLn3v64pMcktpEYJnU ^
    --cookie-dir C:\Source\Meowcoin_Test_2\mewc_maintest

# With explicit RPC credentials:
python stratum_proxy.py ^
    --address MPyNGZSSZ4rbjkVJRLn3v64pMcktpEYJnU ^
    --rpc-user myrpcuser ^
    --rpc-pass myrpcpassword
```

## Connecting a Miner

Point your KAWPOW/ProgPow miner at the proxy's stratum port:

### WildRig Multi
```
wildrig.exe --algo meowpow --url stratum+tcp://127.0.0.1:3333 --user worker1 --pass x
```

### T-Rex
```
t-rex.exe -a kawpow -o stratum+tcp://127.0.0.1:3333 -u worker1 -p x
```

### GMiner
```
miner.exe --algo kawpow --server 127.0.0.1 --port 3333 --user worker1
```

> **Note:** The `--user` and `--pass` values don't matter for solo mining —
> all connections are accepted. The mining reward goes to the `--address`
> specified when starting the proxy.

## Stratum Protocol

The proxy implements the KAWPOW variant of Stratum v1:

| Method                    | Direction      | Description                    |
|---------------------------|----------------|--------------------------------|
| `mining.subscribe`        | miner → proxy  | Session initialization         |
| `mining.authorize`        | miner → proxy  | Always accepted (solo)         |
| `mining.set_target`       | proxy → miner  | Difficulty target (256-bit)    |
| `mining.notify`           | proxy → miner  | New mining job                 |
| `mining.submit`           | miner → proxy  | Submit solution                |

### mining.notify params
```json
["job_id", "header_hash", "seed_hash", "target", clean_jobs, "height_hex", "bits_hex"]
```

### mining.submit params
```json
["worker", "job_id", "nonce_hex", "header_hash", "mix_hash"]
```

## Consensus Details

- **Algorithm:** MeowPoW (custom ProgPow fork)
- **Epoch length:** 7,500 blocks
- **Header hash input:** SHA256d of `nVersion(4) || hashPrevBlock(32) || hashMerkleRoot(32) || nTime(4) || nBits(4) || nHeight(4)` = 80 bytes
- **Block subsidy:** 5,000 MEWC (halving every 2,100,000 blocks)
- **Community fund:** 40% of subsidy to `MPyNGZSSZ4rbjkVJRLn3v64pMcktpEYJnU`
