import { EditorState, RangeSetBuilder } from "@codemirror/state";
import { EditorView, Decoration, ViewPlugin } from "@codemirror/view";
import { jsonc } from "@shopify/lang-jsonc";
import { basicSetup } from "codemirror";

// Regular expression to match URLs
const urlRegex = /\bhttps?:\/\/[^\s"]+/g;

// Function to create decorations for URLs
function urlHighlighter(view) {
  let builder = new RangeSetBuilder();
  for (let { from, to } of view.visibleRanges) {
    let text = view.state.doc.sliceString(from, to);
    let match;
    while ((match = urlRegex.exec(text)) !== null) {
      let start = from + match.index
      let end = start + match[0].length

      // Create a mark decoration that turns the URL into a link
      builder.add(start, end, Decoration.mark({
        tagName: "a",
        attributes: { href: match[0], target: "_blank", rel: "noopener noreferrer nofollow"},
      }));
    }
  }
  return builder.finish();
}

// View plugin to manage URL decorations
const urlDecorator = ViewPlugin.fromClass(class {
  constructor(view) {
    this.decorations = urlHighlighter(view);
  }

  update(update) {
    if (update.docChanged || update.viewportChanged) {
      this.decorations = urlHighlighter(update.view);
    }
  }
}, { decorations: v => v.decorations });

const state = EditorState.create({
  doc: document.getElementById('json-data').textContent.trim(),
  extensions: [
    basicSetup,
    jsonc(),
    EditorView.editable.of(false), // Read-only
    urlDecorator,
    EditorView.lineWrapping,
  ],
});
const view = new EditorView({ state, parent: document.querySelector("#editor") });
