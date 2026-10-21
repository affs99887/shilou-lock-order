/** Normalize pasted Bark app examples without executing copied commands. */
export function normalizeBarkAddress(raw: string): string {
  const text = raw.trim();
  if (!text) return "";
  const matches = text.match(/https?:\/\/[^\s<>"'`\]\)]+/gi) ?? [];
  const urls = [...new Set(matches)];
  if (urls.length !== 1) throw new Error("请粘贴一个 Bark 链接，不要填写多个地址。");
  let url: URL;
  try { url = new URL(urls[0]!); } catch { throw new Error("链接格式不正确，请从 Bark App 重新复制。"); }
  if (url.username || url.password || !["https:", "http:"].includes(url.protocol)) throw new Error("请使用不含账号密码的 Bark 推送链接。");
  const segments = url.pathname.split("/").filter(Boolean);
  if (!segments.length) throw new Error("链接缺少设备密钥，请复制 Bark App 的完整推送链接。");
  if (url.hostname !== "api.day.app" && segments.length > 1) throw new Error("自建 Bark 请粘贴服务地址与设备密钥组成的基础链接，不含示例标题或正文。");
  let key: string;
  try { key = decodeURIComponent(segments[0]!); } catch { throw new Error("设备密钥格式无效，请重新复制链接。"); }
  if (!/^[A-Za-z0-9_-]+$/.test(key) || ["push", "ping", "register", "healthz"].includes(key.toLowerCase())) throw new Error("未识别到有效的 Bark 设备密钥。");
  return `${url.origin}/${encodeURIComponent(key)}`;
}
