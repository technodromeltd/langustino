import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Mic, Player, b64ToUrl } from "./audio";
import type { Lesson, Msg, Progress, ServerState, Teach, TutorMsg, UserMsg } from "./types";

const HOLD_MS = 350; // a press longer than this is push-to-talk; shorter toggles recording on/off

export function App() {
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [model, setModel] = useState("");
  const [connected, setConnected] = useState(false);
  const [lesson, setLesson] = useState<Lesson | null>(null);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [server, setServer] = useState<ServerState>("idle");
  const [recording, setRecording] = useState(false);
  const [playingKey, setPlayingKey] = useState<string | null>(null);
  const [progress, setProgress] = useState<Progress | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [slow, setSlow] = useState(false);
  const [handsFree, setHandsFree] = useState(false);
  const [typing, setTyping] = useState(false);
  const [draft, setDraft] = useState("");
  const [micError, setMicError] = useState<string | null>(null);

  const ws = useRef<WebSocket | null>(null);
  const player = useMemo(() => new Player(), []);
  const mutedTurn = useRef<string | null>(null);
  const lastTutorId = useRef<string | null>(null);
  const level = useRef(0);
  const micBtn = useRef<HTMLButtonElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const pressAt = useRef(0);
  const pressWasRecording = useRef(false);

  const flash = useCallback((t: string) => {
    setToast(t);
    window.setTimeout(() => setToast((cur) => (cur === t ? null : cur)), 3500);
  }, []);

  const send = useCallback((data: object | ArrayBuffer) => {
    const s = ws.current;
    if (!s || s.readyState !== WebSocket.OPEN) return false;
    s.send(data instanceof ArrayBuffer ? data : JSON.stringify(data));
    return true;
  }, []);

  const mic = useMemo(
    () =>
      new Mic({
        onLevel: (rms) => (level.current = rms),
        onUtterance: (pcm) => {
          if (!send(pcm)) flash("Not connected to the voice server");
        },
        onVadSpeech: (speaking) => {
          setRecording(speaking);
          if (speaking) {
            player.stop();
            mutedTurn.current = lastTutorId.current;
          }
        },
      }),
    [send, flash, player],
  );

  // ---- websocket ----
  useEffect(() => {
    let closed = false;
    let retry: number | undefined;
    const connect = () => {
      const s = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
      ws.current = s;
      s.onopen = () => setConnected(true);
      s.onclose = () => {
        setConnected(false);
        setServer("idle");
        if (!closed) retry = window.setTimeout(connect, 1500);
      };
      s.onmessage = (e) => handle(JSON.parse(e.data));
    };
    connect();
    return () => {
      closed = true;
      window.clearTimeout(retry);
      ws.current?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const updateUser = (id: string, f: (m: UserMsg) => UserMsg) =>
    setMsgs((ms) => ms.map((m) => (m.kind === "user" && m.id === id ? f(m) : m)));
  const updateTutor = (id: string, f: (m: TutorMsg) => TutorMsg) =>
    setMsgs((ms) => {
      if (!ms.some((m) => m.kind === "tutor" && m.id === id))
        ms = [...ms, { kind: "tutor", id, text: "", sentences: [], done: false }];
      return ms.map((m) => (m.kind === "tutor" && m.id === id ? f(m) : m));
    });

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function handle(m: any) {
    switch (m.type) {
      case "ready":
        setLessons(m.lessons);
        setModel(m.model);
        break;
      case "status":
        setServer(m.state);
        break;
      case "user":
        setMsgs((ms) => [
          ...ms,
          { kind: "user", id: m.id, text: m.text, english: m.english, mistakes: [], teach: [], words: {}, analyzed: false },
        ]);
        break;
      case "feedback":
        updateUser(m.id, (u) => ({
          ...u,
          mistakes: m.mistakes,
          teach: m.teach.length || !m.partial ? m.teach : u.teach,
          words: m.partial ? u.words : m.words,
          analyzed: true,
        }));
        break;
      case "tutor_delta":
        lastTutorId.current = m.id;
        updateTutor(m.id, (t) => ({ ...t, text: t.text + m.text }));
        break;
      case "audio": {
        const clip = { key: `${m.id}:${m.seq}`, text: m.text, url: b64ToUrl(m.wav) };
        updateTutor(m.id, (t) => ({ ...t, sentences: [...t.sentences, clip] }));
        if (mutedTurn.current !== m.id) player.enqueue(clip);
        break;
      }
      case "tutor_done":
        updateTutor(m.id, (t) => ({ ...t, text: m.reply || t.text, en: m.reply_en, done: true, firstAudioMs: m.timing?.first_audio_ms }));
        break;
      case "progress":
        setProgress(m);
        break;
      case "ignored":
        flash("I didn't catch that — try again, a bit louder or longer.");
        break;
      case "error":
        flash(`Error: ${m.message}`);
        break;
    }
  }

  // ---- audio state wiring ----
  useEffect(() => {
    player.onChange = setPlayingKey;
  }, [player]);
  useEffect(() => {
    player.rate = slow ? 0.8 : 1;
  }, [slow, player]);
  useEffect(() => {
    mic.handsFree = handsFree;
    if (!handsFree) mic.resetVad();
  }, [handsFree, mic]);
  useEffect(() => {
    // Half duplex: hands-free listening pauses while Sofía is thinking or speaking.
    mic.paused = !!playingKey || server !== "idle";
    if (mic.paused) mic.resetVad();
  }, [playingKey, server, mic]);

  // Mic level meter, outside React renders.
  useEffect(() => {
    let raf = 0;
    const tick = () => {
      micBtn.current?.style.setProperty("--level", String(Math.min(1, level.current * 12)));
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [msgs]);

  // ---- talking ----
  const ensureMic = useCallback(async () => {
    if (mic.ready) return true;
    try {
      await mic.init();
      setMicError(null);
      return true;
    } catch (e) {
      setMicError("Microphone blocked. Allow mic access for this page (or use the keyboard button to type).");
      return false;
    }
  }, [mic]);

  const startTalk = useCallback(async () => {
    if (!(await ensureMic())) return;
    if (player.playing || server !== "idle") {
      // Barge in: stop Sofía and drop the rest of her current reply.
      player.stop();
      mutedTurn.current = lastTutorId.current;
      if (server !== "idle") send({ type: "cancel" });
    }
    mic.start();
    setRecording(true);
  }, [ensureMic, player, server, send, mic]);

  const stopTalk = useCallback(() => {
    if (!mic.isRecording) return;
    mic.stop();
    setRecording(false);
  }, [mic]);

  useEffect(() => {
    const isField = (t: EventTarget | null) => t instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName);
    const down = (e: KeyboardEvent) => {
      if (!lesson || isField(e.target)) return;
      if (e.code === "Space") {
        e.preventDefault();
        if (!e.repeat) void startTalk();
      } else if (e.code === "Escape") player.stop();
    };
    const up = (e: KeyboardEvent) => {
      if (e.code === "Space" && lesson && !isField(e.target)) {
        e.preventDefault();
        stopTalk();
      }
    };
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    return () => {
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
    };
  }, [lesson, startTalk, stopTalk, player]);

  const onPressStart = () => {
    pressAt.current = Date.now();
    pressWasRecording.current = recording;
    if (recording) stopTalk();
    else void startTalk();
  };
  const onPressEnd = () => {
    if (!pressWasRecording.current && Date.now() - pressAt.current > HOLD_MS) stopTalk();
  };

  const startLesson = (l: Lesson) => {
    void ensureMic(); // ask for mic permission now, but don't block the lesson on the prompt
    player.stop();
    mic.abort();
    setRecording(false);
    mutedTurn.current = null;
    setMsgs([]);
    setLesson(l);
    send({ type: "start", lesson: l.id });
  };

  const askHelp = () => {
    player.stop();
    mutedTurn.current = null;
    send({ type: "help" });
    setMsgs((ms) => [...ms, { kind: "note", id: `n${Date.now()}`, text: "You asked for help" }]);
  };

  const sendTyped = (e: React.FormEvent) => {
    e.preventDefault();
    const t = draft.trim();
    if (!t) return;
    player.stop();
    mutedTurn.current = lastTutorId.current;
    send({ type: "text", text: t });
    setDraft("");
  };

  // ---- derived ----
  const sessionWords: Teach[] = msgs.flatMap((m) => (m.kind === "user" ? m.teach : []));
  const sessionFixes = msgs.flatMap((m) => (m.kind === "user" ? m.mistakes : []));
  const saidOk = new Set(msgs.flatMap((m) => (m.kind === "user" ? Object.entries(m.words).filter(([, v]) => v === "ok").map(([k]) => k) : [])));

  const status = recording
    ? handsFree
      ? "Listening…"
      : "Listening… release to send"
    : server === "transcribing"
      ? "Transcribing…"
      : server === "thinking" && !playingKey
        ? "Sofía is thinking…"
        : playingKey
          ? "Sofía is speaking · press Space to interrupt"
          : handsFree
            ? "Your turn — just speak"
            : "Your turn — hold Space (or the mic) and speak";

  if (!lesson) {
    return (
      <div className="start">
        <div className="start-inner">
          <div className="brand">
            <span className="logo">¡h!</span> Habla
          </div>
          <h1>Talk Spanish with Sofía</h1>
          <p className="lede">
            A patient tutor you speak with out loud. Say what you can — mix in English when you're stuck, and Sofía will
            teach you the word. Everything runs locally on this Mac.
          </p>
          {!connected && <p className="warn">Connecting to the voice server… (is it running on :8777?)</p>}
          <div className="lesson-grid">
            {lessons.map((l) => (
              <button key={l.id} className="lesson-card" onClick={() => startLesson(l)} disabled={!connected}>
                <span className="lesson-es">{l.title_es}</span>
                <span className="lesson-en">{l.title}</span>
                <span className="lesson-goal">{l.goal}</span>
              </button>
            ))}
          </div>
          {progress && progress.known + progress.learning > 0 && (
            <p className="muted small">
              You can already say {progress.known} of the {progress.total} most common words · {progress.learning} in progress
            </p>
          )}
          {micError && <p className="warn">{micError}</p>}
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="logo">¡h!</span> Habla
        </div>
        <select
          className="lesson-select"
          value={lesson.id}
          onChange={(e) => {
            const l = lessons.find((x) => x.id === e.target.value);
            if (l) startLesson(l);
          }}
        >
          {lessons.map((l) => (
            <option key={l.id} value={l.id}>
              {l.title_es} — {l.title}
            </option>
          ))}
        </select>
        <div className="spacer" />
        {progress && (
          <div className="progress-pill" title={`${progress.learning} words in progress`}>
            <div className="bar">
              <div style={{ width: `${(100 * progress.known) / progress.total}%` }} />
            </div>
            <span>
              <b>{progress.known}</b> / {progress.total} words
            </span>
          </div>
        )}
        <span className={`dot ${connected ? "on" : ""}`} title={connected ? `Connected · ${model}` : "Disconnected"} />
      </header>

      <main className="main">
        <section className="convo" ref={scroller}>
          <div className="convo-inner">
            <div className="lesson-banner">
              <b>{lesson.title_es}</b> · {lesson.goal}
            </div>
            {msgs.map((m) =>
              m.kind === "tutor" ? (
                <TutorBubble key={m.id} m={m} playingKey={playingKey} player={player} />
              ) : m.kind === "user" ? (
                <UserBubble key={m.id} m={m} />
              ) : (
                <div key={m.id} className="note">
                  {m.text}
                </div>
              ),
            )}
            {server === "transcribing" && <div className="bubble user pending">…</div>}
            {server === "thinking" && !msgs.some((m) => m.kind === "tutor" && !m.done) && (
              <div className="row tutor">
                <div className="avatar">S</div>
                <div className="bubble tutor pending">
                  <span className="dots">
                    <i />
                    <i />
                    <i />
                  </span>
                </div>
              </div>
            )}
          </div>
        </section>

        <aside className="side">
          <div className="card">
            <h3>This session</h3>
            <div className="stats">
              <div>
                <b>{saidOk.size}</b>
                <span>words said right</span>
              </div>
              <div>
                <b>{sessionWords.length}</b>
                <span>new words</span>
              </div>
              <div>
                <b>{sessionFixes.length}</b>
                <span>fixes</span>
              </div>
            </div>
          </div>
          {sessionWords.length > 0 && (
            <div className="card">
              <h3>Words to remember</h3>
              <ul className="wordlist">
                {sessionWords.map((w, i) => (
                  <li key={i}>
                    <b>{w.spanish}</b>
                    <span>{w.english}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {sessionFixes.length > 0 && (
            <div className="card">
              <h3>Fixes</h3>
              <ul className="fixlist">
                {sessionFixes.map((f, i) => (
                  <li key={i}>
                    <span className="strike">{f.wrong}</span> → <b>{f.right}</b>
                    <small>{f.why}</small>
                  </li>
                ))}
              </ul>
            </div>
          )}
          {progress && progress.recent.length > 0 && (
            <div className="card">
              <h3>Your vocabulary</h3>
              <p className="muted small">
                {progress.known} known · {progress.learning} learning · {progress.seen} heard
              </p>
              <div className="chips">
                {progress.recent.map((w) => (
                  <span key={w.lemma} className={`chip ${w.status}`} title={`${w.translation ?? ""} · said ${w.produced}× · ${w.errors} errors`}>
                    {w.lemma}
                  </span>
                ))}
              </div>
            </div>
          )}
        </aside>
      </main>

      <footer className="dock">
        {toast && <div className="toast">{toast}</div>}
        <div className={`status ${micError && !recording ? "warn" : ""}`}>{micError && !recording ? micError : status}</div>
        <div className="controls">
          <div className="left-controls">
            <button className={`toggle ${slow ? "on" : ""}`} onClick={() => setSlow((s) => !s)} title="Play Sofía slower">
              🐢 Slower
            </button>
            <button
              className={`toggle ${handsFree ? "on" : ""}`}
              onClick={() => {
                void ensureMic();
                setHandsFree((h) => !h);
              }}
              title="Hands-free: talk without pressing anything (best with headphones)"
            >
              ∞ Hands-free
            </button>
          </div>
          <button
            ref={micBtn}
            className={`mic ${recording ? "rec" : ""} ${playingKey ? "speaking" : ""}`}
            onPointerDown={onPressStart}
            onPointerUp={onPressEnd}
            onPointerLeave={() => recording && Date.now() - pressAt.current > HOLD_MS && !pressWasRecording.current && stopTalk()}
            aria-label="Talk"
          >
            <svg viewBox="0 0 24 24" width="34" height="34" aria-hidden>
              <path
                fill="currentColor"
                d="M12 14a3 3 0 0 0 3-3V5a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11h-2Z"
              />
            </svg>
          </button>
          <div className="right-controls">
            <button className="toggle" onClick={askHelp} disabled={server !== "idle"} title="Sofía suggests something you could say">
              🛟 ¿Qué digo?
            </button>
            <button className={`toggle ${typing ? "on" : ""}`} onClick={() => setTyping((t) => !t)} title="Type instead">
              ⌨︎ Type
            </button>
          </div>
        </div>
        {typing && (
          <form className="typebar" onSubmit={sendTyped}>
            <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} placeholder="Escribe en español (or English)…" />
            <button type="submit">Send</button>
          </form>
        )}
      </footer>
    </div>
  );
}

function TutorBubble({ m, playingKey, player }: { m: TutorMsg; playingKey: string | null; player: Player }) {
  const [showEn, setShowEn] = useState(false);
  // Sentences already voiced, then any streamed text that has no audio yet.
  let rest = m.text;
  for (const s of m.sentences) {
    const i = rest.indexOf(s.text);
    if (i >= 0) rest = rest.slice(i + s.text.length);
  }
  const clips = m.sentences.map(({ key, url }) => ({ key, url }));
  return (
    <div className="row tutor">
      <div className="avatar">S</div>
      <div className="bubble tutor">
        <div className="es">
          {m.sentences.map((s) => (
            <span
              key={s.key}
              className={`sent ${playingKey === s.key ? "speaking" : ""}`}
              onClick={() => player.playNow([{ key: s.key, url: s.url }])}
              title="Play this sentence"
            >
              {s.text}{" "}
            </span>
          ))}
          {rest && <span className="unspoken">{rest}</span>}
        </div>
        {showEn && m.en && <div className="en">{m.en}</div>}
        {m.done && (
          <div className="actions">
            <button onClick={() => player.playNow(clips)} title="Replay">
              ↻ Replay
            </button>
            <button onClick={() => player.playNow(clips, 0.7)} title="Replay slowly">
              🐢 Slow
            </button>
            {m.en && (
              <button className={showEn ? "on" : ""} onClick={() => setShowEn((s) => !s)}>
                EN
              </button>
            )}
            {m.firstAudioMs != null && <span className="latency" title="Time from the end of your speech to Sofía's first audio">{(m.firstAudioMs / 1000).toFixed(1)}s</span>}
          </div>
        )}
      </div>
    </div>
  );
}

function UserBubble({ m }: { m: UserMsg }) {
  return (
    <div className="row user">
      <div className="user-col">
        <div className="bubble user">{highlight(m.text, m.english, m.mistakes.map((x) => x.wrong))}</div>
        {(m.teach.length > 0 || m.mistakes.length > 0) && (
          <div className="cards">
            {m.teach.map((t, i) => (
              <div key={`t${i}`} className="fb teach">
                <span className="tag">New word</span>
                <b>{t.spanish}</b>
                <span className="muted">{t.english}</span>
              </div>
            ))}
            {m.mistakes.map((x, i) => (
              <div key={`m${i}`} className="fb fix">
                <span className="tag">Fix</span>
                <span className="strike">{x.wrong}</span> → <b>{x.right}</b>
                <small>{x.why}</small>
              </div>
            ))}
          </div>
        )}
        {!m.analyzed && <div className="checking">checking…</div>}
        {m.analyzed && m.teach.length === 0 && m.mistakes.length === 0 && <div className="checking ok">✓ Bien dicho</div>}
      </div>
    </div>
  );
}

/** Marks English spans (amber) and mistaken fragments (red underline) inside the transcript. */
function highlight(text: string, english: string[], wrong: string[]) {
  type R = { start: number; end: number; cls: string };
  const ranges: R[] = [];
  const lower = text.toLowerCase();
  const add = (frag: string, cls: string) => {
    const f = frag.trim().toLowerCase();
    if (!f) return;
    const i = lower.indexOf(f);
    if (i >= 0 && !ranges.some((r) => i < r.end && i + f.length > r.start)) ranges.push({ start: i, end: i + f.length, cls });
  };
  english.forEach((e) => add(e, "en-word"));
  wrong.forEach((w) => add(w, "wrong"));
  ranges.sort((a, b) => a.start - b.start);
  const out: React.ReactNode[] = [];
  let pos = 0;
  ranges.forEach((r, i) => {
    if (r.start > pos) out.push(text.slice(pos, r.start));
    out.push(
      <span key={i} className={r.cls}>
        {text.slice(r.start, r.end)}
      </span>,
    );
    pos = r.end;
  });
  out.push(text.slice(pos));
  return out;
}
