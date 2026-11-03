# Intelligent Git Commit Message Generator — Plan

## Implementation approach
A self-contained Flask application will provide REST APIs, cookie-based sessions, SQLite persistence, upload validation, authentication, and the React browser interface. The UI is delivered from `static/` and uses React and ReactDOM from stable CDNs, keeping the project easy to run and package while retaining a real React implementation. AI generation uses an OpenAI-compatible API when configured through environment variables; a clearly labeled demo fallback makes the project runnable without credentials.

## Product flow
Users register or sign in, paste or upload a unified Git diff, inspect file-level change statistics, generate three Conventional Commit suggestions, edit/copy a selected message, and review/search prior generations. Dashboard metrics are computed from stored history. Settings control theme and generation defaults. GitHub is represented as an authorization-gated extension point and never pushes commits.

## Project structure
- `app.py`: Flask application, API routes, authentication, SQLite schema, AI integration, and diff parsing.
- `static/index.html`: app shell and CDN dependencies.
- `static/app.js`: React UI, navigation, forms, dashboard, analyzer, history, settings, and clipboard actions.
- `static/styles.css`: responsive dark/light developer dashboard styling.
- `tests/test_app.py`: authentication, diff parsing, validation, history, and demo AI tests.
- `.env.example`: provider and security configuration template.
- `requirements.txt`: pinned Python dependencies.
- `README.md`: setup, API, testing, security, and deployment instructions.

## Design
Three possible directions: Terminal Aurora (0.06), Blueprint Control Room (0.07), and Midnight Observatory (0.08). Chosen: **Blueprint Control Room**.

- Design Movement: editorial developer tooling with blueprint grids, restrained glass surfaces, and luminous status accents.
- Core Principles: information density without clutter; every action has visible state; code-inspired hierarchy; accessible contrast and responsive behavior.
- Color Philosophy: deep navy provides focus, electric blue signals action, violet marks intelligence, and mint confirms successful analysis.
- Layout Paradigm: a persistent command sidebar paired with a wide workbench; content panels align to a baseline rather than a generic centered grid.
- Signature Elements: blueprint grid texture, monospace diff/editor areas, and a small “AI signal” gradient marker.
- Interaction Philosophy: fast, reversible, and explicit—copy, regenerate, and save actions confirm themselves without interrupting flow.
- Animation: short 160–220ms fades and panel lifts; no distracting looping motion.
- Typography System: Inter for interface text, JetBrains Mono for code and commit messages, with strong numeric dashboard metrics.
- Brand Essence: “Turn code change into team-ready intent.” Precise, calm, useful.
- Brand Voice: direct and technical; examples: “Your diff has a story.” and “Choose the message that matches the change.”
- Wordmark & Logo: a split bracket mark containing a small spark, paired with the `commit//signal` wordmark.
- Signature Brand Color: electric blueprint blue `#4f8cff`.
