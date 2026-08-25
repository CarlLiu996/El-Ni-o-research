from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from elnino_cta.sources.earthdata import (
    _read_region_pixels,
    build_ndvi_anomalies,
    earthdata_session,
    fetch_india_sugar_ndvi,
    fetch_india_sugar_ndvi_ornl,
    locate_subdatasets,
    parse_modis_date,
    read_mod13q1_granule,
    scale_and_mask_ndvi,
)


def regional_ndvi_history(*, latest_valid_ratio: float = 0.90) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    regions = ("Maharashtra", "Uttar Pradesh", "Karnataka")
    for year in range(2001, 2021):
        offset = -0.01 if (year - 2001) % 2 == 0 else 0.01
        for region, value in zip(regions, (0.50, 0.55, 0.60)):
            rows.append({
                "product_date": f"{year}-06-10",
                "region": region,
                "ndvi": value + offset,
                "valid_pixels": 90,
                "total_pixels": 100,
                "published_at": f"{year}-06-20",
                "source": "MOD13Q1.061",
                "granules": f"MOD13Q1.A{year}161.hdf",
            })
    for region, value in zip(regions, (0.55, 0.60, 0.65)):
        rows.append({
            "product_date": "2026-06-10",
            "region": region,
            "ndvi": value,
            "valid_pixels": int(100 * latest_valid_ratio),
            "total_pixels": 100,
            "published_at": "2026-06-20",
            "source": "MOD13Q1.061",
            "granules": "MOD13Q1.A2026161.hdf",
        })
    return pd.DataFrame(rows)


def test_scale_and_mask_ndvi_uses_modland_good_quality_bits() -> None:
    raw = np.array([[5000, 6000], [-3000, 10001]], dtype="int16")
    qa = np.array([[0, 1], [0, 0]], dtype="uint16")

    masked = scale_and_mask_ndvi(raw, qa)

    assert masked[0, 0] == pytest.approx(0.5)
    assert np.isnan(masked[0, 1])
    assert np.isnan(masked[1, 0])
    assert np.isnan(masked[1, 1])


def test_parse_modis_date_uses_year_and_day_of_year() -> None:
    assert parse_modis_date("MOD13Q1.A2026161.h25v06.061.hdf") == pd.Timestamp("2026-06-10")


def test_build_ndvi_anomalies_equal_weights_regions() -> None:
    result = build_ndvi_anomalies(
        regional_ndvi_history(), downloaded_at="2026-08-24T00:00:00Z"
    )

    latest = result.iloc[-1]
    assert latest["region_count"] == 3
    assert latest["ndvi"] == pytest.approx(0.60)
    assert latest["climatology_mean"] == pytest.approx(0.55)
    assert latest["same_period_difference"] == pytest.approx(0.05)
    assert latest["valid_pixel_ratio"] == pytest.approx(0.90)
    assert latest["status"] == "OK"


def test_low_valid_pixel_ratio_is_not_usable() -> None:
    result = build_ndvi_anomalies(regional_ndvi_history(latest_valid_ratio=0.20))

    assert result.iloc[-1]["status"] == "LOW_COVERAGE"
    assert pd.isna(result.iloc[-1]["same_period_difference"])


def test_earthdata_session_reads_token_without_leaking_it(monkeypatch) -> None:
    fake_token = "secret-test-token-that-must-not-appear"
    monkeypatch.setenv("EARTHDATA_TOKEN", fake_token)
    session = earthdata_session()
    assert session.headers["Authorization"] == f"Bearer {fake_token}"

    monkeypatch.delenv("EARTHDATA_TOKEN")
    with pytest.raises(PermissionError) as captured:
        earthdata_session()
    assert str(captured.value) == "AUTH_REQUIRED"
    assert fake_token not in str(captured.value)


def test_locate_subdatasets_requires_one_ndvi_and_one_quality_layer(monkeypatch) -> None:
    class Container:
        subdatasets = [
            'HDF4_EOS:EOS_GRID:"fake.hdf":MODIS_Grid_16DAY_250m_500m_VI:250m 16 days NDVI',
            'HDF4_EOS:EOS_GRID:"fake.hdf":MODIS_Grid_16DAY_250m_500m_VI:250m 16 days VI Quality',
        ]

        def __enter__(self):
            return self

        def __exit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr("elnino_cta.sources.earthdata.rasterio.open", lambda _: Container())

    ndvi, qa = locate_subdatasets("fake.hdf")

    assert ndvi.endswith("250m 16 days NDVI")
    assert qa.endswith("250m 16 days VI Quality")


class FakeGranule:
    def data_links(self) -> list[str]:
        return ["https://data.example/MOD13Q1.A2026161.h25v06.061.hdf"]


class FakeDownloadResponse:
    def raise_for_status(self) -> None:
        return None

    def iter_content(self, chunk_size: int):
        assert chunk_size > 0
        yield b"fake-hdf"


class FakeDownloadSession:
    def get(self, url: str, **kwargs: object) -> FakeDownloadResponse:
        assert url.endswith(".hdf")
        assert kwargs["stream"] is True
        return FakeDownloadResponse()


def test_fetch_india_sugar_ndvi_searches_downloads_and_aggregates(tmp_path) -> None:
    captured: dict[str, object] = {}

    def search_fn(**kwargs: object) -> list[FakeGranule]:
        captured.update(kwargs)
        return [FakeGranule()]

    def regional_reader(path, locations) -> pd.DataFrame:
        assert path.read_bytes() == b"fake-hdf"
        assert len(locations) == 3
        return pd.DataFrame({
            "product_date": ["2026-06-10"] * 3,
            "region": [item["name"] for item in locations],
            "ndvi": [0.55, 0.60, 0.65],
            "valid_pixels": [90, 90, 90],
            "total_pixels": [100, 100, 100],
            "published_at": ["2026-06-20"] * 3,
            "source": ["MOD13Q1.061"] * 3,
            "granules": [path.name] * 3,
        })

    locations = [
        {"name": "Maharashtra", "latitude": 19.75, "longitude": 75.71},
        {"name": "Uttar Pradesh", "latitude": 26.85, "longitude": 80.91},
        {"name": "Karnataka", "latitude": 15.32, "longitude": 75.71},
    ]
    result = fetch_india_sugar_ndvi(
        locations,
        start="2026-06-01",
        end="2026-06-30",
        cache_dir=tmp_path,
        search_fn=search_fn,
        regional_reader=regional_reader,
        session=FakeDownloadSession(),
    )

    assert captured["short_name"] == "MOD13Q1"
    assert captured["version"] == "061"
    assert captured["temporal"] == ("2026-06-01", "2026-06-30")
    assert captured["bounding_box"] == pytest.approx((74.96, 14.57, 81.66, 27.60))
    assert result.iloc[-1]["ndvi"] == pytest.approx(0.60)
    assert result.iloc[-1]["status"] == "OK"


def test_fetch_india_sugar_ndvi_marks_no_published_granules(tmp_path) -> None:
    result = fetch_india_sugar_ndvi(
        [{"name": "Maharashtra", "latitude": 19.75, "longitude": 75.71}],
        start="2026-06-01",
        end="2026-06-30",
        cache_dir=tmp_path,
        search_fn=lambda **_: [],
        session=FakeDownloadSession(),
    )

    assert len(result) == 1
    assert result.iloc[0]["status"] == "NOT_PUBLISHED"
    assert pd.isna(result.iloc[0]["ndvi"])


def test_fetch_india_sugar_ndvi_combines_pixels_across_tiles_before_median(tmp_path) -> None:
    class NamedGranule:
        def __init__(self, name: str):
            self.name = name

        def data_links(self) -> list[str]:
            return [f"https://data.example/{self.name}"]

    def regional_reader(path, locations) -> pd.DataFrame:
        values = np.array([0.4, 0.4]) if "h25v06" in path.name else np.array([0.8, 0.8])
        return pd.DataFrame([{
            "product_date": "2026-06-10",
            "region": item["name"],
            "ndvi": float(np.median(values)),
            "valid_pixels": len(values),
            "total_pixels": 2,
            "published_at": "2026-06-20",
            "source": "MOD13Q1.061",
            "granules": path.name,
            "_valid_values": values,
        } for item in locations])

    locations = [
        {"name": "Maharashtra", "latitude": 19.75, "longitude": 75.71},
        {"name": "Uttar Pradesh", "latitude": 26.85, "longitude": 80.91},
        {"name": "Karnataka", "latitude": 15.32, "longitude": 75.71},
    ]
    result = fetch_india_sugar_ndvi(
        locations,
        start="2026-06-01",
        end="2026-06-30",
        cache_dir=tmp_path,
        search_fn=lambda **_: [
            NamedGranule("MOD13Q1.A2026161.h25v06.061.hdf"),
            NamedGranule("MOD13Q1.A2026161.h26v06.061.hdf"),
        ],
        regional_reader=regional_reader,
        session=FakeDownloadSession(),
    )

    assert result.iloc[-1]["ndvi"] == pytest.approx(0.60)
    assert result.iloc[-1]["valid_pixels"] == 12
    assert "h25v06" in result.iloc[-1]["granules"]
    assert "h26v06" in result.iloc[-1]["granules"]


def _write_test_raster(path, values: np.ndarray) -> None:
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=values.shape[0],
        width=values.shape[1],
        count=1,
        dtype=values.dtype,
        crs="EPSG:3857",
        transform=from_origin(-2500, 2500, 1000, 1000),
    ) as dataset:
        dataset.write(values, 1)


def test_read_region_pixels_applies_circle_and_quality_mask(tmp_path) -> None:
    raw = np.full((5, 5), 5000, dtype="int16")
    qa = np.zeros((5, 5), dtype="uint16")
    qa[2, 2] = 1
    raw[2, 3] = 10001
    ndvi_path = tmp_path / "ndvi.tif"
    qa_path = tmp_path / "qa.tif"
    _write_test_raster(ndvi_path, raw)
    _write_test_raster(qa_path, qa)

    with rasterio.open(ndvi_path) as ndvi, rasterio.open(qa_path) as quality:
        values, total = _read_region_pixels(
            ndvi, quality, longitude=0.0, latitude=0.0, buffer_metres=2500
        )

    assert total == 21
    assert len(values) == 19
    assert np.allclose(values, 0.5)


def test_read_mod13q1_granule_returns_auditable_region_rows(tmp_path, monkeypatch) -> None:
    raw = np.full((5, 5), 6000, dtype="int16")
    qa = np.zeros((5, 5), dtype="uint16")
    ndvi_path = tmp_path / "ndvi.tif"
    qa_path = tmp_path / "qa.tif"
    _write_test_raster(ndvi_path, raw)
    _write_test_raster(qa_path, qa)
    monkeypatch.setattr(
        "elnino_cta.sources.earthdata.locate_subdatasets",
        lambda _: (str(ndvi_path), str(qa_path)),
    )

    result = read_mod13q1_granule(
        tmp_path / "MOD13Q1.A2026161.h25v06.061.hdf",
        [{"name": "Maharashtra", "latitude": 0.0, "longitude": 0.0}],
        buffer_metres=2500,
    )

    assert result.iloc[0]["product_date"] == pd.Timestamp("2026-06-10")
    assert result.iloc[0]["region"] == "Maharashtra"
    assert result.iloc[0]["ndvi"] == pytest.approx(0.60)
    assert result.iloc[0]["valid_pixels"] == 21
    assert result.iloc[0]["total_pixels"] == 21
    assert result.iloc[0]["granules"].endswith("h25v06.061.hdf")


class OrnlResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.payload


class OrnlSession:
    def __init__(self):
        self.urls: list[str] = []

    def get(self, url: str, **kwargs: object) -> OrnlResponse:
        self.urls.append(url)
        if "/dates" in url:
            return OrnlResponse({"dates": [
                {"modis_date": "A2001161", "calendar_date": "2001-06-10"},
                {"modis_date": "A2026161", "calendar_date": "2026-06-10"},
            ]})
        dates = ["2001-06-10", "2026-06-10"]
        band = kwargs["params"]["band"]
        data = [0] * 4 if band.endswith("VI_Quality") else [5000] * 4
        return OrnlResponse({
            "nrows": 2, "ncols": 2, "cellsize": 250,
            "subset": [
                {"calendar_date": date, "modis_date": "A" + date[:4] + "161", "band": band, "data": data}
                for date in dates
            ],
        })


def test_ornl_subset_fetch_builds_ndvi_without_earthdata_token() -> None:
    session = OrnlSession()
    locations = [
        {"name": "Maharashtra", "latitude": 19.75, "longitude": 75.71},
        {"name": "Uttar Pradesh", "latitude": 26.85, "longitude": 80.91},
    ]

    result = fetch_india_sugar_ndvi_ornl(
        locations, start="2001-01-01", end="2026-08-25", session=session,
        buffer_km=1,
    )

    assert result.iloc[-1]["ndvi"] == pytest.approx(0.5)
    assert result.iloc[-1]["region_count"] == 2
    assert result.iloc[-1]["status"] == "OK"
    assert any("/MOD13Q1/dates" in url for url in session.urls)
    assert any("/MOD13Q1/subset" in url for url in session.urls)
    assert not any("Authorization" in url for url in session.urls)
