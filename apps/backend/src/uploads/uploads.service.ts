import { Injectable } from "@nestjs/common";
import { Pool } from "pg";
import { v4 as uuidv4 } from "uuid";

@Injectable()
export class UploadsService {
  private pool = new Pool({ connectionString: process.env.DATABASE_URL });

  async createAttachment(filename: string, url: string, contentType: string, size: number) {
    const id = uuidv4();
    const result = await this.pool.query(
      "INSERT INTO attachments (id, filename, url, content_type, size) VALUES ($1, $2, $3, $4, $5) RETURNING id, filename, url, content_type AS \"contentType\", size",
      [id, filename, url, contentType, size]
    );
    return result.rows[0];
  }
}
