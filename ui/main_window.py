from __future__ import annotations
import traceback
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)
from bathymetry.models import ProcessingConfig
from bathymetry.processor import inspect_csv, run_pipeline
class PipelineWorker(QObject):
    progress = Signal(str)
    completed = Signal(dict)
    failed = Signal(str)
    def __init__(self, config: ProcessingConfig) -> None:
        super().__init__()
        self.config = config
    def run(self) -> None:
        try:
            result = run_pipeline(
                self.config,
                self.progress.emit,
            )
            self.completed.emit(result)
        except Exception:
            self.failed.emit(traceback.format_exc())
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Bathymetry MVP")
        self.resize(920, 720)
        self.input_path: Path | None = None
        self.thread: QThread | None = None
        self.worker: PipelineWorker | None = None
        self.build_ui()
    def build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        input_group = QGroupBox("Входной CSV")
        input_layout = QGridLayout(input_group)
        self.input_edit = QLineEdit()
        self.input_edit.setReadOnly(True)
        choose_button = QPushButton("Выбрать CSV")
        choose_button.clicked.connect(self.choose_csv)
        input_layout.addWidget(QLabel("Файл:"), 0, 0)
        input_layout.addWidget(self.input_edit, 0, 1)
        input_layout.addWidget(choose_button, 0, 2)
        self.csv_info = QLabel("Файл не выбран")
        input_layout.addWidget(
            self.csv_info,
            1,
            0,
            1,
            3,
        )
        layout.addWidget(input_group)
        fields_group = QGroupBox("Поля CSV")
        fields_layout = QFormLayout(fields_group)
        self.latitude_combo = QComboBox()
        self.longitude_combo = QComboBox()
        self.beam_combo = QComboBox()
        fields_layout.addRow(
            "Широта:",
            self.latitude_combo,
        )
        fields_layout.addRow(
            "Долгота:",
            self.longitude_combo,
        )
        fields_layout.addRow(
            "Beam distance:",
            self.beam_combo,
        )
        layout.addWidget(fields_group)
        parameters_group = QGroupBox("Параметры обработки")
        parameters_layout = QFormLayout(parameters_group)
        self.input_crs_edit = QLineEdit("EPSG:4326")
        self.output_crs_edit = QLineEdit("EPSG:32637")
        self.offset_checkbox = QCheckBox(
            "Офсет GNSS-трансдьюсер уже учтен "
            "в исходном CSV"
        )
        self.offset_checkbox.setChecked(True)
        self.water_surface_spin = QDoubleSpinBox()
        self.water_surface_spin.setDecimals(3)
        self.water_surface_spin.setRange(0.0, 10.0)
        self.water_surface_spin.setValue(0.15)
        self.water_surface_spin.setSuffix(" м")
        self.pixel_spin = QDoubleSpinBox()
        self.pixel_spin.setDecimals(3)
        self.pixel_spin.setRange(0.0, 1000.0)
        self.pixel_spin.setValue(0.0)
        self.pixel_spin.setSpecialValueText("Автоматически")
        self.pixel_spin.setSuffix(" м")
        self.edge_spin = QDoubleSpinBox()
        self.edge_spin.setDecimals(3)
        self.edge_spin.setRange(0.0, 10000.0)
        self.edge_spin.setValue(0.0)
        self.edge_spin.setSpecialValueText("Автоматически")
        self.edge_spin.setSuffix(" м")
        self.min_depth_spin = QDoubleSpinBox()
        self.min_depth_spin.setDecimals(3)
        self.min_depth_spin.setRange(0.001, 1000.0)
        self.min_depth_spin.setValue(0.05)
        self.min_depth_spin.setSuffix(" м")
        self.max_depth_spin = QDoubleSpinBox()
        self.max_depth_spin.setDecimals(3)
        self.max_depth_spin.setRange(0.01, 10000.0)
        self.max_depth_spin.setValue(100.0)
        self.max_depth_spin.setSuffix(" м")
        parameters_layout.addRow(
            "Исходная CRS:",
            self.input_crs_edit,
        )
        parameters_layout.addRow(
            "Рабочая CRS:",
            self.output_crs_edit,
        )
        parameters_layout.addRow(
            "",
            self.offset_checkbox,
        )
        parameters_layout.addRow(
            "Поправка от поверхности воды "
            "до трансдьюсера:",
            self.water_surface_spin,
        )
        parameters_layout.addRow(
            "Минимальная глубина:",
            self.min_depth_spin,
        )
        parameters_layout.addRow(
            "Максимальная глубина:",
            self.max_depth_spin,
        )
        parameters_layout.addRow(
            "Размер ячейки TIFF:",
            self.pixel_spin,
        )
        parameters_layout.addRow(
            "Максимальное ребро TIN:",
            self.edge_spin,
        )
        layout.addWidget(parameters_group)
        output_layout = QHBoxLayout()
        self.output_edit = QLineEdit(
            str(Path.cwd() / "results")
        )
        output_button = QPushButton("Выбрать папку")
        output_button.clicked.connect(
            self.choose_output_dir
        )
        output_layout.addWidget(
            QLabel("Папка результатов:")
        )
        output_layout.addWidget(self.output_edit)
        output_layout.addWidget(output_button)
        layout.addLayout(output_layout)
        self.run_button = QPushButton(
            "Запустить обработку"
        )
        self.run_button.clicked.connect(
            self.start_processing
        )
        layout.addWidget(self.run_button)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, 1)
    def append_log(self, text: str) -> None:
        self.log.appendPlainText(text)
    def choose_csv(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Выбор CSV",
            str(Path.home()),
            "CSV (*.csv);;Все файлы (*)",
        )
        if not file_name:
            return
        try:
            self.input_path = Path(file_name)
            inspection = inspect_csv(self.input_path)
            self.input_edit.setText(
                str(self.input_path)
            )
            self.csv_info.setText(
                f"Строк: {inspection.row_count}; "
                f"разделитель: {inspection.delimiter!r}; "
                f"кодировка: {inspection.encoding}"
            )
            for combo in (
                self.latitude_combo,
                self.longitude_combo,
                self.beam_combo,
            ):
                combo.clear()
                combo.addItems(inspection.columns)
            self.select_column(
                self.latitude_combo,
                ["Latitude", "latitude", "lat"],
            )
            self.select_column(
                self.longitude_combo,
                [
                    "Longitude",
                    "longitude",
                    "lon",
                ],
            )
            self.select_column(
                self.beam_combo,
                [
                    "Beam distance",
                    "beam_distance",
                    "depth",
                ],
            )
            self.append_log(
                "CSV успешно прочитан: "
                + self.input_path.name
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Ошибка CSV",
                str(error),
            )
    @staticmethod
    def select_column(
        combo: QComboBox,
        names: list[str],
    ) -> None:
        normalized = {
            name.strip().lower()
            for name in names
        }
        for index in range(combo.count()):
            item = combo.itemText(index)
            if item.strip().lower() in normalized:
                combo.setCurrentIndex(index)
                return
    def choose_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self,
            "Выбор папки результатов",
            self.output_edit.text(),
        )
        if directory:
            self.output_edit.setText(directory)
    def start_processing(self) -> None:
        if self.input_path is None:
            QMessageBox.warning(
                self,
                "Нет файла",
                "Сначала выберите входной CSV",
            )
            return
        if (
            not self.latitude_combo.currentText()
            or not self.longitude_combo.currentText()
            or not self.beam_combo.currentText()
        ):
            QMessageBox.warning(
                self,
                "Нет полей",
                "Выберите поля координат и "
                "Beam distance",
            )
            return
        if (
            self.min_depth_spin.value()
            >= self.max_depth_spin.value()
        ):
            QMessageBox.warning(
                self,
                "Ошибка параметров",
                "Минимальная глубина должна быть "
                "меньше максимальной",
            )
            return
        try:
            base_output = Path(
                self.output_edit.text()
            ).expanduser()
            job_name = datetime.now().strftime(
                "bathymetry_%Y%m%d_%H%M%S"
            )
            config = ProcessingConfig(
                input_csv=self.input_path,
                output_dir=base_output / job_name,
                latitude_field=(
                    self.latitude_combo.currentText()
                ),
                longitude_field=(
                    self.longitude_combo.currentText()
                ),
                beam_distance_field=(
                    self.beam_combo.currentText()
                ),
                input_crs=(
                    self.input_crs_edit.text().strip()
                ),
                output_crs=(
                    self.output_crs_edit.text().strip()
                ),
                source_gnss_transducer_offset_applied=(
                    self.offset_checkbox.isChecked()
                ),
                water_surface_to_transducer_m=float(
                    self.water_surface_spin.value()
                ),
                pixel_size_m=(
                    self.pixel_spin.value() or None
                ),
                max_triangle_edge_m=(
                    self.edge_spin.value() or None
                ),
                min_depth_m=float(
                    self.min_depth_spin.value()
                ),
                max_depth_m=float(
                    self.max_depth_spin.value()
                ),
            )
        except Exception as error:
            QMessageBox.critical(
                self,
                "Ошибка параметров",
                str(error),
            )
            return
        self.run_button.setEnabled(False)
        self.progress.setRange(0, 0)
        self.append_log("Запуск обработки")
        self.thread = QThread(self)
        self.worker = PipelineWorker(config)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(
            self.worker.run
        )
        self.worker.progress.connect(
            self.append_log
        )
        self.worker.completed.connect(
            self.processing_completed
        )
        self.worker.failed.connect(
            self.processing_failed
        )
        self.worker.completed.connect(
            self.thread.quit
        )
        self.worker.failed.connect(
            self.thread.quit
        )
        self.thread.finished.connect(
            self.worker.deleteLater
        )
        self.thread.finished.connect(
            self.thread.deleteLater
        )
        self.thread.start()
    def processing_completed(
        self,
        result: dict,
    ) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        self.run_button.setEnabled(True)
        self.append_log(
            "Готово. Принято точек: "
            f"{result['accepted_aggregated_points']}"
        )
        self.append_log(
            "Архив: " + result["archive"]
        )
        QMessageBox.information(
            self,
            "Обработка завершена",
            "Результаты созданы.\n\n"
            "Принято агрегированных точек: "
            f"{result['accepted_aggregated_points']}\n"
            "Минимальная глубина: "
            f"{result['minimum_depth_m']:.3f} м\n"
            "Максимальная глубина: "
            f"{result['maximum_depth_m']:.3f} м",
        )
    def processing_failed(
        self,
        details: str,
    ) -> None:
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.run_button.setEnabled(True)
        self.append_log(
            "Ошибка обработки:\n" + details
        )
        lines = [
            line
            for line in details.splitlines()
            if line.strip()
        ]
        QMessageBox.critical(
            self,
            "Ошибка обработки",
            lines[-1]
            if lines
            else "Неизвестная ошибка",
        )