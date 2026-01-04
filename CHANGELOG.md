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

## 0.1.1

### Added
- Allow `Muxer` to be used as an async context manager for automatic cleanup.

### Changed
- Update library usage docs to show `async with Muxer(...)` as the preferred pattern.
- Use `Muxer` directly in the CLI instead of `modmux_client`.
- Expand Muxer docstrings with argument and lifecycle guidance.
