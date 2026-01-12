import { create } from "zustand";
import { MessagePayload, ChannelSummary, UserSummary } from "@lightning/shared";

interface ChatState {
  accessToken: string | null;
  refreshToken: string | null;
  user: UserSummary | null;
  channels: ChannelSummary[];
  activeChannelId: string | null;
  messages: Record<string, MessagePayload[]>;
  members: Record<string, UserSummary[]>;
  notifications: Record<string, number>;
  theme: "dark" | "light";
  setAuth: (token: string, refresh: string, user: UserSummary) => void;
  clearAuth: () => void;
  setChannels: (channels: ChannelSummary[]) => void;
  setActiveChannel: (channelId: string | null) => void;
  addMessage: (message: MessagePayload) => void;
  setMessages: (channelId: string, messages: MessagePayload[]) => void;
  setMembers: (channelId: string, members: UserSummary[]) => void;
  incrementNotification: (channelId: string) => void;
  clearNotification: (channelId: string) => void;
  toggleTheme: () => void;
}

export const useChatStore = create<ChatState>((set) => ({
  accessToken: null,
  refreshToken: null,
  user: null,
  channels: [],
  activeChannelId: null,
  messages: {},
  members: {},
  notifications: {},
  theme: "dark",
  setAuth: (token, refresh, user) => set({ accessToken: token, refreshToken: refresh, user }),
  clearAuth: () => set({ accessToken: null, refreshToken: null, user: null }),
  setChannels: (channels) => set({ channels }),
  setActiveChannel: (channelId) => set({ activeChannelId: channelId }),
  addMessage: (message) =>
    set((state) => {
      const channelMessages = state.messages[message.channelId] ?? [];
      return {
        messages: {
          ...state.messages,
          [message.channelId]: [...channelMessages, message]
        }
      };
    }),
  setMessages: (channelId, messages) =>
    set((state) => ({
      messages: {
        ...state.messages,
        [channelId]: messages
      }
    })),
  setMembers: (channelId, members) =>
    set((state) => ({
      members: {
        ...state.members,
        [channelId]: members
      }
    })),
  incrementNotification: (channelId) =>
    set((state) => ({
      notifications: {
        ...state.notifications,
        [channelId]: (state.notifications[channelId] ?? 0) + 1
      }
    })),
  clearNotification: (channelId) =>
    set((state) => ({
      notifications: {
        ...state.notifications,
        [channelId]: 0
      }
    })),
  toggleTheme: () =>
    set((state) => ({
      theme: state.theme === "dark" ? "light" : "dark"
    }))
}));
