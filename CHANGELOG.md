# Changelog

## 0.1.0-preview.5

Crop both README screenshots tightly around the tool with a small wallpaper margin, making the compact input and expanded results/preview readable at normal README width. Application behavior is unchanged.

## 0.1.0-preview.4

Add the reviewed synthetic screenshot gallery above Features. Crop desktop status indicators and strip image metadata. Publication commits now use the public GitHub handle and private GitHub noreply address; earlier public commit identities were sanitized with owner authorization.

## 0.1.0-preview.3

Set index-lock permissions explicitly to 0600 rather than depending on the launching shell's umask. The privacy regression now exercises the common 0022 umask used by GitHub's runner. Includes the older-plocate compatibility correction from preview.2.

## 0.1.0-preview.2

Fix unprivileged indexing with plocate 1.1.19: remove the unsupported `updatedb --config-file` option. Explicit pruning overrides remain in place. This corrects the Ubuntu GitHub Actions failure in preview.1; the user interface and screenshots are unchanged.

## 0.1.0-preview.1

Initial standalone preview: indexed literal/fuzzy search, fixed centered input with downward expansion, selection previews, bottom settings form, and opt-in shortcut/root setup. Includes a self-contained installed CLI and reversible lifecycle.

No previous public release history is implied by earlier local development. Core behavior has native workstation evidence; package lifecycle has isolated-profile tests. A fresh Omarchy graphical-machine installation and independent user acceptance remain required before a stable release claim.
