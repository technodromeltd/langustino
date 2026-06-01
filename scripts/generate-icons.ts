import { writeFileSync } from "node:fs";
import { deflateSync } from "node:zlib";

const sizes = [192, 512];

for (const size of sizes) {
  writeFileSync(`public/icons/icon-${size}.png`, createIcon(size));
  console.log(`Generated public/icons/icon-${size}.png`);
}

function createIcon(size: number) {
  const pixels = Buffer.alloc(size * size * 4);
  fill(pixels, size, 0x10, 0x23, 0x1f, 0xff);
  drawSuperellipse(pixels, size, size * 0.53, size * 0.56, size * 0.29, size * 0.31, 2.7, [0xf4, 0xd0, 0x6f, 0xff]);
  drawSuperellipse(pixels, size, size * 0.52, size * 0.39, size * 0.23, size * 0.2, 3.1, [0x67, 0xb9, 0x9a, 0xff]);
  drawSuperellipse(pixels, size, size * 0.49, size * 0.5, size * 0.13, size * 0.12, 2.3, [0x10, 0x23, 0x1f, 0xff]);
  return encodePng(size, size, pixels);
}

function fill(buffer: Buffer, size: number, red: number, green: number, blue: number, alpha: number) {
  for (let index = 0; index < size * size; index += 1) {
    const offset = index * 4;
    buffer[offset] = red;
    buffer[offset + 1] = green;
    buffer[offset + 2] = blue;
    buffer[offset + 3] = alpha;
  }
}

function drawSuperellipse(
  buffer: Buffer,
  size: number,
  centerX: number,
  centerY: number,
  radiusX: number,
  radiusY: number,
  power: number,
  color: [number, number, number, number],
) {
  for (let y = Math.max(0, Math.floor(centerY - radiusY)); y < Math.min(size, Math.ceil(centerY + radiusY)); y += 1) {
    for (let x = Math.max(0, Math.floor(centerX - radiusX)); x < Math.min(size, Math.ceil(centerX + radiusX)); x += 1) {
      const dx = Math.abs((x - centerX) / radiusX);
      const dy = Math.abs((y - centerY) / radiusY);

      if (dx ** power + dy ** power <= 1) {
        const offset = (y * size + x) * 4;
        buffer[offset] = color[0];
        buffer[offset + 1] = color[1];
        buffer[offset + 2] = color[2];
        buffer[offset + 3] = color[3];
      }
    }
  }
}

function encodePng(width: number, height: number, rgba: Buffer) {
  const scanlines = Buffer.alloc((width * 4 + 1) * height);

  for (let y = 0; y < height; y += 1) {
    const rowStart = y * (width * 4 + 1);
    scanlines[rowStart] = 0;
    rgba.copy(scanlines, rowStart + 1, y * width * 4, (y + 1) * width * 4);
  }

  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr(width, height)),
    chunk("IDAT", deflateSync(scanlines)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

function ihdr(width: number, height: number) {
  const data = Buffer.alloc(13);
  data.writeUInt32BE(width, 0);
  data.writeUInt32BE(height, 4);
  data[8] = 8;
  data[9] = 6;
  data[10] = 0;
  data[11] = 0;
  data[12] = 0;
  return data;
}

function chunk(type: string, data: Buffer) {
  const typeBuffer = Buffer.from(type);
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])), 0);
  return Buffer.concat([length, typeBuffer, data, crc]);
}

function crc32(buffer: Buffer) {
  let crc = 0xffffffff;

  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = crc & 1 ? (crc >>> 1) ^ 0xedb88320 : crc >>> 1;
    }
  }

  return (crc ^ 0xffffffff) >>> 0;
}
