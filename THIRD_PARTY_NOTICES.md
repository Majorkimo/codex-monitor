# Third-party notices

Codex Monitor is released under the MIT License (see `LICENSE`). It includes the following third-party code.

## divoom-minitoo-codex (Codex App Server client)

- Files: `vendor/divoom_minitoo_codex/__init__.py`, `appserver.py`, `LICENSE`
- Upstream: https://github.com/AFrayde01/divoom-minitoo-codex
- License: MIT, Copyright (c) 2026 divoom-minitoo-codex contributors
- Scope: only this small App Server client module is vendored. No Divoom display code or artwork is included.
- Local modifications: none. The vendored files are unchanged. Codex Monitor's adjustments live in `Sources/monitor_client.py`, which subclasses the vendored client (client identity, buffer reset on restart, explicit pipe cleanup, restart of a dead server, a public read-only rate-limit method).
- Verified provenance: `vendor/divoom_minitoo_codex/appserver.py` has SHA-256 `55d1d4ec7fa7453266c03a1c79380f7973d73bd141a6c7d5d15e91f4015b8fd7`, which matches `src/divoom_minitoo_codex/appserver.py` at upstream revision `76425f2f39b0970fde4bc224baf97ef93aff9e89` (checked against a local clone of the upstream repository). This verifies that one module only; no claim is made about the other vendored files.

The license text, as shipped in `vendor/divoom_minitoo_codex/LICENSE`:

```
MIT License

Copyright (c) 2026 divoom-minitoo-codex contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

## Not bundled

Python and the Codex CLI are not part of this project or its builds. They are installed separately and remain under their own licenses and terms.
