import { Module } from "@nestjs/common";
import { JwtModule } from "@nestjs/jwt";
import { MessagesController } from "./messages.controller";
import { MessagesGateway } from "./messages.gateway";
import { MessagesService } from "./messages.service";
import { PresenceService } from "../presence/presence.service";

@Module({
  imports: [
    JwtModule.register({
      secret: process.env.JWT_SECRET || "dev-secret"
    })
  ],
  controllers: [MessagesController],
  providers: [MessagesService, MessagesGateway, PresenceService]
})
export class MessagesModule {}
