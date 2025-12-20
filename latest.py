 #!/usr/bin/env python3

import argparse
import asyncio
import json
import time
import os
import requests
import httpx
import tempfile
import filecmp
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

# ================= CONFIG =================

ORIGINAL_JSON = "valid_combinations.json"
UPDATED_JSON  = "valid_combinations_updated.json"

BASE_URL = "http://fota-cloud-dn.ospserver.net/firmware"
LOG_FILE = Path.home() / "fw_python.log"

MAX_NET_CONCURRENCY = 20
MAX_CPU_THREADS = 8
PUSH_AFTER = True

# SamFW tuning
SAMFW_BASE_URL = "https://samfrew.com/firmware/upload/Desc/{offset}/1000"
SAMFW_THREADS = 6
SAMFW_SLEEP = 0.15

SAMFW_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; CSCUpdater/1.0)",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
}

# ==========================================


# ---------- UTILS ----------

def load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"CSC": {}}


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


# ---------- SAMFW SCRAPER (FAST, SAFE) ----------

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

    base = load_json(ORIGINAL_JSON)
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

    save_json(UPDATED_JSON, updated)
    print(f"[CSC] Done → {UPDATED_JSON}")


# ---------- ASYNC FIRMWARE PIPELINE ----------

async def fetch_xml(client, csc, model, sem):
    url = f"{BASE_URL}/{csc}/{model}/version.xml"
    async with sem:
        try:
            r = await client.get(url, timeout=20)
            if r.status_code == 200:
                return csc, model, r.text, None
            return csc, model, None, f"HTTP {r.status_code}"
        except Exception as e:
            return csc, model, None, str(e)


def process_xml(csc, model, xml):
    out = []
    path = Path(f"current.{csc}.{model}")
    tmp = Path(tempfile.mktemp())

    try:
        root = ET.fromstring(xml)
        node = root.find(".//latest")
        latest = node.text if node is not None else None
        android = node.attrib.get("o") if node is not None else None
    except Exception as e:
        return [f"{csc}/{model} XML error: {e}"]

    if not latest or "/" not in latest:
        return [f"{csc}/{model} invalid data"]

    with open(tmp, "w") as f:
        f.write(latest + "\n")
        if android:
            f.write(f"ANDROID_VERSION={android}\n")

    changed = not path.exists() or not filecmp.cmp(path, tmp, shallow=False)

    if changed:
        tmp.replace(path)
        msg = f"{csc}/{model}: {latest}"
        if android:
            msg += f" (Android {android})"
        subprocess.run(["git", "add", str(path)], check=False)
        subprocess.run(["git", "commit", "-m", msg], check=False)
        out.append(f"{csc}/{model} updated")
    else:
        tmp.unlink(missing_ok=True)
        out.append(f"{csc}/{model} unchanged")

    return out


async def process_all(json_file):
    with open(json_file) as f:
        data = json.load(f)

    pairs = [
        (csc, model)
        for csc, models in data.get("CSC", {}).items()
        for model in models
    ]

    sem = asyncio.Semaphore(MAX_NET_CONCURRENCY)
    loop = asyncio.get_running_loop()
    pool = ThreadPoolExecutor(MAX_CPU_THREADS)
    logs = []

    async with httpx.AsyncClient(http2=True) as client:
        tasks = [fetch_xml(client, c, m, sem) for c, m in pairs]
        for coro in asyncio.as_completed(tasks):
            csc, model, xml, err = await coro
            if err:
                logs.append(f"{csc}/{model} failed ({err})")
                continue
            res = await loop.run_in_executor(pool, process_xml, csc, model, xml)
            logs.extend(res)

    pool.shutdown(wait=True)
    return logs


# ---------- ENTRY POINT ----------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--update", action="store_true", help="Update CSC list first")
    args = parser.parse_args()

    json_file = ORIGINAL_JSON

    if args.update:
        update_csc_file()
        json_file = UPDATED_JSON

    start = time.time()
    logs = asyncio.run(process_all(json_file))
    duration = time.time() - start

    with open(LOG_FILE, "a") as f:
        f.write(f"\nRun {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        for l in logs:
            f.write(l + "\n")
        f.write(f"Finished in {duration:.2f}s\n")

    if PUSH_AFTER:
        subprocess.run(["git", "push"], check=False)

    print(f"Finished in {duration:.2f}s using {json_file}")


if __name__ == "__main__":
    main()

