# Contributing

Useful contributions include reproducible bugs, compatibility reports, documentation fixes and focused improvements. For a substantial feature, open an [Idea](https://github.com/not-that-nda/omarchy-native-search/discussions/categories/ideas) before investing in a large implementation.

1. Explain the user problem and proposed acceptance criteria.
2. Keep code changes focused; preserve existing roots/settings and reversible installation.
3. Run `python3 -m unittest discover -s dev/tests -p 'test_*.py' -v` with the listed test dependencies.
4. For visible changes, include a synthetic screenshot and note the desktop/version tested.
5. Describe configuration/dependency changes and remaining limitations.

Please don't include private documents, credentials, machine mount identifiers or personal screenshots. Check your commit author/email settings before pushing; GitHub provides a noreply email option.

## Source ownership

This repository is a generated standalone distribution of an actively maintained component. External contributions are welcome, but accepted changes must be reconciled with the authoritative component before the next export. Maintainers preserve contributor attribution and avoid overwriting external changes with a generated push. Public users do not need the author's workstation repository to install or run Native Search.

MIT license; see LICENSE. Third-party dependencies retain their own licenses. No CLA is currently required.
