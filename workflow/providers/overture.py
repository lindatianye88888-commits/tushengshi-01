"""Regional Overture extraction with pinned releases and resumable range reads."""
from pathlib import Path
import hashlib
import io
import json
import time
import re

from ..task import write_json, now
from ..catalog import read_json

CATALOG_URL = "https://stac.overturemaps.org/catalog.json"


def arrow_modules():
    try:
        import pyarrow as pa
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
        import requests
    except ImportError as exc:
        raise ValueError("取数依赖未安装，请安装 requirements-data.txt；已有资料出图不需要此依赖。") from exc
    return pa, pc, pq, requests


def bbox_filter(pc, bbox):
    west, south, east, north = bbox
    return ((pc.field("bbox", "xmin") < east) & (pc.field("bbox", "xmax") > west) &
            (pc.field("bbox", "ymin") < north) & (pc.field("bbox", "ymax") > south))


def contains_bbox(outer, inner):
    return (len(outer)==4 and outer[0] <= inner[0] and outer[1] <= inner[1] and
            outer[2] >= inner[2] and outer[3] >= inner[3])


class RangeFile(io.RawIOBase):
    def __init__(self, asset, cache_dir, progress=None, session=None):
        _, _, _, requests = arrow_modules()
        self.url, self.size = asset["href"], asset["file:size"]
        self.position, self.transferred = 0, 0
        self.deadline = time.monotonic()+240
        self.session = session or requests.Session()
        self.progress = progress or (lambda message: None)
        self.cache = Path(cache_dir)/hashlib.sha256(self.url.encode()).hexdigest()
        self.cache.mkdir(parents=True,exist_ok=True)

    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.position

    def seek(self, offset, whence=0):
        position = offset if whence==0 else self.position+offset if whence==1 else self.size+offset
        if position < 0: raise ValueError("Negative range seek")
        self.position = position
        return position

    def read(self, size=-1):
        if size < 0: size = self.size-self.position
        size = min(size, self.size-self.position)
        if size <= 0: return b""
        if size > 80_000_000:
            raise ValueError("本次区域查询超过分块读取预算，请缩小范围。")
        start,end=self.position,self.position+size-1
        exact=self.cache/f"{start}-{end}.bin"
        if exact.exists() and exact.stat().st_size==size:
            data=exact.read_bytes()
        elif size<=262144:
            # Nested Parquet fields issue many adjacent tiny reads. Cache aligned
            # 1MiB blocks while returning exactly the originally requested bytes.
            block=1048576;pieces=[];cursor=start
            while cursor<=end:
                block_start=cursor//block*block;block_end=min(block_start+block-1,self.size-1)
                raw=self._range(block_start,block_end)
                take=min(end,block_end)-cursor+1
                pieces.append(raw[cursor-block_start:cursor-block_start+take]);cursor+=take
            data=b''.join(pieces)
        else:
            data=self._range(start,end)
        self.position+=size
        return data

    def _range(self,start,end):
        size=end-start+1
        target = self.cache/f"{start}-{end}.bin"
        if target.exists() and target.stat().st_size==size:
            data = target.read_bytes()
        else:
            if self.transferred+size>350_000_000:
                raise ValueError("本次区域查询超过分块读取预算，请缩小范围。")
            if time.monotonic() > self.deadline:
                raise TimeoutError("数据分块读取超时，已保留缓存，可重试或更换镜像。")
            self.progress(f"读取分块 {size/1e6:.2f} MB")
            separator = "&" if "?" in self.url else "?"
            response = self.session.get(self.url+f"{separator}site_range={start}-{end}",
                        headers={"Range":f"bytes={start}-{end}"},timeout=(15,25),stream=True)
            try:
                if response.status_code!=206 or response.headers.get("Content-Range")!=f"bytes {start}-{end}/{self.size}":
                    raise ValueError("服务器未返回请求的精确数据分块，停止提取。")
                chunks, received = [], 0
                for chunk in response.iter_content(262144):
                    if time.monotonic()>self.deadline: raise TimeoutError("分块传输超时。")
                    received += len(chunk)
                    if received > size: raise ValueError("数据分块大小超出响应声明。")
                    chunks.append(chunk)
                data=b"".join(chunks)
                if len(data)!=size: raise ValueError("数据分块不完整。")
                temp=target.with_suffix(".tmp");temp.write_bytes(data);temp.replace(target)
                self.transferred += size
            finally:
                response.close()
        return data

    def readinto(self, buffer):
        data=self.read(len(buffer));buffer[:len(data)]=data;return len(data)

    def close(self):
        if hasattr(self,"session"): self.session.close()
        super().close()


def cached_extract(cache, kind, bbox, release=None, required_columns=()):
    """Accept both v0.1 retained extracts and the generic v0.2 cache layout."""
    cache=Path(cache)
    candidates=list(cache.glob(f"overture-{kind}*.source.json"))+list(cache.glob(f"*/{kind}/*.source.json"))
    for meta_path in candidates:
        meta=read_json(meta_path)
        data_path=meta_path.with_name(meta_path.name.replace(".source.json",".parquet"))
        if (data_path.exists() and contains_bbox(meta.get("bbox",[]),bbox)
                and (release is None or meta.get("release")==release)
                and (not meta.get("sha256") or meta["sha256"]==hashlib.sha256(data_path.read_bytes()).hexdigest())):
            if required_columns:
                _,_,pq,_=arrow_modules()
                if not set(required_columns).issubset(pq.read_schema(data_path).names):
                    continue
            return data_path,meta
    return None


def extract(cache, kind, bbox, *, release=None, offline=False, progress=None, required_columns=()):
    if release is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.\d+",release):
        raise ValueError("release 格式需要 YYYY-MM-DD.n。")
    pa,pc,pq,requests=arrow_modules()
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    progress=progress or (lambda message: None)
    retained=cached_extract(cache,kind,bbox,release,required_columns)
    if retained and (offline or release is not None):
        path,meta=retained
        return pq.read_table(path,filters=bbox_filter(pc,bbox)),meta
    if offline:
        raise ValueError(f"离线缓存中没有覆盖本次范围且具有所需字段的 {kind}，请联网获取或提供资料。")
    if release is None:
        response=requests.get(CATALOG_URL,timeout=(10,25));response.raise_for_status()
        catalog=response.json();release=catalog["latest"]
        write_json(cache/"overture-catalog.json",catalog)
    retained=cached_extract(cache,kind,bbox,release,required_columns)
    if retained:
        path,meta=retained
        return pq.read_table(path,filters=bbox_filter(pc,bbox)),meta
    index_path=cache/f"{release}-collections.parquet"
    if not index_path.exists():
        response=requests.get(f"https://stac.overturemaps.org/{release}/collections.parquet",timeout=(10,40))
        response.raise_for_status()
        temp=index_path.with_suffix(".tmp");temp.write_bytes(response.content);temp.replace(index_path)
    index=pq.read_table(index_path)
    assets=index.filter((pc.field("collection")==kind)&(pc.field("type")=="Feature")&bbox_filter(pc,bbox))["assets"].to_pylist()
    if not assets: raise ValueError(f"{kind} 的来源索引未覆盖本次范围，不能断言对象为零。")
    tables, sources = [], []
    for n,asset in enumerate(assets):
        errors=[]
        for provider in ("aws","azure"):
            try:
                progress(f"{kind}：分片 {n+1}/{len(assets)}，{provider} 镜像")
                with RangeFile(asset[provider],cache/"ranges",progress) as handle:
                    schema=pq.ParquetFile(handle).schema_arrow
                    if not set(required_columns).issubset(schema.names):
                        raise ValueError(f"来源缺少必要字段：{sorted(set(required_columns)-set(schema.names))}")
                    columns=[key for key in ("id","geometry","bbox","names","sources","class","subtype","has_parts","height","num_floors","level",
                              "connectors","access_restrictions","prohibited_transitions","road_flags","level_rules",
                              "division_id","country","region","admin_level","is_land","is_territorial") if key in schema.names]
                    handle.seek(0)
                    table=pq.read_table(handle,columns=columns,filters=bbox_filter(pc,bbox),use_threads=False,pre_buffer=False)
                tables.append(table);sources.append(asset[provider]["href"])
                break
            except (OSError,ValueError,TimeoutError,requests.RequestException) as exc:
                errors.append(f"{provider}: {exc}")
        else:
            raise ValueError(f"{kind} 读取失败："+"; ".join(errors))
    table=pa.concat_tables(tables)
    query_hash=hashlib.sha256(json.dumps({"bbox":bbox,"columns":sorted(required_columns)} if required_columns else bbox).encode()).hexdigest()[:16]
    folder=cache/release/kind;folder.mkdir(parents=True,exist_ok=True)
    path=folder/f"{query_hash}.parquet"
    temp=path.with_suffix(".tmp");pq.write_table(table,temp);temp.replace(path)
    meta={"release":release,"kind":kind,"bbox":bbox,"source_files":sources,"row_count":table.num_rows,
          "acquired_at":now(),"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
    write_json(path.with_suffix(".source.json"),meta)
    return table,meta
