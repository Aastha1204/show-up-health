from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, Field, field_validator

from .phone import normalize_phone

Status = Literal["pending", "confirmed", "rescheduled", "cancelled", "arrived", "no_show"]
Language = Literal["en", "hi", "mr"]
ConsentSource = Literal["website_form", "reception", "import", "whatsapp"]


class PatientIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str
    language: Language = "en"
    consent_source: ConsentSource  # required: no consent source, no patient

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalize_phone(v)

    @field_validator("name")
    @classmethod
    def _name(cls, v: str) -> str:
        return v.strip()


class AppointmentCreate(BaseModel):
    patient_id: UUID | None = None
    new_patient: PatientIn | None = None
    doctor_id: UUID
    treatment_type_id: UUID
    start_at: AwareDatetime
    internal_note: str | None = None
    source: Literal["dashboard", "website", "import", "waitlist"] = "dashboard"


class AppointmentUpdate(BaseModel):
    doctor_id: UUID | None = None
    treatment_type_id: UUID | None = None
    start_at: AwareDatetime | None = None
    internal_note: str | None = None


class StatusChange(BaseModel):
    status: Status
    actor: Literal["reception", "system"] = "reception"
    via: Literal["phone"] | None = None     # e.g. "Called - confirmed": recorded in the status history
