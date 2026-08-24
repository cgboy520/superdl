/** 法务文档 Markdown 渲染白名单(F7):react-markdown(不内嵌 HTML)+ rehype-sanitize,
 * 在默认 schema 上去 img;与 CSP default-src 'none' 双保险。管理端预览为同一渲染管线。 */

import ReactMarkdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";

const schema = {
  ...defaultSchema,
  tagNames: (defaultSchema.tagNames ?? []).filter((tag) => tag !== "img"),
};

export function LegalMarkdown({ content }: { content: string }) {
  return <ReactMarkdown rehypePlugins={[[rehypeSanitize, schema]]}>{content}</ReactMarkdown>;
}
