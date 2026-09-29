import { Room, RoomEvent, Track, ParticipantKind } from 'livekit-client';
import { createVoiceClient } from './static/rtc-controller.mjs';

const $ = id => document.getElementById(id);
const attached = new Map(), lines = new Map();
const view = {
  status(text) { $('voice-status').textContent = text; },
  controls({ joining, connected, muted }) {
    $('connect').disabled = joining || connected;
    $('hangup').disabled = !(joining || connected);
    $('mute').disabled = !connected;
    $('mute').textContent = muted ? 'Enable microphone' : 'Mute microphone';
    $('mute').setAttribute('aria-pressed', String(muted));
  },
  autoplay(blocked) { $('play-audio').hidden = !blocked; },
  audio(track) {
    if (attached.has(track)) return;
    const element = track.attach();
    element.autoplay = true;
    element.setAttribute('playsinline', '');
    $('remote-audio').appendChild(element);
    attached.set(track, element);
  },
  removeAudio(track) {
    const element = attached.get(track);
    if (element) { track.detach(element); element.remove(); attached.delete(track); }
  },
  clearAudio() { for (const track of attached.keys()) this.removeAudio(track); },
  transcript(segments, speaker) {
    for (const segment of segments) {
      const key = speaker + ':' + segment.id;
      let line = lines.get(key);
      if (!line) { line = document.createElement('p'); lines.set(key, line); $('transcript').appendChild(line); }
      line.dataset.who = speaker; line.textContent = segment.text;
      if (lines.size > 200) { const oldest = lines.keys().next().value; lines.get(oldest).remove(); lines.delete(oldest); }
    }
  }
};
async function credentials() {
  if (!window.isSecureContext) throw new Error('Use HTTPS or localhost for microphone access');
  await window.TriageAuth.ensure();
  const response = await fetch('/api/rtc/token', { method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: '{}', signal: AbortSignal.timeout(15000) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'RTC token service unavailable; run ui.api, not ui/server.py');
  return data; // Short-lived participant token stays in memory only.
}
const client = createVoiceClient({ Room, RoomEvent, Track, ParticipantKind }, view, credentials);
$('connect').onclick = () => { $('transcript').replaceChildren(); lines.clear(); void client.join(); };
$('hangup').onclick = () => void client.leave();
$('mute').onclick = async () => { $('mute').disabled = true; await client.toggleMic(); };
$('play-audio').onclick = () => void client.resumeAudio();
// Mobile lifecycle: backgrounding pauses the microphone (the call and conversation stay alive) and
// resumes on return; the call ends only after HIDDEN_HANGUP_MS in the background or on page unload.
// True background capture needs a native LiveKit SDK (docs/MOBILE_INTEGRATION.md).
const HIDDEN_HANGUP_MS = 3 * 60 * 1000;
let hiddenTimer = null, wakeLock = null;
async function holdWakeLock() {
  try { if ('wakeLock' in navigator && !wakeLock) { wakeLock = await navigator.wakeLock.request('screen'); wakeLock.addEventListener('release', () => { wakeLock = null; }); } } catch (_) { /* optional */ }
}
window.addEventListener('pagehide', (e) => { if (!e.persisted) void client.leave(); });
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    void client.pause();
    clearTimeout(hiddenTimer);
    hiddenTimer = setTimeout(() => void client.leave('Call ended after 3 minutes in the background. Reconnect to continue.'), HIDDEN_HANGUP_MS);
  } else {
    clearTimeout(hiddenTimer); hiddenTimer = null;
    void client.resume();
    if (client.state().connected) void holdWakeLock();
  }
});
$('connect').addEventListener('click', () => void holdWakeLock());
