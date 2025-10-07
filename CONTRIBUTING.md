# Contributing to Notr

We welcome contributions and feedback! This document outlines the quickest way to get started and the expectations for pull requests.

## Getting Started

1. **Fork and clone** the repository.
2. **Create a virtual environment** (or rely on `pipx`):
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   ```
3. **Install the project in editable mode** with development dependencies:
   ```bash
   pip install -e .[dev]
   ```
4. **Run the test suite** before you begin to ensure the baseline is green:
   ```bash
   pytest
   ```

## Development Workflow

- Follow the existing code style; use descriptive names and add comments only when the intent is not obvious.
- Keep functionality well covered by unit tests. Add new tests when fixing bugs or adding features.
- Run `pytest` (and any relevant linters) before opening a pull request.
- Update the documentation (`README.md`, docstrings) if you introduce new behaviour or configuration.
- Avoid force pushes to shared branches; rebase responsibly.

## Submitting Changes

1. Create a branch for your work.
2. Commit your changes with clear, concise messages.
3. Open a pull request describing:
   - The motivation for the change.
   - A summary of the implementation.
   - Any testing you performed.
4. Respond to review feedback promptly and keep discussions respectful.

By contributing you agree that your submissions will be licensed under the MIT License included in this repository.

Thank you for helping make Notr better!
