import { Injectable } from "@nestjs/common";
import { Pool } from "pg";
import { v4 as uuidv4 } from "uuid";

@Injectable()
export class MessagesService {
  private pool = new Pool({ connectionString: process.env.DATABASE_URL });

  async createChannel(name: string, isGroup: boolean, memberIds: string[]) {
    const channelId = uuidv4();
    await this.pool.query(
      "INSERT INTO channels (id, name, is_group) VALUES ($1, $2, $3)",
      [channelId, name, isGroup]
    );
    if (memberIds.length > 0) {
      const values: string[] = [];
      const params: string[] = [];
      memberIds.forEach((memberId, index) => {
        const baseIndex = index * 3;
        values.push(`($${baseIndex + 1}, $${baseIndex + 2}, $${baseIndex + 3})`);
        params.push(uuidv4(), channelId, memberId);
      });
      await this.pool.query(
        `INSERT INTO channel_members (id, channel_id, user_id) VALUES ${values.join(",")}`,
        params
      );
    }
    return { id: channelId, name, isGroup, memberIds };
  }

  async listChannelsForUser(userId: string) {
    const result = await this.pool.query(
      "SELECT c.id, c.name, c.is_group AS \"isGroup\" FROM channels c JOIN channel_members m ON m.channel_id = c.id WHERE m.user_id = $1",
      [userId]
    );
    return result.rows;
  }

  async listMembers(channelId: string) {
    const result = await this.pool.query(
      "SELECT u.id, u.display_name AS \"displayName\" FROM users u JOIN channel_members m ON m.user_id = u.id WHERE m.channel_id = $1",
      [channelId]
    );
    return result.rows;
  }

  async createMessage(channelId: string, senderId: string, content: string, attachmentIds?: string[]) {
    const messageId = uuidv4();
    const result = await this.pool.query(
      "INSERT INTO messages (id, channel_id, sender_id, content) VALUES ($1, $2, $3, $4) RETURNING id, channel_id AS \"channelId\", sender_id AS \"senderId\", content, created_at AS \"createdAt\"",
      [messageId, channelId, senderId, content]
    );
    if (attachmentIds && attachmentIds.length > 0) {
      const values: string[] = [];
      const params: string[] = [];
      attachmentIds.forEach((attachmentId, index) => {
        const baseIndex = index * 3;
        values.push(`($${baseIndex + 1}, $${baseIndex + 2}, $${baseIndex + 3})`);
        params.push(uuidv4(), messageId, attachmentId);
      });
      await this.pool.query(
        `INSERT INTO message_attachments (id, message_id, attachment_id) VALUES ${values.join(",")}`,
        params
      );
    }
    return result.rows[0];
  }

  async listMessages(channelId: string, limit = 50, before?: string) {
    const params: Array<string | number> = [channelId, limit];
    let query =
      "SELECT id, channel_id AS \"channelId\", sender_id AS \"senderId\", content, created_at AS \"createdAt\" FROM messages WHERE channel_id = $1";
    if (before) {
      params.push(before);
      query += ` AND created_at < $${params.length}`;
    }
    query += " ORDER BY created_at DESC LIMIT $2";
    const result = await this.pool.query(query, params);
    return result.rows;
  }
}
