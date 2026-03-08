from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class AnalystCreate(BaseModel):
    username: str
    display_name: Optional[str] = None
    password: str
    role: str = "analyst"


class AnalystRead(BaseModel):
    analyst_id: int
    username: str
    display_name: Optional[str] = None
    role: str
    is_active: bool
    created_at: Optional[datetime] = None
    last_login_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


class AnalystLoginRequest(BaseModel):
    password: str
    username: Optional[str] = None


class AnalystLoginResponse(BaseModel):
    token: str
    analyst: AnalystRead
