/** 法务文档 Markdown 渲染白名单:与用户端公开页同一渲染管线
 * (react-markdown 不内嵌 HTML + rehype-sanitize 默认 schema 去 img)。 */

import ReactMarkdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";

const schema = {
  ...defaultSchema,
  tagNames: (defaultSchema.tagNames ?? []).filter((tag) => tag !== "img"),
};

export function LegalMarkdown({ content }: { content: string }) {
  return <ReactMarkdown rehypePlugins={[[rehypeSanitize, schema]]}>{content}</ReactMarkdown>;
}
