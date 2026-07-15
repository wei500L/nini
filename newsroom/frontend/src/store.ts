import { create } from "zustand";

import type {
  ConversationMessage,
  DirectorHint,
  GuestDonePayload,
  SessionSnapshot,
  SessionState,
} from "./types";


const nowLabel = () =>
  new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date());

const id = () => crypto.randomUUID();

type SessionStore = {
  sessionId: string;
  scenarioId: string;
  topic: string;
  personaName: string;
  surfaceBio: string;
  state: SessionState;
  clockPhase: "briefing" | "live";
  remainingSeconds: number;
  factsFound: number;
  factsTotal: number;
  foundFactIds: string[];
  messages: ConversationMessage[];
  hints: DirectorHint[];
  activeToastId: string | null;
  currentGuestId: string | null;
  inputLocked: boolean;
  connection: "idle" | "connecting" | "open" | "error";
  error: string | null;
  hydrate: (snapshot: SessionSnapshot) => void;
  setConnection: (connection: SessionStore["connection"]) => void;
  setError: (message: string | null) => void;
  setSessionState: (state: SessionState) => void;
  setClock: (phase: "briefing" | "live", seconds: number) => void;
  beginHostTurn: (text: string) => string;
  appendGuestText: (text: string, guestId?: string) => void;
  finishGuestTurn: (payload: GuestDonePayload, guestId?: string) => void;
  failGuestTurn: (message: string) => void;
  addHint: (hint: Omit<DirectorHint, "id" | "timestamp">) => string;
  clearToast: (id: string) => void;
};

export const useSessionStore = create<SessionStore>((set, get) => ({
  sessionId: "",
  scenarioId: "",
  topic: "",
  personaName: "",
  surfaceBio: "",
  state: "IDLE",
  clockPhase: "briefing",
  remainingSeconds: 0,
  factsFound: 0,
  factsTotal: 0,
  foundFactIds: [],
  messages: [],
  hints: [],
  activeToastId: null,
  currentGuestId: null,
  inputLocked: false,
  connection: "idle",
  error: null,

  hydrate: (snapshot) => {
    sessionStorage.setItem(
      `newsroom:session:${snapshot.id}`,
      JSON.stringify(snapshot),
    );
    set({
      sessionId: snapshot.id,
      scenarioId: snapshot.scenario_id,
      topic: snapshot.topic,
      personaName: snapshot.persona_name,
      surfaceBio: snapshot.surface_bio,
      state: snapshot.state,
      clockPhase: snapshot.state === "BRIEFING" ? "briefing" : "live",
      remainingSeconds:
        snapshot.state === "BRIEFING"
          ? snapshot.briefing_seconds
          : snapshot.duration_seconds,
      factsFound: 0,
      factsTotal: snapshot.facts_total,
      foundFactIds: [],
      messages: [],
      hints: [],
      activeToastId: null,
      currentGuestId: null,
      inputLocked: false,
      error: null,
    });
  },
  setConnection: (connection) => set({ connection }),
  setError: (error) => set({ error }),
  setSessionState: (state) => set({ state }),
  setClock: (clockPhase, remainingSeconds) =>
    set({ clockPhase, remainingSeconds }),
  beginHostTurn: (text) => {
    const guestId = id();
    set((state) => ({
      messages: [
        ...state.messages,
        { id: id(), role: "host", text, timestamp: nowLabel() },
        {
          id: guestId,
          role: "guest",
          text: "",
          timestamp: nowLabel(),
          typing: true,
        },
      ],
      currentGuestId: guestId,
      inputLocked: true,
      error: null,
    }));
    return guestId;
  },
  appendGuestText: (text, guestId) =>
    set((state) => {
      const targetId = guestId ?? state.currentGuestId;
      return {
        messages: state.messages.map((message) =>
          message.id === targetId
            ? { ...message, text: message.text + text }
            : message,
        ),
      };
    }),
  finishGuestTurn: (payload, guestId) => {
    const foundFactIds = [...get().foundFactIds];
    if (
      payload.action === "reveal" &&
      payload.targeted_fact &&
      !foundFactIds.includes(payload.targeted_fact)
    ) {
      foundFactIds.push(payload.targeted_fact);
    }
    set((state) => {
      const targetId = guestId ?? state.currentGuestId;
      return {
        messages: state.messages.map((message) =>
          message.id === targetId
            ? {
                ...message,
                stageDirection: payload.stage_direction,
                typing: false,
              }
            : message,
        ),
        currentGuestId:
          state.currentGuestId === targetId ? null : state.currentGuestId,
        inputLocked: false,
        foundFactIds,
        factsFound: Math.min(foundFactIds.length, state.factsTotal),
      };
    });
  },
  failGuestTurn: (message) =>
    set((state) => ({
      messages: state.messages.filter(
        (item) => item.id !== state.currentGuestId || item.text.length > 0,
      ),
      currentGuestId: null,
      inputLocked: false,
      error: message,
    })),
  addHint: (hint) => {
    const newHint: DirectorHint = { ...hint, id: id(), timestamp: nowLabel() };
    set((state) => ({
      hints: [newHint, ...state.hints],
      activeToastId: newHint.id,
    }));
    return newHint.id;
  },
  clearToast: (toastId) => {
    if (get().activeToastId === toastId) set({ activeToastId: null });
  },
}));
