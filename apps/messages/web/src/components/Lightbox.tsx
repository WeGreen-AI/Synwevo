/** A viewer for an image attachment: the picture as large as the viewport allows, its name, a
 *  download through the same authorised address, and the shared dialog focus rules (trap, Escape
 *  closes, focus returns to the thumbnail that opened it). */
import { useRef } from "react";
import { createPortal } from "react-dom";
import { useI18n, useModalFocus } from "@synwevo/ui";
import { IcClose, IcDownload } from "./icons";

export function Lightbox({ src, downloadHref, name, size, onClose }: { src: string; downloadHref: string; name: string; size: string; onClose: () => void }) {
  const { t } = useI18n();
  const ref = useRef<HTMLDivElement>(null);
  const { onKeyDown, isTop } = useModalFocus(ref, onClose);
  return createPortal(
    <div className="msg-lightbox" onMouseDown={e => { if (e.target === e.currentTarget && isTop()) onClose(); }}>
      <div ref={ref} className="msg-lightbox-box" role="dialog" aria-modal="true" aria-label={t("查看图片：{name}", { name })} tabIndex={-1} onKeyDown={onKeyDown}>
        <div className="msg-lightbox-bar">
          <span className="msg-lightbox-name">{name} <span className="tiny">({size})</span></span>
          <a className="btn sm" href={downloadHref} download={name}><IcDownload size={14} /> {t("下载")}</a>
          <button type="button" className="btn sm icon" onClick={onClose} aria-label={t("关闭")} data-autofocus><IcClose size={16} /></button>
        </div>
        <img src={src} alt={name} />
      </div>
    </div>, document.body);
}
