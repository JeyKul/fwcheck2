#!/usr/bin/env python3
import argparse
import asyncio
import json
import time
import httpx
import xml.etree.ElementTree as ET
import tempfile
import filecmp
import subprocess
import requests
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

# === CONFIG ===
ORIGINAL_JSON = "valid_combinations.json"
UPDATED_JSON = "valid_combinations_updated.json"
BASE_URL = "http://fota-cloud-dn.ospserver.net/firmware"
LOG_FILE = Path.home() / "fw_python.log"

MAX_NET_CONCURRENCY = 20     # concurrent HTTP requests
MAX_CPU_THREADS = 8          # threads for file + git work
REUSE_HTTP_CLIENT = True     # reuse one HTTP client
PUSH_AFTER = True            # push after all commits

# SamFW tuning
SAMFW_BASE_URL = "https://samfrew.com/firmware/upload/Desc/{offset}/1000"
SAMFW_THREADS = 6
SAMFW_SLEEP = 0.15

SAMFW_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CSCUpdater/1.0)",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}
# ===============


# ---------- SAMFW SCRAPER ----------
def fetch_model_region(session, offset):
    url = SAMFW_BASE_URL.format(offset=offset)
    r = session.get(url, timeout=20)
    if r.status_code != 200:
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    divs = soup.find_all("div", class_="firmwareTable_flexCell__KPd_2")

    results = []
    model = region = os_ver = None

    for div in divs:
        label = div.find("span", class_="firmwareTable_flexCellLabel__b2sEY")
        if not label:
            continue

        text = div.get_text(strip=True)

        if text.startswith("Model:"):
            model = text.replace("Model:", "").strip()
        elif text.startswith("Region:"):
            region = text.replace("Region:", "").strip()
        elif text.startswith("OS:"):
            try:
                os_ver = int(text.replace("OS:", "").strip())
            except ValueError:
                os_ver = None

        if model and region and os_ver is not None:
            if os_ver >= 13:
                results.append((model, region))
            model = region = os_ver = None

    return results


def update_csc_file():
    print("[CSC] Updating CSC/model list (fast mode)")

    # Load original data
    if os.path.exists(ORIGINAL_JSON):
        with open(ORIGINAL_JSON, "r", encoding="utf-8") as f:
            base = json.load(f)
    else:
        base = {"CSC": {}}
    
    updated = json.loads(json.dumps(base))  # deep copy

    session = requests.Session()
    session.headers.update(SAMFW_HEADERS)

    offsets = []
    step = 1000
    max_empty = 3
    empty_hits = 0
    current = 0

    # discover offsets dynamically
    while empty_hits < max_empty:
        offsets.append(current)
        current += step
        empty_hits += 1

    with ThreadPoolExecutor(max_workers=SAMFW_THREADS) as pool:
        futures = {
            pool.submit(fetch_model_region, session, off): off
            for off in offsets
        }

        for fut in as_completed(futures):
            entries = fut.result()
            if not entries:
                continue

            for model, region in entries:
                updated.setdefault("CSC", {}).setdefault(region, {})
                if model not in updated["CSC"][region]:
                    updated["CSC"][region][model] = True
                    print(f"[CSC] Added {region}/{model}")

            time.sleep(SAMFW_SLEEP)

    # Save updated JSON
    with open(UPDATED_JSON, "w", encoding="utf-8") as f:
        json.dump(updated, f, indent=2)
    print(f"[CSC] Done → {UPDATED_JSON}")
    return UPDATED_JSON


# ---------- FIRMWARE CHECKER ----------
async def fetch_xml(client, csc, model, sem):
    """Fetch version.xml content."""
    url = f"{BASE_URL}/{csc}/{model}/version.xml"
    async with sem:
        start = time.perf_counter()
        try:
            resp = await client.get(url, timeout=20)
            elapsed = time.perf_counter() - start
            if resp.status_code == 200:
                return csc, model, resp.text, elapsed, None
            return csc, model, None, elapsed, f"HTTP {resp.status_code}"
        except Exception as e:
            return csc, model, None, 0, f"Fetch error: {e}"


def process_xml(csc, model, xml_data):
    """Parse XML, compare file, commit if changed."""
    file_path = Path(f"current.{csc}.{model}")

    # Create temp file in the same directory as the target, not /tmp/
    tmp_path = file_path.parent / f".tmp_{csc}_{model}"
    log_lines = []

    if not xml_data:
        log_lines.append(f"Firmware: {model} CSC:{csc} failed (empty data)")
        return log_lines

    try:
        root = ET.fromstring(xml_data)
        latest = root.findtext(".//latest")
        node = root.find(".//latest")
        android = node.attrib.get("o") if node is not None else None
    except Exception as e:
        log_lines.append(f"Firmware: {model} CSC:{csc} XML parse error: {e}")
        return log_lines

    if not latest or "/" not in latest:
        log_lines.append(f"Firmware: {model} CSC:{csc} invalid latest format: {latest}")
        return log_lines

    # Write new content
    with open(tmp_path, "w") as f:
        f.write(f"{latest}\n")
        if android:
            f.write(f"ANDROID_VERSION={android}\n")

    # Compare existing vs new
    changed = True
    if file_path.exists() and filecmp.cmp(file_path, tmp_path, shallow=False):
        tmp_path.unlink(missing_ok=True)
        changed = False

    if changed:
        # Move safely (works across filesystems)
        try:
            tmp_path.replace(file_path)
        except OSError:
            from shutil import move
            move(str(tmp_path), str(file_path))

        commit_msg = f"{csc}/{model}: {latest}"
        if android:
            commit_msg += f" (Android {android})"
        subprocess.run(["git", "add", str(file_path)], check=False)
        subprocess.run(["git", "commit", "-m", commit_msg], check=False)
        log_lines.append(f"Firmware: {model} CSC:{csc} updated to {latest}")
    else:
        log_lines.append(f"Firmware: {model} CSC:{csc} is already up-to-date")

    return log_lines


async def process_all(json_file):
    """Main task: fetch in async, process in threads."""
    with open(json_file) as f:
        data = json.load(f)

    pairs = [
        (csc, model)
        for csc, models in data.get("CSC", {}).items()
        for model in models.keys()
    ]

    sem = asyncio.Semaphore(MAX_NET_CONCURRENCY)
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=MAX_CPU_THREADS)
    all_logs = []

    async def runner(client):
        tasks = [fetch_xml(client, c, m, sem) for c, m in pairs]
        for coro in asyncio.as_completed(tasks):
            csc, model, xml, elapsed, err = await coro
            if err:
                line = f"Firmware: {model} CSC:{csc} failed ({err})"
                print(f"log:{line}")
                all_logs.append(line)
                continue
            # Send processing to thread pool
            logs = await loop.run_in_executor(executor, process_xml, csc, model, xml)
            for line in logs:
                print(f"log:{line}")
                all_logs.append(line)
            await asyncio.sleep(0.01)  # yield for fairness

    if REUSE_HTTP_CLIENT:
        async with httpx.AsyncClient(http2=True) as client:
            await runner(client)
    else:
        async with httpx.AsyncClient(http2=True) as client:
            await runner(client)

    executor.shutdown(wait=True)
    return all_logs


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true", 
                       help="Update CSC list from SamFW first")
    args = parser.parse_args()

    json_file = ORIGINAL_JSON

    if args.update:
        print("Updating CSC/model list from SamFW...")
        json_file = update_csc_file()
    else:
        print(f"Using existing JSON file: {json_file}")

    start = time.time()
    logs = asyncio.run(process_all(json_file))
    duration = time.time() - start

    with open(LOG_FILE, "a") as f:
        f.write(f"\nRun {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        for line in logs:
            f.write(line + "\n")
        f.write(f"Finished in {duration:.2f} seconds\n")

    if PUSH_AFTER:
        print("Pushing changes to GitHub...")
        subprocess.run(["git", "push"], check=False)

    print(f"Finished in {duration:.2f}s, log: {LOG_FILE}")


if __name__ == "__main__":
    main()
