import { useEffect } from "react";

import { useSessionStore } from "../store";
import type {
  GuestDonePayload,
  SessionHistory,
  SessionSnapshot,
  SessionState,
} from "../types";

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
  const hydrateHistory = useSessionStore((state) => state.hydrateHistory);
  const setConnection = useSessionStore((state) => state.setConnection);
  const setError = useSessionStore((state) => state.setError);

  useEffect(() => {
    if (!sessionId) return;

    let cancelled = false;
    let stream: EventSource | null = null;

    const connect = () => {
      if (cancelled) return;
      setConnection("connecting");
      stream = new EventSource(`/api/session/${sessionId}/stream`);

      stream.addEventListener("state_change", (event) => {
        const { state, error_message, report_id } = parse<{
          state: SessionState;
          error_message?: string;
          report_id?: string;
        }>(
          event as MessageEvent<string>,
        );
        setSessionState(state);
        if (state === "FAILED") {
          setError(error_message || "会话处理失败，可以重试结束采访。");
        }
        if (state === "DONE" && report_id) {
          window.location.href = `/review/${encodeURIComponent(report_id)}`;
        }
      });
      stream.addEventListener("clock", (event) => {
        const payload = parse<ClockPayload>(event as MessageEvent<string>);
        setClock(payload.phase, payload.remaining_seconds);
      });
      stream.addEventListener("guest_delta", (event) => {
        const { delta, request_id } = parse<{
          delta: string;
          request_id?: string | null;
        }>(event as MessageEvent<string>);
        const guestId = request_id ?? useSessionStore.getState().currentGuestId;
        if (!guestId) return;
        appendGuestText(delta, guestId);
      });
      stream.addEventListener("guest_done", (event) => {
        const payload = parse<GuestDonePayload>(event as MessageEvent<string>);
        const guestId =
          payload.request_id ??
          useSessionStore.getState().currentGuestId ??
          undefined;
        if (!guestId) {
          void fetch(`/api/session/${sessionId}/history`)
            .then((response) => response.ok ? response.json() : Promise.reject())
            .then((history: SessionHistory) => hydrateHistory(history))
            .catch(() => undefined);
          return;
        }
        finishGuestTurn(payload, guestId);
      });
      stream.addEventListener("session_error", (event) => {
        const payload = parse<{ message: string }>(event as MessageEvent<string>);
        setError(payload.message);
      });
      stream.addEventListener("report_ready", (event) => {
        const { report_id } = parse<{ report_id: string }>(
          event as MessageEvent<string>,
        );
        window.location.href = `/review/${encodeURIComponent(report_id)}`;
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
        const [snapshotResponse, historyResponse] = await Promise.all([
          fetch(`/api/session/${sessionId}`),
          fetch(`/api/session/${sessionId}/history`),
        ]);
        if (!snapshotResponse.ok || !historyResponse.ok) {
          throw new Error("session not found");
        }
        hydrate((await snapshotResponse.json()) as SessionSnapshot);
        hydrateHistory((await historyResponse.json()) as SessionHistory);
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
    };
  }, [
    addHint,
    appendGuestText,
    clearToast,
    finishGuestTurn,
    hydrate,
    hydrateHistory,
    sessionId,
    setClock,
    setConnection,
    setError,
    setSessionState,
  ]);
}
