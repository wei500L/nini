import { useEffect, useRef } from "react";

import { useSessionStore } from "../store";
import type { GuestDonePayload, SessionSnapshot, SessionState } from "../types";

type ClockPayload = {
  phase: "briefing" | "live";
  remaining_seconds: number;
};

type DirectorPayload = {
  text: string;
  urgency?: 1 | 2 | 3;
  type?: string;
  source?: string;
};

const parse = <T,>(event: MessageEvent<string>): T =>
  JSON.parse(event.data) as T;

export function useSessionEvents(sessionId: string | null) {
  const appendGuestText = useSessionStore((state) => state.appendGuestText);
  const finishGuestTurn = useSessionStore((state) => state.finishGuestTurn);
  const setSessionState = useSessionStore((state) => state.setSessionState);
  const setClock = useSessionStore((state) => state.setClock);
  const addHint = useSessionStore((state) => state.addHint);
  const clearToast = useSessionStore((state) => state.clearToast);
  const hydrate = useSessionStore((state) => state.hydrate);
  const setConnection = useSessionStore((state) => state.setConnection);
  const setError = useSessionStore((state) => state.setError);

  const characterQueue = useRef<Array<{ character: string; guestId: string }>>([]);
  const typewriterTimer = useRef<number | null>(null);
  const pendingDone = useRef<{
    payload: GuestDonePayload;
    guestId?: string;
  } | null>(null);

  useEffect(() => {
    if (!sessionId) return;

    let cancelled = false;
    let stream: EventSource | null = null;

    const startTypewriter = () => {
      if (typewriterTimer.current !== null) return;
      typewriterTimer.current = window.setInterval(() => {
        const next = characterQueue.current.shift();
        if (next) appendGuestText(next.character, next.guestId);
        if (characterQueue.current.length === 0) {
          window.clearInterval(typewriterTimer.current ?? undefined);
          typewriterTimer.current = null;
          if (pendingDone.current) {
            finishGuestTurn(
              pendingDone.current.payload,
              pendingDone.current.guestId,
            );
            pendingDone.current = null;
          }
        }
      }, 24);
    };

    const connect = () => {
      if (cancelled) return;
      setConnection("connecting");
      stream = new EventSource(`/api/session/${sessionId}/stream`);

      stream.addEventListener("state_change", (event) => {
        const { state } = parse<{ state: SessionState }>(
          event as MessageEvent<string>,
        );
        setSessionState(state);
      });
      stream.addEventListener("clock", (event) => {
        const payload = parse<ClockPayload>(event as MessageEvent<string>);
        setClock(payload.phase, payload.remaining_seconds);
      });
      stream.addEventListener("guest_delta", (event) => {
        const { delta } = parse<{ delta: string }>(event as MessageEvent<string>);
        const guestId = useSessionStore.getState().currentGuestId;
        if (!guestId) return;
        characterQueue.current.push(
          ...Array.from(delta).map((character) => ({ character, guestId })),
        );
        startTypewriter();
      });
      stream.addEventListener("guest_done", (event) => {
        const guestId = useSessionStore.getState().currentGuestId ?? undefined;
        const payload = parse<GuestDonePayload>(event as MessageEvent<string>);
        if (characterQueue.current.length > 0) {
          pendingDone.current = { payload, guestId };
        } else {
          finishGuestTurn(payload, guestId);
        }
      });
      stream.addEventListener("director_hint", (event) => {
        const payload = parse<DirectorPayload>(event as MessageEvent<string>);
        const toastId = addHint({
          text: payload.text,
          urgency: payload.urgency ?? 2,
          type: payload.type,
          source: payload.source,
        });
        window.setTimeout(() => clearToast(toastId), 8_000);
      });
      stream.onopen = () => {
        setConnection("open");
        setError(null);
      };
      stream.onerror = () => setConnection("error");
    };

    const initialize = async () => {
      try {
        const cached = sessionStorage.getItem(`newsroom:session:${sessionId}`);
        if (cached) {
          hydrate(JSON.parse(cached) as SessionSnapshot);
        } else {
          const response = await fetch(`/api/session/${sessionId}`);
          if (!response.ok) throw new Error("session not found");
          hydrate((await response.json()) as SessionSnapshot);
        }
        connect();
      } catch {
        if (!cancelled) {
          setConnection("error");
          setError("无法加载访谈会话，请返回首页重新开始。");
        }
      }
    };

    void initialize();

    return () => {
      cancelled = true;
      stream?.close();
      characterQueue.current = [];
      pendingDone.current = null;
      if (typewriterTimer.current !== null) {
        window.clearInterval(typewriterTimer.current);
        typewriterTimer.current = null;
      }
    };
  }, [
    addHint,
    appendGuestText,
    clearToast,
    finishGuestTurn,
    hydrate,
    sessionId,
    setClock,
    setConnection,
    setError,
    setSessionState,
  ]);
}
