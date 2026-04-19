import { marked } from "marked";
import DOMPurify from "isomorphic-dompurify";

marked.setOptions({
  gfm: true,
  breaks: false,
});

export function MarkdownViewer({ content }: { content: string }) {
  const rawHtml = marked.parse(content, { async: false }) as string;
  const safeHtml = DOMPurify.sanitize(rawHtml, {
    ALLOWED_TAGS: [
      "h1", "h2", "h3", "h4", "h5", "h6",
      "p", "br", "hr",
      "ul", "ol", "li",
      "strong", "em", "s", "del", "ins",
      "a", "blockquote",
      "code", "pre",
      "table", "thead", "tbody", "tr", "th", "td",
      "img", "figure", "figcaption",
      "span", "div",
    ],
    ALLOWED_ATTR: ["href", "src", "alt", "title", "class", "id"],
    ALLOW_DATA_ATTR: false,
  });
  return (
    <article
      className="prose-fandom"
      dangerouslySetInnerHTML={{ __html: safeHtml }}
    />
  );
}
