import { create } from "zustand";

import { surfaceBio } from "./data";
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
  connection: "demo" | "connecting" | "open" | "error";
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

const demoMessages: ConversationMessage[] = [
  {
    id: id(),
    role: "host",
    text: "周总，外界最关心的是：惠食究竟什么时候知道原承包商会退出？",
    timestamp: "10:02",
  },
  {
    id: id(),
    role: "guest",
    text: "我们和所有参与方一样，都是从公开渠道关注学校的招标安排。惠食一直尊重规范流程，也相信最终结果体现了我们的服务能力。",
    stageDirection: "短暂停顿，调整了一下袖口",
    timestamp: "10:03",
  },
  {
    id: id(),
    role: "host",
    text: "但你们中标前一个月就进校测量过后厨。是谁带队，具体哪一天？",
    timestamp: "10:03",
  },
  {
    id: id(),
    role: "guest",
    text: "具体日期我现在确实记不清。团队做过一次非正式的场地了解，这在行业里很常见，不能把正常准备解读成提前获知结果。",
    stageDirection: "低头翻看日程，语速变慢",
    timestamp: "10:04",
  },
];

const demoHints: DirectorHint[] = [
  {
    id: id(),
    text: "他在时间上改口了，钉住具体日期",
    urgency: 3,
    type: "追问",
    timestamp: "10:04",
  },
  {
    id: id(),
    text: "先别铺背景，直接问谁带队",
    urgency: 2,
    type: "打断他",
    timestamp: "10:03",
  },
  {
    id: id(),
    text: "套话，换到首次进校时间",
    urgency: 1,
    type: "换角度",
    timestamp: "10:02",
  },
];

export const useSessionStore = create<SessionStore>((set, get) => ({
  sessionId: "DEMO-042",
  scenarioId: "campus_canteen_contractor_change",
  personaName: "满嘴套话的企业家",
  surfaceBio,
  state: "LIVE",
  clockPhase: "live",
  remainingSeconds: 342,
  factsFound: 2,
  factsTotal: 5,
  foundFactIds: ["F1", "F2"],
  messages: demoMessages,
  hints: demoHints,
  activeToastId: demoHints[0].id,
  currentGuestId: null,
  inputLocked: false,
  connection: "demo",
  error: null,

  hydrate: (snapshot) =>
    set({
      sessionId: snapshot.id,
      scenarioId: snapshot.scenario_id,
      personaName: snapshot.persona_name,
      surfaceBio: snapshot.surface_bio,
      state: snapshot.state,
      remainingSeconds:
        snapshot.state === "BRIEFING"
          ? snapshot.briefing_seconds
          : snapshot.duration_seconds,
    }),
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
