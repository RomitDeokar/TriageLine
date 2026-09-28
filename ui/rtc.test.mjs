import test from 'node:test';
import assert from 'node:assert/strict';
import { createVoiceClient } from './static/rtc-controller.mjs';
const deferred = () => { let resolve, reject; const promise = new Promise((a,b) => {resolve=a;reject=b;}); return {promise,resolve,reject}; };
function setup({token,connect,mic} = {}) {
  const rooms=[], messages=[], states=[], audio=[];
  class Room {
    constructor() {
      rooms.push(this); this.handlers={}; this.remoteParticipants=new Map(); this.canPlaybackAudio=true;
      this.disconnected=0; this.enabled=false;
      this.localParticipant={identity:'u1',trackPublications:new Map(),setMicrophoneEnabled:async enabled => { if(mic) await mic.promise; this.enabled=enabled; }};
    }
    on(event,fn){this.handlers[event]=fn;}
    removeAllListeners(){this.handlers={};}
    async startAudio(){}
    async connect(){if(connect) await connect.promise;}
    async disconnect(){this.disconnected++;this.enabled=false;}
  }
  const events=Object.fromEntries(['TrackSubscribed','TrackUnsubscribed','AudioPlaybackStatusChanged','ParticipantConnected','ParticipantDisconnected','Reconnecting','Reconnected','Disconnected','TranscriptionReceived'].map(k=>[k,k]));
  const view={status:x=>messages.push(x),controls:x=>states.push(x),clearAudio:()=>audio.splice(0),autoplay:()=>{},audio:t=>audio.push(t),removeAudio:()=>{},transcript:()=>{}};
  const c=createVoiceClient({Room,RoomEvent:events,Track:{Kind:{Audio:'audio'}},ParticipantKind:{AGENT:4}},view,()=>token?token.promise:Promise.resolve({url:'wss://example',token:'fake'}));
  return {c,rooms,messages,states,audio};
}
const tick=()=>new Promise(r=>setImmediate(r));
test('joins, publishes mic, attaches audio, mutes and cleans up',async()=>{
 const {c,rooms,states,audio}=setup();await c.join();assert.equal(rooms[0].enabled,true);assert.equal(states.at(-1).connected,true);
 rooms[0].handlers.TrackSubscribed({kind:'audio'});assert.equal(audio.length,1);
 await c.toggleMic();assert.equal(rooms[0].enabled,false);
 await c.leave();assert.equal(audio.length,0);assert.equal(states.at(-1).connected,false);
});
test('hangup while token pending never connects or publishes',async()=>{
 const token=deferred(),{c,rooms}=setup({token});const task=c.join();await c.leave();token.resolve({});await task;assert.equal(rooms[0].enabled,false);
});
test('late room connection after hangup is disposed',async()=>{
 const connect=deferred(),{c,rooms}=setup({connect});const task=c.join();await tick();await c.leave();connect.resolve();await task;assert.equal(rooms[0].enabled,false);assert.ok(rooms[0].disconnected>=2);
});
test('late microphone permission after hangup cannot leak capture',async()=>{
 const mic=deferred(),{c,rooms}=setup({mic});const task=c.join();await tick();await c.leave();mic.resolve();await task;assert.equal(rooms[0].enabled,false);
});
test('duplicate connect clicks produce only one room',async()=>{
 const token=deferred(),{c,rooms}=setup({token});const task=c.join();await c.join();assert.equal(rooms.length,1);token.resolve({});await task;await c.leave();
});
test('token failures are visible and release connection',async()=>{
 const token=deferred(),{c,rooms,messages,states}=setup({token});const task=c.join();token.reject(Error('Configure LiveKit'));await task;assert.match(messages.at(-1),/Configure LiveKit/);assert.equal(states.at(-1).joining,false);assert.equal(rooms[0].enabled,false);
});
test('old connection cannot overwrite new connection state',async()=>{
 const connect=deferred(),{c,rooms,states}=setup({connect});const first=c.join();await tick();await c.leave();const second=c.join();connect.resolve();await Promise.all([first,second]);assert.equal(rooms[0].enabled,false);assert.equal(rooms[1].enabled,true);assert.equal(states.at(-1).connected,true);await c.leave();
});
test('backgrounding pauses the mic and returning resumes it without hanging up',async()=>{
 const {c,rooms,states}=setup();await c.join();await c.pause();assert.equal(rooms[0].enabled,false);assert.equal(states.at(-1).connected,true);assert.equal(rooms[0].disconnected,0);
 await c.resume();assert.equal(rooms[0].enabled,true);assert.equal(states.at(-1).muted,false);await c.leave();
});
test('resume never re-enables a mic the user muted',async()=>{
 const {c,rooms}=setup();await c.join();await c.toggleMic();await c.pause();await c.resume();assert.equal(rooms[0].enabled,false);await c.leave();
});
test('agent detected by published audio even without ParticipantKind',async()=>{
 const {c,rooms,messages}=setup();await c.join();
 rooms[0].remoteParticipants.set('w',{identity:'worker',kind:0,trackPublications:new Map([['a',{kind:'audio'}]])});
 rooms[0].handlers.ParticipantConnected();assert.match(messages.at(-1),/Voice connected/);await c.leave();
});
