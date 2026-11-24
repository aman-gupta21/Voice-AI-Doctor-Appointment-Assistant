# CareLine

Voice-first doctor appointment booking, with a real-time dashboard and a Pipecat WebRTC agent.

## Stack

- Frontend: React, TypeScript, Vite
- Voice: Pipecat, ElevenLabs realtime STT and TTS, Google Gemini function calling
- Backend: FastAPI, SQLite
- Browser audio: Pipecat SmallWebRTC

## Run locally

Requirements: Python 3.11+, Node.js 20+, and `uv`.

1. Copy `backend/.env.example` to `backend/.env`. Add fresh `ELEVENLABS_API_KEY` and `GOOGLE_API_KEY` values; set a long random `INTERNAL_API_KEY`.
2. In `backend/`, install dependencies with `uv sync`.
3. Start the appointment API: `uv run uvicorn api:app --reload --port 8000`.
4. In another terminal in `backend/`, start the Pipecat runner: `uv run python bot.py --host 0.0.0.0 --port 7860`.
5. Copy `frontend/.env.example` to `frontend/.env`, then in `frontend/` run `npm install && npm run dev`. Open the Vite URL and allow microphone access.

The API seeds the mock doctors and slots into SQLite on first start. Dashboard reads and voice tools use the same API and database. The agent reads the caller's number back and waits for an explicit confirmation before booking or cancelling.

## Voice pipeline

Browser microphone → SmallWebRTC → ElevenLabs realtime transcription → Gemini with appointment tools → ElevenLabs speech → browser audio. A Silero VAD analyzer handles turn boundaries. The five registered functions cover availability, booking, appointment lookup, cancellation, and the phone confirmation gate. Voice tools call the authenticated internal API so the API and voice service can be hosted separately while sharing one database.

## Hosting

The project includes Docker Compose and Caddy for a single VPS deployment. Copy `.env.example` to `.env`, set `DOMAIN`, fresh provider keys, and a long random `INTERNAL_API_KEY`, point the domain's DNS to the server, then run `docker compose up --build -d`. Open TCP 80/443 and UDP 443 on the host. Caddy obtains HTTPS certificates automatically. SQLite persists in `./data/appointments.db`. The browser, API, and Pipecat signaling endpoints share the HTTPS domain; provider API keys remain in the backend environment. SmallWebRTC may need STUN/TURN servers and a UDP relay range for restrictive networks; configure `PIPECAT_ICE_SERVERS` and firewall rules for the relay you choose.

## Submission checklist

- Push this repository to GitHub (never commit `.env`).
- Record a short browser demo showing number confirmation, availability, and a completed booking.
- Explain the pipeline in your own words in the submission form.
