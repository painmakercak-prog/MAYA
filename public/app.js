const $ = (id) => document.getElementById(id);
const els = {
  avatar: $("avatar"),
  name: $("name"),
  status: $("statusText"),
  select: $("nomiSelect"),
  messages: $("messages"),
  notice: $("setupNotice"),
  mic: $("micButton"),
  hint: $("hint"),
  form: $("textForm"),
  input: $("textInput"),
  player: $("voicePlayer"),
  play: $("playButton"),
};

let nomis = [];
let selectedNomi = null;
let recorder = null;
let mediaStream = null;
let chunks = [];
let busy = false;
let pendingAudioUrl = null;

function setState(label, hint = label) {
  els.status.textContent = label;
  els.hint.textContent = hint;
}

function showNotice(text) {
  els.notice.textContent = text;
  els.notice.classList.toggle("hidden", !text);
}

function clearEmpty() {
  const empty = els.messages.querySelector(".empty-state");
  if (empty) empty.remove();
}

function addMessage(role, text) {
  clearEmpty();
  const bubble = document.createElement("div");
  bubble.className = `bubble ${role === "user" ? "user" : "nomi"}`;
  bubble.textContent = text;
  els.messages.appendChild(bubble);
  els.messages.scrollTop = els.messages.scrollHeight;
}

async function jsonFetch(url, options = {}) {
  const response = await fetch(url, options);
  const type = response.headers.get("content-type") || "";
  const body = type.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof body === "object" ? body.detail ?? body : body;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return body;
}

function selectedId() {
  return selectedNomi?.uuid || null;
}

function applyNomi(nomi) {
  selectedNomi = nomi;
  if (!nomi) return;
  localStorage.setItem("maya-nomi-id", nomi.uuid);
  els.name.textContent = nomi.name;
  els.avatar.src = `/api/nomis/${encodeURIComponent(nomi.uuid)}/avatar`;
  els.avatar.onerror = () => { els.avatar.removeAttribute("src"); };
  setState("Ready", "Tap to talk");
}

async function boot() {
  try {
    const status = await jsonFetch("/api/status");
    const missing = Object.entries(status.configured).filter(([, ok]) => !ok).map(([name]) => name);
    if (missing.length) showNotice(`Server setup incomplete: ${missing.join(", ")}. Add the missing Railway environment variables.`);

    const data = await jsonFetch("/api/nomis");
    nomis = data.nomis || [];
    els.select.innerHTML = "";

    if (!nomis.length) {
      setState("No Nomis found", "Check the Nomi account tied to this API key");
      return;
    }

    for (const nomi of nomis) {
      const option = document.createElement("option");
      option.value = nomi.uuid;
      option.textContent = nomi.name;
      els.select.appendChild(option);
    }

    const remembered = localStorage.getItem("maya-nomi-id");
    const initial = nomis.find((n) => n.uuid === remembered) || nomis[0];
    els.select.value = initial.uuid;
    applyNomi(initial);
  } catch (error) {
    showNotice(error.message);
    setState("Setup needed", "Add a fresh Nomi API key on the server");
  }
}

els.select.addEventListener("change", () => {
  const nomi = nomis.find((n) => n.uuid === els.select.value);
  applyNomi(nomi);
});

function bestMimeType() {
  const choices = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"];
  return choices.find((type) => window.MediaRecorder?.isTypeSupported?.(type)) || "";
}

function extensionFor(type) {
  if (type.includes("mp4")) return "m4a";
  if (type.includes("webm")) return "webm";
  return "audio";
}

function stopPlayback() {
  els.player.pause();
  els.player.currentTime = 0;
  els.play.classList.add("hidden");
}

async function startRecording() {
  if (busy || recorder?.state === "recording") return;
  if (!selectedId()) {
    showNotice("Choose a Nomi first.");
    return;
  }

  stopPlayback();
  showNotice("");

  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
    });
    const mimeType = bestMimeType();
    recorder = mimeType ? new MediaRecorder(mediaStream, { mimeType }) : new MediaRecorder(mediaStream);
    chunks = [];

    recorder.addEventListener("dataavailable", (event) => {
      if (event.data?.size) chunks.push(event.data);
    });

    recorder.addEventListener("stop", async () => {
      const type = recorder.mimeType || mimeType || "audio/webm";
      const blob = new Blob(chunks, { type });
      mediaStream?.getTracks().forEach((track) => track.stop());
      mediaStream = null;
      recorder = null;
      els.mic.classList.remove("recording");
      els.mic.setAttribute("aria-label", "Start recording");
      await handleAudio(blob, type);
    });

    recorder.start();
    els.mic.classList.add("recording");
    els.mic.setAttribute("aria-label", "Stop recording");
    setState("Listening…", "Tap again when you're done");
  } catch (error) {
    showNotice(`Microphone error: ${error.message}`);
    setState("Ready", "Tap to talk");
  }
}

function stopRecording() {
  if (recorder?.state === "recording") recorder.stop();
}

els.mic.addEventListener("click", () => {
  if (recorder?.state === "recording") stopRecording();
  else startRecording();
});

async function handleAudio(blob, type) {
  if (!blob.size) {
    setState("Ready", "Tap to talk");
    return;
  }
  busy = true;
  els.mic.disabled = true;
  try {
    setState("Transcribing…", "Turning your voice into text");
    const form = new FormData();
    form.append("audio", blob, `voice.${extensionFor(type)}`);
    const transcribed = await jsonFetch("/api/transcribe", { method: "POST", body: form });
    addMessage("user", transcribed.transcript);
    await sendToNomi(transcribed.transcript);
  } catch (error) {
    showNotice(error.message);
    setState("Ready", "Tap to talk");
  } finally {
    busy = false;
    els.mic.disabled = false;
  }
}

async function sendToNomi(text) {
  if (!selectedId()) throw new Error("No Nomi selected");
  setState(`${selectedNomi.name} is thinking…`, "Waiting for a reply");

  const data = await jsonFetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nomiId: selectedId(), messageText: text }),
  });

  const reply = data.replyMessage?.text?.trim();
  if (!reply) throw new Error("Nomi returned an empty reply");
  addMessage("nomi", reply);
  await speak(reply);
}

async function speak(text) {
  setState("Speaking…", "Playing the reply");
  els.play.classList.add("hidden");

  const response = await fetch("/api/speak", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text }),
  });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    showNotice(`Reply received, but voice synthesis failed: ${JSON.stringify(body.detail || body)}`);
    setState("Ready", "Tap to talk");
    return;
  }

  const blob = await response.blob();
  if (pendingAudioUrl) URL.revokeObjectURL(pendingAudioUrl);
  pendingAudioUrl = URL.createObjectURL(blob);
  els.player.src = pendingAudioUrl;

  els.player.onended = () => setState("Ready", "Tap to talk");
  els.player.onerror = () => {
    els.play.classList.remove("hidden");
    setState("Reply ready", "Tap Play reply");
  };

  try {
    await els.player.play();
  } catch {
    els.play.classList.remove("hidden");
    setState("Reply ready", "Tap Play reply");
  }
}

els.play.addEventListener("click", async () => {
  try {
    await els.player.play();
    els.play.classList.add("hidden");
    setState("Speaking…", "Playing the reply");
  } catch (error) {
    showNotice(`Playback error: ${error.message}`);
  }
});

els.form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const text = els.input.value.trim();
  if (!text || busy) return;
  stopPlayback();
  showNotice("");
  busy = true;
  els.mic.disabled = true;
  els.input.disabled = true;
  try {
    addMessage("user", text);
    els.input.value = "";
    await sendToNomi(text);
  } catch (error) {
    showNotice(error.message);
    setState("Ready", "Tap to talk");
  } finally {
    busy = false;
    els.mic.disabled = false;
    els.input.disabled = false;
    els.input.focus();
  }
});

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => navigator.serviceWorker.register("/sw.js").catch(() => {}));
}

boot();
