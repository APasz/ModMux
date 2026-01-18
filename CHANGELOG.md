# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

### Added

### Changed

### Fixed

### Deprecated

### Removed

### Security

## 0.2.2

### Added
- Re-export provider credential models from `modmux` for cleaner imports.
- Allow `Muxer` and `modmux_client` to accept a sequence of `ProviderCreds`.

## 0.2.1

### Changed
- `_errors.py` renamed to `modmux_errors.py`

## 0.2.0

### Added
- `Provider.display_name` attribute for a human friendly name for the portal
- `LocaleTag` and `LocalisedText` for attaching translations to mod text fields.
- `Muxer.get_mod(..., locales=[...])` for requesting translated mod fields.

### Changed
- `Mod.name`, `Mod.description_md`, and `ModSummary` text fields now use `LocalisedText` (breaking).

## 0.1.3

### Changed
- Freeze identity-key models (`ProviderCreds`, `ModID`, `Author`, `ModSummary`) for stable hashing.

## 0.1.2

### Added
- Add provider URL parser helpers for resolving mod IDs from links.

## 0.1.1

### Added
- Allow `Muxer` to be used as an async context manager for automatic cleanup.

### Changed
- Update library usage docs to show `async with Muxer(...)` as the preferred pattern.
- Use `Muxer` directly in the CLI instead of `modmux_client`.
- Expand Muxer docstrings with argument and lifecycle guidance.
