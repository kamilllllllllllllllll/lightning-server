import { useEffect, useMemo, useState } from "react";
import { apiFetch, API_URL } from "./api/client";
import { getSocket, disconnectSocket } from "./api/socket";
import { useChatStore } from "./store/useChatStore";
import { MessagePayload } from "@lightning/shared";

interface AuthResponse {
  accessToken: string;
  refreshToken: string;
  user: { id: string; email: string; displayName: string };
}

interface Channel {
  id: string;
  name: string;
  isGroup: boolean;
}

export function App() {
  const {
    accessToken,
    user,
    channels,
    activeChannelId,
    messages,
    notifications,
    theme,
    setAuth,
    clearAuth,
    setChannels,
    setActiveChannel,
    addMessage,
    setMessages,
    clearNotification,
    toggleTheme
  } = useChatStore();
  const [authMode, setAuthMode] = useState<"login" | "register">("login");
  const [formState, setFormState] = useState({ email: "", password: "", displayName: "" });
  const [messageDraft, setMessageDraft] = useState("");
  const [file, setFile] = useState<File | null>(null);

  useEffect(() => {
    if (!accessToken) {
      disconnectSocket();
      return;
    }
    const socket = getSocket(accessToken);
    socket.on("message:new", (message: MessagePayload) => {
      addMessage(message);
      if (message.channelId !== activeChannelId) {
        useChatStore.getState().incrementNotification(message.channelId);
      }
    });
    return () => {
      socket.off("message:new");
    };
  }, [accessToken, activeChannelId, addMessage]);

  useEffect(() => {
    if (!accessToken) {
      return;
    }
    apiFetch<Channel[]>("/messages/channels", {
      headers: { Authorization: `Bearer ${accessToken}` }
    }).then(setChannels);
  }, [accessToken, setChannels]);

  useEffect(() => {
    if (!accessToken || !activeChannelId) {
      return;
    }
    apiFetch<MessagePayload[]>(`/messages?channelId=${activeChannelId}`, {
      headers: { Authorization: `Bearer ${accessToken}` }
    }).then((data) => setMessages(activeChannelId, data.reverse()));
    getSocket(accessToken).emit("channel:join", { channelId: activeChannelId });
    clearNotification(activeChannelId);
  }, [accessToken, activeChannelId, setMessages, clearNotification]);

  const activeMessages = useMemo(() => {
    if (!activeChannelId) {
      return [];
    }
    return messages[activeChannelId] ?? [];
  }, [activeChannelId, messages]);

  const handleAuthSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    const endpoint = authMode === "login" ? "/auth/login" : "/auth/register";
    const payload =
      authMode === "login"
        ? { email: formState.email, password: formState.password }
        : formState;
    const response = await apiFetch<AuthResponse>(endpoint, {
      method: "POST",
      body: JSON.stringify(payload)
    });
    setAuth(response.accessToken, response.refreshToken, {
      id: response.user.id,
      displayName: response.user.displayName,
      avatarUrl: null
    });
  };

  const sendMessage = async () => {
    if (!accessToken || !activeChannelId || !messageDraft.trim()) {
      return;
    }
    let attachmentIds: string[] | undefined;
    if (file) {
      const form = new FormData();
      form.append("file", file);
      const uploadResponse = await fetch(`${API_URL}/uploads`, {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
        body: form
      });
      const attachment = await uploadResponse.json();
      attachmentIds = [attachment.id];
      setFile(null);
    }
    await apiFetch("/messages", {
      method: "POST",
      headers: { Authorization: `Bearer ${accessToken}` },
      body: JSON.stringify({ channelId: activeChannelId, content: messageDraft, attachmentIds })
    });
    setMessageDraft("");
  };

  if (!accessToken || !user) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-midnight-900 text-slate-500">
        <form
          className="w-full max-w-md space-y-4 rounded-2xl bg-midnight-800 p-8 shadow-xl"
          onSubmit={handleAuthSubmit}
        >
          <div className="space-y-2">
            <h1 className="text-2xl font-semibold text-white">Arcadia Chat</h1>
            <p className="text-sm text-slate-700">
              {authMode === "login"
                ? "Welcome back. Sign in to continue."
                : "Create your Arcadia account."}
            </p>
          </div>
          {authMode === "register" && (
            <input
              className="w-full rounded-lg bg-midnight-700 px-4 py-3 text-white"
              placeholder="Display name"
              value={formState.displayName}
              onChange={(event) => setFormState({ ...formState, displayName: event.target.value })}
            />
          )}
          <input
            className="w-full rounded-lg bg-midnight-700 px-4 py-3 text-white"
            placeholder="Email"
            type="email"
            value={formState.email}
            onChange={(event) => setFormState({ ...formState, email: event.target.value })}
          />
          <input
            className="w-full rounded-lg bg-midnight-700 px-4 py-3 text-white"
            placeholder="Password"
            type="password"
            value={formState.password}
            onChange={(event) => setFormState({ ...formState, password: event.target.value })}
          />
          <button className="w-full rounded-lg bg-accent-500 px-4 py-3 font-semibold text-white">
            {authMode === "login" ? "Sign in" : "Create account"}
          </button>
          <button
            type="button"
            className="w-full text-sm text-slate-700"
            onClick={() => setAuthMode(authMode === "login" ? "register" : "login")}
          >
            {authMode === "login" ? "Need an account?" : "Already have an account?"}
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className={theme === "dark" ? "bg-midnight-900 text-slate-500" : "bg-white text-slate-700"}>
      <div className="flex h-screen">
        <aside className="flex w-20 flex-col items-center gap-4 bg-midnight-800 py-6">
          <div className="h-12 w-12 rounded-2xl bg-accent-500 text-center text-lg font-bold text-white">
            A
          </div>
          <div className="h-10 w-10 rounded-2xl bg-midnight-700" />
          <div className="h-10 w-10 rounded-2xl bg-midnight-700" />
        </aside>
        <aside className="w-72 border-r border-midnight-700 bg-midnight-800 p-4">
          <div className="mb-4 flex items-center justify-between">
            <h2 className="text-sm uppercase tracking-wide text-slate-700">Channels</h2>
            <button
              className="text-xs text-accent-500"
              onClick={() =>
                apiFetch<Channel>("/messages/channels", {
                  method: "POST",
                  headers: { Authorization: `Bearer ${accessToken}` },
                  body: JSON.stringify({ name: "New Group", memberIds: [] })
                }).then((channel) => setChannels([channel, ...channels]))
              }
            >
              + New
            </button>
          </div>
          <ul className="space-y-2">
            {channels.map((channel) => (
              <li
                key={channel.id}
                className={`flex items-center justify-between rounded-lg px-3 py-2 text-sm ${
                  activeChannelId === channel.id ? "bg-midnight-700 text-white" : "text-slate-700"
                }`}
                onClick={() => setActiveChannel(channel.id)}
              >
                <span>{channel.name}</span>
                {notifications[channel.id] ? (
                  <span className="rounded-full bg-accent-500 px-2 text-xs text-white">
                    {notifications[channel.id]}
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </aside>
        <main className="flex flex-1 flex-col">
          <header className="flex items-center justify-between border-b border-midnight-700 px-6 py-4">
            <div>
              <h3 className="text-lg font-semibold text-white">
                {channels.find((channel) => channel.id === activeChannelId)?.name ?? "Select a channel"}
              </h3>
              <p className="text-xs text-slate-700">{user.displayName} • Online</p>
            </div>
            <div className="flex items-center gap-3">
              <button className="text-xs text-slate-700" onClick={toggleTheme}>
                Toggle theme
              </button>
              <button
                className="text-xs text-slate-700"
                onClick={() => {
                  clearAuth();
                  disconnectSocket();
                }}
              >
                Logout
              </button>
            </div>
          </header>
          <section className="flex-1 space-y-4 overflow-y-auto px-6 py-4">
            {activeMessages.map((message) => (
              <div key={message.id} className="rounded-xl bg-midnight-800 p-4">
                <p className="text-sm text-white">{message.content}</p>
                <span className="text-xs text-slate-700">{new Date(message.createdAt).toLocaleString()}</span>
              </div>
            ))}
          </section>
          <footer className="border-t border-midnight-700 p-4">
            <div className="flex items-center gap-3 rounded-xl bg-midnight-800 px-4 py-3">
              <input
                className="flex-1 bg-transparent text-sm text-white outline-none"
                placeholder="Message"
                value={messageDraft}
                onChange={(event) => setMessageDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    sendMessage();
                  }
                }}
              />
              <input
                type="file"
                className="text-xs text-slate-700"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
              <button className="rounded-lg bg-accent-500 px-4 py-2 text-sm text-white" onClick={sendMessage}>
                Send
              </button>
            </div>
          </footer>
        </main>
        <aside className="hidden w-64 border-l border-midnight-700 bg-midnight-800 p-4 xl:block">
          <h3 className="text-sm uppercase tracking-wide text-slate-700">Members</h3>
          <div className="mt-4 rounded-lg bg-midnight-700 p-3 text-sm text-white">{user.displayName}</div>
        </aside>
      </div>
    </div>
  );
}
