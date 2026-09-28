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
      line.textContent = speaker + ': ' + segment.text;
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
// Background audio requires a native SDK and OS-specific lifecycle integration.
// This web client deliberately releases the mic rather than claiming background support.
window.addEventListener('pagehide', () => void client.leave());
document.addEventListener('visibilitychange', () => {
  if (document.hidden) void client.leave('Call paused when the app went into the background. Reconnect to continue.');
});
