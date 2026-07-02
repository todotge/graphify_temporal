# Security Policy

## Supported Versions

graphify-temporal is a single-maintainer, actively developed project. Only
the latest release on `master` receives fixes.

| Version | Supported |
|---------|-----------|
| 1.x     | ✅        |
| < 1.0   | ❌        |

## Scope

graphify-temporal is a local CLI tool. It:

- Reads and writes `graph.json` on the filesystem you point it at
- Stats file metadata (mtime/ctime/birthtime) via the OS
- On Linux, calls `statx` through `ctypes` to resolve birthtime
- Optionally shells out to the `graphify` CLI (fixed arguments, no
  user-controlled input) to regenerate HTML/wiki output
- Makes no network calls and handles no credentials or authentication

Realistic risk surface: path handling (glob/include/exclude patterns,
symlinks), the `ctypes`/`statx` binding, and malformed/adversarial
`graph.json` input (e.g. from an untrusted graphify export).

## Reporting a Vulnerability

Please **do not open a public issue** for security reports.

Use GitHub's private vulnerability reporting:
[Report a vulnerability](https://github.com/todotge/graphify-temporal/security/advisories/new)

If that's unavailable, email **luca.gernon@gmail.com** with:

- A description of the issue and its impact
- Steps to reproduce (a minimal `graph.json` + command is ideal)
- Affected version / OS / Python version

You should get an initial response within 5 days. Confirmed issues will be
fixed and released as soon as practical; you'll be credited in the release
notes unless you prefer otherwise.
