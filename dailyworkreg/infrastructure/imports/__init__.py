"""Source-file readers and per-dataset judgement for the staged import workflow."""

from .datasets import (
    DATASETS,
    EXCLUDE,
    GENERAL_HOLIDAY_SPEC,
    INCLUDE,
    SEVERITY_ERROR,
    SEVERITY_OK,
    SEVERITY_WARNING,
    DatasetSpec,
    DatasetVerdict,
    FieldSpec,
    RowOutcome,
    judge_dataset,
)
from .tabular import (
    SUPPORTED_SUFFIXES,
    SourceFormatError,
    TabularSource,
    read_source_bytes,
    read_source_file,
)

__all__ = [
    "DATASETS",
    "EXCLUDE",
    "GENERAL_HOLIDAY_SPEC",
    "INCLUDE",
    "SEVERITY_ERROR",
    "SEVERITY_OK",
    "SEVERITY_WARNING",
    "SUPPORTED_SUFFIXES",
    "DatasetSpec",
    "DatasetVerdict",
    "FieldSpec",
    "RowOutcome",
    "SourceFormatError",
    "TabularSource",
    "judge_dataset",
    "read_source_bytes",
    "read_source_file",
]
