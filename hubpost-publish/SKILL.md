---
version: 0.1.0
name: hubpost-publish
description: |
  Schedule, publish and review social media posts with the HubPost MCP server on Instagram, TikTok, YouTube, Facebook, LinkedIn, X, Threads, Pinterest and Bluesky. Picks the right connected account, attaches media from the HubPost library, adapts the text per platform and confirms before anything goes out. Use when: "post this", "schedule this video", "put this on TikTok and Reels", "what is scheduled this week", or "how did my last posts do". NOT for creating or editing the media itself; make the image or video first, then hand it to this skill.
---

# HubPost Publish

HubPost schedules and publishes posts to the social accounts a user connected in their HubPost workspace.

## Bootstrap

The `hubpost` MCP server ships with this plugin at `https://mcp.hubpost.co/mcp`. The first tool call asks the user to sign in with OAuth. If the tools are missing, tell the user to add that server to their agent; a free plan is available at [hubpost.co/pricing](https://hubpost.co/pricing).

## Start with the accounts

Every post belongs to one connected account, and the platform and its options come from that account. Call `list_social_accounts` before creating a post, and use `get_account` for plan limits.

## Media

Posts can only attach media from the user's HubPost library.

- Reuse what is already there with `list_media`.
- In a chat that renders widgets, call `show_upload_widget` as soon as the user wants media that is not in the library. It returns the mediaId.
- When you can send HTTP requests with the file's bytes yourself (a file on disk, or one attached in Slack), call `get_upload_url`, PUT the raw bytes to that URL (not multipart) with the same Content-Type, then call `confirm_upload` to get the mediaId. The URL expires in 1 hour.

## Posting

- `create_post` saves a draft, or schedules the post when `scheduledAt` is set. A `scheduledAt` that is already due publishes immediately.
- Change a draft or scheduled post with `edit_post`, remove it with `delete_post`.
- Review the calendar with `list_posts` and a single post with `get_post`. Published posts include views, reactions, comments and shares when the platform reports them, so use these to answer how recent posts did.
- Published posts cannot be edited or deleted. Confirm the account, the text, the media and the time with the user before publishing or scheduling, unless they already approved that exact post.
- One post per account. To cover several platforms, create one post for each account and adapt the text to each platform.

## Batches

When the user hands over several clips or images at once, spread them over time instead of posting them together: propose a schedule (for example one per account per day at the user's usual hour), show it as a short table, and create the posts only after they approve it.
