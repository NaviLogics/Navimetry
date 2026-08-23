from __future__ import annotations
import csv
import hashlib
import json
import math
import shutil
from pathlib import Path
from typing import Callable
import laspy
import matplotlib
matplotlib.use("Agg")
import numpy as np
import pandas as pd
import rasterio
import trimesh
from matplotlib import pyplot as plt
from pyproj import CRS, Transformer
from rasterio.transform import from_origin
from scipy.interpolate import LinearNDInterpolator
from scipy.spatial import Delaunay, QhullError, cKDTree
from bathymetry.models import CsvInspection, ProcessingConfig
class ProcessingError(Exception):
    """Ошибка обработки батиметрических данных."""
def detect_csv_format(path: Path) -> tuple[str, str]:
    """Определяет кодировку и разделитель CSV-файла."""
    if not path.exists():
        raise ProcessingError(
            f"Входной CSV-файл не найден: {path}"
        )
    if not path.is_file():
        raise ProcessingError(
            f"Путь к CSV не является файлом: {path}"
        )
    raw = path.read_bytes()[:65536]
    text: str | None = None
    used_encoding = "utf-8-sig"
    for encoding in (
        "utf-8-sig",
        "utf-8",
        "cp1251",
        "latin-1",
    ):
        try:
            text = raw.decode(encoding)
            used_encoding = encoding
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        raise ProcessingError(
            "Не удалось определить кодировку CSV"
        )
    try:
        delimiter = csv.Sniffer().sniff(
            text,
            delimiters=",;\t|",
        ).delimiter
    except csv.Error:
        delimiter = ","
    return used_encoding, delimiter
def inspect_csv(path: Path) -> CsvInspection:
    """Читает заголовок CSV и определяет число строк."""
    encoding, delimiter = detect_csv_format(path)
    try:
        preview = pd.read_csv(
            path,
            encoding=encoding,
            sep=delimiter,
            nrows=5,
            dtype=str,
        )
    except Exception as error:
        raise ProcessingError(
            f"Не удалось прочитать CSV-файл: {error}"
        ) from error
    with path.open(
        "r",
        encoding=encoding,
        errors="replace",
        newline="",
    ) as stream:
        row_count = max(
            0,
            sum(1 for _ in stream) - 1,
        )
    return CsvInspection(
        columns=[
            str(column).strip()
            for column in preview.columns
        ],
        row_count=row_count,
        delimiter=delimiter,
        encoding=encoding,
    )
def sha256sum(path: Path) -> str:
    """Вычисляет SHA256 файла."""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(
            lambda: stream.read(1024 * 1024),
            b"",
        ):
            digest.update(block)
    return digest.hexdigest()
def median_spacing(points: np.ndarray) -> float:
    """Вычисляет медианное расстояние до ближайшей точки."""
    if points.ndim != 2 or points.shape[1] != 2:
        raise ProcessingError(
            "Точки должны иметь форму (N, 2)"
        )
    if len(points) < 2:
        raise ProcessingError(
            "Недостаточно точек для оценки "
            "плотности промеров"
        )
    distances, _ = cKDTree(points).query(
        points,
        k=2,
    )
    value = float(np.median(distances[:, 1]))
    if not math.isfinite(value) or value <= 0:
        raise ProcessingError(
            "Невозможно определить расстояние "
            "между промерами"
        )
    return value
def triangle_max_edges(
    points: np.ndarray,
    faces: np.ndarray,
) -> np.ndarray:
    """Возвращает максимальное ребро каждого треугольника."""
    if len(faces) == 0:
        return np.empty(
            0,
            dtype=np.float64,
        )
    result = np.empty(
        len(faces),
        dtype=np.float64,
    )
    for index, face in enumerate(faces):
        triangle = points[face]
        edge_01 = np.linalg.norm(
            triangle[0] - triangle[1]
        )
        edge_12 = np.linalg.norm(
            triangle[1] - triangle[2]
        )
        edge_20 = np.linalg.norm(
            triangle[2] - triangle[0]
        )
        result[index] = max(
            float(edge_01),
            float(edge_12),
            float(edge_20),
        )
    return result
def filter_faces(
    points: np.ndarray,
    faces: np.ndarray,
    max_edge_m: float,
) -> np.ndarray:
    """Оставляет треугольники с допустимой длиной ребер."""
    if not math.isfinite(max_edge_m):
        raise ProcessingError(
            "Максимальное ребро TIN должно быть "
            "конечным числом"
        )
    if max_edge_m <= 0:
        raise ProcessingError(
            "Максимальное ребро TIN должно быть "
            "больше нуля"
        )
    face_max_edges = triangle_max_edges(
        points,
        faces,
    )
    accepted = faces[
        face_max_edges <= max_edge_m
    ]
    if len(accepted) == 0:
        if len(face_max_edges) == 0:
            raise ProcessingError(
                "Триангуляция не содержит треугольников"
            )
        shortest = float(face_max_edges.min())
        longest = float(face_max_edges.max())
        raise ProcessingError(
            "После ограничения максимального ребра "
            "не осталось треугольников. "
            f"Заданный порог: {max_edge_m:.3f} м; "
            f"минимальная максимальная сторона "
            f"треугольника: {shortest:.3f} м; "
            f"максимальная: {longest:.3f} м. "
            "Увеличьте параметр "
            "\"Максимальное ребро TIN\"."
        )
    return np.asarray(
        accepted,
        dtype=np.int64,
    )
def filter_faces_automatic(
    points: np.ndarray,
    faces: np.ndarray,
    spacing: float,
) -> tuple[np.ndarray, float]:
    """
    Фильтрует треугольники в автоматическом режиме.
    Сначала используется порог spacing * 4. Если он удаляет
    все треугольники, порог увеличивается до минимального
    фактического размера треугольника.
    """
    if spacing <= 0:
        raise ProcessingError(
            "Расстояние между промерами должно быть "
            "больше нуля"
        )
    proposed_edge = spacing * 4.0
    try:
        accepted = filter_faces(
            points,
            faces,
            proposed_edge,
        )
        return accepted, proposed_edge
    except ProcessingError:
        pass
    face_max_edges = triangle_max_edges(
        points,
        faces,
    )
    if len(face_max_edges) == 0:
        raise ProcessingError(
            "Триангуляция не содержит треугольников"
        )
    shortest_edge = float(face_max_edges.min())
    fallback_edge = shortest_edge * 1.001
    accepted = filter_faces(
        points,
        faces,
        fallback_edge,
    )
    return accepted, fallback_edge
def normalize_numeric(series: pd.Series) -> pd.Series:
    """Преобразует значения серии в числа."""
    return pd.to_numeric(
        series.astype(str)
        .str.strip()
        .str.replace(
            ",",
            ".",
            regex=False,
        ),
        errors="coerce",
    )
def read_and_prepare(
    config: ProcessingConfig,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Читает CSV, проверяет строки, преобразует координаты
    и агрегирует совпадающие плановые точки.
    """
    encoding, delimiter = detect_csv_format(
        config.input_csv
    )
    try:
        source = pd.read_csv(
            config.input_csv,
            encoding=encoding,
            sep=delimiter,
            dtype=str,
        )
    except Exception as error:
        raise ProcessingError(
            f"Не удалось прочитать исходный CSV: {error}"
        ) from error
    if source.empty:
        raise ProcessingError(
            "Исходный CSV-файл не содержит строк"
        )
    source.columns = [
        str(column).strip()
        for column in source.columns
    ]
    required = [
        config.latitude_field,
        config.longitude_field,
        config.beam_distance_field,
    ]
    missing = [
        field
        for field in required
        if field not in source.columns
    ]
    if missing:
        raise ProcessingError(
            "В CSV отсутствуют поля: "
            + ", ".join(missing)
        )
    quality = source.copy()
    quality["source_row"] = np.arange(
        2,
        len(source) + 2,
        dtype=np.int64,
    )
    quality["latitude_deg"] = normalize_numeric(
        source[config.latitude_field]
    )
    quality["longitude_deg"] = normalize_numeric(
        source[config.longitude_field]
    )
    quality["beam_distance_m"] = normalize_numeric(
        source[config.beam_distance_field]
    )
    quality["depth_m"] = (
        quality["beam_distance_m"]
        + config.water_surface_to_transducer_m
    )
    quality["x_m"] = np.nan
    quality["y_m"] = np.nan
    quality["quality_status"] = "valid"
    quality["quality_reason"] = ""
    invalid_coordinates = (
        quality["latitude_deg"].isna()
        | quality["longitude_deg"].isna()
        | ~quality["latitude_deg"].between(-90, 90)
        | ~quality["longitude_deg"].between(
            -180,
            180,
        )
    )
    invalid_beam = (
        quality["beam_distance_m"].isna()
        | ~np.isfinite(
            quality["beam_distance_m"]
        )
        | (quality["beam_distance_m"] <= 0)
    )
    invalid_depth = (
        quality["depth_m"].isna()
        | ~np.isfinite(quality["depth_m"])
        | (quality["depth_m"] < config.min_depth_m)
        | (quality["depth_m"] > config.max_depth_m)
    )
    quality.loc[
        invalid_coordinates,
        "quality_status",
    ] = "rejected"
    quality.loc[
        invalid_coordinates,
        "quality_reason",
    ] = "Некорректные координаты"
    quality.loc[
        invalid_beam,
        "quality_status",
    ] = "rejected"
    quality.loc[
        invalid_beam,
        "quality_reason",
    ] = "Некорректный Beam distance"
    quality.loc[
        invalid_depth,
        "quality_status",
    ] = "rejected"
    quality.loc[
        invalid_depth,
        "quality_reason",
    ] = "Глубина вне допустимого диапазона"
    try:
        source_crs = CRS.from_user_input(
            config.input_crs
        )
        output_crs = CRS.from_user_input(
            config.output_crs
        )
    except Exception as error:
        raise ProcessingError(
            f"Ошибка системы координат: {error}"
        ) from error
    if not output_crs.is_projected:
        raise ProcessingError(
            "Рабочая система координат должна "
            "быть проекционной"
        )
    valid_rows = quality.loc[
        quality["quality_status"] == "valid"
    ].copy()
    if len(valid_rows) < 3:
        raise ProcessingError(
            "После базовой проверки осталось "
            "меньше трех точек"
        )
    transformer = Transformer.from_crs(
        source_crs,
        output_crs,
        always_xy=True,
    )
    try:
        x, y = transformer.transform(
            valid_rows["longitude_deg"].to_numpy(),
            valid_rows["latitude_deg"].to_numpy(),
        )
    except Exception as error:
        raise ProcessingError(
            "Не удалось преобразовать координаты: "
            f"{error}"
        ) from error
    valid_rows["x_m"] = np.asarray(
        x,
        dtype=np.float64,
    )
    valid_rows["y_m"] = np.asarray(
        y,
        dtype=np.float64,
    )
    finite_xy = (
        np.isfinite(valid_rows["x_m"])
        & np.isfinite(valid_rows["y_m"])
    )
    bad_rows = valid_rows.loc[
        ~finite_xy,
        "source_row",
    ]
    if not bad_rows.empty:
        quality.loc[
            quality["source_row"].isin(
                bad_rows
            ),
            [
                "quality_status",
                "quality_reason",
            ],
        ] = [
            "rejected",
            "Ошибка преобразования координат",
        ]
    valid_rows = valid_rows.loc[
        finite_xy
    ].copy()
    if len(valid_rows) < 3:
        raise ProcessingError(
            "После преобразования координат "
            "осталось меньше трех точек"
        )
    valid_source_rows = valid_rows[
        "source_row"
    ].to_numpy()
    quality.loc[
        quality["source_row"].isin(
            valid_source_rows
        ),
        "x_m",
    ] = valid_rows["x_m"].to_numpy()
    quality.loc[
        quality["source_row"].isin(
            valid_source_rows
        ),
        "y_m",
    ] = valid_rows["y_m"].to_numpy()
    valid_rows["x_round"] = (
        valid_rows["x_m"].round(2)
    )
    valid_rows["y_round"] = (
        valid_rows["y_m"].round(2)
    )
    duplicates = valid_rows.duplicated(
        subset=[
            "x_round",
            "y_round",
        ],
        keep="first",
    )
    duplicate_source_rows = valid_rows.loc[
        duplicates,
        "source_row",
    ]
    if not duplicate_source_rows.empty:
        quality.loc[
            quality["source_row"].isin(
                duplicate_source_rows
            ),
            [
                "quality_status",
                "quality_reason",
            ],
        ] = [
            "suspect",
            (
                "Повторная плановая точка, "
                "включена в медианную агрегацию"
            ),
        ]
    aggregated = (
        valid_rows.groupby(
            [
                "x_round",
                "y_round",
            ],
            as_index=False,
        )
        .agg(
            x_m=("x_m", "median"),
            y_m=("y_m", "median"),
            depth_m=("depth_m", "median"),
            beam_distance_m=(
                "beam_distance_m",
                "median",
            ),
            sample_count=(
                "source_row",
                "count",
            ),
        )
    )
    if len(aggregated) < 3:
        raise ProcessingError(
            "После агрегации совпадающих точек "
            "осталось меньше трех точек"
        )
    return quality, aggregated
def write_xyz(
    points: pd.DataFrame,
    path: Path,
) -> None:
    """Записывает принятые точки в XYZ."""
    points[
        [
            "x_m",
            "y_m",
            "depth_m",
        ]
    ].to_csv(
        path,
        sep=" ",
        index=False,
        header=False,
        float_format="%.4f",
    )
def _las_scale_and_offset(
    values: np.ndarray,
) -> tuple[float, float]:
    """
    Рассчитывает масштаб и offset для одной координаты LAS.
    Координаты LAS хранятся как signed int32. Используется
    запас 10 процентов, чтобы исключить переполнение при
    округлении чисел с плавающей точкой.
    """
    if values.size == 0:
        raise ProcessingError(
            "Нельзя рассчитать параметры LAS "
            "для пустого массива"
        )
    if not np.all(np.isfinite(values)):
        raise ProcessingError(
            "Координаты LAS содержат "
            "нечисловые или бесконечные значения"
        )
    int32_limit = float(
        min(
            np.iinfo(np.int32).max,
            abs(np.iinfo(np.int32).min),
        )
    )
    safe_limit = int32_limit * 0.90
    value_min = float(values.min())
    value_max = float(values.max())
    value_span = value_max - value_min
    # Минимальная точность 1 мм сохраняется для обычных
    # пространственных охватов.
    scale = 0.001
    if value_span > safe_limit * scale:
        scale = value_span / safe_limit
    if not math.isfinite(scale) or scale <= 0:
        raise ProcessingError(
            "Не удалось рассчитать масштаб координаты LAS"
        )
    # Offset равен минимуму координаты. Тогда нижняя точка
    # хранится около нуля, а весь диапазон помещается в int32.
    offset = value_min
    return scale, offset
def write_las(
    points: pd.DataFrame,
    path: Path,
    output_crs: str,
) -> dict[str, list[float]]:
    """
    Записывает точки в LAS 1.2.
    Масштаб и offset рассчитываются отдельно для X, Y и Z.
    Это предотвращает OverflowError при больших координатах,
    большом пространственном охвате или отрицательных Z.
    """
    x_values = points["x_m"].to_numpy(
        dtype=np.float64
    )
    y_values = points["y_m"].to_numpy(
        dtype=np.float64
    )
    depth_values = points["depth_m"].to_numpy(
        dtype=np.float64
    )
    z_values = -depth_values
    for name, values in (
        ("X", x_values),
        ("Y", y_values),
        ("Z", z_values),
    ):
        if values.size == 0:
            raise ProcessingError(
                f"Нет значений координаты {name} "
                "для записи LAS"
            )
        if not np.all(np.isfinite(values)):
            raise ProcessingError(
                f"Координата {name} содержит "
                "нечисловые или бесконечные значения"
            )
    x_scale, x_offset = _las_scale_and_offset(
        x_values
    )
    y_scale, y_offset = _las_scale_and_offset(
        y_values
    )
    z_scale, z_offset = _las_scale_and_offset(
        z_values
    )
    scales = np.array(
        [
            x_scale,
            y_scale,
            z_scale,
        ],
        dtype=np.float64,
    )
    offsets = np.array(
        [
            x_offset,
            y_offset,
            z_offset,
        ],
        dtype=np.float64,
    )
    header = laspy.LasHeader(
        point_format=3,
        version="1.2",
    )
    header.scales = scales
    header.offsets = offsets
    try:
        header.add_crs(
            CRS.from_user_input(output_crs)
        )
    except Exception as error:
        raise ProcessingError(
            f"Не удалось добавить CRS в LAS: {error}"
        ) from error
    header.add_extra_dim(
        laspy.ExtraBytesParams(
            name="depth_m",
            type=np.float32,
        )
    )
    las = laspy.LasData(header)
    try:
        las.x = x_values
        las.y = y_values
        las.z = z_values
        las.depth_m = depth_values.astype(
            np.float32
        )
        beam_values = points[
            "beam_distance_m"
        ].to_numpy(
            dtype=np.float64
        )
        las.intensity = np.clip(
            beam_values * 1000.0,
            0,
            65535,
        ).astype(np.uint16)
        las.write(path)
    except OverflowError as error:
        raise ProcessingError(
            "Координаты не помещаются в диапазон LAS "
            "после автоматического выбора масштаба "
            "и offset"
        ) from error
    except Exception as error:
        raise ProcessingError(
            f"Не удалось записать LAS-файл: {error}"
        ) from error
    return {
        "scales": [
            float(value)
            for value in scales
        ],
        "offsets": [
            float(value)
            for value in offsets
        ],
    }
def write_mesh(
    points: np.ndarray,
    depths: np.ndarray,
    faces: np.ndarray,
    output_dir: Path,
    output_crs: str,
) -> None:
    """Создает OBJ и STL локальной поверхности."""
    if len(points) < 3:
        raise ProcessingError(
            "Недостаточно точек для создания 3D-модели"
        )
    origin_x = float(points[:, 0].min())
    origin_y = float(points[:, 1].min())
    vertices = np.column_stack(
        (
            points[:, 0] - origin_x,
            points[:, 1] - origin_y,
            -depths,
        )
    )
    mesh = trimesh.Trimesh(
        vertices=vertices,
        faces=faces,
        process=False,
    )
    mesh.remove_unreferenced_vertices()
    try:
        mesh.export(
            output_dir / "depth_surface.obj"
        )
        mesh.export(
            output_dir / "depth_surface.stl"
        )
    except Exception as error:
        raise ProcessingError(
            f"Не удалось записать 3D-модель: {error}"
        ) from error
    metadata = {
        "horizontal_crs": output_crs,
        "units": "meters",
        "local_origin_x_m": origin_x,
        "local_origin_y_m": origin_y,
        "model_z_definition": "z = -depth_m",
        "depth_definition": (
            "Positive depth below water surface"
        ),
    }
    (
        output_dir
        / "depth_surface_metadata.json"
    ).write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
def write_tiff_and_pdf(
    points: pd.DataFrame,
    triangulation: Delaunay,
    faces: np.ndarray,
    pixel_size_m: float,
    output_dir: Path,
    output_crs: str,
) -> None:
    """Создает GeoTIFF и PDF-карту."""
    if pixel_size_m <= 0:
        raise ProcessingError(
            "Размер ячейки TIFF должен быть "
            "больше нуля"
        )
    x = points["x_m"].to_numpy(
        dtype=np.float64
    )
    y = points["y_m"].to_numpy(
        dtype=np.float64
    )
    depth = points["depth_m"].to_numpy(
        dtype=np.float64
    )
    west = float(x.min())
    east = float(x.max())
    south = float(y.min())
    north = float(y.max())
    width = max(
        1,
        int(
            math.ceil(
                (east - west) / pixel_size_m
            )
        ),
    )
    height = max(
        1,
        int(
            math.ceil(
                (north - south) / pixel_size_m
            )
        ),
    )
    if width * height > 20_000_000:
        raise ProcessingError(
            "Растр слишком большой. "
            "Увеличьте размер ячейки TIFF"
        )
    grid_x = west + (
        np.arange(width) + 0.5
    ) * pixel_size_m
    grid_y = north - (
        np.arange(height) + 0.5
    ) * pixel_size_m
    mesh_x, mesh_y = np.meshgrid(
        grid_x,
        grid_y,
    )
    grid_points = np.column_stack(
        (
            mesh_x.ravel(),
            mesh_y.ravel(),
        )
    )
    interpolator = LinearNDInterpolator(
        triangulation,
        depth,
        fill_value=np.nan,
    )
    values = np.asarray(
        interpolator(grid_points),
        dtype=np.float64,
    )
    simplex_ids = triangulation.find_simplex(
        grid_points
    )
    allowed_faces = {
        tuple(sorted(face))
        for face in faces.tolist()
    }
    for index, simplex_id in enumerate(
        simplex_ids
    ):
        if simplex_id < 0:
            values[index] = np.nan
            continue
        source_face = tuple(
            sorted(
                triangulation.simplices[
                    simplex_id
                ].tolist()
            )
        )
        if source_face not in allowed_faces:
            values[index] = np.nan
    raster = values.reshape(
        height,
        width,
    )
    transform = from_origin(
        west,
        north,
        pixel_size_m,
        pixel_size_m,
    )
    raster_output = np.where(
        np.isfinite(raster),
        raster,
        -9999.0,
    ).astype(np.float32)
    tiff_path = (
        output_dir / "bathymetry_depth.tiff"
    )
    try:
        with rasterio.open(
            tiff_path,
            "w",
            driver="GTiff",
            height=height,
            width=width,
            count=1,
            dtype="float32",
            crs=CRS.from_user_input(
                output_crs
            ),
            transform=transform,
            nodata=-9999.0,
            compress="deflate",
        ) as dataset:
            dataset.write(
                raster_output,
                1,
            )
            dataset.set_band_description(
                1,
                "Depth below water surface, m",
            )
            dataset.update_tags(
                units="m",
                depth_direction="positive_down",
                interpolation=(
                    "Delaunay triangulation with "
                    "maximum edge filter"
                ),
            )
    except Exception as error:
        raise ProcessingError(
            f"Не удалось записать GeoTIFF: {error}"
        ) from error
    pdf_path = (
        output_dir / "bathymetry_map.pdf"
    )
    figure, axis = plt.subplots(
        figsize=(11.69, 8.27)
    )
    try:
        image = axis.imshow(
            raster,
            extent=(
                west,
                east,
                south,
                north,
            ),
            origin="upper",
            cmap="Blues_r",
            aspect="equal",
        )
        axis.scatter(
            x,
            y,
            s=2,
            c="black",
            alpha=0.35,
            label="Промеры",
        )
        finite_raster = raster[
            np.isfinite(raster)
        ]
        if (
            finite_raster.size > 1
            and float(finite_raster.max())
            > float(finite_raster.min())
        ):
            axis.contour(
                mesh_x,
                mesh_y,
                raster,
                colors="black",
                linewidths=0.35,
            )
        colorbar = figure.colorbar(
            image,
            ax=axis,
            shrink=0.8,
        )
        colorbar.set_label("Глубина, м")
        axis.set_title(
            "Батиметрическая карта"
        )
        axis.set_xlabel("X, м")
        axis.set_ylabel("Y, м")
        axis.legend(
            loc="upper right"
        )
        axis.text(
            0.01,
            0.01,
            (
                "CRS: "
                + output_crs
                + "\n"
                "Глубина положительна вниз "
                "от поверхности воды\n"
                f"Размер ячейки: "
                f"{pixel_size_m:.3f} м"
            ),
            transform=axis.transAxes,
            fontsize=8,
            va="bottom",
            bbox={
                "facecolor": "white",
                "alpha": 0.8,
                "edgecolor": "gray",
            },
        )
        figure.tight_layout()
        figure.savefig(
            pdf_path,
            dpi=200,
        )
    except Exception as error:
        raise ProcessingError(
            f"Не удалось создать PDF-карту: {error}"
        ) from error
    finally:
        plt.close(figure)
def run_pipeline(
    config: ProcessingConfig,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Запускает полный конвейер обработки."""
    def notify(message: str) -> None:
        if progress is not None:
            progress(message)
    if config.water_surface_to_transducer_m < 0:
        raise ProcessingError(
            "Поправка до трансдьюсера "
            "не может быть отрицательной"
        )
    if config.min_depth_m >= config.max_depth_m:
        raise ProcessingError(
            "Минимальная глубина должна быть "
            "меньше максимальной"
        )
    if (
        config.pixel_size_m is not None
        and config.pixel_size_m <= 0
    ):
        raise ProcessingError(
            "Размер ячейки TIFF должен быть "
            "больше нуля"
        )
    if (
        config.max_triangle_edge_m is not None
        and config.max_triangle_edge_m <= 0
    ):
        raise ProcessingError(
            "Максимальное ребро TIN должно быть "
            "больше нуля"
        )
    output_dir = config.output_dir
    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )
    notify("Чтение и проверка CSV")
    quality, points = read_and_prepare(
        config
    )
    shutil.copy2(
        config.input_csv,
        output_dir / "source.csv",
    )
    quality.to_csv(
        output_dir / "quality_points.csv",
        index=False,
        encoding="utf-8-sig",
    )
    points.to_csv(
        output_dir / "accepted_points.csv",
        index=False,
        encoding="utf-8-sig",
    )
    xy = points[
        [
            "x_m",
            "y_m",
        ]
    ].to_numpy(
        dtype=np.float64
    )
    depths = points[
        "depth_m"
    ].to_numpy(
        dtype=np.float64
    )
    spacing = median_spacing(xy)
    notify("Построение триангуляции")
    try:
        triangulation = Delaunay(xy)
    except QhullError as error:
        raise ProcessingError(
            "Не удалось построить поверхность. "
            "Точки могут лежать на одной линии"
        ) from error
    if config.max_triangle_edge_m is None:
        faces, max_edge = filter_faces_automatic(
            xy,
            triangulation.simplices,
            spacing,
        )
        edge_mode = "automatic"
    else:
        max_edge = config.max_triangle_edge_m
        faces = filter_faces(
            xy,
            triangulation.simplices,
            max_edge,
        )
        edge_mode = "manual"
    if config.pixel_size_m is None:
        pixel_size = max(
            spacing / 2,
            0.05,
        )
    else:
        pixel_size = config.pixel_size_m
    notify(
        "Триангуляция: "
        f"{len(triangulation.simplices)} "
        "треугольников; принято после фильтра: "
        f"{len(faces)}; максимальное ребро: "
        f"{max_edge:.3f} м; режим: {edge_mode}"
    )
    notify("Создание XYZ и LAS")
    write_xyz(
        points,
        output_dir / "bottom_points.xyz",
    )
    las_info = write_las(
        points,
        output_dir / "bottom_points.las",
        config.output_crs,
    )
    notify("Создание OBJ и STL")
    write_mesh(
        xy,
        depths,
        faces,
        output_dir,
        config.output_crs,
    )
    notify("Создание GeoTIFF и PDF")
    write_tiff_and_pdf(
        points,
        triangulation,
        faces,
        pixel_size,
        output_dir,
        config.output_crs,
    )
    report = {
        "source_file": config.input_csv.name,
        "source_sha256": sha256sum(
            config.input_csv
        ),
        "source_rows": int(len(quality)),
        "accepted_aggregated_points": int(
            len(points)
        ),
        "rejected_rows": int(
            (
                quality["quality_status"]
                == "rejected"
            ).sum()
        ),
        "suspect_rows": int(
            (
                quality["quality_status"]
                == "suspect"
            ).sum()
        ),
        "gnss_transducer_offset_applied_in_source": (
            config.source_gnss_transducer_offset_applied
        ),
        "gnss_transducer_offset_applied_by_pipeline": (
            False
        ),
        "water_surface_to_transducer_m": (
            config.water_surface_to_transducer_m
        ),
        "formula": (
            "depth_m = beam_distance_m "
            "+ water_surface_to_transducer_m"
        ),
        "input_crs": config.input_crs,
        "output_crs": config.output_crs,
        "median_spacing_m": spacing,
        "pixel_size_m": pixel_size,
        "max_triangle_edge_m": max_edge,
        "max_triangle_edge_mode": edge_mode,
        "triangles_before_filter": int(
            len(triangulation.simplices)
        ),
        "triangles_after_filter": int(
            len(faces)
        ),
        "minimum_depth_m": float(
            points["depth_m"].min()
        ),
        "maximum_depth_m": float(
            points["depth_m"].max()
        ),
        "las_scales": las_info["scales"],
        "las_offsets": las_info["offsets"],
        "las_z_definition": (
            "z = -depth_m; positive depth is "
            "stored in extra field depth_m"
        ),
    }
    (
        output_dir / "processing_report.json"
    ).write_text(
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    processing_config = {
        "latitude_field": (
            config.latitude_field
        ),
        "longitude_field": (
            config.longitude_field
        ),
        "beam_distance_field": (
            config.beam_distance_field
        ),
        "input_crs": config.input_crs,
        "output_crs": config.output_crs,
        "source_gnss_transducer_offset_applied": (
            config.source_gnss_transducer_offset_applied
        ),
        "water_surface_to_transducer_m": (
            config.water_surface_to_transducer_m
        ),
        "pixel_size_m": config.pixel_size_m,
        "max_triangle_edge_m": (
            config.max_triangle_edge_m
        ),
        "min_depth_m": config.min_depth_m,
        "max_depth_m": config.max_depth_m,
    }
    (
        output_dir / "processing_config.json"
    ).write_text(
        json.dumps(
            processing_config,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    notify("Создание ZIP-архива")
    archive = shutil.make_archive(
        str(
            output_dir.parent
            / f"{output_dir.name}_results"
        ),
        "zip",
        root_dir=output_dir,
    )
    report["archive"] = archive
    notify("Обработка завершена")
    return report