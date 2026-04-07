from pydantic import BaseModel, ConfigDict, Field
from typing import List, Optional
from decimal import Decimal
from enum import Enum


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class ExposureItem(BaseModel):
    name: Optional[str]
    percentage: Optional[Decimal]


class ExtractedFactsheet(BaseModel):
    """
    Schema for the data extracted from a factsheet PDF, typically by Bedrock.
    """

    isin: Optional[str] = "UNKNOWN"
    name: Optional[str] = "UNKNOWN"
    documentDate: Optional[str] = "UNKNOWN"
    marketExposure: List[ExposureItem] = Field(default_factory=list)
    topHoldings: List[ExposureItem] = Field(default_factory=list)
    industryExposure: List[ExposureItem] = Field(default_factory=list)


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
    isin: Optional[str] = None
    name: Optional[str] = None
    documentDate: Optional[str] = None
    marketExposure: Optional[List[ExposureItem]] = None
    topHoldings: Optional[List[ExposureItem]] = None
    industryExposure: Optional[List[ExposureItem]] = None
