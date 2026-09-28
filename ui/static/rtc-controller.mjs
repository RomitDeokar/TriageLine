/* SDK-injected controller: all pending joins/capture operations are generation guarded. */
export function createVoiceClient({ Room, RoomEvent, Track, ParticipantKind }, view, getToken) {
  let room = null, generation = 0, joining = false, connected = false, muted = true;
  const current = (r, g) => room === r && generation === g;
  const controls = () => view.controls({ joining, connected, muted });
  const dispose = async (r) => {
    if (!r) return;
    r.removeAllListeners();
    try { await r.disconnect(true); } catch (_) { /* release local media even after network failure */ }
    for (const p of r.localParticipant.trackPublications.values()) p.track?.stop();
  };
  async function leave(message = 'Call ended. Microphone released.') {
    generation++;
    const previous = room;
    room = null; joining = false; connected = false; muted = true; pausedByApp = false;
    view.clearAudio(); view.autoplay(false); view.status(message); controls();
    await dispose(previous);
  }
  async function join() {
    if (joining || connected) return;
    const g = ++generation;
    const r = new Room({ adaptiveStream: true, dynacast: true,
      audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    room = r; joining = true; controls(); view.status('Signing in and connecting…');
    // Called inside the click gesture, before authentication awaits, for mobile autoplay.
    r.startAudio().catch(() => { if (current(r, g)) view.autoplay(true); });
    // Agent detection: ParticipantKind.AGENT when the SDK/server populate it; otherwise any remote
    // participant that is publishing audio or whose identity marks it as an agent worker.
    const isAgent = p => (ParticipantKind && p.kind === ParticipantKind.AGENT) || /^agent[-_]/i.test(p.identity || '')
      || [...(p.trackPublications?.values?.() || [])].some(t => t.kind === Track.Kind.Audio);
    const agentPresent = () => [...r.remoteParticipants.values()].some(isAgent);
    const updateStatus = () => view.status(agentPresent() ? 'Voice connected — speak naturally; interrupt at any time.' : 'Room connected — waiting for the voice worker. Start cascaded_agent.py dev.');
    r.on(RoomEvent.TrackSubscribed, (track) => {
      if (current(r, g) && track.kind === Track.Kind.Audio) { view.audio(track); updateStatus(); }
    });
    r.on(RoomEvent.TrackUnsubscribed, track => view.removeAudio(track));
    r.on(RoomEvent.AudioPlaybackStatusChanged, () => { if (current(r, g)) view.autoplay(!r.canPlaybackAudio); });
    r.on(RoomEvent.ParticipantConnected, () => { if (current(r, g)) updateStatus(); });
    r.on(RoomEvent.ParticipantDisconnected, () => { if (current(r, g)) updateStatus(); });
    r.on(RoomEvent.Reconnecting, () => { if (current(r, g)) view.status('Connection interrupted — reconnecting…'); });
    r.on(RoomEvent.Reconnected, () => { if (current(r, g)) updateStatus(); });
    r.on(RoomEvent.Disconnected, () => { if (current(r, g)) void leave('Disconnected. Press Connect to start again.'); });
    r.on(RoomEvent.TranscriptionReceived, (segments, participant) => {
      if (current(r, g)) view.transcript(segments, participant?.identity === r.localParticipant.identity ? 'You' : 'Assistant');
    });
    try {
      const credentials = await getToken();
      if (!current(r, g)) return;
      await r.connect(credentials.url, credentials.token);
      if (!current(r, g)) { await dispose(r); return; }
      view.status('Allow microphone access to begin…');
      await r.localParticipant.setMicrophoneEnabled(true);
      if (!current(r, g)) { await dispose(r); return; }
      joining = false; connected = true; muted = false; controls(); updateStatus();
      view.autoplay(!r.canPlaybackAudio);
    } catch (error) {
      if (current(r, g)) await leave('Could not connect: ' + (error.message || 'check microphone permissions and server configuration'));
      else await dispose(r);
    }
  }
  async function toggleMic() {
    const r = room, g = generation;
    if (!connected || !r) return;
    const enable = muted;
    try {
      await r.localParticipant.setMicrophoneEnabled(enable);
      if (!current(r, g)) { await dispose(r); return; }
      muted = !enable; controls();
    } catch (_) { if (current(r, g)) view.status('Microphone unavailable. Check browser permissions.'); }
  }
  // Backgrounding (screen lock, notification shade, app switch) pauses the microphone instead of
  // hanging up; the caller decides when a long absence should end the call.
  let pausedByApp = false;
  async function pause() {
    const r = room, g = generation;
    if (!connected || !r || muted) return;
    try {
      await r.localParticipant.setMicrophoneEnabled(false);
      if (!current(r, g)) return;
      muted = true; pausedByApp = true; controls();
      view.status('Paused while the app is in the background — microphone off.');
    } catch (_) { /* leave state unchanged */ }
  }
  async function resume() {
    const r = room, g = generation;
    if (!connected || !r || !pausedByApp) return;
    pausedByApp = false;
    try {
      await r.localParticipant.setMicrophoneEnabled(true);
      if (!current(r, g)) return;
      muted = false; controls(); view.status('Voice connected — speak naturally; interrupt at any time.');
    } catch (_) { if (current(r, g)) view.status('Microphone unavailable. Check browser permissions.'); }
  }
  async function resumeAudio() {
    const r = room, g = generation;
    if (!r) return;
    try { await r.startAudio(); if (current(r, g)) view.autoplay(!r.canPlaybackAudio); }
    catch (_) { if (current(r, g)) view.status('Audio playback blocked. Check device audio settings.'); }
  }
  controls();
  const state = () => ({ joining, connected, muted, pausedByApp });
  return { join, leave, toggleMic, resumeAudio, pause, resume, state };
}
