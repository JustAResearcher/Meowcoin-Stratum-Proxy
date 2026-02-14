"""Connect to a real kawpow pool with valid address to capture full mining.notify"""
import socket
import json
import time

host, port = "rvn.2miners.com", 6060

print(f"Connecting to {host}:{port}...")
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(15)
sock.connect((host, port))
print("Connected!")

def recv_all(sock, timeout=5):
    """Read all available data with timeout."""
    data = b""
    sock.settimeout(timeout)
    while True:
        try:
            chunk = sock.recv(8192)
            if not chunk:
                break
            data += chunk
        except socket.timeout:
            break
    return data

def print_messages(label, data):
    print(f"\n{label}:")
    for line in data.decode(errors='replace').strip().split("\n"):
        line = line.strip()
        if line:
            try:
                parsed = json.loads(line)
                print(f"  << {json.dumps(parsed)}")
            except:
                print(f"  << {line}")

# Send subscribe
msg = {"id": 1, "method": "mining.subscribe", "params": ["SRBMiner-MULTI/3.1.2"]}
print(f"\n>> {json.dumps(msg)}")
sock.sendall((json.dumps(msg) + "\n").encode())
time.sleep(2)
print_messages("After subscribe", recv_all(sock, 3))

# Try with a well-known RVN address (Binance hot wallet)
msg = {"id": 2, "method": "mining.authorize", "params": ["RNs3ne88DoNEnXFTqUrj6zrBRoS3Z36Waa.test", "x"]}
print(f"\n>> {json.dumps(msg)}")
sock.sendall((json.dumps(msg) + "\n").encode())
time.sleep(5)
print_messages("After authorize", recv_all(sock, 5))

# Wait for mining.notify
time.sleep(5)
print_messages("Additional messages", recv_all(sock, 5))

sock.close()
print("\nDone.")
