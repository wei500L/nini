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

  const characterQueue = useRef<Array<{ character: string; guestId: string }>>([]);
  const typewriterTimer = useRef<number | null>(null);

  useEffect(() => {
    if (!sessionId) return;

    const cached = sessionStorage.getItem(`newsroom:session:${sessionId}`);
    if (cached) {
      hydrate(JSON.parse(cached) as SessionSnapshot);
    }

    setConnection("connecting");
    const stream = new EventSource(`/api/session/${sessionId}/stream`);

    const startTypewriter = () => {
      if (typewriterTimer.current !== null) return;
      typewriterTimer.current = window.setInterval(() => {
        const next = characterQueue.current.shift();
        if (next) appendGuestText(next.character, next.guestId);
        if (characterQueue.current.length === 0) {
          window.clearInterval(typewriterTimer.current ?? undefined);
          typewriterTimer.current = null;
        }
      }, 24);
    };

    const onState = (event: Event) => {
      const { state } = parse<{ state: SessionState }>(
        event as MessageEvent<string>,
      );
      setSessionState(state);
    };
    const onClock = (event: Event) => {
      const payload = parse<ClockPayload>(event as MessageEvent<string>);
      setClock(payload.phase, payload.remaining_seconds);
    };
    const onDelta = (event: Event) => {
      const { delta } = parse<{ delta: string }>(event as MessageEvent<string>);
      const guestId = useSessionStore.getState().currentGuestId;
      if (!guestId) return;
      characterQueue.current.push(
        ...Array.from(delta).map((character) => ({ character, guestId })),
      );
      startTypewriter();
    };
    const onDone = (event: Event) => {
      const guestId = useSessionStore.getState().currentGuestId;
      finishGuestTurn(
        parse<GuestDonePayload>(event as MessageEvent<string>),
        guestId ?? undefined,
      );
    };
    const onHint = (event: Event) => {
      const payload = parse<DirectorPayload>(event as MessageEvent<string>);
      const toastId = addHint({
        text: payload.text,
        urgency: payload.urgency ?? 2,
        type: payload.type,
        source: payload.source,
      });
      window.setTimeout(() => clearToast(toastId), 8_000);
    };

    stream.addEventListener("state_change", onState);
    stream.addEventListener("clock", onClock);
    stream.addEventListener("guest_delta", onDelta);
    stream.addEventListener("guest_done", onDone);
    stream.addEventListener("director_hint", onHint);
    stream.onopen = () => setConnection("open");
    stream.onerror = () => setConnection("error");

    return () => {
      stream.close();
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
    setSessionState,
  ]);
}
