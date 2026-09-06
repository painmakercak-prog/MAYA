const $ = (id) => document.getElementById(id);

const els = {
  avatar: $("avatar"),
  name: $("name"),
  status: $("statusText"),
  select: $("nomiSelect"),
  notice: $("setupNotice"),
  transcript: $("transcript"),
  liveText: $("liveText"),
  call: $("callButton"),
  callLabel: $("callLabel"),
  orb: $("orb"),
};

let nomis = [];
let selectedNomi = null;
let socket = null;
let mediaStream = null;
let audioContext = null;
let sourceNode = null;
let workletNode = null;
let muteNode = null;
let pendingPcm = [];
let playbackCursor = 0;
let playbackNodes = new Set();
let callActive = false;
let acceptAudio = false;

function setStatus(text) {
  els.status.textContent = text;
  els.liveText.textContent = text;
}

function showNotice(text) {
  els.notice.textContent = text || "";
  els.notice.classList.toggle("hidden", !text);
}

function setOrb(mode) {
  els.orb.dataset.mode = mode;
}

async function jsonFetch(url) {
  const response = await fetch(url, { cache: "no-store" });
  const data = await response.json();
  if (!response.ok) throw new Error(data?.detail || `Request failed (${response.status})`);
  return data;
}

function applyNomi(nomi) {
  selectedNomi = nomi;
  if (!nomi) return;
  localStorage.setItem("maya-nomi-id", nomi.uuid);
  els.name.textContent = nomi.name;
  els.avatar.src = `/api/nomis/${encodeURIComponent(nomi.uuid)}/avatar`;
}

async function boot() {
  try {
    const status = await jsonFetch("/api/status");
    const missing = Object.entries(status.configured)
      .filter(([, ok]) => !ok)
      .map(([name]) => name);
    if (missing.length) showNotice(`Missing server configuration: ${missing.join(", ")}`);

    const data = await jsonFetch("/api/nomis");
    nomis = data.nomis || [];
    els.select.innerHTML = "";
    for (const nomi of nomis) {
      const option = document.createElement("option");
      option.value = nomi.uuid;
      option.textContent = nomi.name;
      els.select.appendChild(option);
    }
    if (!nomis.length) throw new Error("No Nomis found on this account");

    const remembered = localStorage.getItem("maya-nomi-id");
    const initial = nomis.find((n) => n.uuid === remembered) || nomis[0];
    els.select.value = initial.uuid;
    applyNomi(initial);
    setStatus("Ready for a live call");
  } catch (error) {
    showNotice(error.message);
    setStatus("Setup needed");
  }
}

els.select.addEventListener("change", () => {
  if (callActive) return;
  applyNomi(nomis.find((n) => n.uuid === els.select.value));
});

function downsampleTo16k(input, inputRate) {
  if (inputRate === 16000) return input;
  const ratio = inputRate / 16000;
  const length = Math.max(1, Math.floor(input.length / ratio));
  const output = new Float32Array(length);
  let outputIndex = 0;
  let inputIndex = 0;

  while (outputIndex < length) {
    const nextInputIndex = Math.min(input.length, Math.round((outputIndex + 1) * ratio));
    let sum = 0;
    let count = 0;
    for (let i = inputIndex; i < nextInputIndex; i += 1) {
      sum += input[i];
      count += 1;
    }
    output[outputIndex] = count ? sum / count : 0;
    outputIndex += 1;
    inputIndex = nextInputIndex;
  }
  return output;
}

function floatToInt16(floatData) {
  const out = new Int16Array(floatData.length);
  for (let i = 0; i < floatData.length; i += 1) {
    const s = Math.max(-1, Math.min(1, floatData[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function enqueueCapture(floatData) {
  if (!socket || socket.readyState !== WebSocket.OPEN) return;
  const downsampled = downsampleTo16k(floatData, audioContext.sampleRate);
  const pcm = floatToInt16(downsampled);
  for (let i = 0; i < pcm.length; i += 1) pendingPcm.push(pcm[i]);

  while (pendingPcm.length >= 320) {
    const frame = new Int16Array(320);
    for (let i = 0; i < 320; i += 1) frame[i] = pendingPcm.shift();
    socket.send(frame.buffer);
  }
}

function stopPlayback() {
  acceptAudio = false;
  for (const node of playbackNodes) {
    try { node.stop(); } catch {}
  }
  playbackNodes.clear();
  if (audioContext) playbackCursor = audioContext.currentTime;
}

function playPcmChunk(buffer) {
  if (!acceptAudio || !audioContext || !buffer.byteLength) return;
  const ints = new Int16Array(buffer);
  const audioBuffer = audioContext.createBuffer(1, ints.length, 24000);
  const channel = audioBuffer.getChannelData(0);
  for (let i = 0; i < ints.length; i += 1) channel[i] = ints[i] / 32768;

  const node = audioContext.createBufferSource();
  node.buffer = audioBuffer;
  node.connect(audioContext.destination);
  const startAt = Math.max(audioContext.currentTime + 0.025, playbackCursor);
  node.start(startAt);
  playbackCursor = startAt + audioBuffer.duration;
  playbackNodes.add(node);
  node.onended = () => playbackNodes.delete(node);
}

async function startAudio() {
  mediaStream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  audioContext = new AudioContext({ latencyHint: "interactive" });
  await audioContext.resume();
  await audioContext.audioWorklet.addModule("/pcm-worklet.js?v=2");

  sourceNode = audioContext.createMediaStreamSource(mediaStream);
  workletNode = new AudioWorkletNode(audioContext, "pcm-capture");
  muteNode = audioContext.createGain();
  muteNode.gain.value = 0;
  sourceNode.connect(workletNode);
  workletNode.connect(muteNode);
  muteNode.connect(audioContext.destination);
  workletNode.port.onmessage = (event) => enqueueCapture(event.data);
}

async function startCall() {
  if (!selectedNomi || callActive) return;
  showNotice("");
  els.call.disabled = true;

  try {
    await startAudio();
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${location.host}/ws/live?nomi_id=${encodeURIComponent(selectedNomi.uuid)}`);
    socket.binaryType = "arraybuffer";

    socket.onopen = () => {
      callActive = true;
      els.select.disabled = true;
      els.call.classList.add("active");
      els.callLabel.textContent = "End live call";
      els.call.disabled = false;
      setOrb("listening");
      setStatus("Live — just talk");
    };

    socket.onmessage = (event) => {
      if (typeof event.data !== "string") {
        playPcmChunk(event.data);
        return;
      }

      let message;
      try { message = JSON.parse(event.data); } catch { return; }

      switch (message.type) {
        case "ready":
          setStatus("Live — just talk");
          setOrb("listening");
          break;
        case "speech_started":
          setStatus("Listening…");
          setOrb("hearing");
          break;
        case "interim":
          els.transcript.textContent = message.text || "";
          break;
        case "user_final":
          els.transcript.textContent = message.text || "";
          setStatus(`${selectedNomi.name} is thinking…`);
          setOrb("thinking");
          break;
        case "thinking":
          setStatus(`${selectedNomi.name} is thinking…`);
          setOrb("thinking");
          break;
        case "assistant_text":
          if (!message.interrupted) els.transcript.textContent = message.text || "";
          break;
        case "speaking":
          acceptAudio = true;
          playbackCursor = audioContext.currentTime + 0.025;
          setStatus(`${selectedNomi.name} is speaking`);
          setOrb("speaking");
          break;
        case "barge_in":
          stopPlayback();
          setStatus("Listening…");
          setOrb("hearing");
          break;
        case "turn_complete":
          if (!playbackNodes.size) {
            setStatus("Live — just talk");
            setOrb("listening");
          }
          break;
        case "error":
          showNotice(message.message || "Live call error");
          setStatus("Call error");
          setOrb("error");
          break;
      }
    };

    socket.onerror = () => {
      showNotice("The live connection hit an error.");
    };

    socket.onclose = () => {
      if (callActive) endCall(false);
    };
  } catch (error) {
    showNotice(`Could not start live audio: ${error.message}`);
    await endCall(false);
  } finally {
    els.call.disabled = false;
  }
}

async function endCall(sendStop = true) {
  stopPlayback();
  if (sendStop && socket?.readyState === WebSocket.OPEN) {
    socket.send(JSON.stringify({ type: "stop" }));
  }
  try { socket?.close(); } catch {}
  socket = null;

  workletNode?.disconnect();
  sourceNode?.disconnect();
  muteNode?.disconnect();
  mediaStream?.getTracks().forEach((track) => track.stop());
  mediaStream = null;
  workletNode = null;
  sourceNode = null;
  muteNode = null;
  pendingPcm = [];

  if (audioContext) {
    try { await audioContext.close(); } catch {}
  }
  audioContext = null;

  callActive = false;
  acceptAudio = false;
  els.select.disabled = false;
  els.call.classList.remove("active");
  els.callLabel.textContent = "Start live call";
  setOrb("idle");
  setStatus("Ready for a live call");
}

els.call.addEventListener("click", async () => {
  if (callActive) await endCall(true);
  else await startCall();
});

window.addEventListener("beforeunload", () => {
  if (socket?.readyState === WebSocket.OPEN) socket.close();
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js?v=2").catch(() => {}));
}

boot();
