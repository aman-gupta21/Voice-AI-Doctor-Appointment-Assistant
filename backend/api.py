from __future__ import annotations

import os
import hmac
from contextlib import asynccontextmanager
from datetime import date

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

import store

load_dotenv()


@asynccontextmanager
async def lifespan(_: FastAPI):
    store.initialize()
    yield


app = FastAPI(title="CareLine API", version="1.0.0", lifespan=lifespan)
origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                   allow_methods=["GET", "POST", "DELETE"], allow_headers=["*"])


class BookingRequest(BaseModel):
    phone: str = Field(min_length=10, max_length=30)
    doctor_id: str
    slot_id: str


class CancellationRequest(BaseModel):
    phone: str = Field(min_length=10, max_length=30)


class AvailabilityRequest(BaseModel):
    doctor_or_specialty: str = ""
    day: str = ""


class InternalBookingRequest(BookingRequest):
    pass


class InternalCancellationRequest(CancellationRequest):
    appointment_id: str = ""


def normalize_phone(raw: str) -> str:
    value = "".join(ch for ch in raw if ch.isdigit())
    if not 10 <= len(value) <= 15:
        raise HTTPException(422, "Phone number must contain 10 to 15 digits.")
    return value


def authorize_internal(x_internal_key: str = Header(default="")):
    expected = os.getenv("INTERNAL_API_KEY", "")
    if not expected or not hmac.compare_digest(x_internal_key, expected):
        raise HTTPException(403, "Internal service authentication failed.")


@app.api_route("/", methods=["GET", "HEAD"])
def root():
    return {"status": "ok", "service": "careline-api", "health": "/health", "docs": "/docs"}


@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
def favicon():
    return Response(status_code=204)


@app.get("/health")
def health():
    return {"status": "ok", "service": "careline-api"}


@app.get("/api/doctors")
def doctors():
    return store.list_doctors()


@app.get("/api/slots")
def slots(doctor: str = "", day: str = ""):
    if day:
        try:
            date.fromisoformat(day)
        except ValueError:
            raise HTTPException(422, "Use YYYY-MM-DD for day.")
    return store.list_slots(doctor, day)


@app.get("/api/appointments")
def appointments():
    # In this demonstration, phone numbers are not exposed in the dashboard feed.
    return [{k: v for k, v in a.items() if k != "phone"} for a in store.list_appointments()]


@app.post("/internal/availability")
def internal_availability(payload: AvailabilityRequest, _: None = Depends(authorize_internal)):
    if payload.day:
        try:
            date.fromisoformat(payload.day)
        except ValueError:
            raise HTTPException(422, "Use YYYY-MM-DD for day.")
    return store.list_slots(payload.doctor_or_specialty, payload.day)


@app.post("/internal/appointments/book", status_code=201)
def internal_book(payload: InternalBookingRequest, _: None = Depends(authorize_internal)):
    phone = normalize_phone(payload.phone)
    result = store.book(phone, payload.doctor_id, payload.slot_id)
    if not result["ok"]:
        raise HTTPException(409, result["message"])
    return {k: v for k, v in result.items() if k != "phone"}


@app.post("/internal/appointments/cancel")
def internal_cancel(payload: InternalCancellationRequest, _: None = Depends(authorize_internal)):
    phone = normalize_phone(payload.phone)
    result = store.cancel(phone, payload.appointment_id)
    if not result["ok"]:
        raise HTTPException(404, "No appointment for that number matches this booking.")
    return result


@app.post("/internal/appointments/find")
def internal_find(payload: CancellationRequest, _: None = Depends(authorize_internal)):
    phone = normalize_phone(payload.phone)
    return [a for a in store.list_appointments() if a["phone"] == phone]


@app.delete("/api/appointments/{appointment_id}")
def delete_appointment(appointment_id: str, payload: CancellationRequest):
    phone = normalize_phone(payload.phone)
    result = store.cancel(phone, appointment_id)
    if not result["ok"]:
        raise HTTPException(404, "No appointment for that number matches this booking.")
    return {"ok": True}

