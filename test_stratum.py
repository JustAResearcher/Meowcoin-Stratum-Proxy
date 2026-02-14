"""Quick test: connect to stratum proxy and print all messages"""
import socket
import json
import time

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(10)
sock.connect(("127.0.0.1", 3333))

# Send subscribe
subscribe = {"id": 1, "method": "mining.subscribe", "params": ["TestMiner/1.0"]}
sock.sendall((json.dumps(subscribe) + "\n").encode())
time.sleep(1)

# Read all available
data = b""
while True:
    try:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    except socket.timeout:
        break

print("After subscribe:")
for line in data.decode().strip().split("\n"):
    print(f"  {line}")

# Send authorize
authorize = {"id": 2, "method": "mining.authorize", "params": ["MWcgFVkdBV32GP9HLd2ypQBoyTFNeb8zZ6", "x"]}
sock.sendall((json.dumps(authorize) + "\n").encode())
time.sleep(1)

# Read response
data = b""
while True:
    try:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
    except socket.timeout:
        break

print("\nAfter authorize:")
for line in data.decode().strip().split("\n"):
    print(f"  {line}")

sock.close()
