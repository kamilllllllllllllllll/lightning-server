import { Body, Controller, Post } from "@nestjs/common";
import { AuthService } from "./auth.service";
import { LoginDto, RefreshDto, RegisterDto } from "./dto";

@Controller("auth")
export class AuthController {
  constructor(private readonly authService: AuthService) {}

  @Post("register")
  async register(@Body() body: RegisterDto) {
    return this.authService.register(body.email, body.password, body.displayName);
  }

  @Post("login")
  async login(@Body() body: LoginDto) {
    return this.authService.login(body.email, body.password);
  }

  @Post("refresh")
  async refresh(@Body() body: RefreshDto) {
    return this.authService.refresh(body.refreshToken);
  }
}
