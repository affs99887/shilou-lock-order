import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

/**
 * 配置弹窗：盖在页面上，点遮罩、点右上角的关闭按钮或按 Esc 都能关闭。
 *
 * 关闭时只隐藏、不卸载，里面没保存的输入（推送地址、联系人资料、预算）都会保留。
 * 没有用 Radix 的 Dialog：它要在关闭时保留内容就得强制挂载，而强制挂载会让页面其余部分
 * 一直对读屏软件隐藏，主按钮就点不到了。
 */
export function ConfigModal({ id, open, title, description, closeLabel, onClose, footer, narrow, children }: {
  id: string;
  open: boolean;
  title: string;
  description?: string;
  /** 关闭按钮的读屏名称，同一页有多个弹窗时用它区分。 */
  closeLabel: string;
  onClose: () => void;
  /** 固定在弹窗底部、不随内容滚动的操作区，比如「保存设置」。 */
  footer?: ReactNode;
  /** 内容少的弹窗用窄一些的宽度。 */
  narrow?: boolean;
  children: ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const opener = useRef<Element | null>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    if (!open) return;
    opener.current = document.activeElement;
    panel.current?.focus();
    const scroll = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // Esc 监听整个页面：弹窗里的按钮在保存时会暂时禁用，焦点会跑到页面上，只监听弹窗本身就收不到。
    // 弹窗里的下拉菜单自己处理 Esc 时会标记已处理，这时只关菜单、不关弹窗。
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) { e.preventDefault(); close.current(); }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = scroll;
      // 关闭后把焦点还给打开弹窗的按钮，键盘用户不用从页面开头重新找。
      if (opener.current instanceof HTMLElement) opener.current.focus();
    };
  }, [open]);
  return (
    <div className="config-modal" hidden={!open}>
      <div className="config-modal-backdrop" aria-hidden="true" onClick={onClose} />
      <div
        ref={panel}
        id={id}
        role="dialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        tabIndex={-1}
        className={`config-modal-panel${narrow ? " is-narrow" : ""}`}
      >
        <div className="config-modal-header">
          <div>
            <h2 id={`${id}-title`}>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          <button type="button" className="config-modal-close" aria-label={closeLabel} onClick={onClose}><X size={18} /></button>
        </div>
        <div className="config-modal-body">{children}</div>
        {footer && <div className="config-modal-footer">{footer}</div>}
      </div>
    </div>
  );
}
