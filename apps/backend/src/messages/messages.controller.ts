import { Body, Controller, Get, Param, Post, Query, Request, UseGuards } from "@nestjs/common";
import { JwtAuthGuard } from "../auth/jwt-auth.guard";
import { CreateChannelDto, CreateMessageDto } from "./dto";
import { MessagesService } from "./messages.service";

@Controller("messages")
@UseGuards(JwtAuthGuard)
export class MessagesController {
  constructor(private readonly messagesService: MessagesService) {}

  @Post("channels")
  async createChannel(@Body() body: CreateChannelDto, @Request() req: { user: { userId: string } }) {
    const memberIds = Array.from(new Set([req.user.userId, ...(body.memberIds ?? [])]));
    return this.messagesService.createChannel(body.name, memberIds.length > 2, memberIds);
  }

  @Get("channels")
  async listChannels(@Request() req: { user: { userId: string } }) {
    return this.messagesService.listChannelsForUser(req.user.userId);
  }

  @Get("channels/:channelId/members")
  async listMembers(@Param("channelId") channelId: string) {
    return this.messagesService.listMembers(channelId);
  }

  @Post()
  async createMessage(@Body() body: CreateMessageDto, @Request() req: { user: { userId: string } }) {
    return this.messagesService.createMessage(body.channelId, req.user.userId, body.content, body.attachmentIds);
  }

  @Get()
  async listMessages(
    @Query("channelId") channelId: string,
    @Query("limit") limit?: string,
    @Query("before") before?: string
  ) {
    return this.messagesService.listMessages(channelId, limit ? Number(limit) : 50, before);
  }
}
