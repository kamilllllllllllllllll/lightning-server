export type UserPresence = "online" | "offline" | "away";

export interface AuthTokens {
  accessToken: string;
  refreshToken: string;
}

export interface UserSummary {
  id: string;
  displayName: string;
  avatarUrl?: string | null;
  presence?: UserPresence;
}

export interface MessagePayload {
  id: string;
  channelId: string;
  senderId: string;
  content: string;
  createdAt: string;
  attachments?: Attachment[];
}

export interface Attachment {
  id: string;
  filename: string;
  url: string;
  contentType: string;
  size: number;
}

export interface ChannelSummary {
  id: string;
  name: string;
  isGroup: boolean;
  memberIds: string[];
}
