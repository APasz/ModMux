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

## 0.5.0

### Added
- Add transportfever.net provider support using the Transport Fever CommonAPI repositories.
- Add a transportfever.net HTML fallback for filebase entry IDs missing from the repository refresh.
- Add `tpf1` and `tpf2` game selection for transportfever.net repository lookups.
- Accept `tf1`/`tf2` and `transportfever1`/`transportfever2` aliases for transportfever.net game selection.

## 0.4.0

### Added
- Add `Mod.latest_version`, `ModVersion`, `FileAsset`, and richer dependency metadata via `DependencyRelation`.
- Populate latest-version metadata for CurseForge, Modrinth, mod.io, and Factorio Mods (Wube).
- Add provider-specific dependency extraction for CurseForge, Modrinth, mod.io, and Factorio Mods (Wube).
- Add support for resolving CurseForge game slugs from CurseForge URLs during slug lookups.

### Changed
- Improve Wube latest-release selection so the newest release metadata is chosen consistently.
- Improve CurseForge latest-file selection to prefer the newest dated file.

### Fixed
- Fix Modrinth latest-version fallback so dependency `version_id` values do not overwrite the release version identifier.

## 0.3.0

### Added
- Add bulk mod lookup via `Muxer.get_mods(...)` and `ProviderClient.get_mods(...)`.
- Add native bulk fetching for Steam Workshop, mod.io, Modrinth, and CurseForge.
- Add CLI support for fetching multiple mod ids in one invocation.
- Add CLI `--from-urls` mode for reading mod URLs from a file and resolving them in bulk.
- Add `BatchResponseError` for malformed or incomplete bulk provider responses.
- Add development tooling and configuration for `basedpyright`, `coverage`, `pytest`, `pytest-cov`, and `ruff`.

### Changed
- Reuse native bulk paths from provider `get_mod(...)` implementations where available.
- Improve CLI credential loading so `.env` can be resolved from the current working directory, repo root, or URL file directory.
- Expand test coverage across CLI flows, batch provider paths, and retry/error branches.

## 0.2.6

### Added
- Add `Author.raw` for preserving provider-specific author/user metadata.

### Changed
- Populate `Author.raw` across built-in providers where author sub-payloads are available.
- Steam `get_user(...)` now returns raw Steam player summary data in `Author.raw`.

## 0.2.5

### Added
- Add generic tri-state toggle helpers: `ToggleMode`, `UNDEFINED`, and `UndefinedType`.
- Add optional interoperability for `hikari.undefined.UNDEFINED`.
- Add `Muxer.get_user(...)` and `ProviderClient.get_user(...)` for provider user lookups.

### Changed
- Steam and Modrinth now use `author_resolution` to control optional author enrichment calls.


## 0.2.4

### Added
- ProviderClient export

## 0.2.3

### Added
- Add provider brand colour metadata via `ProviderClient.colour` for all built-in providers.
- Add `Colour` and `ColourValue` types with hex validation and conversion helpers (`as_hex`, `as_rgb`, `as_rgb_css`, `as_int`).
- Re-export `Colour` and `ColourValue` from `modmux`.
- Add test coverage for colour validation/conversion and provider colour registration.

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
