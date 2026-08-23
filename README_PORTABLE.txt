Bathymetry MVP Portable
=======================
Приложение предназначено для обработки батиметрических CSV-файлов
и построения набора выходных геоданных.
Сборка
------
Для сборки требуется Windows x64 и Python 3.11.
Запустите файл:
    build_portable.bat
Скрипт автоматически:
1. Создает виртуальное окружение .venv-build.
2. Устанавливает зависимости приложения и сборки.
3. Проверяет синтаксис Python-файлов.
4. Запускает тесты.
5. Собирает приложение через PyInstaller.
6. Копирует sample_data и этот README в portable-каталог.
7. Создает ZIP-архив.
8. Создает SHA256-файл архива.
После успешной сборки будут созданы:
    dist\BathymetryMVP
    portable_release\BathymetryMVP_Portable.zip
    portable_release\BathymetryMVP_Portable.zip.sha256.txt
Запуск
------
Для запуска приложения без Python откройте:
    dist\BathymetryMVP\BathymetryMVP.exe
Для проверки на другом компьютере можно скопировать весь каталог
dist\BathymetryMVP или распаковать ZIP-архив.
Входной CSV
-----------
CSV должен содержать поля:
- Latitude
- Longitude
- Beam distance
Названия полей можно выбрать вручную в интерфейсе.
Поддерживаются разделители:
- запятая
- точка с запятой
- табуляция
- вертикальная черта
Поддерживаются кодировки:
- UTF-8 with BOM
- UTF-8
- CP1251
- Latin-1
Результаты
----------
В каталоге результатов создаются:
- source.csv
- quality_points.csv
- accepted_points.csv
- bottom_points.xyz
- bottom_points.las
- depth_surface.obj
- depth_surface.stl
- depth_surface_metadata.json
- bathymetry_depth.tiff
- bathymetry_map.pdf
- processing_report.json
- processing_config.json
- ZIP-архив результатов
Глубина рассчитывается по формуле:
depth_m = beam_distance_m + water_surface_to_transducer_m
В LAS-файле:
- координаты X и Y записаны в метрах;
- координата Z равна -depth_m;
- положительная глубина также записана в дополнительном поле depth_m.
Проверка результатов
--------------------
GeoTIFF рекомендуется открыть в QGIS.
LAS и трехмерные модели OBJ/STL можно проверить в CloudCompare.
Если приложение сообщает об отсутствии proj.db, gdal.dll или библиотек
rasterio, сначала проверьте запуск из каталога:
    dist\BathymetryMVP
Если ошибка сохраняется, для конкретной версии PyInstaller может
потребоваться отдельный runtime hook для переменных PROJ_DATA и GDAL_DATA.