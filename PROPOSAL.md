# Suggestion: native indexed file search with previews and fuzzy matching

An optional Omarchy-native file finder could offer a centered input that expands downward, fast search across explicitly selected local drives, and a document preview to the right.

This prototype uses a user-owned Quickshell plugin, a small Python helper and per-user plocate indexes. It includes exact/fuzzy filename matching, configurable roots/exclusions, open/reveal/copy actions, local previews, ordinary settings controls and reversible installation. It neither uploads documents nor requires an AI service.

Dependencies and supported shell APIs are in README. PDF/image previews use optional system Poppler/Pillow packages. Indexes refresh periodically; results disclose incomplete candidate sets and stale/offline roots. PDF previews are first-page only; DOCX/ODT previews show extracted text.

The standalone repository is a community preview, not an approved Omarchy feature. Would maintainers prefer an optional plugin, integration with the existing menu/launcher, or a first-party file-search surface? The binding is configurable; no personal shortcut is proposed as a default.

Tests and release checks are published with the repository. Before stable distribution, verify a clean Omarchy graphical install. Add a synthetic-only demo link when available; do not attach screenshots of personal filenames.
