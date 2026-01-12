import { Injectable } from "@nestjs/common";
import { Pool } from "pg";
import { v4 as uuidv4 } from "uuid";

export interface UserRecord {
  id: string;
  email: string;
  passwordHash: string;
  displayName: string;
}

@Injectable()
export class UsersService {
  private pool = new Pool({
    connectionString: process.env.DATABASE_URL
  });

  async findByEmail(email: string): Promise<UserRecord | null> {
    const result = await this.pool.query(
      "SELECT id, email, password_hash AS \"passwordHash\", display_name AS \"displayName\" FROM users WHERE email = $1",
      [email]
    );
    return result.rows[0] ?? null;
  }

  async findById(id: string): Promise<UserRecord | null> {
    const result = await this.pool.query(
      "SELECT id, email, password_hash AS \"passwordHash\", display_name AS \"displayName\" FROM users WHERE id = $1",
      [id]
    );
    return result.rows[0] ?? null;
  }

  async createUser(email: string, passwordHash: string, displayName: string): Promise<UserRecord> {
    const id = uuidv4();
    const result = await this.pool.query(
      "INSERT INTO users (id, email, password_hash, display_name) VALUES ($1, $2, $3, $4) RETURNING id, email, password_hash AS \"passwordHash\", display_name AS \"displayName\"",
      [id, email, passwordHash, displayName]
    );
    return result.rows[0];
  }
}
