"""
Minimal stratum server to test exactly what mining.notify format
SRBMiner accepts for meowpow.

Tests multiple formats to find which one doesn't trigger
"PARSE error: missing block height data"
"""
import asyncio
import json
import time

HOST = "127.0.0.1"
PORT = 3333

# Sample data
HEADER_HASH = "cbab9228a96f38fd575c8d33590049e3710dc2fd2e4516fe5c4da03f3d83491c"
SEED_HASH   = "4309e9a28e2f625744bd29b2446d99ec20583751a6ea3b8f975f34ec941cc15b"
TARGET      = "000000000128de00000000000000000000000000000000000000000000000000"
PREV_HASH   = "000000006a6be1279e9c3be45e3bff21e59fc16d55d023b51cb8ae7a82e89901"
HEIGHT      = 1793964
NBITS       = "1c0128de"
JOB_ID      = "00000001"

# Try different formats
FORMATS = {
    # Format A: 7 params, height as hex string (current)
    "A_7param_hex": [JOB_ID, HEADER_HASH, SEED_HASH, TARGET, True, format(HEIGHT, "x"), NBITS],
    
    # Format B: 7 params, height as integer 
    "B_7param_int": [JOB_ID, HEADER_HASH, SEED_HASH, TARGET, True, HEIGHT, NBITS],
    
    # Format C: 8 params with prev_hash, height as hex
    "C_8param_hex": [JOB_ID, PREV_HASH, HEADER_HASH, SEED_HASH, TARGET, True, format(HEIGHT, "x"), NBITS],
    
    # Format D: 8 params with prev_hash, height as int
    "D_8param_int": [JOB_ID, PREV_HASH, HEADER_HASH, SEED_HASH, TARGET, True, HEIGHT, NBITS],
    
    # Format E: 9 params (some pools add ntime)
    "E_9param": [JOB_ID, PREV_HASH, HEADER_HASH, SEED_HASH, TARGET, True, HEIGHT, NBITS, format(int(time.time()), "x")],
}

# Which format to test — change this!
import sys
FORMAT_KEY = sys.argv[1] if len(sys.argv) > 1 else "B_7param_int"

async def handle_client(reader, writer):
    peer = writer.get_extra_info("peername")
    print(f"[+] Miner connected: {peer}")
    
    async def send_json(obj):
        line = json.dumps(obj) + "\n"
        print(f"  >> {line.strip()}")
        writer.write(line.encode())
        await writer.drain()
    
    try:
        while True:
            data = await reader.readline()
            if not data:
                break
            line = data.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            print(f"  << {line}")
            msg = json.loads(line)
            method = msg.get("method", "")
            msg_id = msg.get("id")
            
            if method == "mining.subscribe":
                # kawpow pool style response
                await send_json({"id": msg_id, "result": ["00abcdef", "00"], "error": None})
                
            elif method == "mining.authorize":
                await send_json({"id": msg_id, "result": True, "error": None})
                
                # Now send the job
                notify_params = FORMATS[FORMAT_KEY]
                print(f"\n  *** Sending format: {FORMAT_KEY} ***")
                print(f"  *** Params: {json.dumps(notify_params)} ***\n")
                
                await send_json({"id": None, "method": "mining.set_target", "params": [TARGET]})
                await send_json({"id": None, "method": "mining.notify", "params": notify_params})
                
            elif method == "mining.extranonce.subscribe":
                await send_json({"id": msg_id, "result": True, "error": None})
                
            elif method == "mining.submit":
                print(f"  !!! GOT SHARE SUBMISSION !!!")
                await send_json({"id": msg_id, "result": True, "error": None})
                
            else:
                print(f"  [?] Unknown method: {method}")
                await send_json({"id": msg_id, "result": True, "error": None})
                
    except Exception as e:
        print(f"  [!] Error: {e}")
    finally:
        writer.close()
        print(f"[-] Miner disconnected: {peer}")

async def main():
    print(f"Testing format: {FORMAT_KEY}")
    print(f"Params: {json.dumps(FORMATS[FORMAT_KEY])}")
    print(f"Listening on {HOST}:{PORT}...")
    server = await asyncio.start_server(handle_client, HOST, PORT)
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
