from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ExposureItem(BaseModel):
    name: str | None
    percentage: Decimal | None


class ExtractedFactsheet(BaseModel):
    """
    Schema for the data extracted from a factsheet PDF, typically by Bedrock.
    """

    isin: str | None = "UNKNOWN"
    name: str | None = "UNKNOWN"
    documentDate: str | None = "UNKNOWN"
    marketExposure: list[ExposureItem] = Field(default_factory=list)
    topHoldings: list[ExposureItem] = Field(default_factory=list)
    industryExposure: list[ExposureItem] = Field(default_factory=list)


class JobRecord(BaseModel):
    """
    Schema representing a DynamoDB record for a factsheet processing job.
    """

    model_config = ConfigDict(populate_by_name=True)

    userId: str
    jobId: str
    status: JobStatus
    weighting: Decimal = Field(default=Decimal("0.0"))

    # Factsheet fields populated upon completion
    isin: str | None = None
    name: str | None = None
    documentDate: str | None = None
    marketExposure: list[ExposureItem] | None = None
    topHoldings: list[ExposureItem] | None = None
    industryExposure: list[ExposureItem] | None = None
