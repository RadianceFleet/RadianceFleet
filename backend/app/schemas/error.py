"""Standard error response schema."""
from __future__ import annotations

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    detail: str
    code: str = "error"
