from datetime import datetime

from pydantic import BaseModel


class AnalystCreate(BaseModel):
    username: str
    display_name: str | None = None
    password: str
    role: str = "analyst"


class AnalystRead(BaseModel):
    analyst_id: int
    username: str
    display_name: str | None = None
    role: str
    is_active: bool
    created_at: datetime | None = None
    last_login_at: datetime | None = None
    model_config = {"from_attributes": True}


class AnalystLoginRequest(BaseModel):
    password: str
    username: str | None = None


class AnalystLoginResponse(BaseModel):
    token: str
    analyst: AnalystRead
