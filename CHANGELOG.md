# Changelog

Notable changes to DakarBot. Every push to `main` deploys, so entries are grouped by
deploy date rather than version number. New changes go under **Unreleased** until they
ship.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added
- Translate: right-click any message and pick Apps > Translate to see it in your own
  Discord language, privately. `/translate` translates typed text into a language you
  choose (your own by default). Both work as user apps, so they also work in DMs and
  servers the bot isn't in. Up to 1,500 characters at a time.

## 2026-11-26

### Added
- `/profile`: your balance, level, daily streak, favorite action and item count on one
  card, with server ranks.
- `/trivia`: a multiple-choice question from Open Trivia DB with answer buttons and 20
  seconds to answer. A correct answer pays 50, 100 or 150 depending on difficulty.
  Works as a user app too, but only pays out in servers the bot is in.
- `/remind in|list|cancel`: reminders from a minute to 10 years away, up to 25 each.
  They arrive by DM, or in the channel you set them in if your DMs are closed.
  Reminders that came due while the bot was down are sent when it's back. Works as a
  user app too.
- `/coinflip`: call heads or tails, and a correct call doubles your bet.
- `/slots`: three reels. Three of a kind pays 5x to 100x, and two Cherries pay 1.5x.
- `/blackjack`: Hit, Stand and Double buttons. The dealer stands on 17, and a natural
  blackjack pays 3:2. If you walk away, you stand after 2 minutes.
- `/rps @member <bet>`: rock paper scissors for a pot. Both bets are held until the
  duel ends, and a decline, timeout or tie refunds them.
- `/rob @member`: a 40% chance to steal 10-30% of someone's balance. If you're caught,
  you pay them a fine. One try an hour. It's off until an admin turns it on with
  `/currency rob`.
- Bets in games that were cut short by a restart are refunded when the bot starts.
- Leveling: chatting earns 15-25 XP, at most once a minute. Each new highest level pays
  100 × the level in currency, and admins can attach a role to any level with
  `/levels-admin reward-set`. `/rank` shows your level and progress, and `/leaderboard`
  has a Levels board.
- `/cash-in`: trade XP for currency, 1 XP for 1 coin. Your level drops, and levels
  you've already been paid for don't pay again when you climb back.
- `/levels-admin announce`: post level-ups where the member chatted, in a set channel,
  or not at all.
- `/daily`: claim a reward once per UTC day. It starts at 200 and grows by 50 for each
  day in a row, up to 500 on a 7-day streak. Missing a day resets the streak.
- Shop: `/shop` lists what's for sale, `/buy` buys it and `/inventory` shows what you
  own. Admins manage it with `/shop-admin add|remove` (Manage Server). Items either give
  a role or are collectibles that stack. If the bot can't give the role, you aren't
  charged. Roles with moderation or admin permissions can't be sold. Purchases go to
  the currency log.
- `/leaderboard`: the server's richest members and longest active daily streaks, plus
  your own rank.
- `/action` counts: actions toward someone now show a running tally, like "Alice has
  hugged Bob 14 times". `/action-stats` shows the actions someone gives and gets most.
  Counts are kept across all servers and DMs.

### Fixed
- `/rob` crashed instead of saying when you could try again.

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
