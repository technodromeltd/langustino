import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Mic, Player, b64ToUrl, chime, concatPcm } from "./audio";
import { AttemptButton, ResultLine } from "./AttemptView";
import { Review } from "./Review";
import { SettingsPanel } from "./SettingsPanel";
import { Words } from "./Words";
import { ScenarioSheet, Scenarios } from "./Scenarios";
import { listLabel, MISTAKE_LABEL } from "./types";
import type {
  Attempt,
  AttemptPhase,
  AttemptResult,
  Attempts,
  Lesson,
  LessonState,
  Scenario,
  Msg,
  Phase,
  Progress,
  QA,
  Recap,
  ServerState,
  Settings,
  Target,
  Teach,
  TutorMsg,
  UserMsg,
} from "./types";

const PHASES: { id: Phase; label: string }[] = [
  { id: "warmup", label: "Warm-up" },
  { id: "introduce", label: "New words" },
  { id: "practice", label: "Practice" },
  { id: "recap", label: "Recap" },
];
const STATE_LABEL: Record<Target["state"], string> = {
  pending: "coming up",
  introduced: "introduced — try saying it",
  practiced: "repeated — now use it on your own",
  used: "you used it on your own ✓",
  known: "you already knew it",
};

const KNOWN_TIP = "Content words you've said on your own on different days (small words like de, no, a aren't counted)";

const HOLD_MS = 350; // a press longer than this is push-to-talk; shorter toggles recording on/off

// Hands-free: how long a pause ends what they're saying. Beginners stop mid-sentence to find a word.
const END_SILENCE_MS: Record<string, number> = { A0: 1800, A1: 1500, A2: 1200, B1: 900 };
const END_SILENCE_DEFAULT_MS = 1200;
const CONT_HOLD_MAX_MS = 15000; // safety: stop holding Sofía's audio back if a continuation's turn never shows up

// A continuation (hands-free, talking on before her answer started): "speaking" while recording it, "sent" until
// the server's user message for it (cont == contSeq) arrives. Meanwhile her (cut) answer isn't played.
type Cont = "off" | "speaking" | "sent";

const PULSE_MS = 600; // the green ring on a target chip the learner just used

/** localStorage, never throwing (private window, blocked storage). */
function readStore(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}
function writeStore(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* storage unavailable */
  }
}
const todayKey = () => new Date().toLocaleDateString("en-CA"); // YYYY-MM-DD, local time

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
  const [lessonState, setLessonState] = useState<LessonState | null>(null);
  const [voices, setVoices] = useState<{ id: string; label: string }[]>([]);
  const [voice, setVoice] = useState("");
  const [screen, setScreen] = useState<"home" | "lesson" | "review" | "words">("home");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  // The attempt being recorded ("listening") or graded ("checking"); n tells its result from a left one's.
  const [attemptNow, setAttemptNow] = useState<{ key: string; target: string; n: number; phase: AttemptPhase } | null>(null);
  const [attemptResults, setAttemptResults] = useState<Record<string, AttemptResult>>({});
  const [threads, setThreads] = useState<Record<string, QA[]>>({});
  const threadsRef = useRef(threads);
  threadsRef.current = threads;
  const [pausing, setPausing] = useState(false); // hands-free: quiet mid-utterance, still recording
  // Sofía's answer to the last utterance has started playing (false from sending it until then). Before that,
  // hands-free keeps listening, so a learner who goes on talking continues the same turn.
  const [turnAudioStarted, setTurnAudioStarted] = useState(true);
  const [sounds, setSounds] = useState(() => readStore("habla.sounds") !== "0"); // the "word used" chime
  const [pulse, setPulse] = useState<Set<string>>(() => new Set()); // target chips pulsing right now
  const [sheetOpen, setSheetOpen] = useState(false); // narrow screens: the session panel as a bottom sheet

  const ws = useRef<WebSocket | null>(null);
  const player = useMemo(() => new Player(), []);
  const mutedTurn = useRef<string | null>(null);
  const lastTutorId = useRef<string | null>(null);
  const startedClips = useRef<Record<string, number>>({}); // tutor turn id -> sentences that started playing
  // Tutor turns whose late messages must not (re)create a bubble: cut off before any of them was heard, or
  // from a lesson that's been left. Turn ids are unique per socket.
  const droppedTurns = useRef<Set<string>>(new Set());
  const level = useRef(0);
  const micBtn = useRef<HTMLButtonElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const pressAt = useRef(0);
  const pressWasRecording = useRef(false);
  const attemptRef = useRef<Attempt | null>(null); // the next utterance is a graded attempt, not a turn
  const attemptSeq = useRef(0);
  const activeAttempt = useRef<number | null>(null); // n of the attempt whose result is awaited
  const goalMet = useRef<{ met: boolean; goal: number; day: string } | null>(null);
  // The lesson's targets as of its last lesson_state (lemma -> state), and those already celebrated this lesson.
  const prevTargets = useRef<Map<string, Target["state"]> | null>(null);
  const celebrated = useRef<Set<string>>(new Set());
  const pulseTimer = useRef<number | undefined>(undefined);
  const soundsRef = useRef(sounds);
  // Read by the Mic callbacks (built once): mirrors of state.
  const serverRef = useRef<ServerState>("idle");
  const handsFreeRef = useRef(false);
  const turnAudioRef = useRef(true);
  const sentNoStatus = useRef(false); // an utterance was sent and no status has come since
  const cont = useRef<Cont>("off");
  const contSeq = useRef(0);
  const contTimer = useRef<number | undefined>(undefined);

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

  const setCont = useCallback((c: Cont) => {
    cont.current = c;
    window.clearTimeout(contTimer.current);
    if (c === "sent")
      contTimer.current = window.setTimeout(() => {
        if (cont.current === "sent") cont.current = "off";
      }, CONT_HOLD_MAX_MS);
  }, []);
  const setTurnAudio = useCallback((started: boolean) => {
    turnAudioRef.current = started;
    setTurnAudioStarted(started);
  }, []);

  const mic = useMemo(() => {
    const m: Mic = new Mic({
      onLevel: (rms) => (level.current = rms),
      onUtterance: (pcm) => {
        const a = attemptRef.current;
        attemptRef.current = null;
        if (a) {
          setAttemptNow((cur) => (cur && cur.n === a.n ? { ...cur, phase: "checking" } : cur));
          send({ type: "attempt", ...a });
          if (!send(pcm)) {
            flash("Not connected to the voice server");
            activeAttempt.current = null;
            setAttemptNow(null);
          }
          return;
        }
        const fresh = new Int16Array(pcm);
        const prev = m.lastPcm;
        let ok: boolean;
        if (cont.current === "speaking" && prev) {
          // They went on talking before her answer started: one turn, the last utterance plus this.
          const whole = concatPcm(prev, fresh);
          contSeq.current += 1;
          send({ type: "continue", prev: prev.length, n: contSeq.current });
          ok = send(whole.buffer as ArrayBuffer);
          m.lastPcm = whole;
          setCont("sent");
        } else {
          if (cont.current === "speaking") setCont("off");
          ok = send(pcm);
          m.lastPcm = fresh;
        }
        if (!ok) {
          flash("Not connected to the voice server");
          m.lastPcm = null;
          setCont("off");
          return;
        }
        sentNoStatus.current = true;
        setTurnAudio(false);
      },
      onAutoStop: () => {
        setRecording(false);
        setAttemptNow((cur) => (cur ? { ...cur, phase: "checking" } : cur));
      },
      onVadSpeech: (speaking) => {
        setRecording(speaking);
        if (!speaking) return;
        player.stop();
        mutedTurn.current = lastTutorId.current;
        // Her answer to what they just said hasn't started: they're still saying it.
        const waiting = serverRef.current !== "idle" || sentNoStatus.current;
        if (handsFreeRef.current && !attemptRef.current && m.lastPcm && !turnAudioRef.current && waiting)
          setCont("speaking");
      },
      onPause: setPausing,
    });
    return m;
  }, [send, flash, player, setCont, setTurnAudio]);

  // ---- websocket ----
  useEffect(() => {
    let closed = false;
    let retry: number | undefined;
    const connect = () => {
      const s = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
      ws.current = s;
      s.onopen = () => {
        droppedTurns.current.clear(); // a new socket counts its turns from t1 again
        setConnected(true);
      };
      s.onclose = () => {
        setConnected(false);
        setServer("idle");
        serverRef.current = "idle";
        cont.current = "off";
        // A pending attempt's result won't come over a new socket (and its recording mustn't become a turn).
        activeAttempt.current = null;
        if (attemptRef.current) {
          attemptRef.current = null;
          if (mic.isRecording) mic.abort();
          setRecording(false);
        }
        setAttemptNow(null);
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
      if (droppedTurns.current.has(id)) return ms; // a late message must not bring a dropped bubble back
      if (!ms.some((m) => m.kind === "tutor" && m.id === id))
        ms = [...ms, { kind: "tutor", id, text: "", sentences: [], done: false }];
      return ms.map((m) => (m.kind === "tutor" && m.id === id ? f(m) : m));
    });

  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  function handle(m: any) {
    switch (m.type) {
      case "ready": {
        setLessons(m.lessons);
        setModel(m.model);
        setVoices(m.voices);
        setSettings(m.settings);
        let saved: string | null = null;
        try {
          saved = localStorage.getItem("habla.voice");
        } catch {
          /* storage unavailable */
        }
        if (saved && saved !== m.voice && m.voices.some((v: { id: string }) => v.id === saved)) {
          ws.current?.send(JSON.stringify({ type: "voice", id: saved, silent: true }));
          setVoice(saved);
        } else setVoice(m.voice);
        break;
      }
      case "status":
        setServer(m.state);
        serverRef.current = m.state;
        sentNoStatus.current = false;
        break;
      case "user": {
        // The turn for the continuation we sent: from here on, audio is her answer to the whole thing.
        if (cont.current === "sent" && m.cont === contSeq.current) setCont("off");
        if (!m.command) setTurnAudio(false);
        const fresh: UserMsg = {
          kind: "user",
          id: m.id,
          text: m.text,
          english: m.english,
          unclear: m.unclear ?? [],
          mistakes: [],
          teach: [],
          words: {},
          analyzed: !!m.command,
          command: m.command,
        };
        // replace: they went on talking, so this is the whole message (its old feedback no longer applies).
        setMsgs((ms) =>
          m.replace && ms.some((x) => x.kind === "user" && x.id === m.id)
            ? ms.map((x) => (x.kind === "user" && x.id === m.id ? fresh : x))
            : [...ms, fresh],
        );
        break;
      }
      case "settings":
        setSettings(m);
        break;
      case "hint":
        setMsgs((ms) => [...ms, { kind: "hint", id: `h${Date.now()}`, text: m.text, spanish: m.spanish, level: m.level }]);
        break;
      case "replay": {
        // Voice command "¿cómo?" / "más despacio": replay Sofía's last reply, no LLM involved.
        setMsgs((ms) => {
          const last = [...ms].reverse().find((x) => x.kind === "tutor") as TutorMsg | undefined;
          if (last) player.playNow(last.sentences.map(({ key, url }) => ({ key, url })), m.slow ? 0.75 : undefined);
          return ms;
        });
        if (m.slow) setSlow(true);
        break;
      }
      case "attempt_result":
        if (m.n !== activeAttempt.current) break; // an attempt they left (or a newer one is on)
        activeAttempt.current = null;
        setAttemptNow(null);
        setAttemptResults((r) => ({ ...r, [m.key]: m as AttemptResult }));
        // Hear the right version: always in the review drill, and after a miss elsewhere.
        if (m.kind === "review" || m.kind === "fix" || !m.ok) player.playNow([{ key: `say:${m.key}`, url: b64ToUrl(m.wav) }]);
        break;
      case "teacher_question":
        // The voice question is transcribed: show it (pending) while the teacher answers.
        if (m.n === activeAttempt.current) {
          activeAttempt.current = null;
          setAttemptNow(null);
        }
        setThreads((t) => ({ ...t, [m.key]: [...(t[m.key] ?? []).filter((x) => !(x.pending && x.q === m.question)), { q: m.question, a: "", pending: true }] }));
        break;
      case "teacher_answer": {
        if (m.n != null && m.n === activeAttempt.current) {
          activeAttempt.current = null;
          setAttemptNow(null);
        }
        if (!m.question) {
          flash(m.error || "I didn't catch the question — try again.");
          break;
        }
        setThreads((t) => {
          const list = [...(t[m.key] ?? [])];
          const i = list.findIndex((x) => x.pending && x.q === m.question);
          const qa: QA = { q: m.question, a: m.answer || "", error: m.error || undefined };
          if (i >= 0) list[i] = qa;
          else list.push(qa);
          return { ...t, [m.key]: list };
        });
        const clips: string[] = m.clips ?? [];
        if (clips.length) player.playNow(clips.map((c, i) => ({ key: `teach:${m.key}:${i}`, url: b64ToUrl(c) })));
        break;
      }
      case "coach":
        updateTutor(m.id, (t) => ({ ...t, coach: m.text }));
        break;
      case "user_en":
        updateUser(m.id, (u) => ({ ...u, en: m.text || u.en }));
        break;
      case "feedback":
        // Keyed by id: feedback for an older turn still lands on its bubble after a newer turn has started.
        if (m.merged) {
          // Cut off before Sofía answered: it's checked together with the next message instead.
          updateUser(m.id, (u) => ({ ...u, analyzed: true, merged: true }));
          break;
        }
        if (m.unchecked) {
          // The check didn't work this time: no cards, and no "Bien dicho" either.
          updateUser(m.id, (u) => ({ ...u, en: m.learner_en || u.en, analyzed: true, unchecked: true }));
          break;
        }
        if (m.clarify) {
          // Too unclear to grade: Sofía asks them to say it again instead.
          updateUser(m.id, (u) => ({ ...u, en: m.learner_en || u.en, analyzed: true, clarify: true }));
          break;
        }
        updateUser(m.id, (u) => ({
          ...u,
          en: m.learner_en || u.en,
          better: m.better ?? u.better,
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
        if (m.id === "sample" || m.id === "say") {
          player.playNow([{ key: m.id, url: b64ToUrl(m.wav) }]);
          break;
        }
        const clip = { key: `${m.id}:${m.seq}`, text: m.text, url: b64ToUrl(m.wav) };
        updateTutor(m.id, (t) => ({ ...t, sentences: [...t.sentences, clip] }));
        // While they're still talking (a continuation), her answer to the first part isn't played: the server
        // drops it.
        if (cont.current !== "off" || droppedTurns.current.has(m.id)) break;
        setTurnAudio(true);
        if (mutedTurn.current !== m.id) player.enqueue(clip);
        break;
      }
      case "tutor_done":
        updateTutor(m.id, (t) => ({
          ...t,
          text: m.reply || t.text,
          en: m.reply_en || t.en, // the translation arrives later, in tutor_en
          done: true,
          firstAudioMs: m.timing?.first_audio_ms,
          glosses: m.glosses,
          coach: m.coach || t.coach,
        }));
        break;
      case "tutor_dropped":
        // Cut off before any of it was heard: she didn't really say it, so take it off the screen.
        droppedTurns.current.add(m.id);
        setMsgs((ms) => ms.filter((x) => !(x.kind === "tutor" && x.id === m.id)));
        break;
      case "tutor_en":
        // Don't create a bubble: this may be for a turn of a lesson that's already been left.
        setMsgs((ms) => ms.map((x) => (x.kind === "tutor" && x.id === m.id ? { ...x, en: m.reply_en } : x)));
        break;
      case "turn_done": // the turn's analysis is finished; nothing to do yet
        break;
      case "progress": {
        setProgress(m);
        const st = m.stats;
        if (st && typeof st.today_sentences === "number") {
          // Toast only when today's count crosses the goal (not when the page loads past it, the goal is
          // changed, or it was already shown today).
          const day = todayKey();
          const met = st.today_sentences >= st.goal_sentences;
          const was = goalMet.current;
          if (was && !was.met && met && was.goal === st.goal_sentences && was.day === day && readStore("habla.goalDay") !== day) {
            writeStore("habla.goalDay", day);
            flash("🎉 Daily goal reached — ¡muy bien!");
          }
          goalMet.current = { met, goal: st.goal_sentences, day };
        }
        break;
      }
      case "lesson_state": {
        setLessonState(m);
        // A target the learner just used on their own: a toast, a pulse on its chip and a soft chime, once per
        // target per lesson.
        const targets: Target[] = m.targets ?? [];
        const prev = prevTargets.current;
        prevTargets.current = new Map(targets.map((t) => [t.lemma, t.state]));
        if (!prev) break; // the lesson's first state: nothing changed yet
        const fresh = targets.filter(
          (t) => t.state === "used" && prev.get(t.lemma) !== "used" && !celebrated.current.has(t.lemma),
        );
        if (!fresh.length) break;
        fresh.forEach((t) => celebrated.current.add(t.lemma));
        const names = fresh.map((t) => `«${t.display}»`).join(" and ");
        flash(`✓ ¡Usaste ${names}!${m.goal > 0 ? ` ${m.used}/${m.goal}` : ""}`);
        setPulse(new Set(fresh.map((t) => t.lemma)));
        window.clearTimeout(pulseTimer.current);
        pulseTimer.current = window.setTimeout(() => setPulse(new Set()), PULSE_MS + 50);
        if (soundsRef.current) chime();
        break;
      }
      case "phrase_used": {
        const ps: string[] = m.phrases ?? [];
        if (!ps.length) break;
        flash(`✓ ¡Dijiste ${ps.map((p) => `«${p}»`).join(" y ")}!`);
        if (soundsRef.current) chime();
        break;
      }
      case "introduce":
        lastTutorId.current = m.id;
        updateTutor(m.id, (t) => ({ ...t, words: m.words }));
        break;
      case "recap":
        setMsgs((ms) => [...ms, { kind: "recap", id: `r${Date.now()}`, recap: m as Recap }]);
        break;
      case "ignored":
        if (cont.current === "sent" && m.cont === contSeq.current) setCont("off");
        flash("I didn't catch that — try again, a bit louder or longer.");
        break;
      case "error":
        if (cont.current === "sent") setCont("off");
        flash(`Error: ${m.message}`);
        break;
    }
  }

  // ---- audio state wiring ----
  useEffect(() => {
    player.onChange = (key) => {
      setPlayingKey(key);
      const [id, seq] = key ? key.split(":") : [];
      if (id && /^\d+$/.test(seq ?? "")) startedClips.current[id] = Math.max(startedClips.current[id] ?? 0, Number(seq) + 1);
    };
  }, [player]);
  useEffect(() => {
    player.rate = slow ? 0.8 : 1;
  }, [slow, player]);
  useEffect(() => {
    mic.handsFree = handsFreeRef.current = handsFree;
    if (!handsFree) mic.resetVad();
  }, [handsFree, mic]);
  useEffect(() => {
    // Half duplex: hands-free listening pauses while Sofía is thinking or speaking. Until her answer to what they
    // just said starts playing, it keeps listening: talking on continues the same turn.
    const waiting = handsFree && !turnAudioStarted && !!mic.lastPcm && server !== "idle";
    mic.paused = !!playingKey || (server !== "idle" && !waiting);
    if (mic.paused) mic.resetVad();
  }, [playingKey, server, mic, handsFree, turnAudioStarted]);
  useEffect(() => {
    const lv = settings?.level;
    mic.endSilenceMs = (lv && END_SILENCE_MS[lv]) || END_SILENCE_DEFAULT_MS;
  }, [settings?.level, mic]);

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

  // Barge in: stop Sofía and drop the rest of her current reply. The server keeps the sentences that were
  // heard. When it's already idle (only playback left), it just cuts the written reply to those.
  const bargeIn = useCallback(() => {
    const wasPlaying = player.playing;
    player.stop();
    mutedTurn.current = lastTutorId.current;
    const id = lastTutorId.current;
    const played = id ? startedClips.current[id] ?? 0 : 0;
    if (server !== "idle") send({ type: "cancel", played });
    else if (wasPlaying && id) send({ type: "heard", id, played });
  }, [player, server, send]);

  const startTalk = useCallback(async () => {
    if (!(await ensureMic())) return;
    if (player.playing || server !== "idle") bargeIn();
    mic.start();
    setRecording(true);
  }, [ensureMic, player, server, bargeIn, mic]);

  // ---- graded spoken attempts (review drill, saying a correction, shadowing) ----
  // Leave any attempt: the next utterance is a normal turn again, and a late result for it is ignored.
  const cancelAttempt = useCallback(() => {
    attemptRef.current = null;
    activeAttempt.current = null;
    setAttemptNow(null);
    if (mic.isRecording) {
      mic.abort();
      setRecording(false);
    }
    send({ type: "attempt_cancel" });
  }, [mic, send]);

  const startAttempt = useCallback(
    async (a: Attempt) => {
      if (!(await ensureMic())) return;
      cancelAttempt();
      player.stop();
      if (cont.current === "speaking") setCont("off"); // that recording was just dropped
      const n = ++attemptSeq.current;
      attemptRef.current = { ...a, n };
      activeAttempt.current = n;
      setAttemptNow({ key: a.key, target: a.target, n, phase: "listening" });
      setAttemptResults((r) => {
        const { [a.key]: _old, ...rest } = r; // eslint-disable-line @typescript-eslint/no-unused-vars
        return rest;
      });
      mic.start(true); // stops by itself when they stop talking
      setRecording(true);
    },
    [ensureMic, cancelAttempt, player, mic, setCont],
  );
  // Safety: a grade that never comes (the server failed) mustn't leave "Checking…" up forever.
  useEffect(() => {
    if (attemptNow?.phase !== "checking") return;
    const n = attemptNow.n;
    const t = window.setTimeout(() => {
      if (activeAttempt.current !== n) return;
      activeAttempt.current = null;
      setAttemptNow(null);
    }, 20000);
    return () => window.clearTimeout(t);
  }, [attemptNow]);
  const attempts: Attempts = useMemo(
    () => ({
      start: (a) => void startAttempt(a),
      cancel: cancelAttempt,
      active: attemptNow?.key ?? null,
      phase: attemptNow?.phase ?? null,
      results: attemptResults,
      threads,
      askText: (a, text) => {
        const done = (threadsRef.current[a.key] ?? []).filter((x) => !x.pending && x.a);
        setThreads((t) => ({ ...t, [a.key]: [...(t[a.key] ?? []), { q: text, a: "", pending: true }] }));
        player.stop();
        send({ type: "ask_text", key: a.key, card: a.card, said: a.said ?? "", thread: done, text });
      },
    }),
    [startAttempt, cancelAttempt, attemptNow, attemptResults, threads, player, send],
  );

  const speak = useCallback((text: string, slowly = false) => send({ type: "speak", text, slow: slowly }), [send]);

  const changeSettings = (patch: Partial<Settings>) => {
    setSettings((st) => (st ? { ...st, ...patch } : st));
    send({ type: "settings", ...patch });
  };
  const changeSounds = (on: boolean) => {
    setSounds(on);
    soundsRef.current = on;
    writeStore("habla.sounds", on ? "1" : "0");
    if (on) chime(); // a sample
  };
  const changeVoice = (id: string) => {
    setVoice(id);
    try {
      localStorage.setItem("habla.voice", id);
    } catch {
      /* storage unavailable */
    }
    send({ type: "voice", id });
  };

  const stopTalk = useCallback(() => {
    if (!mic.isRecording) return;
    mic.stop();
    setRecording(false);
  }, [mic]);

  // The bottom sheet is for narrow windows only: widening the window closes it (the side panel is back).
  useEffect(() => {
    if (!sheetOpen || typeof window.matchMedia !== "function") return;
    const wide = window.matchMedia("(min-width: 861px)");
    const onChange = () => wide.matches && setSheetOpen(false);
    onChange();
    wide.addEventListener?.("change", onChange);
    return () => wide.removeEventListener?.("change", onChange);
  }, [sheetOpen]);

  useEffect(() => {
    const isField = (t: EventTarget | null) => t instanceof HTMLElement && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName);
    const inLesson = screen === "lesson" && !!lesson;
    const down = (e: KeyboardEvent) => {
      if (sheetOpen && e.code === "Escape") {
        e.preventDefault();
        setSheetOpen(false);
        return;
      }
      if (!inLesson || isField(e.target) || showSettings) return;
      if (e.code === "Space") {
        e.preventDefault();
        if (!e.repeat) void startTalk();
      } else if (e.code === "Escape") player.stop();
    };
    const up = (e: KeyboardEvent) => {
      // Not while an attempt records: it stops by itself. (Keyed on the mic, not on attemptRef, which a left
      // attempt used to leave set, so push-to-talk never stopped.)
      if (e.code === "Space" && inLesson && !isField(e.target) && !mic.autoStopping) {
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
  }, [lesson, screen, showSettings, sheetOpen, startTalk, stopTalk, player, mic]);

  const onPressStart = () => {
    pressAt.current = Date.now();
    pressWasRecording.current = recording;
    if (recording) stopTalk();
    else void startTalk();
  };
  const onPressEnd = () => {
    if (!pressWasRecording.current && Date.now() - pressAt.current > HOLD_MS) stopTalk();
  };

  const startLesson = (l: Lesson, mode: "easy" | "real" = "easy") => {
    void ensureMic(); // ask for mic permission now, but don't block the lesson on the prompt
    player.stop();
    cancelAttempt();
    mic.abort();
    setRecording(false);
    mutedTurn.current = null;
    mic.lastPcm = null; // nothing to continue in the new lesson
    setCont("off");
    setTurnAudio(true);
    // The old lesson's turns may still send messages (a cut reply, its translation): keep them out of this chat.
    if (lastTutorId.current) droppedTurns.current.add(lastTutorId.current);
    setMsgs((ms) => {
      ms.forEach((m) => m.kind === "tutor" && droppedTurns.current.add(m.id));
      return [];
    });
    setLessonState(null);
    prevTargets.current = null;
    celebrated.current = new Set();
    setPulse(new Set());
    setAttemptResults({});
    setLesson(l);
    setScreen("lesson");
    send({ type: "start", lesson: l.id, mode });
  };

  const finishLesson = () => {
    bargeIn();
    mic.lastPcm = null; // the goodbye isn't an answer to their last words: talking over it is a new turn
    send({ type: "finish" });
  };

  // Hint ladder: each press since your last answer gives a bit more.
  const lastUserIdx = msgs.map((m) => m.kind).lastIndexOf("user");
  const hintsGiven = msgs.slice(lastUserIdx + 1).filter((m) => m.kind === "hint" && m.level > 0).length;
  const hintLabel = hintsGiven === 0 ? "Hint" : hintsGiven === 1 ? "More help" : "Show me";
  const askHelp = () => {
    player.stop();
    send({ type: "help" });
  };
  const goHome = () => {
    player.stop();
    cancelAttempt();
    mic.abort();
    setRecording(false);
    if (cont.current === "speaking") setCont("off"); // that recording was just dropped
    setSheetOpen(false);
    setScreen("home");
    fetch("/api/progress")
      .then((r) => r.json())
      .then(setProgress)
      .catch(() => {});
  };

  const sendTyped = (e: React.FormEvent) => {
    e.preventDefault();
    const t = draft.trim();
    if (!t) return;
    bargeIn();
    if (cont.current === "speaking") setCont("off");
    mic.lastPcm = null; // a typed turn can't be continued by voice
    send({ type: "text", text: t });
    setDraft("");
  };

  // ---- derived ----
  const sessionWords: Teach[] = msgs.flatMap((m) => (m.kind === "user" ? m.teach : []));
  const sessionFixes = msgs.flatMap((m) => (m.kind === "user" ? m.mistakes : []));
  const saidOk = new Set(msgs.flatMap((m) => (m.kind === "user" ? Object.entries(m.words).filter(([, v]) => v === "ok").map(([k]) => k) : [])));

  const status = attemptNow
    ? attemptNow.phase === "listening"
      ? `Say «${attemptNow.target}»… I'll stop when you pause`
      : "Checking…"
    : recording
    ? handsFree
      ? pausing
        ? "Still listening… take your time"
        : "Listening…"
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

  const settingsPanel = showSettings && settings && (
    <SettingsPanel
      settings={settings}
      voices={voices}
      voice={voice}
      onSettings={changeSettings}
      onVoice={changeVoice}
      sounds={sounds}
      onSounds={changeSounds}
      onClose={() => setShowSettings(false)}
    />
  );
  const stats = progress?.stats;
  // The goal as just set in Settings (stats catch up with the next progress message).
  const goalN = settings?.goal_sentences ?? stats?.goal_sentences ?? 20;
  const goalPct = stats ? Math.min(100, (100 * (stats.today_sentences ?? 0)) / Math.max(1, goalN)) : 0;
  const spokeTip = stats ? ` · ${Math.round(stats.today_seconds / 60)} min spoken` : "";
  const floatingToast = toast && <div className="toast floating">{toast}</div>;
  const focus = progress?.patterns ?? [];

  if (screen === "review")
    return (
      <>
        <Review attempts={attempts} speak={speak} send={send} onExit={goHome} />
        {floatingToast}
      </>
    );
  if (screen === "words") return <Words speak={speak} onExit={goHome} />;

  if (screen === "home" || !lesson) {
    return (
      <div className="start">
        {settingsPanel}
        {floatingToast}
        <div className="start-inner">
          <div className="home-top">
            <div className="brand">
              <span className="logo">¡h!</span> Habla
            </div>
            <button className="icon-btn" onClick={() => setShowSettings(true)} title="Settings" disabled={!settings}>
              ⚙︎ {settings ? `${settings.level}` : ""}
            </button>
          </div>
          <h1>Talk Spanish with Sofía</h1>
          <p className="lede">
            A patient tutor you speak with out loud. Say what you can — mix in English when you're stuck, and Sofía will
            teach you the word. Everything runs locally on this Mac.
          </p>
          {!connected && <p className="warn">Connecting to the voice server… (is it running on :8777?)</p>}
          {stats && progress && (
            <div className="today">
              <div className="today-item">
                <b>🔥 {stats.streak}</b>
                <span>day streak</span>
              </div>
              <div className="today-item">
                <div className="goal-bar" title={`${Math.round(goalPct)}% of today's goal`}>
                  <div style={{ width: `${goalPct}%` }} className={goalPct >= 100 ? "met" : ""} />
                </div>
                <b title={`Turns, attempts and Repaso drills count${spokeTip}`}>
                  {stats.today_sentences} / {goalN}
                </b>
                <span>sentences today</span>
              </div>
              <div className="today-item" title={`${KNOWN_TIP}, out of ${listLabel(progress)}`}>
                <b>
                  {progress.known} / {progress.total_content ?? progress.total}
                </b>
                <span>words you can say</span>
              </div>
              <button className="today-btn" onClick={() => setScreen("review")} disabled={!connected}>
                🔁 Repaso
                <small>{stats.due_reviews ? `${stats.due_reviews} due` : "quick practice"}</small>
              </button>
              <button className="today-btn" onClick={() => setScreen("words")}>
                📒 Mis palabras
                <small>{progress.known + progress.learning} words</small>
              </button>
            </div>
          )}
          {focus.length > 0 && (
            <p className="focus-line small">
              <b>Your focus:</b>{" "}
              {focus.map((f, i) => (
                <span key={f.type} title={f.tip}>
                  {i > 0 && " · "}
                  {f.label} ×{f.count}
                </span>
              ))}{" "}
              <span className="muted">— Sofía will give you chances to practice these.</span>
            </p>
          )}
          {lesson && msgs.length > 0 && (
            <button className="resume" onClick={() => setScreen("lesson")}>
              ↩ Continue “{lesson.title_es}”
            </button>
          )}
          <Scenarios connected={connected} speak={speak} onStart={(s, mode) => startLesson(s, mode)} />
          <h3 className="section-title">Lessons</h3>
          <div className="lesson-grid">
            {lessons.map((l) => {
              const done = progress?.lessons?.[l.id];
              const complete = (id: string) => {
                const d = progress?.lessons?.[id];
                return !!d && d.best_used >= d.goal;
              };
              const next = lessons.find((x) => !complete(x.id))?.id === l.id;
              return (
                <button key={l.id} className={`lesson-card ${next ? "next" : ""}`} onClick={() => startLesson(l)} disabled={!connected}>
                  {done && done.best_used >= done.goal ? (
                    <span className="badge done">✓ Done{done.times > 1 ? ` ×${done.times}` : ""}</span>
                  ) : done ? (
                    <span className="badge tried">
                      {done.best_used}/{done.goal} words
                    </span>
                  ) : next ? (
                    <span className="badge">Start here</span>
                  ) : null}
                  <span className="lesson-es">{l.title_es}</span>
                  <span className="lesson-en">{l.title}</span>
                  <span className="lesson-goal">{l.goal}</span>
                </button>
              );
            })}
          </div>
          {micError && <p className="warn">{micError}</p>}
        </div>
      </div>
    );
  }

  const sideContent = (
    <>
      {lesson?.custom && (
        <div className="card">
          <h3>Cheat sheet</h3>
          <ScenarioSheet s={lesson as Scenario} speak={speak} connected={connected} />
        </div>
      )}
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
      {focus.length > 0 && (
        <div className="card focus">
          <h3>Focus areas</h3>
          {focus.map((f) => (
            <div key={f.type} className="focus-item">
              <div>
                <b>{f.label}</b> <span className="muted small">×{f.count}</span>
              </div>
              <p className="small">{f.tip}</p>
              <p className="small muted">
                <span className="strike">{f.example.wrong}</span> → <b>{f.example.right}</b>
              </p>
            </div>
          ))}
        </div>
      )}
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
                {f.right_en && <span className="right-en"> “{f.right_en}”</span>}
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
    </>
  );

  return (
    <div className="app">
      {settingsPanel}
      {sheetOpen && (
        <div className="sheet-backdrop" onClick={() => setSheetOpen(false)}>
          <div className="sheet" role="dialog" aria-label="Session panel" onClick={(e) => e.stopPropagation()}>
            <div className="sheet-head">
              <b>📊 Session</b>
              <button className="icon-btn" onClick={() => setSheetOpen(false)} aria-label="Close session panel">
                ✕
              </button>
            </div>
            {sideContent}
          </div>
        </div>
      )}
      <header className="top">
        <button className="brand as-button" onClick={goHome} title="Home">
          <span className="logo">¡h!</span> Habla
        </button>
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
        {stats && (
          <div className="progress-pill" title={`Today: ${stats.today_sentences} of ${goalN} sentences${spokeTip}`}>
            <span>🔥 {stats.streak}</span>
            <div className="bar">
              <div style={{ width: `${goalPct}%` }} className={goalPct >= 100 ? "met" : ""} />
            </div>
          </div>
        )}
        {progress && (
          <div className="progress-pill" title={`${KNOWN_TIP}, out of ${listLabel(progress)} · ${progress.learning} words in progress`}>
            <span>
              <b>{progress.known}</b>
              <span className="pill-total"> / {progress.total_content ?? progress.total} words</span>
            </span>
          </div>
        )}
        <button
          className={`icon-btn sheet-btn ${sheetOpen ? "on" : ""}`}
          onClick={() => setSheetOpen((o) => !o)}
          aria-label="Session panel"
          aria-expanded={sheetOpen}
          title="This session: words, fixes, focus"
        >
          📊
        </button>
        <button className="icon-btn" onClick={() => setShowSettings(true)} title="Settings: level, voice, subtitles, goal" disabled={!settings}>
          ⚙︎ {settings?.level}
        </button>
        <span className={`dot ${connected ? "on" : ""}`} title={connected ? `Connected · ${model}` : "Disconnected"} />
      </header>

      <main className="main">
        <section className="convo" ref={scroller}>
          <div className="convo-inner">
            <div className="lesson-banner">
              <b>{lesson.title_es}</b> · {lesson.goal}
            </div>
            {lessonState && <LessonStrip st={lessonState} onFinish={finishLesson} busy={server !== "idle"} pulse={pulse} />}
            {msgs.map((m) =>
              m.kind === "tutor" ? (
                <TutorBubble
                  key={m.id}
                  m={m}
                  playingKey={playingKey}
                  player={player}
                  attempts={attempts}
                  subtitles={settings?.subtitles ?? "always"}
                  autoEn={settings?.level === "A0"}
                />
              ) : m.kind === "user" ? (
                <UserBubble key={m.id} m={m} attempts={attempts} />
              ) : m.kind === "recap" ? (
                <RecapCard key={m.id} r={m.recap} onNext={goHome} />
              ) : m.kind === "hint" ? (
                <HintCard key={m.id} m={m} speak={speak} attempts={attempts} />
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

        <aside className="side">{sideContent}</aside>
      </main>

      <footer className="dock">
        {toast && <div className="toast">{toast}</div>}
        <div className={`status ${micError && !recording ? "warn" : ""}`}>{micError && !recording ? micError : status}</div>
        <div className="controls">
          <div className="left-controls">
            <button className={`toggle ${slow ? "on" : ""}`} onClick={() => setSlow((s) => !s)} title="Play Sofía slower" aria-label="Slower" aria-pressed={slow}>
              🐢<span className="label"> Slower</span>
            </button>
            <button
              className={`toggle ${handsFree ? "on" : ""}`}
              onClick={() => {
                void ensureMic();
                setHandsFree((h) => !h);
              }}
              title="Hands-free: talk without pressing anything (best with headphones)"
              aria-label="Hands-free"
              aria-pressed={handsFree}
            >
              ∞<span className="label"> Hands-free</span>
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
            <button
              className="toggle"
              onClick={askHelp}
              disabled={server !== "idle"}
              title="Stuck? Each press gives a bit more help: a key word, then a sentence starter, then a full example"
              aria-label={hintLabel}
            >
              🛟<span className="label"> {hintLabel}</span>
            </button>
            <button
              className={`toggle ${typing ? "on" : ""}`}
              onClick={() => setTyping((t) => !t)}
              title="Type instead"
              aria-label="Type"
              aria-pressed={typing}
            >
              ⌨︎<span className="label"> Type</span>
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

function TutorBubble({
  m,
  playingKey,
  player,
  attempts,
  subtitles,
  autoEn,
}: {
  m: TutorMsg;
  playingKey: string | null;
  player: Player;
  attempts: Attempts;
  subtitles: "always" | "after" | "tap";
  autoEn: boolean;
}) {
  const [showEn, setShowEn] = useState(autoEn);
  const [tapped, setTapped] = useState(false);
  const [heard, setHeard] = useState(false); // her audio for this message has finished playing
  const [started, setStarted] = useState(false);
  const [shadow, setShadow] = useState<number | null>(null);

  // Listening mode: notice when this message's audio started and then finished.
  const mine = !!playingKey && playingKey.startsWith(`${m.id}:`);
  useEffect(() => {
    if (mine) setStarted(true);
    else if (started) setHeard(true);
  }, [mine, started]);
  useEffect(() => {
    if (m.done && m.sentences.length === 0) setHeard(true);
  }, [m.done, m.sentences.length]);
  const visible = subtitles === "always" || tapped || (subtitles === "after" && heard);

  // Sentences already voiced, then any streamed text that has no audio yet.
  let rest = m.text;
  for (const s of m.sentences) {
    const i = rest.indexOf(s.text);
    if (i >= 0) rest = rest.slice(i + s.text.length);
  }
  const clips = m.sentences.map(({ key, url }) => ({ key, url }));

  // Shadowing: she says one sentence, you repeat it, each word is checked. Cycles through her sentences.
  const shadowAt = (i: number) => {
    const s = m.sentences[i];
    if (!s) return;
    setShadow(i);
    player.playNow([{ key: s.key, url: s.url }], 0.9, () =>
      attempts.start({ kind: "shadow", target: s.text, key: `${m.id}:sh${i}` }),
    );
  };
  const shKey = shadow !== null ? `${m.id}:sh${shadow}` : "";
  const shResult = shKey ? attempts.results[shKey] : undefined;

  return (
    <div className="row tutor">
      <div className="avatar">S</div>
      <div className="tutor-col">
        <div className="bubble tutor">
          <div className="trans">
            <div
              className={`es ${visible ? "" : "hidden-text"}`}
              onClick={visible ? undefined : () => setTapped(true)}
              title={visible ? undefined : "Listen first — tap to show the text"}
            >
              {m.sentences.map((s) => (
                <span
                  key={s.key}
                  className={`sent ${playingKey === s.key ? "speaking" : ""}`}
                  onClick={visible ? () => player.playNow([{ key: s.key, url: s.url }]) : undefined}
                >
                  {glossify(s.text, m.glosses)}{" "}
                </span>
              ))}
              {rest && <span className="unspoken">{rest}</span>}
            </div>
            {visible && m.en && !showEn && <div className="pop">{m.en}</div>}
            {!visible && <span className="reveal-hint">👂 listen · tap to show</span>}
          </div>
          {m.words && m.words.length > 0 && (
            <div className="newwords">
              {m.words.map((w) => (
                <div key={w.lemma} className="newword">
                  <span className="tag">New word</span>
                  <b>{w.display}</b>
                  <span>{w.english}</span>
                  <small>{w.pos}</small>
                  <AskTeacher attempts={attempts} k={`${m.id}:ask-${w.lemma}`} card={`New word: «${w.display}» = ${w.english} (${w.pos}), used by Sofía in «${m.text.trim()}»`} />
                </div>
              ))}
            </div>
          )}
          {showEn && m.en && <div className="en">{m.en}</div>}
          {shadow !== null && (
            <div className={`shadow ${shResult?.ok && attempts.active !== shKey ? "ok-flash" : ""}`}>
              <span className="tag">Repeat after her</span>
              {attempts.active === shKey ? (
                attempts.phase === "checking" ? (
                  <span className="muted">
                    <span className="spinner" aria-hidden /> Checking…
                  </span>
                ) : (
                  <span className="muted">● Your turn — say it… I'll stop when you pause</span>
                )
              ) : shResult ? (
                <ResultLine r={shResult} />
              ) : (
                <span className="muted">Listen…</span>
              )}
              {shResult && (
                <span className="shadow-nav">
                  <button onClick={() => shadowAt(shadow)}>↻ Again</button>
                  {shadow + 1 < m.sentences.length && <button onClick={() => shadowAt(shadow + 1)}>Next sentence →</button>}
                </span>
              )}
            </div>
          )}
          {m.done && (
            <div className="actions">
              <button onClick={() => player.playNow(clips)} title="Replay">
                ↻ Replay
              </button>
              <button onClick={() => player.playNow(clips, 0.7)} title="Replay slowly">
                🐢 Slow
              </button>
              {m.sentences.length > 0 && (
                <button
                  className={shadow !== null ? "on" : ""}
                  onClick={() => shadowAt(0)}
                  title="Shadowing: she says it, you repeat it — great for pronunciation and fluency"
                >
                  🗣 Repeat
                </button>
              )}
              {m.en && (
                <button className={showEn ? "on" : ""} onClick={() => setShowEn((s) => !s)}>
                  EN
                </button>
              )}
              {m.firstAudioMs != null && (
                <span className="latency" title="Time from the end of your speech to Sofía's first audio">
                  {(m.firstAudioMs / 1000).toFixed(1)}s
                </span>
              )}
            </div>
          )}
        </div>
        {m.coach && (
          <div className="coach">
            <span className="coach-tag">Teacher</span>
            <span>{withSpanish(m.coach)}</span>
            <AskTeacher attempts={attempts} k={`${m.id}:ask-coach`} card={`Teacher tip after Sofía said «${m.text.trim()}»: ${m.coach}`} />
          </div>
        )}
      </div>
    </div>
  );
}

/** Hint ladder card (or an instant answer to "¿qué significa…?"). */
function HintCard({ m, speak, attempts }: { m: Extract<Msg, { kind: "hint" }>; speak: (t: string) => void; attempts: Attempts }) {
  const say = (m.spanish ?? "").replace(/[…\.]+$/, "").trim();
  return (
    <div className="row tutor hint-row">
      <div className="avatar ghost">?</div>
      <div className="coach hint">
        <span className="coach-tag">{m.level === 0 ? "Meaning" : `Hint ${m.level}/3`}</span>
        <span>{withSpanish(m.text)}</span>
        {say && (
          <span className="hint-tools">
            <button className="icon" onClick={() => speak(say)} title="Hear it">
              🔊
            </button>
            {m.level === 3 && <AttemptButton attempts={attempts} attempt={{ kind: "shadow", target: say, key: `${m.id}:say` }} label="Say it" />}
          </span>
        )}
      </div>
    </div>
  );
}

/** "Ask the teacher" about a card: by voice (the mic stops when they stop talking) or typed. The answer is shown
 * on the card and read aloud. Follow-up questions keep the thread. */
function AskTeacher({ attempts, k, card, said }: { attempts: Attempts; k: string; card: string; said?: string }) {
  const thread = attempts.threads[k] ?? [];
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const phase = attempts.active === k ? attempts.phase : null;
  const busy = phase !== null || thread.some((x) => x.pending);
  if (!open && thread.length === 0)
    return (
      <button className="ask-btn" onClick={(e) => (e.stopPropagation(), setOpen(true))} title="Ask the teacher about this">
        💬 Ask
      </button>
    );
  const done = thread.filter((x) => !x.pending && x.a);
  const submit = () => {
    const q = text.trim();
    if (!q || busy) return;
    setText("");
    attempts.askText({ key: k, card, said }, q);
  };
  return (
    <div className="ask" onClick={(e) => e.stopPropagation()}>
      {thread.map((x, i) => (
        <div key={i} className="ask-qa">
          <div className="ask-q">“{x.q}”</div>
          {x.pending ? (
            <div className="ask-a muted">
              <span className="spinner" aria-hidden /> Thinking…
            </div>
          ) : (
            <div className="ask-a">{x.error ? <span className="muted">{x.error}</span> : withSpanish(x.a)}</div>
          )}
        </div>
      ))}
      <div className="ask-tools">
        <button
          className={`try ${phase === "listening" ? "rec" : ""}`}
          disabled={busy && phase !== "listening"}
          onClick={() =>
            phase === "listening"
              ? attempts.cancel()
              : attempts.start({ kind: "ask", key: k, target: "", card, said: said ?? "", thread: done })
          }
        >
          {phase === "listening" ? "● Listening… (tap to cancel)" : phase === "checking" ? "Transcribing…" : thread.length ? "🎙 Ask more" : "🎙 Ask by voice"}
        </button>
        <input
          className="ask-input"
          value={text}
          placeholder="…or type a question"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            e.stopPropagation(); // Space must not start the lesson mic
            if (e.key === "Enter") submit();
          }}
        />
      </div>
    </div>
  );
}

/** Highlights «Spanish examples» inside the English teacher guide. */
function withSpanish(text: string) {
  return text.split(/(«[^»]+»)/).map((part, i) =>
    part.startsWith("«") ? (
      <b key={i} className="es-example">
        {part.slice(1, -1)}
      </b>
    ) : (
      part
    ),
  );
}

function UserBubble({ m, attempts }: { m: UserMsg; attempts: Attempts }) {
  const [pinned, setPinned] = useState(false);
  if (m.command)
    return (
      <div className="row user">
        <div className="bubble user command" title="Answered instantly">
          {m.text} <small>{m.command === "meaning" ? "📖" : m.command === "slower" ? "🐢" : "↻"}</small>
        </div>
      </div>
    );
  return (
    <div className="row user">
      <div className="user-col">
        <div className="trans" onClick={() => setPinned((p) => !p)}>
          <div className="bubble user">{highlight(m.text, m.english, m.mistakes.map((x) => x.wrong), m.unclear ?? [])}</div>
          {m.en && !pinned && (
            <div className="pop">
              <small>You said:</small> “{m.en}”
            </div>
          )}
        </div>
        {m.en && pinned && <div className="user-en">“{m.en}”</div>}
        {(m.teach.length > 0 || m.mistakes.length > 0) && (
          <div className="cards">
            {m.teach.map((t, i) => (
              <div key={`t${i}`} className="fb teach">
                <span className="tag">New word</span>
                <b>{t.spanish}</b>
                <span className="muted">{t.english}</span>
                <AskTeacher attempts={attempts} k={`${m.id}:ask-teach${i}`} said={m.text} card={`New word: «${t.spanish}» = ${t.english}`} />
              </div>
            ))}
            {m.mistakes.map((x, i) => (
              <div key={`m${i}`} className="fb fix">
                <span className="tag">Fix{x.type && MISTAKE_LABEL[x.type] ? ` · ${MISTAKE_LABEL[x.type]}` : ""}</span>
                <span className="strike">{x.wrong}</span> → <b>{x.right}</b>
                {x.right_en && <span className="right-en">“{x.right_en}”</span>}
                <small>{x.why}</small>
                {/* Saying the correction out loud right away is what makes it stick. */}
                <AttemptButton attempts={attempts} attempt={{ kind: "repair", target: x.right, key: `${m.id}:fix${i}` }} label="Say it right" />
                <AskTeacher
                  attempts={attempts}
                  k={`${m.id}:ask-fix${i}`}
                  said={m.text}
                  card={`Correction: «${x.wrong}» → «${x.right}»${x.right_en ? ` (“${x.right_en}”)` : ""}. Explanation shown: ${x.why || "none"}`}
                />
              </div>
            ))}
          </div>
        )}
        {!m.analyzed && <div className="checking">checking…</div>}
        {m.analyzed && !m.merged && !m.unchecked && !m.clarify && m.text.trim().split(/\s+/).length >= 2 && m.teach.length === 0 && m.mistakes.length === 0 && m.english.length === 0 && (
          <div className="checking ok">✓ Bien dicho</div>
        )}
      </div>
    </div>
  );
}

const UNCLEAR_TIP = "Sofía wasn't sure she heard this — say it again if it matters";

/** Marks English spans (amber), mistaken fragments (red underline) and words that may be misheard (dotted amber
 * underline, every whole-word occurrence) inside the transcript. */
function highlight(text: string, english: string[], wrong: string[], unclear: string[] = []) {
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
  unclear.forEach((u) => {
    const esc = u.trim().replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    if (!esc) return;
    for (const hit of text.matchAll(new RegExp(`(?<![\\p{L}\\p{N}])${esc}(?![\\p{L}\\p{N}])`, "giu"))) {
      const i = hit.index ?? -1;
      const end = i + hit[0].length;
      if (i >= 0 && !ranges.some((r) => i < r.end && end > r.start)) ranges.push({ start: i, end, cls: "unclear" });
    }
  });
  ranges.sort((a, b) => a.start - b.start);
  const out: React.ReactNode[] = [];
  let pos = 0;
  ranges.forEach((r, i) => {
    if (r.start > pos) out.push(text.slice(pos, r.start));
    out.push(
      <span key={i} className={r.cls} title={r.cls === "unclear" ? UNCLEAR_TIP : undefined}>
        {text.slice(r.start, r.end)}
      </span>,
    );
    pos = r.end;
  });
  out.push(text.slice(pos));
  return out;
}

function LessonStrip({
  st,
  onFinish,
  busy,
  pulse,
}: {
  st: LessonState;
  onFinish: () => void;
  busy: boolean;
  pulse: Set<string>;
}) {
  const current = st.phase === "free" ? PHASES.length : PHASES.findIndex((p) => p.id === st.phase);
  const shown = st.targets.filter((t) => t.state !== "known" || t.kind === "new");
  const phrases = st.phrases ?? [];
  const phrasesUsed = phrases.filter((p) => p.used).length;
  return (
    <div className="strip">
      <div className="steps">
        {PHASES.map((p, i) => (
          <span key={p.id} className={`step ${i < current ? "past" : i === current ? "now" : ""}`}>
            {p.label}
          </span>
        ))}
        <span className="spacer" />
        {phrases.length > 0 ? (
          <span className={`goal ${phrasesUsed >= (st.phrase_goal ?? 0) ? "met" : ""}`}>
            {phrasesUsed}/{st.phrase_goal} phrases said
          </span>
        ) : st.goal > 0 && (
          <span className={`goal ${st.used >= st.goal ? "met" : ""}`}>
            {st.used}/{st.goal} words used
          </span>
        )}
        {st.phase !== "recap" && st.phase !== "free" && (
          <button className="finish" onClick={onFinish} disabled={busy}>
            Finish
          </button>
        )}
      </div>
      {phrases.length > 0 && (
        <div className="targets phrases">
          {phrases.map((p) => (
            <span key={p.es} className={`target phrase ${p.used ? "used" : "pending"}`} title={p.en}>
              <i />
              {p.es}
            </span>
          ))}
        </div>
      )}
      {shown.length > 0 && (
        <div className="targets">
          {shown.map((t) => (
            <span
              key={t.lemma}
              className={`target ${t.state} ${t.kind} ${pulse.has(t.lemma) ? "pulse" : ""}`}
              title={`${t.english} · ${STATE_LABEL[t.state]}`}
            >
              <i />
              {t.state === "pending" && t.kind === "new" ? "?" : t.display}
              {t.kind === "review" && <small>review</small>}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function RecapCard({ r, onNext }: { r: Recap; onNext: () => void }) {
  const learned = r.targets.filter((t) => t.state !== "known");
  const knew = r.targets.filter((t) => t.state === "known");
  return (
    <div className="recap">
      <div className="recap-head">
        <h2>{(r.phrases?.length ? r.phrases.filter((p) => p.used).length >= Math.ceil(r.phrases.length * 0.6) : r.used >= r.goal && r.goal > 0) ? "¡Lección completada!" : "Fin de la lección"}</h2>
        <span className="muted small">
          {r.turns} turns · {r.minutes} min · {r.said_ok} different words said right
        </span>
      </div>
      {(r.phrases?.length ?? 0) > 0 && (
        <>
          <h4>Your phrases</h4>
          <ul className="recap-words">
            {r.phrases!.map((p) => (
              <li key={p.es} className={p.used ? "used" : "pending"}>
                <i />
                <b>{p.es}</b> <span>{p.en}</span>
                <small>{p.used ? "said it" : "practise this one"}</small>
              </li>
            ))}
          </ul>
          <p className="muted small">The full cheat sheet stays on the home screen under “Practise a real situation”.</p>
        </>
      )}
      <h4>Today's words</h4>
      <ul className="recap-words">
        {learned.map((t) => (
          <li key={t.lemma} className={t.state}>
            <i />
            <b>{t.display}</b> <span>{t.english}</span>
            <small>{t.state === "used" ? "used on your own" : t.state === "practiced" ? "repeated" : t.state === "introduced" ? "heard it" : "not reached"}</small>
          </li>
        ))}
      </ul>
      {knew.length > 0 && <p className="muted small">You already knew: {knew.map((t) => t.display).join(", ")}</p>}
      {r.taught.length > 0 && (
        <>
          <h4>Also picked up</h4>
          <p className="small">{r.taught.map((w) => `${w.spanish} (${w.english})`).join(" · ")}</p>
        </>
      )}
      {r.mistakes.length > 0 && (
        <>
          <h4>Fixes to remember</h4>
          <p className="muted small">These come back in Repaso.</p>
          <ul className="fixlist">
            {r.mistakes.map((f, i) => (
              <li key={i}>
                <span className="strike">{f.wrong}</span> → <b>{f.right}</b>
                {f.right_en && <span className="right-en"> “{f.right_en}”</span>}
                <small>{f.why}</small>
              </li>
            ))}
          </ul>
        </>
      )}
      {r.review_next.length > 0 && <p className="muted small">Coming back for review: {r.review_next.join(", ")}</p>}
      <div className="recap-actions">
        <span className="muted small">Keep talking with Sofía, or</span>
        <button onClick={onNext}>Choose next lesson →</button>
      </div>
    </div>
  );
}

/** Wraps known words in spans that show their meaning on hover. */
function glossify(text: string, glosses?: Record<string, string>) {
  if (!glosses) return text;
  return text.split(/(\p{L}+)/u).map((part, i) => {
    const g = glosses[part.toLowerCase()];
    return g ? (
      <span key={i} className="gw" data-gloss={g}>
        {part}
      </span>
    ) : (
      part
    );
  });
}
