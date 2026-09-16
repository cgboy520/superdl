/** Legal document Markdown rendering: react-markdown + rehype-sanitize (default schema without img); one pipeline for both consoles. */

import ReactMarkdown from "react-markdown";
import rehypeSanitize, { defaultSchema } from "rehype-sanitize";

const schema = {
  ...defaultSchema,
  tagNames: (defaultSchema.tagNames ?? []).filter((tag) => tag !== "img"),
};

export function LegalMarkdown({ content }: { content: string }) {
  return <ReactMarkdown rehypePlugins={[[rehypeSanitize, schema]]}>{content}</ReactMarkdown>;
}
