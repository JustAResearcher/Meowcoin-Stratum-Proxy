"""Connect to a real kawpow pool and capture the exact stratum protocol messages"""
import socket
import json
import time
import sys

# Try a public Ravencoin (kawpow) pool
POOLS = [
    ("stratum.ravenminer.com", 3838),
    ("rvn.2miners.com", 6060),
    ("stratum+tcp://us-rvn.2miners.com", 6060),
]

host, port = "rvn.2miners.com", 6060

print(f"Connecting to {host}:{port}...")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(15)
try:
    sock.connect((host, port))
except Exception as e:
    print(f"Connection failed: {e}")
    # Try another
    host, port = "stratum.ravenminer.com", 3838
    print(f"Trying {host}:{port}...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(15)
    sock.connect((host, port))

print("Connected!")

# Send subscribe
subscribe = {"id": 1, "method": "mining.subscribe", "params": ["SRBMiner-MULTI/3.1.2"]}
print(f"\n>> {json.dumps(subscribe)}")
sock.sendall((json.dumps(subscribe) + "\n").encode())
time.sleep(2)

# Read all available
data = b""
while True:
    try:
        chunk = sock.recv(8192)
        if not chunk:
            break
        data += chunk
    except socket.timeout:
        break

print("\nResponses after subscribe:")
for line in data.decode(errors='replace').strip().split("\n"):
    if line.strip():
        try:
            parsed = json.loads(line)
            print(f"<< {json.dumps(parsed, indent=2)}")
        except:
            print(f"<< {line}")

# Send authorize
authorize = {"id": 2, "method": "mining.authorize", "params": ["RJjW1FiGCTnSfFNVzWsAPubnfKCh3wvvED.test", "x"]}
print(f"\n>> {json.dumps(authorize)}")
sock.sendall((json.dumps(authorize) + "\n").encode())
time.sleep(3)

# Read response
data = b""
while True:
    try:
        chunk = sock.recv(8192)
        if not chunk:
            break
        data += chunk
    except socket.timeout:
        break

print("\nResponses after authorize:")
for line in data.decode(errors='replace').strip().split("\n"):
    if line.strip():
        try:
            parsed = json.loads(line)
            print(f"<< {json.dumps(parsed, indent=2)}")
        except:
            print(f"<< {line}")

sock.close()
print("\nDone.")
