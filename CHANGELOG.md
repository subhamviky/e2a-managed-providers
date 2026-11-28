# Changelog

Newest first. [Keep a Changelog](https://keepachangelog.com). This file was started with the engineering records (28 November 2026) and records what is known from the repository's tags and pull requests; earlier history is in the git log.

## [Unreleased]
### Fixed
- A Gemma model rejects a thinking level, so the adapter no longer sends one to it (found by the live probe in the GenAI assistant's repository). On `main`; **no tag contains it yet**: the assistant installs from `main` until `v0.2.1` is tagged.

## v0.2.0
### Added
- The `gemini_api` profile for the Gemini Developer API's free tier (used by the Google Cloud landscape), and the model lifecycle check (a configured model past its retirement date fails).
