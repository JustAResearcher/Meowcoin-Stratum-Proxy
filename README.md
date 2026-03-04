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
- **openpyxl** — for Excel block-find logging

```powershell
pip install pycryptodome requests openpyxl
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
| `--block-log`      | `block_finds.xlsx` | Excel file for block find history      |
| `--discord-webhook`| *(disabled)*       | Discord webhook URL for block-found notifications |

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

## Block Find Logging

Every time the proxy finds a block, it:

1. **Fetches the live MEWC/USDT price** from the [NonKYC.io](https://nonkyc.io) public ticker (no API key needed)
2. **Appends a row** to an Excel spreadsheet (`block_finds.xlsx` by default)

Each row records:

| Column | Example |
|---|---|
| Date/Time (UTC) | 2026-02-26 18:30:00 |
| Height | 100000 |
| Block Reward (MEWC) | 3,000.00 |
| Fees (MEWC) | 0.50 |
| Total (MEWC) | 3,000.50 |
| MEWC/USDT Price | $0.00003608 |
| Block Value (USD) | $0.1082 |
| Worker | rig1 |
| Nonce | deadbeef12345678 |
| Cumulative Blocks | 5 |
| Cumulative MEWC | 15,000.50 |
| Cumulative USD | $0.5415 |

The file is created automatically on the first block find. Subsequent blocks append rows with running cumulative totals.

## Discord Webhook Notifications

The proxy can send real-time block-found alerts to a Discord channel via a
webhook. Pass `--discord-webhook <URL>` to enable it.

### Setup

1. In your Discord server, go to **Server Settings → Integrations → Webhooks**
2. Click **New Webhook**, pick a channel, and copy the webhook URL
3. Start the proxy with the webhook flag:

```powershell
python stratum_proxy.py ^
    --address MPyNGZSSZ4rbjkVJRLn3v64pMcktpEYJnU ^
    --discord-webhook "https://discord.com/api/webhooks/123456789/abcdef..."
```

Or with the standalone EXE:

```powershell
meowcoin-stratum-proxy.exe ^
    --address MPyNGZSSZ4rbjkVJRLn3v64pMcktpEYJnU ^
    --discord-webhook "https://discord.com/api/webhooks/123456789/abcdef..."
```

### How It Works

The `DiscordWebhook` class lives inside `stratum_proxy.py` and hooks into the
block-submission pipeline:

```
Miner submits solution
        │
        ▼
Proxy assembles full block
        │
        ▼
submitblock RPC → Meowcoin Node
        │
   ┌────┴────┐
   │         │
Accepted  Rejected
   │         │
   ▼         ▼
Green embed  Red embed
sent to      sent to
Discord      Discord
```

1. **Block accepted** — A **green** embed is posted with the block height,
   reward, fees, live MEWC price (from NonKYC.io), USD/CAD value, worker name,
   nonce, and the coinbase transaction ID.
2. **Block rejected** — A **red** embed is posted with the height, worker, and
   nonce so you can investigate.
3. **No webhook URL** — If `--discord-webhook` is omitted, the class silently
   no-ops. There is zero overhead.

The notification is **fire-and-forget** — a failed HTTP request is logged but
never blocks mining. The webhook call uses a 10-second timeout to avoid hangs.

### Embed Fields

| Field | Description | When |
|---|---|---|
| **Reward** | Block subsidy in MEWC | Accepted |
| **Fees** | Total transaction fees in MEWC | Accepted |
| **Total** | Reward + Fees | Accepted |
| **MEWC Price** | Live MEWC/USDT from NonKYC.io | If price available |
| **Value (USD)** | Total MEWC × price | If price available |
| **Value (CAD)** | USD value × CAD exchange rate | If CAD rate available |
| **Worker** | Miner worker name | Always |
| **Nonce** | Solution nonce (hex) | Always |
| **Coinbase TxID** | First 16 chars of the coinbase txid | Accepted |

### Example Embed

> **⛏️  Block Found — Height 123,456**
>
> | Reward | Fees | Total |
> |---|---|---|
> | 3,000.00 MEWC | 0.50000000 MEWC | 3,000.50 MEWC |
>
> | MEWC Price | Value (USD) | Value (CAD) |
> |---|---|---|
> | $0.00003608 | $0.1083 | C$0.1480 |
>
> | Worker | Nonce |
> |---|---|
> | rig1 | `deadbeef12345678` |
>
> **Coinbase TxID:** `a1b2c3d4e5f6g7h8...`
>
> *Meowcoin Solo Mining Proxy — 2026-03-04T12:00:00Z*

## Consensus Details

- **Algorithm:** MeowPoW (custom ProgPow fork)
- **Epoch length:** 7,500 blocks
- **Header hash input:** SHA256d of `nVersion(4) || hashPrevBlock(32) || hashMerkleRoot(32) || nTime(4) || nBits(4) || nHeight(4)` = 80 bytes
- **Block subsidy:** 5,000 MEWC (halving every 2,100,000 blocks)
- **Community fund:** 40% of subsidy to `MPyNGZSSZ4rbjkVJRLn3v64pMcktpEYJnU`
