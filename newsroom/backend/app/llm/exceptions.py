from __future__ import annotations


class SchemaViolation(ValueError):
    def __init__(self, message: str, *, raw_response: str, retry_count: int) -> None:
        super().__init__(message)
        self.raw_response = raw_response
        self.retry_count = retry_count
