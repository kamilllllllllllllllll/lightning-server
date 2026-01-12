import { Injectable } from "@nestjs/common";
import Redis from "ioredis";

@Injectable()
export class PresenceService {
  private redis = new Redis(process.env.REDIS_URL || "redis://localhost:6379");

  async setOnline(userId: string, socketId: string) {
    await this.redis.hset(`presence:${userId}`, "status", "online", "socketId", socketId);
    await this.redis.expire(`presence:${userId}`, 60);
  }

  async setOffline(userId: string, socketId: string) {
    const current = await this.redis.hget(`presence:${userId}`, "socketId");
    if (current === socketId) {
      await this.redis.hset(`presence:${userId}`, "status", "offline");
      await this.redis.expire(`presence:${userId}`, 60);
    }
  }

  async getPresence(userId: string) {
    const status = await this.redis.hget(`presence:${userId}`, "status");
    return status ?? "offline";
  }
}
