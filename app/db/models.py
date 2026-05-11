from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.database import Base


class ReportPackage(Base):
    __tablename__ = "report_packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="created", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    files: Mapped[list["ReportFile"]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
    )
    sheets: Mapped[list["ParsedSheet"]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
    )
    raw_rows: Mapped[list["RawExtractedRow"]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
    )
    raw_metrics: Mapped[list["RawMetric"]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
    )
    daily_chain_metrics: Mapped[list["DailyChainMetric"]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
    )
    product_wagon_metrics: Mapped[list["ProductWagonMetric"]] = relationship(
        back_populates="package",
        cascade="all, delete-orphan",
    )


class ReportFile(Base):
    __tablename__ = "report_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("report_packages.id"), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_type: Mapped[str] = mapped_column(String(50), nullable=False)
    detected_report_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), default="uploaded", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    package: Mapped[ReportPackage] = relationship(back_populates="files")
    raw_rows: Mapped[list["RawExtractedRow"]] = relationship(back_populates="file")
    raw_metrics: Mapped[list["RawMetric"]] = relationship(back_populates="file")


class ParsedSheet(Base):
    __tablename__ = "parsed_sheets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("report_packages.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    file_id: Mapped[int | None] = mapped_column(ForeignKey("report_files.id"), nullable=True)
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    rows_count: Mapped[int] = mapped_column(Integer, nullable=False)
    columns_count: Mapped[int] = mapped_column(Integer, nullable=False)

    package: Mapped[ReportPackage] = relationship(back_populates="sheets")
    file: Mapped[ReportFile | None] = relationship()


class RawExtractedRow(Base):
    __tablename__ = "raw_extracted_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("report_packages.id"), nullable=False)
    file_id: Mapped[int] = mapped_column(ForeignKey("report_files.id"), nullable=False)
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    row_text: Mapped[str] = mapped_column(Text, nullable=False)
    matched_keywords: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    package: Mapped[ReportPackage] = relationship(back_populates="raw_rows")
    file: Mapped[ReportFile] = relationship(back_populates="raw_rows")


class RawMetric(Base):
    __tablename__ = "raw_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("report_packages.id"), nullable=False)
    file_id: Mapped[int] = mapped_column(ForeignKey("report_files.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sheet_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    row_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metric_key: Mapped[str] = mapped_column(String(120), nullable=False)
    metric_label: Mapped[str] = mapped_column(String(255), nullable=False)
    metric_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    product: Mapped[str | None] = mapped_column(String(120), nullable=True)
    shift_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    package: Mapped[ReportPackage] = relationship(back_populates="raw_metrics")
    file: Mapped[ReportFile] = relationship(back_populates="raw_metrics")


class DailyChainMetric(Base):
    __tablename__ = "daily_chain_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("report_packages.id"), nullable=False)
    report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    shift_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    wagons_in_surgut: Mapped[int | None] = mapped_column(Integer, nullable=True)
    arrived_prom: Mapped[int | None] = mapped_column(Integer, nullable=True)
    processed_prom: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loaded_wagons: Mapped[int | None] = mapped_column(Integer, nullable=True)
    documented_wagons: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_surgut: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    package: Mapped[ReportPackage] = relationship(back_populates="daily_chain_metrics")


class ProductWagonMetric(Base):
    __tablename__ = "product_wagon_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("report_packages.id"), nullable=False)
    report_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    shift_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    product: Mapped[str] = mapped_column(String(120), nullable=False)
    wagon_group: Mapped[str] = mapped_column(String(50), nullable=False)
    planned_loading_wagons: Mapped[int | None] = mapped_column(Integer, nullable=True)
    planned_loading_tons: Mapped[float | None] = mapped_column(Float, nullable=True)
    product_stock_tons: Mapped[float | None] = mapped_column(Float, nullable=True)
    available_wagons_total: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available_wagons_good: Mapped[int | None] = mapped_column(Integer, nullable=True)
    available_wagons_bad: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loaded_wagons: Mapped[int | None] = mapped_column(Integer, nullable=True)
    loaded_tons: Mapped[float | None] = mapped_column(Float, nullable=True)
    documented_wagons: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sent_wagons: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wagon_balance: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wagon_coverage_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    main_limitation: Mapped[str] = mapped_column(String(120), default="нет данных", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    package: Mapped[ReportPackage] = relationship(back_populates="product_wagon_metrics")
