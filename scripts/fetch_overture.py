"""Bounded regional extraction from Overture's STAC index and GeoParquet."""
from pathlib import Path
import io
import json
import os
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".vendor"))
sys.path.insert(0, str(ROOT))
import requests
import pyarrow.compute as pc
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.fs as fs
import pyarrow.parquet as pq
from workflow.task import write_json

RAW = ROOT / "projects/chengdu-mixc-5km/data/raw"
BBOX = (104.052, 30.598, 104.174, 30.703)


class HTTPRangeFile(io.RawIOBase):
    """Read only Parquet byte ranges through the same proxy as requests."""
    def __init__(self, asset):
        self.url, self.size = asset["href"], asset["file:size"]
        self.position, self.transferred = 0, 0
        self.session = requests.Session()

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        self.position = offset if whence == 0 else self.position+offset if whence == 1 else self.size+offset
        return self.position

    def read(self, size=-1):
        if size < 0:
            size = self.size-self.position
        size = min(size, self.size-self.position)
        if size <= 0:
            return b""
        if size > 80_000_000 or self.transferred+size > 350_000_000:
            raise ValueError("Extraction exceeded the regional byte-range budget.")
        start, end = self.position, self.position+size-1
        print(f"  requesting {size/1e6:.2f} MB at byte {start}", flush=True)
        url = self.url + f"?site_range={start}-{end}"
        response = self.session.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=(15, 70), stream=True)
        try:
            if response.status_code != 206 or not response.headers.get("Content-Range", "").startswith(f"bytes {start}-{end}/"):
                raise ValueError(f"Range response mismatch: {response.status_code}, {response.headers.get('Content-Range')}")
            data = response.content
            if len(data) != size:
                raise ValueError("Truncated HTTP range response")
            self.position += size
            self.transferred += size
            print(f"  range {size/1e6:.2f} MB; total {self.transferred/1e6:.2f} MB", flush=True)
            return data
        finally:
            response.close()

    def readinto(self, buffer):
        data = self.read(len(buffer))
        buffer[:len(data)] = data
        return len(data)


def main():
    RAW.mkdir(parents=True, exist_ok=True)
    metadata = RAW / "overture-catalog.json"
    if metadata.exists():
        catalog = json.loads(metadata.read_text(encoding="utf-8"))
    else:
        response = requests.get("https://stac.overturemaps.org/catalog.json", timeout=25)
        response.raise_for_status()
        catalog = response.json()
        write_json(metadata, catalog)
    release = catalog["latest"]
    index_path = RAW / f"{release}-collections.parquet"
    if not index_path.exists():
        response = requests.get(f"https://stac.overturemaps.org/{release}/collections.parquet", timeout=40)
        response.raise_for_status()
        index_path.write_bytes(response.content)
    index = pq.read_table(index_path)
    west, south, east, north = BBOX
    intersects = ((pc.field("bbox", "xmin") < east) & (pc.field("bbox", "xmax") > west) &
                  (pc.field("bbox", "ymin") < north) & (pc.field("bbox", "ymax") > south))
    for kind in sys.argv[1:] or ["building", "segment", "land_use"]:
        provider = os.environ.get("OVERTURE_PROVIDER", "azure")
        suffix = "-aws" if provider == "aws" else ""
        target = RAW / f"overture-{kind}{suffix}.parquet"
        if target.exists():
            print(f"Cached {kind}: {target.stat().st_size:,} bytes", flush=True)
            continue
        selected = index.filter((pc.field("collection") == kind) & (pc.field("type") == "Feature") & intersects)
        assets = selected["assets"].to_pylist()
        paths = [a["aws"]["alternate"]["s3"]["href"][5:] for a in assets]
        print(f"{kind}: release {release}, {len(paths)} source shards", flush=True)
        if not paths:
            raise ValueError(f"No source shards intersect {BBOX}")
        started = time.monotonic()
        tables = []
        for asset in assets:
            handle = HTTPRangeFile(asset[provider])
            schema = pq.ParquetFile(handle).schema_arrow
            wanted = [c for c in ("id", "geometry", "bbox", "names", "sources", "class", "subtype",
                                   "has_parts", "height", "num_floors", "level") if c in schema.names]
            handle.seek(0)
            tables.append(pq.read_table(handle, columns=wanted, filters=intersects, use_threads=False))
        table = pa.concat_tables(tables)
        temporary = target.with_suffix(".parquet.tmp")
        pq.write_table(table, temporary)
        temporary.replace(target)
        write_json(RAW / f"overture-{kind}{suffix}.source.json", {
            "release": release, "kind": kind, "bbox": BBOX, "source_files": ["s3://"+p for p in paths],
            "row_count": table.num_rows, "columns": wanted, "seconds": round(time.monotonic()-started, 1)})
        print(f"{kind}: saved {table.num_rows:,} features in {time.monotonic()-started:.1f}s", flush=True)


if __name__ == "__main__":
    main()
