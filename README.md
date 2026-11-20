# HubPost Skills

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](./LICENSE)
[![Version](https://img.shields.io/badge/version-0.2.0-green.svg)](./VERSION)

Agent skills for [HubPost](https://hubpost.co): schedule and publish to Instagram, TikTok, YouTube, Facebook, LinkedIn, X, Threads, Pinterest and Bluesky from Claude Code, Codex, Cursor, Gemini CLI and any agent that loads Markdown skills.

## Install

### `npx skills`

```bash
npx skills add hubpost-co/skills
```

### `gh skill install`

```bash
gh skill install hubpost-co/skills
```

### Claude Code

```
/plugin marketplace add hubpost-co/skills
/plugin install hubpost@hubpost
```

### Gemini CLI

```bash
gemini extensions install https://github.com/hubpost-co/skills
```

## Skills

| Skill | Invoke (Claude Code) | What it does |
|---|---|---|
| [`hubpost-clipper`](./hubpost-clipper) | `/hubpost:hubpost-clipper` | Turn a livestream (live or VOD), a podcast or any long video into finished vertical clips on your own computer: chat and sound spikes, facecam layout, captions, frame checks. Needs no HubPost account; hands finished clips to `hubpost-publish` when you want them scheduled. |
| [`hubpost-publish`](./hubpost-publish) | `/hubpost:hubpost-publish` | Schedule, publish and review posts across every connected account, with per platform text and a confirmation before anything goes out. |

## Sign in

The skills use the hosted HubPost MCP server at `https://mcp.hubpost.co/mcp`. The first call asks you to sign in with your HubPost account through OAuth. Your agent never sees your password or your social platform tokens. A free plan is available at [hubpost.co/pricing](https://hubpost.co/pricing).

## Adding a skill

Each skill is a folder named `hubpost-<job>` with a `SKILL.md` at its root. Its frontmatter needs `name` (matching the folder), `version` (matching [`VERSION`](./VERSION)) and a `description` that says when to use it ("Use when") and when not to ("NOT for"). Add the folder to `.claude-plugin/marketplace.json`. CI checks all of this on every pull request.

## Support

support@hubpost.co · [Privacy](https://hubpost.co/legal/privacy-policy) · [Terms](https://hubpost.co/legal/terms-of-use)
