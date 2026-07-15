import {
  Clock3,
  FileAudio,
  Headphones,
  LockKeyhole,
  LoaderCircle,
  Mic,
  Radio,
  Send,
  Square,
  UserRound,
  WifiOff,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { useSessionEvents } from "../hooks/useSessionEvents";
import { useVoiceTranscription } from "../hooks/useVoiceTranscription";
import { useSessionStore } from "../store";
import type { DirectorHint, GuestDonePayload, SessionSnapshot } from "../types";

const formatClock = (seconds: number) => {
  const safe = Math.max(0, seconds);
  return `${String(Math.floor(safe / 60)).padStart(2, "0")}:${String(
    safe % 60,
  ).padStart(2, "0")}`;
};

const urgencyLabel = (urgency: DirectorHint["urgency"]) =>
  urgency === 3 ? "立即" : urgency === 2 ? "注意" : "建议";

function DirectorCard({ hint, toast = false }: { hint: DirectorHint; toast?: boolean }) {
  return (
    <article
      className={`director-card urgency-${hint.urgency}${toast ? " director-toast" : ""}`}
    >
      <div className="director-card__meta">
        <span className="director-card__level">
          <span className="director-card__dot" />
          {urgencyLabel(hint.urgency)}
        </span>
        <span>{hint.timestamp}</span>
      </div>
      <p>{hint.text}</p>
      {hint.type && <span className="director-card__type">{hint.type}</span>}
    </article>
  );
}

function Conversation() {
  const messages = useSessionStore((state) => state.messages);
  const personaName = useSessionStore((state) => state.personaName);
  const feedRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    feedRef.current?.scrollTo({
      top: feedRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  return (
    <div className="conversation-feed" ref={feedRef} aria-live="polite">
      <div className="conversation-start">
        <span>REC</span>
        访谈记录已开始 · 所有发言将进入复盘
      </div>
      {messages.map((message) => (
        <article
          className={`message-row message-row--${message.role}`}
          key={message.id}
        >
          <div className="message-avatar" aria-hidden="true">
            {message.role === "host" ? "主" : "嘉"}
          </div>
          <div className="message-content">
            <div className="message-meta">
              <span>{message.role === "host" ? "主持人 · 你" : `${personaName || "嘉宾"} · 嘉宾`}</span>
              <time>{message.timestamp}</time>
            </div>
            <div className="message-bubble">
              {message.stageDirection && (
                <p className="stage-direction">（{message.stageDirection}）</p>
              )}
              <p>
                {message.text}
                {message.typing && <span className="typing-caret" />}
              </p>
              {message.typing && !message.text && (
                <span className="thinking-dots" aria-label="嘉宾正在回应">
                  <i />
                  <i />
                  <i />
                </span>
              )}
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}

export function StudioPage() {
  const params = useMemo(() => new URLSearchParams(window.location.search), []);
  const requestedSessionId = params.get("session");
  useSessionEvents(requestedSessionId);

  const store = useSessionStore();
  const [draft, setDraft] = useState("");
  const [ending, setEnding] = useState(false);
  const voice = useVoiceTranscription((text) => {
    setDraft((current) =>
      [current.trim(), text.trim()].filter(Boolean).join(" ").slice(0, 300),
    );
  });
  const activeToast = store.hints.find(
    (hint) => hint.id === store.activeToastId,
  );
  const canSend =
    draft.trim().length > 0 &&
    !store.inputLocked &&
    voice.state === "idle" &&
    (store.state === "LIVE" || store.state === "WRAPPING");
  const voiceEnabled =
    !store.inputLocked &&
    (store.state === "LIVE" || store.state === "WRAPPING");
  const canEnd =
    store.turnCount > 0 &&
    !store.inputLocked &&
    voice.state === "idle" &&
    (store.state === "LIVE" ||
      store.state === "WRAPPING" ||
      store.state === "FAILED");

  useEffect(() => {
    if (!requestedSessionId) window.location.replace("/");
  }, [requestedSessionId]);

  useEffect(() => {
    if (!requestedSessionId) return;
    void fetch("/api/transcription/warmup", { method: "POST" }).catch(() => {
      // The upload action reports a concrete error if warmup actually failed.
    });
  }, [requestedSessionId]);

  useEffect(() => {
    if (!store.activeToastId) return;
    const toastId = store.activeToastId;
    const timer = window.setTimeout(() => store.clearToast(toastId), 8_000);
    return () => window.clearTimeout(timer);
  }, [store.activeToastId, store.clearToast]);

  const submit = async () => {
    const text = draft.trim();
    if (!text || !canSend) return;
    setDraft("");
    const guestId = store.beginHostTurn(text);

    if (!requestedSessionId) return store.failGuestTurn("会话不存在，请返回首页重新开始。");

    try {
      const response = await fetch(`/api/session/${requestedSessionId}/turn`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, request_id: guestId }),
      });
      if (!response.ok) throw new Error("turn request failed");
      const payload = (await response.json()) as { guest: GuestDonePayload };
      if (useSessionStore.getState().currentGuestId === guestId) {
        store.finishGuestTurn(payload.guest, guestId);
      }
    } catch {
      store.failGuestTurn("发送失败，请检查访谈服务后重试。");
    }
  };

  const endInterview = async () => {
    if (ending || !canEnd) return;
    setEnding(true);
    if (!requestedSessionId) return;
    try {
      const response = await fetch(`/api/session/${requestedSessionId}/end`, {
        method: "POST",
      });
      if (!response.ok) throw new Error("end request failed");
      const snapshot = (await response.json()) as SessionSnapshot;
      if (snapshot.state === "FAILED" || !snapshot.report_id) {
        setEnding(false);
        store.setError(snapshot.error_message || "复盘生成失败，可以再次结束采访重试。");
        return;
      }
      window.location.href = `/review/${snapshot.report_id}`;
    } catch {
      setEnding(false);
      store.setError("暂时无法结束采访，请稍后重试。");
    }
  };

  return (
    <main className="studio-shell">
      <header className="studio-topbar">
        <a className="brand" href="/" aria-label="Newsroom 首页">
          <span className="brand-mark">N</span>
          <span>
            <strong>NEWSROOM</strong>
            <small>INTERVIEW LAB</small>
          </span>
        </a>
        <div className="topbar-center">
          <span className="live-indicator"><i /> LIVE</span>
          <span>{store.topic || "正在载入真实选题"}</span>
        </div>
        <div className="session-code">
          SESSION&nbsp; {store.sessionId.slice(0, 12).toUpperCase()}
        </div>
      </header>

      <div className="studio-grid">
        <aside className="briefing-panel panel-scroll">
          <div className="panel-kicker"><Radio size={14} /> 嘉宾简报</div>
          <section className="persona-block">
            <div className="persona-avatar"><UserRound size={26} /></div>
            <div>
              <span>今日嘉宾</span>
              <h1>{store.personaName}</h1>
            </div>
          </section>

          <section className="bio-card">
            <div className="bio-card__label">公开资料 · SURFACE BIO</div>
            <p>{store.surfaceBio}</p>
          </section>

          <section className="clock-card">
            <div className="clock-card__top">
              <span><Clock3 size={14} /> 剩余时间</span>
              <span className={store.state === "WRAPPING" ? "state-warn" : ""}>
                {store.state === "BRIEFING" ? "备稿" : store.state === "WRAPPING" ? "收尾" : "采访中"}
              </span>
            </div>
            <strong>{formatClock(store.remainingSeconds)}</strong>
            <div className="clock-track"><span /></div>
          </section>

          <section className="fact-progress" aria-label={`已挖出 ${store.factsFound}/${store.factsTotal} 条`}>
            <div className="fact-progress__top">
              <div>
                <span>线索进度</span>
                <strong>已挖出 {store.factsFound}/{store.factsTotal} 条</strong>
              </div>
              <span className="fact-count">{store.factsFound}</span>
            </div>
            <div className="fact-bars" aria-hidden="true">
              {Array.from({ length: store.factsTotal }, (_, index) => (
                <i className={index < store.factsFound ? "is-found" : ""} key={index} />
              ))}
            </div>
            <p>采访结束前只显示数量，不揭晓内容。</p>
          </section>
        </aside>

        <section className="dialogue-panel">
          <header className="dialogue-header">
            <div>
              <span className="eyebrow">STUDIO A · 现场</span>
              <h2>对话流</h2>
            </div>
            <button className="end-button" type="button" onClick={endInterview} disabled={ending || !canEnd}>
              <Square size={12} fill="currentColor" />
              {ending ? "正在生成复盘" : store.state === "FAILED" ? "重试复盘" : "结束采访"}
            </button>
          </header>

          <Conversation />

          <footer className="composer-wrap">
            {store.error && <div className="inline-error"><WifiOff size={14} /> {store.error}</div>}
            {voice.error && <div className="inline-error"><WifiOff size={14} /> {voice.error}</div>}
            {store.inputLocked && (
              <div className="input-lock"><LockKeyhole size={13} /> 嘉宾回应中，输入已锁定</div>
            )}
            <div className="voice-toolbar">
              <button
                className={`voice-record${voice.state === "recording" ? " is-recording" : ""}`}
                type="button"
                disabled={!voiceEnabled || voice.state === "transcribing"}
                onClick={() =>
                  voice.state === "recording"
                    ? voice.stopRecording()
                    : void voice.startRecording()
                }
              >
                {voice.state === "recording" ? <Square size={12} fill="currentColor" /> : <Mic size={15} />}
                {voice.state === "recording"
                  ? `停止录音 ${formatClock(voice.seconds)}`
                  : "麦克风提问"}
              </button>
              <label className={`voice-upload${voice.state !== "idle" || !voiceEnabled ? " is-disabled" : ""}`}>
                <FileAudio size={15} /> 上传音频
                <input
                  accept="audio/*,video/webm,video/mp4"
                  disabled={voice.state !== "idle" || !voiceEnabled}
                  type="file"
                  onChange={(event) => {
                    voice.uploadFile(event.target.files?.[0] ?? null);
                    event.target.value = "";
                  }}
                />
              </label>
              <span className="voice-state">
                {voice.state === "transcribing" ? (
                  <><LoaderCircle className="spin" size={13} /> 本地 Whisper 正在转录，首次使用需加载模型</>
                ) : voice.lastResult ? (
                  `已转录 ${voice.lastResult.duration_seconds.toFixed(1)} 秒 · ${voice.lastResult.device}`
                ) : (
                  "录音停止后先回填文字，确认后再发送"
                )}
              </span>
            </div>
            <div className={`composer${store.inputLocked ? " is-locked" : ""}`}>
              <textarea
                aria-label="输入采访问题"
                disabled={!voiceEnabled || voice.state === "recording"}
                maxLength={300}
                onChange={(event) => setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && !event.shiftKey) {
                    event.preventDefault();
                    void submit();
                  }
                }}
                placeholder={store.state === "BRIEFING" ? "备稿结束后可开始提问…" : "输入你的下一个问题…"}
                rows={2}
                value={draft}
              />
              <button type="button" aria-label="发送问题" disabled={!canSend} onClick={() => void submit()}>
                <Send size={18} />
              </button>
            </div>
            <div className="composer-foot">
              <span>Enter 发送 · Shift + Enter 换行</span>
              <span>{draft.length}/300</span>
            </div>
          </footer>
        </section>

        <aside className="director-panel">
          <header className="director-header">
            <div className="director-title-icon"><Headphones size={19} /></div>
            <div>
              <span className="eyebrow">CONTROL ROOM</span>
              <h2>导播耳返</h2>
            </div>
            <span className="signal-bars" aria-label="耳返已连接"><i /><i /><i /></span>
          </header>

          <section className="toast-stage">
            <div className="toast-stage__label">
              <span>实时提示</span>
              <i />
            </div>
            {activeToast ? (
              <DirectorCard hint={activeToast} toast />
            ) : (
              <div className="toast-empty"><Headphones size={18} /> 正在监听对话信号</div>
            )}
          </section>

          <section className="hint-history">
            <div className="history-heading">
              <span>历史提示</span>
              <strong>{store.hints.length}</strong>
            </div>
            <div className="history-list">
              {store.hints.map((hint) => <DirectorCard hint={hint} key={hint.id} />)}
            </div>
          </section>

        </aside>
      </div>
    </main>
  );
}
