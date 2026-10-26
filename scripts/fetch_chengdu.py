"""Fetch one public OSM snapshot for the Chengdu MixC preview, retaining raw evidence."""
from pathlib import Path
import sys
import time
import xml.etree.ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from workflow.task import write_json

PROJECT = ROOT / "projects/chengdu-mixc-5km"
RAW = PROJECT / "data/raw"
LAT, LON = 30.6507515, 104.1126387
ENDPOINT = "https://maps.mail.ru/osm/tools/overpass/api/interpreter"


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    target = RAW / "chengdu.osm"
    if target.exists():
        print(f"Using cached raw snapshot: {target}", flush=True)
        return
    around = f"(around:5700,{LAT},{LON})"
    query = ('[out:xml][timeout:100];('
             f'way["highway"]{around};'
             f'nwr["building"]{around};'
             f'nwr["landuse"]{around};'
             f'nwr["amenity"~"school|college|university|hospital|clinic|library|kindergarten|place_of_worship|police|fire_station"]{around};'
             f'nwr["leisure"~"park|garden|sports_centre|stadium|pitch"]{around};'
             f'nwr["natural"~"water|wood|scrub|wetland"]{around};'
             ');(._;>;);out body;')
    (RAW / "query.overpassql").write_text(query, encoding="utf-8")
    print(f"Fetching 5.7km OSM source envelope from {ENDPOINT}", flush=True)
    started = time.monotonic()
    response = requests.get(ENDPOINT, params={"data": query}, timeout=(15, 120),
                            headers={"User-Agent": "SiteAnalysisWorkflow/0.1 (local research map)"})
    response.raise_for_status()
    tree = ET.fromstring(response.content)
    if tree.tag != "osm" or tree.find("remark") is not None:
        raise ValueError(f"Incomplete Overpass response: {response.text[:1000]}")
    target.write_bytes(response.content)
    write_json(RAW / "download.json", {"endpoint": ENDPOINT, "query": query,
               "osm_base": tree.find("meta").get("osm_base"),
               "center": {"lon": LON, "lat": LAT, "crs": "EPSG:4326",
                          "source": "OSM way/259719310: 成都万象城 landuse=retail; bbox center"},
               "bytes": len(response.content), "seconds": round(time.monotonic()-started, 1)})
    print(f"Saved {len(response.content):,} bytes in {time.monotonic()-started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
