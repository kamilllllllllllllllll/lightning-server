import { IsNotEmpty, IsOptional, IsString, IsUUID } from "class-validator";

export class CreateMessageDto {
  @IsUUID()
  channelId!: string;

  @IsString()
  @IsNotEmpty()
  content!: string;

  @IsOptional()
  attachmentIds?: string[];
}

export class CreateChannelDto {
  @IsString()
  @IsNotEmpty()
  name!: string;

  @IsOptional()
  memberIds?: string[];
}
