"""Pipecat WebRTC voice pipeline: ElevenLabs STT -> Gemini -> ElevenLabs TTS."""
from __future__ import annotations

import os
import re

import httpx
from dotenv import load_dotenv
from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair, LLMUserAggregatorParams
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import TransportParams
from pipecat.workers.runner import WorkerRunner

load_dotenv()
APPOINTMENTS_API = os.getenv("APPOINTMENTS_API_URL", "http://localhost:8000").rstrip("/")


async def appointment_api(path: str, payload: dict):
    key = os.getenv("INTERNAL_API_KEY", "")
    if not key:
        raise RuntimeError("Set INTERNAL_API_KEY in backend/.env")
    async with httpx.AsyncClient(timeout=8) as client:
        response = await client.post(f"{APPOINTMENTS_API}{path}", json=payload,
                                     headers={"X-Internal-Key": key})
        response.raise_for_status()
        return response.json()

SYSTEM_PROMPT = """You are CareLine, a warm, concise clinic receptionist. Help with appointment availability, booking, and cancellation only. Never diagnose, recommend treatment, or promise a doctor is available without using a tool.

Identify the caller only with the phone number they speak. Ask for the number before any booking or cancellation, read the full number back slowly, and wait for a clear yes before calling confirm_caller_phone. If they say no or correct a digit, repeat the corrected number and wait again. Never ask for an account, login, OTP, or caller ID.

After number confirmation, use that session's confirmed number for any booking or cancellation. For a booking, check slots first, ask which exact time they want, and call book_appointment only for that selected slot. For cancellation, call find_my_appointments, read the doctor and appointment time back, and wait for a clear yes before cancel_appointment. If the caller has multiple appointments, ask which one.

Keep spoken replies short and natural. Use the doctor's name, weekday/date, and local time when offering slots. Never read database IDs aloud. If a tool reports an error, explain it simply and offer another slot."""


def valid_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw)
    if not 10 <= len(digits) <= 15:
        return ""
    return digits


async def run_bot(transport, runner_args: RunnerArguments):
    confirmed_phone = ""

    async def confirm_caller_phone(params: FunctionCallParams, phone_number: str, caller_confirmed: bool):
        """Confirm a caller's spoken phone number after you read it back and hear yes.

        Args:
            phone_number: The 10 to 15 digit number the caller said.
            caller_confirmed: True only after the caller clearly said yes to the readback.
        """
        nonlocal confirmed_phone
        candidate = valid_phone(phone_number)
        if not candidate:
            result = "That number needs 10 to 15 digits. Ask the caller to repeat it."
        elif not caller_confirmed:
            result = "Do not continue until the caller confirms the readback."
        else:
            confirmed_phone = candidate
            result = f"Phone number ending {candidate[-4:]} is confirmed for this call."
        await params.result_callback({"result": result})

    async def check_doctor_availability(params: FunctionCallParams, doctor_or_specialty: str, day: str = ""):
        """Return open doctor slots for a requested doctor, specialty, or optional date.

        Args:
            doctor_or_specialty: Doctor's name or specialty, such as Dr. Maya Shah or Cardiology.
            day: Optional date in YYYY-MM-DD format.
        """
        try:
            slots = await appointment_api("/internal/availability", {"doctor_or_specialty": doctor_or_specialty, "day": day})
            await params.result_callback({"slots": slots[:12], "message": "No open slots found." if not slots else "Availability retrieved."})
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.error(f"Availability API error: {exc}")
            await params.result_callback({"slots": [], "message": "The appointment service is temporarily unavailable."})

    async def book_appointment(params: FunctionCallParams, doctor_or_specialty: str, slot_id: str):
        """Book the exact slot the caller selected, using their confirmed phone number.

        Args:
            doctor_or_specialty: Doctor name or specialty previously checked.
            slot_id: Exact slot ID returned by check_doctor_availability.
        """
        if not confirmed_phone:
            await params.result_callback({"ok": False, "message": "First ask for the caller's phone, read it back, and confirm it."})
            return
        try:
            result = await appointment_api("/internal/appointments/book", {
                "phone": confirmed_phone, "doctor_id": doctor_or_specialty, "slot_id": slot_id,
            })
            await params.result_callback(result)
        except httpx.HTTPStatusError as exc:
            await params.result_callback({"ok": False, "message": exc.response.text})
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.error(f"Booking API error: {exc}")
            await params.result_callback({"ok": False, "message": "The appointment service is temporarily unavailable."})

    async def find_my_appointments(params: FunctionCallParams):
        """Find the appointments belonging to this caller's phone number, after phone confirmation."""
        if not confirmed_phone:
            await params.result_callback({"appointments": [], "message": "Confirm the caller's spoken phone number first."})
            return
        try:
            appointments = await appointment_api("/internal/appointments/find", {"phone": confirmed_phone})
            await params.result_callback({"appointments": appointments,
                                          "message": "No current appointment found." if not appointments else "Appointments retrieved."})
        except (httpx.HTTPError, RuntimeError) as exc:
            logger.error(f"Appointment lookup API error: {exc}")
            await params.result_callback({"appointments": [], "message": "The appointment service is temporarily unavailable."})

    async def cancel_appointment(params: FunctionCallParams, appointment_id: str, caller_confirmed_cancellation: bool):
        """Cancel the selected caller appointment after its details are read back and confirmed.

        Args:
            appointment_id: ID of the appointment returned by find_my_appointments. Never speak this ID.
            caller_confirmed_cancellation: True only after explicit confirmation of the appointment details.
        """
        if not confirmed_phone:
            result = {"ok": False, "message": "Confirm the caller's spoken phone number first."}
        elif not caller_confirmed_cancellation:
            result = {"ok": False, "message": "Read back the appointment and wait for a clear yes before cancelling."}
        else:
            try:
                result = await appointment_api("/internal/appointments/cancel", {"phone": confirmed_phone,
                                                                                   "appointment_id": appointment_id})
            except httpx.HTTPStatusError as exc:
                result = {"ok": False, "message": exc.response.text}
            except (httpx.HTTPError, RuntimeError) as exc:
                logger.error(f"Cancellation API error: {exc}")
                result = {"ok": False, "message": "The appointment service is temporarily unavailable."}
        await params.result_callback(result)

    llm = GoogleLLMService(
        api_key=os.environ["GOOGLE_API_KEY"],
        stream_idle_timeout_secs=60,
        settings=GoogleLLMService.Settings(model=os.getenv("GEMINI_MODEL", "gemini-3.8-flash"),
                                           system_instruction=SYSTEM_PROMPT, temperature=0.25),
    )
    stt = ElevenLabsRealtimeSTTService(api_key=os.environ["ELEVENLABS_API_KEY"])
    tts = ElevenLabsTTSService(api_key=os.environ["ELEVENLABS_API_KEY"],
                               settings=ElevenLabsTTSService.Settings(voice=os.getenv("ELEVENLABS_VOICE_ID", "EXAVITQu4vr4xnSDxMaL")))
    context = LLMContext(tools=[confirm_caller_phone, check_doctor_availability, book_appointment,
                                 find_my_appointments, cancel_appointment])
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context, user_params=LLMUserAggregatorParams(vad_analyzer=SileroVADAnalyzer())
    )
    pipeline = Pipeline([transport.input(), stt, user_aggregator, llm, tts, transport.output(), assistant_aggregator])
    worker = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
                            idle_timeout_secs=runner_args.pipeline_idle_timeout_secs)
    runner = WorkerRunner(handle_sigint=runner_args.handle_sigint)
    await runner.add_workers(worker)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(_transport, _client):
        nonlocal confirmed_phone
        confirmed_phone = ""
        context.add_message({"role": "developer", "content": "Greet the caller and ask what they need help with. Use the phone number they speak as their only caller identity."})
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(_transport, _client):
        logger.info("Caller disconnected")
        await runner.cancel()

    await runner.run()


async def bot(runner_args: RunnerArguments):
    transport_params = {"webrtc": lambda: TransportParams(audio_in_enabled=True, audio_out_enabled=True)}
    transport = await create_transport(runner_args, transport_params)
    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main
    main()

