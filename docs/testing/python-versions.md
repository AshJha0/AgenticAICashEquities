# Python versions (0.10.0 item E6, finding C-P2-2)

**Declared and tested.** Python 3.11, 3.12 and 3.13. The classifiers in `pyproject.toml` and the
`test` job matrix in `.github/workflows/ci.yml` list the same three versions, and
`tests/repo/test_e6_python_versions.py` fails if they drift apart. Coverage is measured on 3.12
only; 3.11 and 3.13 run the same PR tier without the tracer.

**Goldens: one set, compared on every interpreter.** The golden files under `tests/golden/` have
no per-interpreter variants and neither the golden tests nor `ceap.golden` branch on the
interpreter version (checked by the same test file). Every matrix leg therefore compares against
the same committed bytes, so a green matrix is the proof of equality across interpreters. This
already holds across different numerical stacks: the universal lock resolves numpy 2.4.6 and
scipy 1.17.1 on 3.11 and numpy 2.5.3 and scipy 1.18.1 on 3.12 and later, and the goldens pass on
both. 3.13 resolves the same pins as 3.12.

**3.14: not blocked by wheels, not yet tested.** Resolving the committed lock for 3.14 on Linux
with binary wheels only (`uv pip compile requirements.lock --python-version 3.14
--python-platform x86_64-manylinux_2_28 --only-binary :all:`, run 2026-11-14) succeeds with the
same pins as 3.12 and 3.13: no dependency lacks a 3.14 wheel, so there is no blocking package to
name. 3.14 is not in the matrix because it has not been run; adding it is a one-line matrix and
classifier change, and the drift test above keeps the two in step. Evidence class: resolver
output, not a test run.
