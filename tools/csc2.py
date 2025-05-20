import requests
from bs4 import BeautifulSoup
import json
import time
import os

OUTPUT_FILE = "filtered_csc_data.json"
BASE_URL = "https://samfrew.com/firmware/upload/Desc/{offset}/1000"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DataCollector/1.0; +https://example.com/bot)"
}

def load_existing_data():
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"CSC": {}}

def save_data(data):
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def fetch_model_region(offset):
    url = BASE_URL.format(offset=offset)
    print(f"Fetching: {url}")
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        print(f"Failed to fetch page at offset {offset}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    divs = soup.find_all("div", class_="firmwareTable_flexCell__KPd_2")

    entries = []

    # Process in groups of 6 (model, region, version, OS, build, changelist)
    for i in range(0, len(divs), 6):
        group = divs[i:i + 6]
        if len(group) < 6:
            continue  # Skip incomplete entries

        model_div = group[0]
        region_div = group[1]
        os_div = group[3]

        model_label = model_div.find("span", class_="firmwareTable_flexCellLabel__b2sEY")
        region_label = region_div.find("span", class_="firmwareTable_flexCellLabel__b2sEY")
        os_label = os_div.find("span", class_="firmwareTable_flexCellLabel__b2sEY")

        if not (model_label and region_label and os_label):
            continue  # Skip malformed

        model = model_div.get_text(strip=True).replace("Model:", "").strip()
        region = region_div.get_text(strip=True).replace("Region:", "").strip()
        os_text = os_div.get_text(strip=True).replace("OS:", "").strip()

        try:
            os_version = int(os_text)
            if os_version >= 13:
                entries.append((model, region))
        except ValueError:
            continue  # Skip if OS is not an integer

    return entries


def fetch_model_region(offset):
    url = BASE_URL.format(offset=offset)
    print(f"Fetching: {url}")
    response = requests.get(url, headers=HEADERS)
    if response.status_code != 200:
        print(f"Failed to fetch page at offset {offset}")
        return []

    soup = BeautifulSoup(response.text, "html.parser")
    entries = []

    divs = soup.find_all("div", class_="firmwareTable_flexCell__KPd_2")
    current_model = None
    current_region = None
    current_os = None

    for div in divs:
        label_span = div.find("span", class_="firmwareTable_flexCellLabel__b2sEY")
        if not label_span:
            continue

        label = label_span.get_text(strip=True)
        full_text = div.get_text(strip=True)

        if label.startswith("Model:"):
            current_model = full_text.replace("Model:", "").strip()
        elif label.startswith("Region:"):
            current_region = full_text.replace("Region:", "").strip()
        elif label.startswith("OS:"):
            try:
                current_os = int(full_text.replace("OS:", "").strip())
            except ValueError:
                current_os = None

        if current_model and current_region and current_os is not None:
            if current_os >= 13:
                entries.append((current_model, current_region))
            # Reset after processing a full group
            current_model = None
            current_region = None
            current_os = None

    return entries

def update_and_save(model, region, csc_data):
    if region not in csc_data["CSC"]:
        csc_data["CSC"][region] = {}
    if model not in csc_data["CSC"][region]:
        csc_data["CSC"][region][model] = True
        save_data(csc_data)
        print(f"Added: {model} to region {region}")

def build_live_json():
    csc_data = load_existing_data()
    offset = 0

    while True:
        entries = fetch_model_region(offset)
        if not entries:
            print("No more entries. Done.")
            break

        for model, region in entries:
            update_and_save(model, region, csc_data)

        offset += 1000
        time.sleep(1)  # Be polite to the server

if __name__ == "__main__":
    build_live_json()
