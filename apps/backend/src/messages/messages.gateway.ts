import {
  ConnectedSocket,
  MessageBody,
  OnGatewayConnection,
  OnGatewayDisconnect,
  SubscribeMessage,
  WebSocketGateway,
  WebSocketServer
} from "@nestjs/websockets";
import { JwtService } from "@nestjs/jwt";
import { Server, Socket } from "socket.io";
import { MessagesService } from "./messages.service";
import { PresenceService } from "../presence/presence.service";

interface SocketUser {
  userId: string;
  displayName: string;
}

@WebSocketGateway({ cors: { origin: true, credentials: true } })
export class MessagesGateway implements OnGatewayConnection, OnGatewayDisconnect {
  @WebSocketServer() server!: Server;

  constructor(
    private readonly jwtService: JwtService,
    private readonly messagesService: MessagesService,
    private readonly presenceService: PresenceService
  ) {}

  async handleConnection(client: Socket) {
    try {
      const token = client.handshake.auth?.token as string | undefined;
      if (!token) {
        client.disconnect();
        return;
      }
      const payload = await this.jwtService.verifyAsync<{ sub: string; displayName: string }>(token, {
        secret: process.env.JWT_SECRET || "dev-secret"
      });
      client.data.user = { userId: payload.sub, displayName: payload.displayName } as SocketUser;
      await this.presenceService.setOnline(payload.sub, client.id);
      client.emit("presence:update", { userId: payload.sub, status: "online" });
    } catch (error) {
      client.disconnect();
    }
  }

  async handleDisconnect(client: Socket) {
    const user = client.data.user as SocketUser | undefined;
    if (!user) {
      return;
    }
    await this.presenceService.setOffline(user.userId, client.id);
    client.broadcast.emit("presence:update", { userId: user.userId, status: "offline" });
  }

  @SubscribeMessage("channel:join")
  async joinChannel(@MessageBody() body: { channelId: string }, @ConnectedSocket() client: Socket) {
    await client.join(body.channelId);
    client.emit("channel:joined", { channelId: body.channelId });
  }

  @SubscribeMessage("channel:leave")
  async leaveChannel(@MessageBody() body: { channelId: string }, @ConnectedSocket() client: Socket) {
    await client.leave(body.channelId);
    client.emit("channel:left", { channelId: body.channelId });
  }

  @SubscribeMessage("typing:start")
  async typingStart(@MessageBody() body: { channelId: string }, @ConnectedSocket() client: Socket) {
    const user = client.data.user as SocketUser;
    client.to(body.channelId).emit("typing:update", { channelId: body.channelId, userId: user.userId, typing: true });
  }

  @SubscribeMessage("typing:stop")
  async typingStop(@MessageBody() body: { channelId: string }, @ConnectedSocket() client: Socket) {
    const user = client.data.user as SocketUser;
    client.to(body.channelId).emit("typing:update", { channelId: body.channelId, userId: user.userId, typing: false });
  }

  @SubscribeMessage("message:send")
  async sendMessage(
    @MessageBody() body: { channelId: string; content: string; attachmentIds?: string[] },
    @ConnectedSocket() client: Socket
  ) {
    const user = client.data.user as SocketUser;
    const message = await this.messagesService.createMessage(
      body.channelId,
      user.userId,
      body.content,
      body.attachmentIds
    );
    this.server.to(body.channelId).emit("message:new", message);
    return message;
  }
}
