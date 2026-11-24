# Changelog

Notable changes to DakarBot. Every push to `main` deploys, so entries are grouped by
deploy date rather than version number. New changes go under **Unreleased** until they
ship.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

Nothing yet.

## 2026-11-24

### Added
- This changelog.
- User-app support: `/about`, `/emoji`, `/fox`, `/pokemon`, `/action`, `/avatar`,
  `/suggest` and `/poll` can be added to your own account and used in DMs, group chats
  and servers the bot isn't in. `/about` has an "Add to my apps" button for this.
- `/about`: servers, users, uptime, latency, memory/CPU and versions, with Invite and
  Support links.
- Icon button on `/server` to view the server icon at full size.
- `/emoji`: a custom emoji's image, details and download link.
- `/poll`: Discord's native polls, with 2–10 answers and a chosen duration.
- `/announce`: post a styled announcement to a channel through a form (Manage Messages).
- `/suggest`: send a suggestion to the developers. The owner approves or denies it with
  a reason, and the submitter gets a DM.
- Currency: `/balance`, `/balance-reset`, `/work`, `/pay`, plus `/currency set` and
  `/currency name` for admins and `/settings currency-log` for a log channel.
- `/action`: hug, pat, boop, cuddle, blush and more, each with a GIF. The owner manages
  the GIFs with `/gifs add|remove|list`.
- `/fox` and `/pokemon`.
- `/owner` tools for the bot owner: server and user lookups, a user/server blacklist,
  leaving a server, restarting the bot, and read-only SQL queries.
- Notices when the bot joins or leaves a server, and a log of every command run.
- Blacklisted users and servers get a private notice instead of a response.

### Changed
- Cleaner, more professional messages: removed emoji from headings, labels, buttons
  and replies.

### Fixed
- `/action` GIFs from Tenor's old `media1.tenor.com` links showed "Image not found";
  they now use `media.tenor.com`, existing ones are repaired on startup, and `/gifs add`
  converts them automatically.
- Removed five Imgur GIFs that Discord only showed as still images.

## 2026-11-17

### Added
- Errors and warnings are reported to a Discord channel.
- Headings, icons and per-action colors across the bot's messages.
- Automatic test-and-deploy pipeline.

### Fixed
- Very long durations are capped at 10 years instead of crashing the command.

## 2026-08-22

### Added
- Initial release: moderation (ban, kick, mute, strike, pardon and case management),
  automod filters, bad-word and auto-response rules, logging, server and user info,
  channel lock/unlock, purge, per-server settings, and data import/export.
