import { useCallback, useEffect, useRef, useState } from 'react'
import { Activity, ArrowDownRight, ArrowUpRight, CalendarDays, Check, ChevronDown, Clock3, HeartPulse, Mic, MicOff, Phone, PhoneOff, Plus, Search, ShieldCheck, Sparkles, Stethoscope, X } from 'lucide-react'
import type { BotOutputData, PipecatClient as PipecatClientInstance } from '@pipecat-ai/client-js'

const API = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'
const PIPECAT = import.meta.env.VITE_PIPECAT_URL ?? 'http://localhost:7860'
const MAX_CALL_SECONDS = 500

type Slot = { id: string; starts_at: string; doctor_name?: string; specialty?: string }
type Doctor = { id: string; name: string; specialty: string; initials: string; note: string; color: string; slots: Slot[] }
type Appointment = { id: string; slot_id: string; starts_at: string; doctor_id: string; doctor_name: string; specialty: string }
type Line = { role: 'assistant' | 'caller' | 'system'; text: string; at: string; segmentId?: number }
type CallState = 'idle' | 'ringing' | 'connecting' | 'connected' | 'ended' | 'error'

function fmtDate(value: string, options: Intl.DateTimeFormatOptions = { weekday: 'short', month: 'short', day: 'numeric' }) {
  return new Intl.DateTimeFormat('en-IN', { ...options, timeZone: 'Asia/Kolkata' }).format(new Date(value))
}
function fmtTime(value: string) { return new Intl.DateTimeFormat('en-IN', { hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Kolkata' }).format(new Date(value)) }
function timeNow() { return new Intl.DateTimeFormat('en-IN', { hour: 'numeric', minute: '2-digit', timeZone: 'Asia/Kolkata' }).format(new Date()) }
function dayLabel() { return new Intl.DateTimeFormat('en-IN', { weekday: 'long', month: 'long', day: 'numeric', timeZone: 'Asia/Kolkata' }).format(new Date()).toUpperCase() }

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API}${path}`)
  if (!response.ok) throw new Error('Could not load clinic data')
  return response.json() as Promise<T>
}

export default function App() {
  const [doctors, setDoctors] = useState<Doctor[]>([])
  const [appointments, setAppointments] = useState<Appointment[]>([])
  const [allSlots, setAllSlots] = useState<Slot[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [callOpen, setCallOpen] = useState(false)
  const [callState, setCallState] = useState<CallState>('idle')
  const [muted, setMuted] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const [transcript, setTranscript] = useState<Line[]>([])
  const [query, setQuery] = useState('')
  const [toast, setToast] = useState('')
  const [cancelTarget, setCancelTarget] = useState<Appointment | null>(null)
  const [cancelPhone, setCancelPhone] = useState('')
  const [cancelError, setCancelError] = useState('')
  const clientRef = useRef<PipecatClientInstance | null>(null)
  const endCallRef = useRef<() => void>(() => {})
  const transcriptEnd = useRef<HTMLDivElement>(null)

  const refresh = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true)
    try {
      const [doctorRows, appointmentRows, slotRows] = await Promise.all([
        getJson<Doctor[]>('/api/doctors'), getJson<Appointment[]>('/api/appointments'), getJson<Slot[]>('/api/slots'),
      ])
      setDoctors(doctorRows); setAppointments(appointmentRows); setAllSlots(slotRows); setError('')
    } catch { setError('The clinic service is offline. Start the API on port 8000 to load live appointment data.') }
    finally { setLoading(false) }
  }, [])

  useEffect(() => { void refresh(); const id = window.setInterval(() => void refresh(true), 12000); return () => window.clearInterval(id) }, [refresh])
  useEffect(() => { transcriptEnd.current?.scrollIntoView({ behavior: 'smooth' }) }, [transcript])
  useEffect(() => {
    if (callState !== 'connected') return
    const intervalId = window.setInterval(() => {
      setSeconds(value => Math.min(value + 1, MAX_CALL_SECONDS))
    }, 1000)
    const limitId = window.setTimeout(() => {
      setSeconds(MAX_CALL_SECONDS)
      endCallRef.current()
    }, MAX_CALL_SECONDS * 1000)
    return () => {
      window.clearInterval(intervalId)
      window.clearTimeout(limitId)
    }
  }, [callState])
  useEffect(() => { if (!toast) return; const id = window.setTimeout(() => setToast(''), 2800); return () => window.clearTimeout(id) }, [toast])

  const pushLine = useCallback((role: Line['role'], text: string) => {
    if (!text?.trim()) return
    setTranscript(lines => [...lines, { role, text: text.trim(), at: timeNow() }])
    if (role === 'assistant') void refresh(true)
  }, [refresh])

  const pushBotOutput = useCallback((data: BotOutputData) => {
    const text = data.spoken_progress
      ? (data.spoken_progress.accumulated_text + data.spoken_progress.remaining_text).trim()
      : data.text.trim()
    if (!text) return
    setTranscript(lines => {
      const segmentId = data.segment_id
      if (segmentId === undefined) return [...lines, { role: 'assistant', text, at: timeNow() }]
      const existing = lines.findIndex(line => line.role === 'assistant' && line.segmentId === segmentId)
      if (existing === -1) return [...lines, { role: 'assistant', text, at: timeNow(), segmentId }]
      return lines.map((line, index) => index === existing ? { ...line, text } : line)
    })
    if (data.spoken_status === 'completed' || data.spoken_status === undefined) void refresh(true)
  }, [refresh])

  async function startCall() {
    setCallOpen(true); setCallState('ringing'); setSeconds(0); setMuted(false); setSpeaking(false); setTranscript([])
    pushLine('system', 'Connecting to CareLine')
    try {
      await new Promise(resolve => window.setTimeout(resolve, 650))
      setCallState('connecting')
      const [{ PipecatClient }, { SmallWebRTCTransport }] = await Promise.all([
        import('@pipecat-ai/client-js'), import('@pipecat-ai/small-webrtc-transport'),
      ])
      const client = new PipecatClient({
        transport: new SmallWebRTCTransport(), enableMic: true, enableCam: false,
        callbacks: {
          onConnected: () => { setCallState('connected'); pushLine('system', 'Connected · microphone is live') },
          onDisconnected: () => { setCallState('ended'); setSpeaking(false) },
          onTransportStateChanged: (state) => { if (String(state).toLowerCase().includes('error')) setCallState('error') },
          onUserTranscript: (data) => { if (data.final) pushLine('caller', data.text) },
          onBotOutput: (data) => pushBotOutput(data),
          onUserStartedSpeaking: () => setSpeaking(true),
          onUserStoppedSpeaking: () => setSpeaking(false),
          onBotStartedSpeaking: () => setSpeaking(true),
          onBotStoppedSpeaking: () => setSpeaking(false),
          onError: (message) => { setCallState('error'); setError(String(message)) },
        },
      })
      clientRef.current = client
      await client.startBotAndConnect({ endpoint: `${PIPECAT}/start` })
    } catch (e) {
      setCallState('error')
      pushLine('system', e instanceof Error ? e.message : 'Could not connect. Check that the Pipecat server is running.')
    }
  }

  async function endCall() {
    const client = clientRef.current
    clientRef.current = null
    if (client) { try { await client.disconnect() } catch { /* connection may already have closed */ } }
    setCallState('ended'); setSpeaking(false); pushLine('system', 'Call ended'); void refresh(true)
  }

  endCallRef.current = () => { void endCall() }

  function toggleMute() {
    const next = !muted
    try { clientRef.current?.enableMic(!next) } catch { /* client API may be in a transition */ }
    setMuted(next)
  }

  async function cancelAppointment() {
    if (!cancelTarget) return
    try {
      const response = await fetch(`${API}/api/appointments/${cancelTarget.id}`, { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ phone: cancelPhone }) })
      const data = await response.json()
      if (!response.ok) throw new Error(data.detail || 'Cancellation could not be completed')
      setToast(`Appointment with ${cancelTarget.doctor_name} cancelled`); setCancelTarget(null); void refresh(true)
    } catch (e) { setCancelError(e instanceof Error ? e.message : 'Cancellation failed') }
  }

  const filteredDoctors = doctors.filter(d => `${d.name} ${d.specialty}`.toLowerCase().includes(query.toLowerCase()))
  const duration = `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`
  const upcomingCount = appointments.filter(a => new Date(a.starts_at) >= new Date()).length

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#top"><span className="brand-mark"><HeartPulse size={20} strokeWidth={2.3} /></span><span>careline<span className="brand-period">.</span><small>CLINIC DESK</small></span></a>
      <div className="side-label">WORKSPACE</div>
      <nav className="nav-list"><a className="nav-item active" href="#overview"><Activity size={17} /> Overview <span className="nav-dot" /></a><a className="nav-item" href="#appointments"><CalendarDays size={17} /> Appointments</a><a className="nav-item" href="#doctors"><Stethoscope size={17} /> Care team</a></nav>
      <div className="sidebar-spacer" />
      <div className="help-card"><div className="help-icon"><ShieldCheck size={17} /></div><b>Private by design</b><p>Callers are identified by the phone number they speak. No patient login required.</p></div>
      <div className="user-chip"><div className="user-avatar">CD</div><div><b>Clinic desk</b><span>CareLine demo</span></div><ChevronDown size={15} /></div>
    </aside>

    <main className="main" id="top">
      <header className="topbar"><div className="crumb">Workspace <span>/</span> Overview</div><div className="top-actions"><span className="live-pill"><i /> All systems normal</span><div className="top-date"><CalendarDays size={15} /> {fmtDate(new Date().toISOString())}</div><div className="top-avatar">CD</div></div></header>
      <div className="page-wrap" id="overview">
        <section className="welcome-row"><div><div className="eyebrow"><span className="eyebrow-line" /> {dayLabel()}</div><h1>A calmer way to care.</h1><p className="subhead">Your appointment desk, at a glance.</p></div><button className="primary-button" onClick={() => void startCall()}><Phone size={16} fill="currentColor" /> Try a call <span className="button-arrow">↗</span></button></section>

        {error && <div className="alert"><span className="alert-mark">!</span>{error}<button onClick={() => void refresh()}>Retry</button></div>}

        <section className="metrics-grid" aria-label="Clinic summary">
          <article className="metric-card"><div className="metric-head"><span>Upcoming appointments</span><span className="metric-icon sage"><CalendarDays size={16} /></span></div><div className="metric-value">{loading ? '—' : String(upcomingCount).padStart(2, '0')}</div><div className="metric-foot"><span className="metric-trend"><ArrowUpRight size={13} /> Live</span> <span>booked visits</span></div><div className="metric-spark sage-spark"><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/></div></article>
          <article className="metric-card"><div className="metric-head"><span>Open appointment times</span><span className="metric-icon blue"><Clock3 size={16} /></span></div><div className="metric-value">{loading ? '—' : String(allSlots.length).padStart(2, '0')}</div><div className="metric-foot"><span className="metric-trend neutral"><ArrowDownRight size={13} /> Next 7 days</span> <span>across the care team</span></div><div className="metric-spark blue-spark"><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/></div></article>
          <article className="metric-card accent-metric"><div className="metric-head"><span>Voice desk</span><span className="metric-icon warm"><Sparkles size={16} /></span></div><div className="metric-value metric-word">Ready when you are</div><div className="metric-foot"><span className="metric-trend"><span className="status-breath" /> Online</span> <span>Pipecat · ElevenLabs</span></div><div className="voice-wave"><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/></div></article>
        </section>

        <section className="section-block" id="appointments">
          <div className="section-heading"><div><div className="eyebrow">THE DAYS AHEAD</div><h2>Upcoming appointments <span className="count-badge">{appointments.length}</span></h2></div><button className="text-button" onClick={() => document.getElementById('doctors')?.scrollIntoView({ behavior: 'smooth' })}>View availability <span>↗</span></button></div>
          <div className="appointments-card"><div className="table-head"><span>DOCTOR</span><span>DATE & TIME</span><span>SPECIALTY</span><span>STATUS</span><span /></div>
            {appointments.length === 0 ? <div className="empty-row"><div className="empty-calendar"><CalendarDays size={22} /></div><b>A little room in the calendar.</b><span>Booked visits will appear here as soon as they’re made.</span><button className="subtle-button" onClick={() => void startCall()}><Phone size={14} /> Start a voice booking</button></div> : appointments.slice(0, 6).map((a, i) => <div className="appointment-row" key={a.id}><div className="doctor-cell"><div className={`doctor-avatar doctor-${i % 4}`}>{doctors.find(d => d.id === a.doctor_id)?.initials || 'DR'}</div><span>{a.doctor_name}</span></div><div className="date-cell"><b>{fmtDate(a.starts_at)}</b><span>{fmtTime(a.starts_at)}</span></div><div className="specialty-cell">{a.specialty}</div><div><span className="status-tag"><i /> Confirmed</span></div><button className="row-menu" title="Cancel appointment" aria-label={`Cancel appointment with ${a.doctor_name}`} onClick={() => { setCancelTarget(a); setCancelPhone(''); setCancelError('') }}><X size={15} /></button></div>)}
          </div>
        </section>

        <section className="section-block team-block" id="doctors">
          <div className="section-heading"><div><div className="eyebrow">GOOD PEOPLE, GOOD CARE</div><h2>Meet your care team</h2></div><label className="search-box"><Search size={15} /><input value={query} onChange={e => setQuery(e.target.value)} placeholder="Find a doctor or specialty" /></label></div>
          <div className="doctor-grid">{filteredDoctors.map((doctor, index) => {
            const next = doctor.slots?.[0]
            return <article className="doctor-card" key={doctor.id}><div className="doctor-top"><div className={`large-avatar tone-${index % 4}`}>{doctor.initials}</div><span className="availability-dot"><i /> Available</span></div><h3>{doctor.name}</h3><span className="doctor-specialty">{doctor.specialty}</span><p>{doctor.note}</p><div className="slot-strip" aria-label={`Available times for ${doctor.name}`}>{doctor.slots.slice(0, 3).map(slot => <span className="slot-chip" key={slot.id}><b>{fmtDate(slot.starts_at, { weekday: 'short', day: 'numeric' })}</b>{fmtTime(slot.starts_at)}</span>)}</div><div className="doctor-card-foot"><div><small>UPCOMING TIMES</small><b>{next ? `${fmtDate(next.starts_at, { weekday: 'short', month: 'short', day: 'numeric' })} · ${fmtTime(next.starts_at)}` : 'Check for openings'}</b></div><button aria-label={`Start call to book ${doctor.name}`} onClick={() => void startCall()}><Plus size={17} /></button></div></article>
          })}</div>
        </section>

        <footer className="footer"><span>CareLine <span className="brand-period">·</span> Appointment desk</span><span>Built for clearer conversations and easier care.</span><span><i className="privacy-dot" /> Patient privacy first</span></footer>
      </div>
    </main>

    {callOpen && <div className="call-backdrop" role="dialog" aria-modal="true" aria-label="CareLine voice call">
      <div className="call-window">
        <div className="call-header"><div className="call-brand"><span className="brand-mark small"><HeartPulse size={16} /></span><b>careline<span className="brand-period">.</span></b><span className="call-label">VOICE DESK</span></div><button className="close-call" aria-label="Close call" onClick={() => { if (callState === 'connected') void endCall(); setCallOpen(false) }}><X size={18} /></button></div>
        <div className="call-body">
          <div className="call-profile"><div className={`agent-orb ${speaking ? 'speaking' : ''}`}><span className="orb-core"><HeartPulse size={30} /></span><i className="orb-ring ring-one"/><i className="orb-ring ring-two"/><i className="orb-ring ring-three"/></div><div className="call-title">CareLine assistant</div><div className="call-caption">Your clinic’s virtual receptionist</div>
            <div className={`call-status state-${callState}`}><i />{callState === 'ringing' ? 'Ringing' : callState === 'connecting' ? 'Connecting securely' : callState === 'connected' ? (speaking ? 'In conversation' : 'Connected') : callState === 'error' ? 'Connection issue' : 'Call ended'}</div>
            <div className="call-time">{callState === 'connected' ? duration : callState === 'ringing' || callState === 'connecting' ? '00:--' : duration}</div>
            <div className="call-visualizer" aria-label="Voice activity"><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/><i/></div>
            <p className="privacy-note"><ShieldCheck size={14} /> Your phone number is only used to find your appointments.</p>
          </div>
          <div className="transcript-panel"><div className="transcript-head"><div><span className="eyebrow">LIVE CAPTIONS</span><h3>Conversation</h3></div><span className="caption-live"><i /> LIVE</span></div>
            <div className="transcript-list">{transcript.length === 0 ? <div className="transcript-empty"><span className="caption-icon"><Mic size={18} /></span><b>Your conversation will appear here.</b><span>Speak naturally to check availability or manage an appointment.</span></div> : transcript.map((line, i) => <div className={`transcript-line ${line.role}`} key={`${line.at}-${i}`}><div className="line-meta"><span>{line.role === 'caller' ? 'YOU' : line.role === 'assistant' ? 'CARELINE' : 'SYSTEM'}</span><time>{line.at}</time></div><p>{line.text}</p></div>)}<div ref={transcriptEnd} /></div>
            <div className="call-controls">{callState === 'connected' ? <><button className={`control-button ${muted ? 'control-muted' : ''}`} onClick={toggleMute}>{muted ? <MicOff size={18} /> : <Mic size={18} />}<span>{muted ? 'Unmute' : 'Mute'}</span></button><button className="end-call-button" onClick={() => void endCall()}><PhoneOff size={18} /><span>End call</span></button></> : callState === 'ended' || callState === 'error' ? <button className="primary-button reconnect-button" onClick={() => void startCall()}><Phone size={16} /> Call again</button> : <button className="control-button" onClick={() => { setCallOpen(false); setCallState('idle') }}><X size={18} /><span>Cancel</span></button>}</div>
          </div>
        </div>
        <div className="call-bottom"><span><span className="secure-icon"><ShieldCheck size={13} /></span> Secure, encrypted connection</span><span>CareLine Voice · Gemini</span></div>
      </div>
    </div>}
    {cancelTarget && <div className="confirm-backdrop" role="dialog" aria-modal="true" aria-labelledby="cancel-title"><div className="confirm-card"><button className="close-call confirm-close" aria-label="Close" onClick={() => setCancelTarget(null)}><X size={17} /></button><div className="confirm-icon"><CalendarDays size={19} /></div><div className="eyebrow">APPOINTMENT CHANGE</div><h3 id="cancel-title">Cancel this visit?</h3><p className="confirm-visit">{cancelTarget.doctor_name}<span>{fmtDate(cancelTarget.starts_at)} · {fmtTime(cancelTarget.starts_at)}</span></p><label className="phone-field">Caller phone number<input autoFocus type="tel" value={cancelPhone} onChange={e => setCancelPhone(e.target.value)} placeholder="Enter the number used to book" /></label>{cancelError && <div className="form-error">{cancelError}</div>}<div className="confirm-actions"><button className="subtle-button" onClick={() => setCancelTarget(null)}>Keep appointment</button><button className="end-call-button" onClick={() => void cancelAppointment()}>Confirm cancellation</button></div><small className="confirm-privacy"><ShieldCheck size={13} /> Phone number is used only to verify this booking.</small></div></div>}
    {toast && <div className="toast"><span><Check size={15} /></span>{toast}</div>}
  </div>
}

