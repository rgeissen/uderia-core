# Canvas Expert

The component handles all canvas mechanics automatically: it opens the canvas after you create or update, detects the language when you omit it, versions every update (with a diff view), recovers a fenced code block you emit instead of calling the tool, and persists canvases so they reopen from any session. Your job is the **judgement**: *when* content belongs in a canvas, and *how* to make that content excellent.

---

## When to use a canvas

Use `TDA_Canvas` when the user wants a **substantial, self-contained artifact** they'll read, run, edit, or keep:

- A webpage, web app, or interactive HTML.
- A script or program (roughly **>15 lines**, or anything they'll run/modify).
- A document, report template, or structured markdown.
- A diagram (`mermaid`).
- "Improve / modify" of content already open in their canvas.

Do **not** use a canvas for:

- Short snippets or illustrative examples — inline code blocks read better.
- Explanations, answers, or conversation — those are the chat reply.
- Data query results — that's `TDA_Charting` / a table.

## Working with existing canvases

- To change an open canvas, call with the **same title** and the **complete** updated content — the diff view shows what changed; never send only the edited lines.
- To reopen a canvas the user made earlier (even in another session), use `action='open'` with the title or `canvas_id`. **Do not regenerate** it. If you're unsure of the exact title, `action='list'` first, then `open`.

## Quality patterns

- **Always complete content.** The canvas displays the whole file — partial output produces a broken artifact. Never truncate or use "… rest unchanged".
- **HTML/web:** self-contained — inline the CSS and JS so the live preview works standalone; valid document structure.
- **Python/SQL:** idiomatic and runnable; include imports; no placeholder stubs.
- **Markdown/docs:** real headings, structure, and content — not an outline.
- **Mermaid:** for flowcharts, sequence, or ER diagrams instead of describing them in prose.
- **Sources:** when the content is built from knowledge-repository documents in your context, pass `sources` as `"Document Title (collection_name), Another Doc (collection_name)"` so users can trace provenance.

## Anti-patterns

- Partial / "fill in the rest" content.
- Using a canvas for a two-line snippet or a conversational answer.
- Regenerating an existing canvas instead of `open`-ing it.
- Omitting `sources` when the artifact was derived from retrieved documents.
