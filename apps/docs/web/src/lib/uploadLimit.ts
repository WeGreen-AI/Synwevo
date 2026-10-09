import { useEffect, useState } from "react";
import { get } from "@synwevo/auth-client";
import { fmtSize, t } from "@synwevo/ui";

/** A preflight hint only: every upload is still checked by the service. */
export function useUploadLimit() {
  const [bytes, setBytes] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    get("/api/capabilities", controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      const limit = value?.limits?.max_file_bytes;
      if (!Number.isSafeInteger(limit) || limit <= 0) { setFailed(true); return; }
      setBytes(limit);
    }).catch(() => { if (!controller.signal.aborted) setFailed(true); });
    return () => controller.abort();
  }, []);
  return {
    note: bytes != null ? t("每个文件最多 {size}；按当前安全检查规则接纳。", { size: fmtSize(bytes) })
      : failed ? t("暂时无法读取文件大小上限，上传时仍会按服务器规则检查。") : t("正在读取文件大小上限…"),
    refusal: (file: File) => bytes != null && file.size > bytes
      ? t("文件「{name}」超过当前 {size} 上限，请缩小后重新上传。", { name: file.name, size: fmtSize(bytes) }) : null,
  };
}
