from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


class UserBase(BaseModel):
    name: str
    email: EmailStr


class UserCreate(UserBase):
    pass


class User(UserBase):
    id: int

    class Config:
        orm_mode = True


class NotificationCreate(BaseModel):
    title: str
    body: str


class NotificationPatch(BaseModel):
    read: Optional[bool] = None


class NotificationResponse(BaseModel):
    id: int
    title: str
    body: str
    read: bool
    created_at: datetime

    class Config:
        orm_mode = True


class UnreadCountResponse(BaseModel):
    count: int
