import { Controller, Get, Param, Post, UploadedFile, UseGuards, UseInterceptors } from "@nestjs/common";
import { FileInterceptor } from "@nestjs/platform-express";
import { diskStorage } from "multer";
import { JwtAuthGuard } from "../auth/jwt-auth.guard";
import { v4 as uuidv4 } from "uuid";
import { extname, join } from "path";
import { createReadStream, existsSync } from "fs";
import { Response } from "express";
import { Res } from "@nestjs/common";
import { UploadsService } from "./uploads.service";

const uploadDirectory = join(process.cwd(), "uploads");

@Controller("uploads")
@UseGuards(JwtAuthGuard)
export class UploadsController {
  constructor(private readonly uploadsService: UploadsService) {}

  @Post()
  @UseInterceptors(
    FileInterceptor("file", {
      storage: diskStorage({
        destination: uploadDirectory,
        filename: (_req, file, cb) => {
          const uniqueName = `${uuidv4()}${extname(file.originalname)}`;
          cb(null, uniqueName);
        }
      })
    })
  )
  async upload(@UploadedFile() file: Express.Multer.File) {
    return this.uploadsService.createAttachment(
      file.originalname,
      `/uploads/${file.filename}`,
      file.mimetype,
      file.size
    );
  }

  @Get(":filename")
  async download(@Param("filename") filename: string, @Res() res: Response) {
    const filePath = join(uploadDirectory, filename);
    if (!existsSync(filePath)) {
      return res.status(404).json({ message: "File not found" });
    }
    const stream = createReadStream(filePath);
    return stream.pipe(res);
  }
}
