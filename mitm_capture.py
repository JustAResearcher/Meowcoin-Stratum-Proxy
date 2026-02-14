"""
MITM proxy: sit between SRBMiner and a real kawpow pool, log everything.
Listen on port 3334, forward to rvn.2miners.com:6060.
"""
import asyncio
import sys

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = 3334
REMOTE_HOST = "rvn.2miners.com"
REMOTE_PORT = 6060

async def pipe(label, reader, writer, log_writer):
    """Read from reader, log, forward to writer."""
    try:
        while True:
            data = await reader.readline()
            if not data:
                break
            line = data.decode("utf-8", errors="replace").strip()
            print(f"{label}: {line}", flush=True)
            log_writer.write(data)
            await log_writer.drain()
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass

async def handle_client(local_reader, local_writer):
    peer = local_writer.get_extra_info("peername")
    print(f"\n=== New connection from {peer} ===", flush=True)

    # Connect to remote pool
    remote_reader, remote_writer = await asyncio.open_connection(REMOTE_HOST, REMOTE_PORT)
    print(f"Connected to {REMOTE_HOST}:{REMOTE_PORT}", flush=True)

    # Pipe both directions
    t1 = asyncio.create_task(pipe("MINER>>POOL", local_reader, remote_writer, remote_writer))
    t2 = asyncio.create_task(pipe("POOL>>MINER", remote_reader, local_writer, local_writer))

    await asyncio.gather(t1, t2, return_exceptions=True)

    remote_writer.close()
    local_writer.close()
    print(f"=== Connection closed ===\n", flush=True)

async def main():
    server = await asyncio.start_server(handle_client, LISTEN_HOST, LISTEN_PORT)
    print(f"MITM listening on {LISTEN_HOST}:{LISTEN_PORT} -> {REMOTE_HOST}:{REMOTE_PORT}")
    print(f"Point SRBMiner at stratum+tcp://127.0.0.1:{LISTEN_PORT}")
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
