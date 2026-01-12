import { Injectable, UnauthorizedException, ConflictException } from "@nestjs/common";
import { JwtService } from "@nestjs/jwt";
import * as bcrypt from "bcrypt";
import { Pool } from "pg";
import { UsersService } from "../users/users.service";

@Injectable()
export class AuthService {
  private pool = new Pool({ connectionString: process.env.DATABASE_URL });

  constructor(private readonly jwtService: JwtService, private readonly usersService: UsersService) {}

  async register(email: string, password: string, displayName: string) {
    const existing = await this.usersService.findByEmail(email);
    if (existing) {
      throw new ConflictException("Email already registered");
    }
    const passwordHash = await bcrypt.hash(password, 12);
    const user = await this.usersService.createUser(email, passwordHash, displayName);
    return this.issueTokens(user.id, user.email, user.displayName);
  }

  async validateUser(email: string, password: string) {
    const user = await this.usersService.findByEmail(email);
    if (!user) {
      throw new UnauthorizedException("Invalid credentials");
    }
    const match = await bcrypt.compare(password, user.passwordHash);
    if (!match) {
      throw new UnauthorizedException("Invalid credentials");
    }
    return user;
  }

  async login(email: string, password: string) {
    const user = await this.validateUser(email, password);
    return this.issueTokens(user.id, user.email, user.displayName);
  }

  async refresh(refreshToken: string) {
    const stored = await this.pool.query(
      "SELECT user_id AS \"userId\", token_hash AS \"tokenHash\" FROM refresh_tokens WHERE expires_at > NOW()",
      []
    );
    const match = stored.rows.find((row) => bcrypt.compareSync(refreshToken, row.tokenHash));
    if (!match) {
      throw new UnauthorizedException("Invalid refresh token");
    }
    const user = await this.usersService.findById(match.userId);
    if (!user) {
      throw new UnauthorizedException("User not found");
    }
    return this.issueTokens(user.id, user.email, user.displayName);
  }

  private async issueTokens(userId: string, email: string, displayName: string) {
    const accessToken = await this.jwtService.signAsync({ sub: userId, email, displayName });
    const refreshToken = await this.jwtService.signAsync(
      { sub: userId, type: "refresh" },
      { expiresIn: "7d" }
    );
    const tokenHash = await bcrypt.hash(refreshToken, 10);
    await this.pool.query(
      "INSERT INTO refresh_tokens (id, user_id, token_hash, expires_at) VALUES (gen_random_uuid(), $1, $2, NOW() + INTERVAL '7 days')",
      [userId, tokenHash]
    );
    return {
      accessToken,
      refreshToken,
      user: { id: userId, email, displayName }
    };
  }
}
