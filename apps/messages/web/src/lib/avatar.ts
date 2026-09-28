/** A group avatar from a picture the person chose (§7.17). The platform accepts one thing: a plain
 *  8-bit PNG of at most 256 KB and 512 × 512 pixels, which the server rebuilds without metadata. The
 *  browser can decode far more (JPEG, WebP, a PNG of any size), so the shell draws the centre square
 *  of the picture into a canvas and sends that canvas as a PNG — smaller sides until it fits. The
 *  server still decides; nothing here is trusted by it. */

export const AVATAR_MAX_BYTES = 256 * 1024;
export const AVATAR_SIDES = [256, 192, 128, 96];

/** The centred square of a width × height picture: the part a round or rounded avatar shows. */
export function coverSquare(width: number, height: number): { sx: number; sy: number; side: number } {
  const side = Math.max(1, Math.min(width, height));
  return { sx: Math.max(0, Math.floor((width - side) / 2)), sy: Math.max(0, Math.floor((height - side) / 2)), side };
}

export class AvatarPictureError extends Error {}

function base64Of(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 8192) binary += String.fromCharCode(...bytes.subarray(i, i + 8192));
  return btoa(binary);
}

/** Decode the picture, crop the centre square and encode a PNG that fits the platform's limit;
 *  resolves to its base64. Rejects with `AvatarPictureError` when the browser cannot read the file
 *  as a picture or no size fits. */
export async function avatarPng(file: Blob, maxBytes = AVATAR_MAX_BYTES): Promise<string> {
  let bitmap: ImageBitmap;
  try { bitmap = await createImageBitmap(file); } catch { throw new AvatarPictureError("unreadable"); }
  try {
    const { sx, sy, side } = coverSquare(bitmap.width, bitmap.height);
    for (const size of AVATAR_SIDES) {
      const out = Math.min(size, side);
      const canvas = document.createElement("canvas");
      canvas.width = out; canvas.height = out;
      const ctx = canvas.getContext("2d");
      if (!ctx) throw new AvatarPictureError("no canvas");
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(bitmap, sx, sy, side, side, 0, 0, out, out);
      const blob = await new Promise<Blob | null>(res => canvas.toBlob(res, "image/png"));
      if (blob && blob.size <= maxBytes) return base64Of(new Uint8Array(await blob.arrayBuffer()));
    }
    throw new AvatarPictureError("too large");
  } finally {
    bitmap.close?.();
  }
}
