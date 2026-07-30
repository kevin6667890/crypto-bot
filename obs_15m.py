import subprocess, time, json, sqlite3, urllib.request

def run(cmd):
    try: return subprocess.check_output(cmd, shell=True, text=True, timeout=5).strip()
    except: return ""

def get_health():
    try:
        req = urllib.request.Request("http://127.0.0.1:8770/health", headers={'User-Agent': 'Mozilla'})
        with urllib.request.urlopen(req, timeout=3) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        return {"error": str(e)}

def main():
    print("=== 15m Observation Started ===")
    results = []
    for _ in range(30):
        # 1. IO Stats
        iostat = run("iostat -x -k 1 2 | tail -2 | head -1")
        iowait = run("iostat -c 1 2 | awk '/avg-cpu/ {getline; print $4}' | tail -1")
        # 2. Health
        h = get_health()
        queue = h.get('queue_depth', h.get('collector_queue', 0))
        lag = h.get('writer_lag_ms', 0)
        gap = h.get('critical_live_gap', False)
        # 3. WAL
        wal = run("ls -l /opt/crypto-bot/data_cache/market_microstructure.db-wal | awk '{print $5}'")
        # 4. Containers
        containers = run("docker ps --format '{{.Names}} {{.Status}}'")
        
        row = {
            "ts": time.time(),
            "iowait": iowait,
            "iostat": iostat,
            "queue": queue,
            "writer_lag": lag,
            "wal_bytes": wal,
            "gap": gap,
            "containers": containers
        }
        results.append(row)
        time.sleep(30)
    
    with open('/tmp/obs_15m.json', 'w') as f:
        json.dump(results, f)
    print("=== 15m Observation Completed ===")

if __name__ == '__main__':
    main()
