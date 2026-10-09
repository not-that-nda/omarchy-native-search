# Native Search: an optional file finder for Omarchy, with fuzzy matching and previews

Sharing **Native Search**, a standalone community tool for finding local files from a small Omarchy-themed popup. It is optional software you choose to install; this is not a request to bundle it with Omarchy.

![Search results and a document preview](https://raw.githubusercontent.com/not-that-nda/omarchy-native-search/main/docs/screenshots/expanded.png)

## What it does

- Opens as a centered single-line input and expands downward as you type.
- Searches filenames and paths in explicitly configured local directories using per-user plocate indexes.
- Supports literal or fuzzy matching, file/folder/extension filters, and configurable indexing exclusions.
- Shows a right-side preview for PDFs, images, text/code and DOCX/ODT documents.
- Opens files, reveals them in the file manager, or copies their paths/URIs.
- Includes ordinary settings controls, an optional advanced JSON editor, and reversible installation/removal.

![Compact search input](https://raw.githubusercontent.com/not-that-nda/omarchy-native-search/main/docs/screenshots/collapsed.png)

## Try the preview

[Repository and installation](https://github.com/not-that-nda/omarchy-native-search#install) · [Preview downloads](https://github.com/not-that-nda/omarchy-native-search/releases)

This targets Omarchy's newer Quickshell plugin shell and Lua bindings; it does not support older Walker-only installations. The README lists required APIs, dependencies and the environment used for native checks. Home is the initial scope, other roots are explicit, and a keyboard shortcut is opt-in so it won't replace your existing bindings.

Everything runs locally. Indexes are refreshed snapshots, not real-time watches; broad searches may be capped and are marked partial. PDF previews show the first page, and office-document previews show extracted text. This is a preview release: isolated/backend checks and a native fixture pass, while wider clean-machine graphical acceptance is still wanted.

## Feedback, ideas and voting

- [Request or upvote a feature](https://github.com/not-that-nda/omarchy-native-search/discussions/categories/ideas)
- [Ask an installation/usage question](https://github.com/not-that-nda/omarchy-native-search/discussions/categories/q-a)
- [Report a reproducible bug](https://github.com/not-that-nda/omarchy-native-search/issues/new/choose)
- [Roadmap](https://github.com/not-that-nda/omarchy-native-search/blob/main/ROADMAP.md)

Please search for existing requests before adding a new one and upvote ideas you'd use. Feedback is reviewed in batches; there is no guaranteed response time. Keep tool-specific reports in the Native Search repository so they don't add work to Omarchy's maintainers.

The screenshots use synthetic documents and contain no personal file inventory.
