# NBA plugin proposal (`nba.1h.py`) — 2026-10-25

Follow `bball.6h.py` conventions: uv script header, swiftbar metadata, SF Symbol title,
Menlo tables with header rows, `color=<light>,<dark>` pairs, last-good cache in
`~/.cache/swiftbar-plugins/`, `--check` exiting 1 on failure, ⌥-alternate Add to Calendar
(Fantastical `x-fantastical3://parse?add=1&sentence=`), pure `parse_*` functions tested in
`tests/test_nba_parsers.py` against trimmed fixtures.

## Config `~/.config/swiftbar-plugins/nba.json`
- `players`: ESPN athlete IDs, default `[1966, 3992]` (LeBron James, James Harden).
  Teams derive from the players every run (ESPN currently lists LeBron on PHI, Harden on CLE).
- `extra_teams`: optional ESPN team IDs followed independently of a player.
- `max_past_games` (5), `leader_categories` (["PTS","AST","REB"] on the main line; STL, BLK, 3PM in submenus),
  `timezone` ("America/Chicago").

## Verified ESPN endpoints (all free, undocumented)
- Athlete + season averages + team + injury status:
  `https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{id}/overview`
  (`statistics.labels` = GP MIN FG% 3P% FT% REB AST BLK STL PF TO PTS; `statistics.splits[0].stats`)
- Game log: `https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{id}/gamelog`
  (`labels` = MIN FG FG% 3PT 3P% FT FT% REB AST BLK STL PF TO PTS; `events{}` has gameDate, opponent,
  gameResult, score; `seasonTypes[].categories[].events[]` has eventId + stats)
- Team (name, abbreviation): `https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{id}` → `athlete.team`
- Leaders: `https://site.web.api.espn.com/apis/site/v3/sports/basketball/nba/leaders?limit=10`
  (`leaders.categories[]` abbreviation PTS/AST/REB/STL/BLK/3PM…, both per-game and totals categories
  share abbreviations — pick by displayName; team abbreviation was missing in probe, resolve via athlete)
- Scoreboard (today, live scores, broadcasts, records):
  `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard`
- Team schedule: `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams/{id}/schedule`
  (same shape as the college endpoint `parse_espn_schedule` already handles)
- Standings: `https://site.web.api.espn.com/apis/v2/sports/basketball/nba/standings`
  (`children[]` = conferences; `standings.entries[].stats` names wins, losses, playoffSeed, gamesBehind,
  streak, "Last Ten Games")

## Menu layout
```
🏀 2        ← tracked-player games today; basketball.fill + color while a tracked game is live
---
MY GUYS
LeBron James · PHI #23   15.5 pts  9.5 reb  6.0 ast  33.5 min   (tooltip FG%/3P%/FT%/TO)
--Last 5 games  Menlo table: DATE OPP W/L MIN PTS REB AST FG 3PT
--Season highs: pts · reb · ast
--Status line (injury note in yellow when present)
James Harden · CLE #1 ...
---
NEXT UP      relative day, tip in Central, matchup, TV network; ⌥ Add to Calendar
HEAD TO HEAD only when the two tracked teams meet
---
LEAGUE LEADERS   PTS / AST / REB on one line; submenu per category top 10 + tracked players' rank
---
STANDINGS   EAST / WEST, tracked teams always shown with seed, GB, streak, L10
---
Refresh
```
Off-season: countdown to opening night like bball's `print_openers`.

## Optional ideas (ask user which to include)
- Milestone watch: LeBron career points + points to next thousand.
- Load-management flag: mute Next Up line with reason when player is Out/Questionable.
- Clutch line: last game +/- and 4th-quarter points.
- macOS notification when a tracked player passes a point threshold in a live game.
- Faster effective refresh (5 min) while a tracked game is live.

## Open questions
- Title count: games today vs. players' combined points last night.
