// Microphone capture (push-to-talk + optional hands-free VAD) and sequential audio playback.

const TARGET_RATE = 16000;
const PREROLL_MS = 350; // audio kept from just before the key press, so first syllables aren't clipped
const POSTROLL_MS = 250; // keep recording briefly after release: people let go early

export type MicEvents = {
  onLevel: (rms: number) => void;
  onUtterance: (pcm16: ArrayBuffer, seconds: number) => void;
  onVadSpeech?: (speaking: boolean) => void;
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
  private vadSpeaking = false;
  private vadLoud = 0;
  private vadQuiet = 0;
  private noise = 0.004;

  constructor(private ev: MicEvents) {}

  get ready() {
    return !!this.node;
  }
  get isRecording() {
    return this.recording;
  }

  async init() {
    if (this.node) return;
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    this.ctx = new AudioContext();
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
    } else {
      this.vadQuiet += ms;
      if (!this.vadSpeaking) this.vadLoud = Math.max(0, this.vadLoud - ms);
    }
    if (!this.vadSpeaking && this.vadLoud > 120) {
      this.vadSpeaking = true;
      this.ev.onVadSpeech?.(true);
      this.start();
    } else if (this.vadSpeaking && this.vadQuiet > 900) {
      this.vadSpeaking = false;
      this.vadLoud = 0;
      this.ev.onVadSpeech?.(false);
      this.stop(0);
    }
  }

  resetVad() {
    if (this.vadSpeaking) {
      this.vadSpeaking = false;
      this.ev.onVadSpeech?.(false);
    }
    this.vadLoud = this.vadQuiet = 0;
  }

  start() {
    if (!this.ctx || this.recording) return;
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

  /** Discard the current recording (e.g. it was too short). */
  abort() {
    window.clearTimeout(this.stopTimer);
    this.recording = false;
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

  playNow(clips: Clip[], rate?: number) {
    this.stop();
    this.queue = [...clips];
    this.overrideRate = rate;
    this.next();
  }

  stop() {
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
