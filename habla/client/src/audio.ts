// Microphone capture (push-to-talk + optional hands-free VAD) and sequential audio playback.

const TARGET_RATE = 16000;
const PREROLL_MS = 350; // audio kept from just before the key press, so first syllables aren't clipped
const POSTROLL_MS = 250; // keep recording briefly after release: people let go early
const PAUSE_MS = 600; // hands-free: a pause this long mid-utterance shows "still listening"

export type MicEvents = {
  onLevel: (rms: number) => void;
  onUtterance: (pcm16: ArrayBuffer, seconds: number) => void;
  onVadSpeech?: (speaking: boolean) => void;
  onAutoStop?: () => void;
  /** Hands-free: the learner went quiet mid-utterance (true) or started talking again / finished (false). */
  onPause?: (pausing: boolean) => void;
};

export class Mic {
  private ctx?: AudioContext;
  private stream?: MediaStream;
  private node?: AudioWorkletNode;
  private ring: Float32Array[] = [];
  private ringLen = 0;
  private chunks: Float32Array[] = [];
  private recording = false;
  private stopTimer?: number;
  // hands-free
  handsFree = false;
  paused = false; // VAD ignores input while the tutor is speaking (half duplex, avoids hearing itself)
  endSilenceMs = 900; // quiet that ends a hands-free utterance (longer for beginners, who pause to find a word)
  lastPcm: Int16Array | null = null; // the last utterance sent as a turn (16 kHz int16): a continuation extends it
  private vadSpeaking = false;
  private vadPausing = false;
  private vadLoud = 0;
  private vadQuiet = 0;
  private noise = 0.004;
  // auto-stop (attempts): finish by itself after the learner stops talking
  private autoStop = false;
  private heardSpeech = false;
  private quietMs = 0;
  private recMs = 0;

  constructor(private ev: MicEvents) {}

  get ready() {
    return !!this.node;
  }
  get isRecording() {
    return this.recording;
  }
  /** Recording an attempt that stops by itself (a key release must not stop it). */
  get autoStopping() {
    return this.recording && this.autoStop;
  }

  async init() {
    if (this.node) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    this.ctx = new AudioContext();
    sharedCtx = this.ctx; // the chime reuses it
    await this.ctx.audioWorklet.addModule("/recorder-worklet.js");
    const src = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, "recorder");
    this.node.port.onmessage = (e: MessageEvent<Float32Array>) => this.onFrame(e.data);
    src.connect(this.node);
  }

  private onFrame(f: Float32Array) {
    let sum = 0;
    for (let i = 0; i < f.length; i++) sum += f[i] * f[i];
    const rms = Math.sqrt(sum / f.length);
    this.ev.onLevel(rms);

    if (this.recording) {
      this.chunks.push(f);
      if (this.autoStop) this.checkAutoStop(rms, f.length);
    } else {
      this.ring.push(f);
      this.ringLen += f.length;
      const max = ((this.ctx?.sampleRate ?? 48000) * PREROLL_MS) / 1000;
      while (this.ringLen - this.ring[0].length > max) this.ringLen -= this.ring.shift()!.length;
    }
    if (this.handsFree && !this.paused) this.vad(rms, f.length);
  }

  // Simple energy VAD with an adaptive noise floor. Good enough with a headset / quiet room.
  private vad(rms: number, n: number) {
    const ms = (n / (this.ctx?.sampleRate ?? 48000)) * 1000;
    if (!this.vadSpeaking) this.noise = this.noise * 0.995 + Math.min(rms, 0.05) * 0.005;
    const thresh = Math.max(0.012, this.noise * 3);
    if (rms > thresh) {
      this.vadLoud += ms;
      this.vadQuiet = 0;
      this.setPausing(false);
    } else {
      this.vadQuiet += ms;
      if (!this.vadSpeaking) this.vadLoud = Math.max(0, this.vadLoud - ms);
    }
    if (!this.vadSpeaking && this.vadLoud > 120) {
      this.vadSpeaking = true;
      this.ev.onVadSpeech?.(true);
      this.start();
    } else if (this.vadSpeaking && this.vadQuiet > this.endSilenceMs) {
      this.vadSpeaking = false;
      this.vadLoud = 0;
      this.setPausing(false);
      this.ev.onVadSpeech?.(false);
      this.stop(0);
    } else if (this.vadSpeaking && this.vadQuiet > PAUSE_MS) this.setPausing(true);
  }

  private setPausing(p: boolean) {
    if (p === this.vadPausing) return;
    this.vadPausing = p;
    this.ev.onPause?.(p);
  }

  private checkAutoStop(rms: number, n: number) {
    const ms = (n / (this.ctx?.sampleRate ?? 48000)) * 1000;
    this.recMs += ms;
    const loud = rms > Math.max(0.012, this.noise * 3);
    if (loud) {
      this.heardSpeech = true;
      this.quietMs = 0;
    } else this.quietMs += ms;
    // Stop after ~0.9 s of quiet once they've said something, or after 4 s of nothing, or at 15 s.
    if ((this.heardSpeech && this.quietMs > 900) || (!this.heardSpeech && this.recMs > 4000) || this.recMs > 15000) {
      this.autoStop = false;
      this.ev.onAutoStop?.();
      this.stop(0);
    }
  }

  resetVad() {
    this.setPausing(false);
    if (this.vadSpeaking) {
      this.vadSpeaking = false;
      this.ev.onVadSpeech?.(false);
    }
    this.vadLoud = this.vadQuiet = 0;
  }

  start(autoStop = false) {
    if (!this.ctx || this.recording) return;
    this.autoStop = autoStop;
    this.heardSpeech = false;
    this.quietMs = this.recMs = 0;
    window.clearTimeout(this.stopTimer);
    void this.ctx.resume();
    this.chunks = [...this.ring];
    this.ring = [];
    this.ringLen = 0;
    this.recording = true;
  }

  /** Stops after a short post-roll and emits the utterance. */
  stop(postroll = POSTROLL_MS) {
    if (!this.recording) return;
    window.clearTimeout(this.stopTimer);
    this.stopTimer = window.setTimeout(() => void this.finish(), postroll);
  }

  /** Discard the current recording (e.g. it was too short). Nothing is emitted. */
  abort() {
    window.clearTimeout(this.stopTimer);
    this.recording = false;
    this.autoStop = false;
    this.chunks = [];
  }

  private async finish() {
    this.recording = false;
    const chunks = this.chunks;
    this.chunks = [];
    const rate = this.ctx!.sampleRate;
    const total = chunks.reduce((n, c) => n + c.length, 0);
    if (total < rate * 0.3) return;
    const mono = new Float32Array(total);
    let o = 0;
    for (const c of chunks) {
      mono.set(c, o);
      o += c.length;
    }
    const pcm = await resample(mono, rate);
    this.ev.onUtterance(pcm.buffer as ArrayBuffer, total / rate);
  }
}

let sharedCtx: AudioContext | undefined; // the Mic's context once it exists, or one made for the chime

/** A soft two-note chime (no asset files): a target word was used. Never throws; silent if audio is unavailable. */
export function chime() {
  try {
    if (!sharedCtx || sharedCtx.state === "closed") {
      const Ctx = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!Ctx) return;
      sharedCtx = new Ctx();
    }
    const ctx = sharedCtx;
    if (ctx.state === "suspended") void ctx.resume().catch(() => {});
    const t0 = ctx.currentTime + 0.01;
    // Two short notes, a fifth apart: 0–65 ms and 65–140 ms, peak gain 0.06.
    [
      [659.25, 0],
      [987.77, 0.065],
    ].forEach(([freq, at]) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = "sine";
      osc.frequency.value = freq;
      const start = t0 + at;
      gain.gain.setValueAtTime(0, start);
      gain.gain.linearRampToValueAtTime(0.06, start + 0.008);
      gain.gain.exponentialRampToValueAtTime(0.0005, start + 0.075);
      osc.connect(gain).connect(ctx.destination);
      osc.start(start);
      osc.stop(start + 0.075);
    });
  } catch {
    /* no audio: the toast is enough */
  }
}

/** One int16 PCM buffer after another (same sample format): a continuation is the last utterance plus the rest. */
export function concatPcm(a: Int16Array, b: Int16Array): Int16Array {
  const out = new Int16Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

async function resample(input: Float32Array, rate: number): Promise<Int16Array> {
  const frames = Math.ceil((input.length * TARGET_RATE) / rate);
  const off = new OfflineAudioContext(1, frames, TARGET_RATE);
  const buf = off.createBuffer(1, input.length, rate);
  buf.copyToChannel(input as Float32Array<ArrayBuffer>, 0);
  const src = off.createBufferSource();
  src.buffer = buf;
  src.connect(off.destination);
  src.start();
  const out = (await off.startRendering()).getChannelData(0);
  const pcm = new Int16Array(out.length);
  for (let i = 0; i < out.length; i++) pcm[i] = Math.max(-1, Math.min(1, out[i])) * 0x7fff;
  return pcm;
}

export type Clip = { key: string; url: string };

/** Plays clips one after another. Used for streamed tutor sentences and for replays. */
export class Player {
  private el = new Audio();
  private queue: Clip[] = [];
  private current?: Clip;
  private overrideRate?: number; // replay speed, for the current queue only
  private onEnd?: () => void; // called once when a playNow() queue finishes
  rate = 1;
  onChange: (playingKey: string | null) => void = () => {};

  constructor() {
    this.el.preservesPitch = true;
    this.el.onended = () => this.next();
    this.el.onerror = () => this.next();
  }

  get playing() {
    return !!this.current;
  }

  enqueue(clip: Clip) {
    this.queue.push(clip);
    if (!this.current) {
      this.overrideRate = undefined;
      this.next();
    }
  }

  playNow(clips: Clip[], rate?: number, onEnd?: () => void) {
    this.stop();
    this.queue = [...clips];
    this.overrideRate = rate;
    this.onEnd = onEnd;
    this.next();
  }

  stop() {
    this.onEnd = undefined; // interrupted: don't fire
    this.queue = [];
    this.current = undefined;
    this.el.pause();
    this.onChange(null);
  }

  private next() {
    this.current = this.queue.shift();
    if (!this.current) {
      this.overrideRate = undefined;
      this.onChange(null);
      const done = this.onEnd;
      this.onEnd = undefined;
      done?.();
      return;
    }
    this.el.src = this.current.url;
    this.el.playbackRate = this.overrideRate ?? this.rate;
    this.onChange(this.current.key);
    this.el.play().catch(() => this.next());
  }
}

export function b64ToUrl(b64: string): string {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return URL.createObjectURL(new Blob([bytes], { type: "audio/wav" }));
}
