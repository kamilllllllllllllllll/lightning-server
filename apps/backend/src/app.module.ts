import { Module } from "@nestjs/common";
import { ConfigModule } from "@nestjs/config";
import { AuthModule } from "./auth/auth.module";
import { MessagesModule } from "./messages/messages.module";
import { PresenceModule } from "./presence/presence.module";
import { UploadsModule } from "./uploads/uploads.module";

@Module({
  imports: [
    ConfigModule.forRoot({ isGlobal: true }),
    AuthModule,
    MessagesModule,
    PresenceModule,
    UploadsModule
  ]
})
export class AppModule {}
