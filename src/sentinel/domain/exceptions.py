"""Domain-level errors for dataset operations."""


class SentinelError(Exception):
    """Base class for expected Sentinel failures."""


class ValidationError(SentinelError):
    """An input does not satisfy the dataset contract."""


class UnsupportedFormatError(ValidationError):
    """The source extension is not supported."""


class DatasetNotFoundError(SentinelError):
    """A requested dataset or version does not exist."""


class StorageError(SentinelError):
    """Managed snapshot storage failed."""


class PersistenceError(SentinelError):
    """Metadata persistence failed."""
