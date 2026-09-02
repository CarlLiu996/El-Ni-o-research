from __future__ import annotations

import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import earthaccess
import numpy as np
import pandas as pd
import rasterio
import requests
from pyproj import Transformer
from rasterio.windows import Window, from_bounds


MOD13Q1_DOI = "https://doi.org/10.5067/MODIS/MOD13Q1.061"
MYD13Q1_DOI = "https://doi.org/10.5067/MODIS/MYD13Q1.061"
ORNL_MODIS_API = "https://modis.ornl.gov/rst/api/v1"
ORNL_MODIS_DOCS = "https://modis.ornl.gov/data/modis_webservice.html"
PLANETARY_STAC_API = "https://planetarycomputer.microsoft.com/api/stac/v1"
PLANETARY_DATA_API = "https://planetarycomputer.microsoft.com/api/data/v1"
PLANETARY_MODIS_SOURCE = (
    f"{MYD13Q1_DOI} via Microsoft Planetary Computer modis-13Q1-061 point gateway"
)
PLANETARY_SAMPLE_OFFSETS = ((0.0, 0.0), (0.05, 0.0), (-0.05, 0.0), (0.0, 0.05), (0.0, -0.05))
ORNL_NDVI_BAND = "250m_16_days_NDVI"
ORNL_QA_BAND = "250m_16_days_VI_Quality"
BUFFER_METRES = 50_000
NDVI_COLUMNS = [
    "product_date",
    "composite_slot",
    "ndvi",
    "climatology_mean",
    "climatology_std",
    "same_period_difference",
    "z_score",
    "historical_percentile",
    "valid_pixels",
    "total_pixels",
    "valid_pixel_ratio",
    "region_count",
    "status",
    "published_at",
    "downloaded_at",
    "source",
    "granules",
]


def earthdata_session() -> requests.Session:
    token = os.getenv("EARTHDATA_TOKEN")
    if not token:
        raise PermissionError("AUTH_REQUIRED")
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})
    return session


def scale_and_mask_ndvi(ndvi_raw: np.ndarray, qa_raw: np.ndarray) -> np.ndarray:
    if ndvi_raw.shape != qa_raw.shape:
        raise ValueError("NDVI and QA arrays must have the same shape")
    good_quality = (qa_raw.astype("uint16") & 0b11) == 0
    valid_range = (ndvi_raw >= -2000) & (ndvi_raw <= 10000)
    return np.where(
        good_quality & valid_range,
        ndvi_raw.astype("float64") * 0.0001,
        np.nan,
    )


def parse_modis_date(path: str | Path) -> pd.Timestamp:
    match = re.search(r"\.A(\d{4})(\d{3})\.", Path(path).name)
    if not match:
        raise ValueError(f"Cannot parse MODIS date from {Path(path).name}")
    return pd.Timestamp(year=int(match.group(1)), month=1, day=1) + pd.Timedelta(
        days=int(match.group(2)) - 1
    )


def locate_subdatasets(path: str | Path) -> tuple[str, str]:
    with rasterio.open(path) as container:
        names = list(container.subdatasets)
    ndvi = [name for name in names if name.endswith(":250m 16 days NDVI")]
    qa = [name for name in names if name.endswith(":250m 16 days VI Quality")]
    if len(ndvi) != 1 or len(qa) != 1:
        raise ValueError(f"Unexpected MOD13Q1 subdatasets in {Path(path).name}")
    return ndvi[0], qa[0]


def _read_region_pixels(
    ndvi_dataset: Any,
    qa_dataset: Any,
    *,
    longitude: float,
    latitude: float,
    buffer_metres: int = BUFFER_METRES,
) -> tuple[np.ndarray, int]:
    if ndvi_dataset.crs != qa_dataset.crs or ndvi_dataset.transform != qa_dataset.transform:
        raise ValueError("NDVI and QA subdatasets must share CRS and transform")
    transformer = Transformer.from_crs("EPSG:4326", ndvi_dataset.crs, always_xy=True)
    x, y = transformer.transform(longitude, latitude)
    requested = from_bounds(
        x - buffer_metres,
        y - buffer_metres,
        x + buffer_metres,
        y + buffer_metres,
        transform=ndvi_dataset.transform,
    ).round_offsets().round_lengths()
    window = requested.intersection(Window(0, 0, ndvi_dataset.width, ndvi_dataset.height))
    ndvi_raw = ndvi_dataset.read(1, window=window)
    qa_raw = qa_dataset.read(1, window=window)
    scaled = scale_and_mask_ndvi(ndvi_raw, qa_raw)
    transform = ndvi_dataset.window_transform(window)
    row_indices, column_indices = np.indices(scaled.shape)
    xs, ys = rasterio.transform.xy(
        transform, row_indices, column_indices, offset="center"
    )
    x_centres = np.asarray(xs).reshape(scaled.shape)
    y_centres = np.asarray(ys).reshape(scaled.shape)
    circle = (x_centres - x) ** 2 + (y_centres - y) ** 2 <= buffer_metres**2
    total_pixels = int(circle.sum())
    valid_values = scaled[circle & np.isfinite(scaled)]
    return valid_values, total_pixels


def read_mod13q1_granule(
    path: str | Path,
    locations: list[dict[str, object]],
    *,
    buffer_metres: int = BUFFER_METRES,
) -> pd.DataFrame:
    granule_path = Path(path)
    ndvi_name, qa_name = locate_subdatasets(granule_path)
    product_date = parse_modis_date(granule_path)
    rows: list[dict[str, object]] = []
    with rasterio.open(ndvi_name) as ndvi_dataset, rasterio.open(qa_name) as qa_dataset:
        for location in locations:
            values, total_pixels = _read_region_pixels(
                ndvi_dataset,
                qa_dataset,
                longitude=float(location["longitude"]),
                latitude=float(location["latitude"]),
                buffer_metres=buffer_metres,
            )
            rows.append({
                "product_date": product_date,
                "region": str(location["name"]),
                "ndvi": float(np.median(values)) if len(values) else np.nan,
                "valid_pixels": len(values),
                "total_pixels": total_pixels,
                "published_at": pd.NaT,
                "source": MOD13Q1_DOI,
                "granules": granule_path.name,
                "_valid_values": values,
            })
    return pd.DataFrame(rows)


def _join_unique(values: pd.Series) -> str:
    return "; ".join(sorted({str(value) for value in values.dropna() if str(value)}))


def build_ndvi_anomalies(
    regional_history: pd.DataFrame,
    downloaded_at: str | None = None,
) -> pd.DataFrame:
    required = {
        "product_date",
        "region",
        "ndvi",
        "valid_pixels",
        "total_pixels",
        "published_at",
        "source",
        "granules",
    }
    missing = required.difference(regional_history.columns)
    if missing:
        raise ValueError(f"Missing NDVI input columns: {sorted(missing)}")
    if regional_history.empty:
        return pd.DataFrame(columns=NDVI_COLUMNS)

    regional = regional_history.copy()
    regional["product_date"] = pd.to_datetime(regional["product_date"], errors="coerce")
    regional["published_at"] = pd.to_datetime(regional["published_at"], errors="coerce")
    for column in ("ndvi", "valid_pixels", "total_pixels"):
        regional[column] = pd.to_numeric(regional[column], errors="coerce")
    regional = regional.dropna(subset=["product_date", "region"])
    regional["composite_slot"] = ((regional["product_date"].dt.dayofyear - 1) // 16) + 1

    rows: list[dict[str, Any]] = []
    for product_date, group in regional.groupby("product_date", sort=True):
        region_values = group.drop_duplicates("region")
        valid_pixels = int(region_values["valid_pixels"].fillna(0).sum())
        total_pixels = int(region_values["total_pixels"].fillna(0).sum())
        rows.append({
            "product_date": product_date,
            "composite_slot": int(region_values["composite_slot"].iloc[0]),
            "ndvi": float(region_values["ndvi"].mean()),
            "valid_pixels": valid_pixels,
            "total_pixels": total_pixels,
            "valid_pixel_ratio": valid_pixels / total_pixels if total_pixels else pd.NA,
            "region_count": int(region_values["ndvi"].notna().sum()),
            "published_at": region_values["published_at"].max(),
            "source": _join_unique(region_values["source"]),
            "granules": _join_unique(region_values["granules"]),
        })
    result = pd.DataFrame(rows).sort_values("product_date").reset_index(drop=True)
    baseline = result[result["product_date"].dt.year.between(2001, 2020)].copy()
    climatology = baseline.groupby("composite_slot")["ndvi"].agg(["mean", "std"])
    distributions = baseline.groupby("composite_slot")["ndvi"].apply(list)
    result["climatology_mean"] = result["composite_slot"].map(climatology["mean"])
    result["climatology_std"] = result["composite_slot"].map(climatology["std"])
    result["same_period_difference"] = result["ndvi"] - result["climatology_mean"]
    result["z_score"] = result["same_period_difference"] / result["climatology_std"]

    def percentile(row: pd.Series) -> float | Any:
        history = pd.to_numeric(
            pd.Series(distributions.get(row["composite_slot"], [])), errors="coerce"
        ).dropna()
        if history.empty or pd.isna(row["ndvi"]):
            return pd.NA
        return float(history.le(float(row["ndvi"])).mean())

    result["historical_percentile"] = result.apply(percentile, axis=1)
    usable = result["valid_pixel_ratio"].ge(0.30) & result["region_count"].ge(2)
    result["status"] = usable.map({True: "OK", False: "LOW_COVERAGE"})
    unavailable = ~usable
    result.loc[
        unavailable,
        ["same_period_difference", "z_score", "historical_percentile"],
    ] = pd.NA
    result["downloaded_at"] = downloaded_at or pd.Timestamp.now(tz="UTC").isoformat()
    result["source"] = result["source"].replace("", MOD13Q1_DOI)
    return result[NDVI_COLUMNS]


def _not_published(downloaded_at: str | None = None) -> pd.DataFrame:
    row = {column: pd.NA for column in NDVI_COLUMNS}
    row.update({
        "status": "NOT_PUBLISHED",
        "downloaded_at": downloaded_at or pd.Timestamp.now(tz="UTC").isoformat(),
        "source": MOD13Q1_DOI,
    })
    return pd.DataFrame([row], columns=NDVI_COLUMNS)


def _ornl_calendar_date(record: dict[str, object]) -> pd.Timestamp:
    calendar = pd.to_datetime(record.get("calendar_date"), errors="coerce")
    if pd.notna(calendar):
        return pd.Timestamp(calendar)
    match = re.fullmatch(r"A(\d{4})(\d{3})", str(record.get("modis_date", "")))
    if not match:
        return pd.NaT
    return pd.Timestamp(year=int(match.group(1)), month=1, day=1) + pd.Timedelta(
        days=int(match.group(2)) - 1
    )


def _ornl_circle_values(
    ndvi_record: dict[str, object],
    qa_record: dict[str, object],
    *,
    nrows: int,
    ncols: int,
    cellsize: float,
    buffer_km: int,
) -> tuple[np.ndarray, int]:
    ndvi_raw = pd.to_numeric(pd.Series(ndvi_record.get("data", [])), errors="coerce").to_numpy()
    qa_raw = pd.to_numeric(pd.Series(qa_record.get("data", [])), errors="coerce").to_numpy()
    expected = nrows * ncols
    if len(ndvi_raw) != expected or len(qa_raw) != expected:
        raise ValueError("Unexpected ORNL subset dimensions")
    ndvi_array = np.where(np.isfinite(ndvi_raw), ndvi_raw, -3001).astype("int32").reshape(nrows, ncols)
    qa_array = np.where(np.isfinite(qa_raw), qa_raw, 3).astype("uint16").reshape(nrows, ncols)
    scaled = scale_and_mask_ndvi(ndvi_array, qa_array)
    rows, columns = np.indices((nrows, ncols))
    y = (rows - (nrows - 1) / 2.0) * cellsize
    x = (columns - (ncols - 1) / 2.0) * cellsize
    circle = x**2 + y**2 <= (buffer_km * 1000) ** 2
    return scaled[circle & np.isfinite(scaled)], int(circle.sum())


def _ornl_subset(
    session: requests.Session,
    *,
    latitude: float,
    longitude: float,
    band: str,
    start_date: str,
    end_date: str,
    buffer_km: int,
    timeout: int,
) -> dict[str, object]:
    response = session.get(
        f"{ORNL_MODIS_API}/MOD13Q1/subset",
        params={
            "latitude": latitude,
            "longitude": longitude,
            "band": band,
            "startDate": start_date,
            "endDate": end_date,
            "kmAboveBelow": buffer_km,
            "kmLeftRight": buffer_km,
        },
        headers={"Accept": "application/json"},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def fetch_india_sugar_ndvi_ornl(
    locations: list[dict[str, object]],
    start: str,
    end: str,
    *,
    session: requests.Session | None = None,
    buffer_km: int = 1,
    timeout: int = 60,
    downloaded_at: str | None = None,
) -> pd.DataFrame:
    """Fetch a lightweight MOD13Q1 point-neighbourhood proxy from ORNL TESViS.

    The public service limits each request to ten composite dates. A 1-km radius
    is the default so a 25-year refresh remains operationally small; the output
    is therefore a representative-point vegetation proxy, not a crop mask.
    """
    if not locations:
        raise ValueError("At least one India sugar region is required")
    if not 1 <= buffer_km <= 100:
        raise ValueError("buffer_km must be between 1 and 100")
    client = session or requests.Session()
    start_timestamp = pd.Timestamp(start)
    end_timestamp = pd.Timestamp(end)
    regional_rows: list[dict[str, object]] = []
    for location in locations:
        latitude = float(location["latitude"])
        longitude = float(location["longitude"])
        response = client.get(
            f"{ORNL_MODIS_API}/MOD13Q1/dates",
            params={"latitude": latitude, "longitude": longitude},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        response.raise_for_status()
        available = []
        for record in response.json().get("dates", []):
            date = _ornl_calendar_date(record)
            if pd.notna(date) and start_timestamp <= date <= end_timestamp:
                available.append((str(record["modis_date"]), pd.Timestamp(date)))
        available.sort(key=lambda item: item[1])
        for offset in range(0, len(available), 10):
            batch = available[offset:offset + 10]
            if not batch:
                continue
            payloads = {
                band: _ornl_subset(
                    client,
                    latitude=latitude,
                    longitude=longitude,
                    band=band,
                    start_date=batch[0][0],
                    end_date=batch[-1][0],
                    buffer_km=buffer_km,
                    timeout=timeout,
                )
                for band in (ORNL_NDVI_BAND, ORNL_QA_BAND)
            }
            ndvi_payload = payloads[ORNL_NDVI_BAND]
            qa_payload = payloads[ORNL_QA_BAND]
            nrows = int(ndvi_payload["nrows"])
            ncols = int(ndvi_payload["ncols"])
            cellsize = float(ndvi_payload["cellsize"])
            ndvi_by_date = {
                str(record.get("modis_date")): record
                for record in ndvi_payload.get("subset", [])
                if record.get("band") == ORNL_NDVI_BAND
            }
            qa_by_date = {
                str(record.get("modis_date")): record
                for record in qa_payload.get("subset", [])
                if record.get("band") == ORNL_QA_BAND
            }
            for modis_date, date in batch:
                if modis_date not in ndvi_by_date or modis_date not in qa_by_date:
                    continue
                values, total_pixels = _ornl_circle_values(
                    ndvi_by_date[modis_date],
                    qa_by_date[modis_date],
                    nrows=nrows,
                    ncols=ncols,
                    cellsize=cellsize,
                    buffer_km=buffer_km,
                )
                regional_rows.append({
                    "product_date": date,
                    "region": str(location["name"]),
                    "ndvi": float(np.median(values)) if len(values) else np.nan,
                    "valid_pixels": len(values),
                    "total_pixels": total_pixels,
                    "published_at": pd.NaT,
                    "source": f"{MOD13Q1_DOI} via {ORNL_MODIS_DOCS}",
                    "granules": f"ORNL:{modis_date}:{location['name']}:r{buffer_km}km",
                })
    if not regional_rows:
        return _not_published(downloaded_at)
    return build_ndvi_anomalies(pd.DataFrame(regional_rows), downloaded_at=downloaded_at)


def _planetary_product_date(item_id: str) -> pd.Timestamp:
    match = re.search(r"\.A(\d{4})(\d{3})\.", item_id)
    if not match:
        return pd.NaT
    return pd.Timestamp(year=int(match.group(1)), month=1, day=1) + pd.Timedelta(
        days=int(match.group(2)) - 1
    )


def fetch_india_sugar_ndvi_planetary(
    locations: list[dict[str, object]],
    start: str,
    end: str,
    *,
    session: requests.Session | None = None,
    baseline_years: tuple[int, int] = (2003, 2020),
    recent_composites: int = 6,
    sample_offsets: tuple[tuple[float, float], ...] = PLANETARY_SAMPLE_OFFSETS,
    max_workers: int = 8,
    downloaded_at: str | None = None,
) -> pd.DataFrame:
    """Read a compact MYD13Q1 point proxy through Planetary Computer.

    The gateway samples the original NASA MODIS COG assets server-side, so the
    repository stores only the three-region statistics instead of hundreds of
    200+ MB source granules. Aqua MYD13Q1 is used because its 16-day cycle is
    more current in the catalog; the 2003-2020 baseline is product-consistent.
    """
    if not locations:
        raise ValueError("At least one India sugar region is required")
    if recent_composites < 1:
        raise ValueError("recent_composites must be positive")
    if not sample_offsets:
        raise ValueError("sample_offsets must not be empty")
    client = session or requests.Session()
    if session is None:
        client.trust_env = False
    thread_state = threading.local()

    def point_client() -> requests.Session:
        if session is not None:
            return client
        if not hasattr(thread_state, "client"):
            thread_state.client = requests.Session()
            thread_state.client.trust_env = False
        return thread_state.client
    regional_rows: list[dict[str, object]] = []
    start_year, end_year = baseline_years

    for location in locations:
        longitude = float(location["longitude"])
        latitude = float(location["latitude"])
        response = client.post(
            f"{PLANETARY_STAC_API}/search",
            json={
                "collections": ["modis-13Q1-061"],
                "bbox": [longitude - 0.1, latitude - 0.1, longitude + 0.1, latitude + 0.1],
                "datetime": f"{start}/{end}",
                "limit": 1000,
            },
            timeout=60,
        )
        response.raise_for_status()
        dated_items: list[tuple[pd.Timestamp, dict[str, object]]] = []
        for item in response.json().get("features", []):
            item_id = str(item.get("id", ""))
            assets = item.get("assets", {})
            product_date = _planetary_product_date(item_id)
            if (
                item_id.startswith("MYD13Q1.")
                and pd.notna(product_date)
                and "250m_16_days_NDVI" in assets
                and "250m_16_days_VI_Quality" in assets
            ):
                dated_items.append((product_date, item))
        if not dated_items:
            continue

        current_dates = sorted({
            product_date
            for product_date, _ in dated_items
            if product_date.year > end_year
        })[-recent_composites:]
        slots = {int((date.dayofyear - 1) // 16 + 1) for date in current_dates}
        selected = [
            (product_date, item)
            for product_date, item in dated_items
            if product_date in current_dates
            or (
                start_year <= product_date.year <= end_year
                and int((product_date.dayofyear - 1) // 16 + 1) in slots
            )
        ]
        candidates_by_date = {
            product_date: [item for date, item in selected if date == product_date]
            for product_date in sorted({date for date, _ in selected})
        }

        def sample_point(task: tuple[pd.Timestamp, float, float]) -> dict[str, object]:
            product_date, longitude_offset, latitude_offset = task
            for item in candidates_by_date[product_date]:
                item_id = str(item["id"])
                try:
                    point = point_client().get(
                        f"{PLANETARY_DATA_API}/item/point/"
                        f"{longitude + longitude_offset},{latitude + latitude_offset}",
                        params={
                            "collection": "modis-13Q1-061",
                            "item": item_id,
                            "assets": ["250m_16_days_NDVI", "250m_16_days_VI_Quality"],
                        },
                        timeout=60,
                    )
                    point.raise_for_status()
                except (requests.RequestException, RuntimeError):
                    continue
                payload = point.json()
                observations = dict(zip(payload.get("band_names", []), payload.get("values", [])))
                ndvi_raw = observations.get("250m_16_days_NDVI_b1")
                qa_raw = observations.get("250m_16_days_VI_Quality_b1")
                if ndvi_raw is None or qa_raw is None:
                    continue
                scaled = scale_and_mask_ndvi(
                    np.asarray([[ndvi_raw]]), np.asarray([[qa_raw]])
                )[0, 0]
                properties = item.get("properties", {})
                return {
                    "product_date": product_date,
                    "value": scaled,
                    "published_at": properties.get("created") or properties.get("updated"),
                    "granule": item_id,
                }
            return {
                "product_date": product_date,
                "value": np.nan,
                "published_at": pd.NaT,
                "granule": "",
            }

        tasks = [
            (product_date, longitude_offset, latitude_offset)
            for product_date in candidates_by_date
            for longitude_offset, latitude_offset in sample_offsets
        ]
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            samples = pd.DataFrame(executor.map(sample_point, tasks))
        for product_date, group in samples.groupby("product_date", sort=True):
            values = pd.to_numeric(group["value"], errors="coerce").dropna()
            regional_rows.append({
                "product_date": product_date,
                "region": str(location["name"]),
                "ndvi": float(values.median()) if not values.empty else np.nan,
                "valid_pixels": len(values),
                "total_pixels": len(group),
                "published_at": pd.to_datetime(group["published_at"], errors="coerce").max(),
                "source": PLANETARY_MODIS_SOURCE,
                "granules": _join_unique(group["granule"]),
            })

    if not regional_rows:
        return _not_published(downloaded_at)
    return build_ndvi_anomalies(pd.DataFrame(regional_rows), downloaded_at=downloaded_at)


def _download_granule(granule: Any, cache_dir: Path, session: requests.Session) -> Path:
    links = [url for url in granule.data_links() if urlparse(url).path.lower().endswith(".hdf")]
    if not links:
        raise ValueError("MOD13Q1 granule has no HTTPS HDF data link")
    source_url = links[0]
    target = cache_dir / Path(urlparse(source_url).path).name
    if target.exists() and target.stat().st_size > 0:
        return target
    response = session.get(source_url, stream=True, timeout=180)
    response.raise_for_status()
    temporary = target.with_suffix(".part")
    with temporary.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    temporary.replace(target)
    return target


def _combine_tile_pixels(regional: pd.DataFrame) -> pd.DataFrame:
    if "_valid_values" not in regional.columns:
        return regional
    rows: list[dict[str, object]] = []
    for (product_date, region), group in regional.groupby(["product_date", "region"], sort=True):
        arrays = [
            np.asarray(value, dtype="float64")
            for value in group["_valid_values"]
            if isinstance(value, (np.ndarray, list, tuple))
        ]
        values = np.concatenate(arrays) if arrays else np.array([], dtype="float64")
        rows.append({
            "product_date": product_date,
            "region": region,
            "ndvi": float(np.median(values)) if len(values) else np.nan,
            "valid_pixels": len(values),
            "total_pixels": int(pd.to_numeric(group["total_pixels"], errors="coerce").sum()),
            "published_at": pd.to_datetime(group["published_at"], errors="coerce").max(),
            "source": _join_unique(group["source"]),
            "granules": _join_unique(group["granules"]),
        })
    return pd.DataFrame(rows)


def fetch_india_sugar_ndvi(
    locations: list[dict[str, object]],
    start: str,
    end: str,
    cache_dir: str | Path,
    *,
    search_fn: Callable[..., list[Any]] | None = None,
    regional_reader: Callable[[Path, list[dict[str, object]]], pd.DataFrame] | None = None,
    session: requests.Session | None = None,
    downloaded_at: str | None = None,
) -> pd.DataFrame:
    if not locations:
        raise ValueError("At least one India sugar region is required")
    cache = Path(cache_dir)
    cache.mkdir(parents=True, exist_ok=True)
    client = session or earthdata_session()
    search = search_fn or earthaccess.search_data
    longitudes = [float(item["longitude"]) for item in locations]
    latitudes = [float(item["latitude"]) for item in locations]
    bounds = (
        min(longitudes) - 0.75,
        min(latitudes) - 0.75,
        max(longitudes) + 0.75,
        max(latitudes) + 0.75,
    )
    granules = search(
        short_name="MOD13Q1",
        version="061",
        bounding_box=bounds,
        temporal=(start, end),
    )
    if not granules:
        return _not_published(downloaded_at)
    reader = regional_reader or read_mod13q1_granule
    frames = [reader(_download_granule(item, cache, client), locations) for item in granules]
    regional = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if regional.empty:
        return _not_published(downloaded_at)
    regional = _combine_tile_pixels(regional)
    return build_ndvi_anomalies(regional, downloaded_at=downloaded_at)
