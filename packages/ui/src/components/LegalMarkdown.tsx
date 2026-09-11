/** 法务文档 Markdown 渲染:react-markdown + rehype-sanitize(默认 schema 去 img);两端同一管线。 */

import ReactMarkdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";

const schema = {
  ...defaultSchema,
  tagNames: (defaultSchema.tagNames ?? []).filter((tag) => tag !== "img"),
};

export function LegalMarkdown({ content }: { content: string }) {
  return <ReactMarkdown rehypePlugins={[[rehypeSanitize, schema]]}>{content}</ReactMarkdown>;
}
