# Welke is mooier? How DataVault ranks photos from pairwise choices

DataVault's Shows feature has a game called **Welke is mooier?** ("Which one is nicer?"). It shows two
photos side by side. The user picks one, calls it a draw, or skips the pair. Those choices build a
ranking of the user's own photos. Shows uses that ranking to play the best photos more often, and
the user can turn the top of the ranking into an album or an export.

This document explains how the ranking works, why it was rebuilt, and how the rebuild was measured.

## The problem

Pairwise ranking looks simple. You count wins. In practice three things make it hard:

1. **Noise.** People are not consistent. Two similar sunsets are close to a coin flip, and some
   clicks are mistakes or a passing mood.
2. **Budget.** Nobody compares 100 photos with each other 4,950 times. Each choice must teach as much
   as possible.
3. **Feel.** It is a game. It must feel instant and varied, and it must never punish a double click
   or a held key.

## The first version: classic Elo

The first version used chess-style **Elo**. Every photo started at 1000. A choice moved both photos
by `K * (S - E)`, where `E = 1 / (1 + 10^((Rb - Ra) / 400))` is the expected score. K was 32 for a
photo's first 30 comparisons and 16 after that. It chose pairs like this:

- The first photo was weighted towards photos with few comparisons.
- The opponent was one of the 8 photos nearest in rating.

That works, but it has three structural weaknesses:

- **No uncertainty.** A photo with one comparison and a photo with two hundred count the same. A new
  photo moves at most 32 points per choice, so it needs many choices to reach its level.
- **A noise floor.** With a fixed K, ratings keep moving by up to K points after every choice. Even a
  well-known photo keeps bouncing around, so accuracy levels off instead of improving.
- **Pairs chosen by rule of thumb.** "Few games, close rating" is a reasonable heuristic, but it
  does not ask which comparison would teach the most.

## The new version

### Rating model: Glicko-2

Each photo now has three numbers, stored in the `elo_ratings` table:

| | Meaning |
|---|---|
| `mu` | The rating, on the familiar Elo scale (400 points means 10:1 odds). |
| `rd` | The *rating deviation*: how unsure we are. It is 350 for a photo nobody has compared. |
| `vol` | The volatility: how erratic this photo's results are. |

After a comparison, each photo gets the Glicko-2 update for one game (Glickman, 2012) against the
other photo's state *before* the game. A draw scores 0.5. The update is large when we are unsure and
small when we are sure:

- Two new photos move about ±162 points on their first comparison.
- A photo that already has 30 comparisons moves only a few points.

This is what makes new photos converge quickly, and why the ranking keeps improving instead of
levelling off. The test suite checks the implementation against the worked example in Glickman's
paper (1464.06 / 151.52 / 0.05999).

The ranking can now show its own confidence. Every photo's rating comes with a 95% band of
`mu ± 2·rd`, and the Ranglijst (leaderboard) draws that band.

### One scale, votes included

The slideshow has always had thumbs up and down (+50 or -50 on the same 1000-based scale), and
Shows, smart albums and filters all read `file_ratings.rating`. That number stays the public rating:
`round(mu)`, never below 100.

A vote changes `file_ratings.rating` exactly as before. The model treats a vote as a shift of `mu`:
the effective rating is `mu + (rating_now - rating_written_last)`. No vote is lost, and the vote
code did not change.

### Pair selection: expected information

For a candidate pair, we compute how much one comparison would shrink *both* photos'
uncertainty. This is the log variance reduction from Glicko's update,
`log(1 + φ² g(φ_other)² E(1-E))` for each side. It is largest for two uncertain photos whose outcome
is close to a coin flip.

The selection works in four steps:

1. Draw about 10 "first" photos. The weight is `rd²` (uncertain photos first), with an extra weight
   for photos that may belong to the top. The top is what Shows and the Ranglijst show most.
2. For each first photo, take the 14 photos nearest in rating plus 3 random ones. The random ones
   are exploration: they mix in new and rarely compared photos.
3. Score every candidate pair by its expected information.
4. Draw one pair with probability ∝ information³. The result is close to the best pair, but never
   a predictable pattern.

On top of that:

- Photos from the last 8 pairs are held back, so no photo appears twice in a row.
- Pairs compared before rarely come back.
- The screen sends the last 8 pairs it showed (`recent=`), also those whose pick is still being
  saved. They do not come back while other pairs can, and their photos are held back. New pairs
  are only asked for after the pending picks are saved. In an 8-photo album, 40 fast picks in
  WebKit used to show 12 of the 28 pairs, the pair just picked returning every third step; now
  they show 26 of them and none returns within 8 steps.
- In a small collection the new pairs may share a photo with a queued pair (in a tiny one, also
  with the pair on screen). Keeping every queued photo out left so few photos that the same
  handful of pairs came round again and again.
- These pairs are never shown:
  - the pair on screen and the pairs already queued;
  - a pair skipped in the last 30 days;
  - exact copies;
  - burst frames (one series in a folder, each frame within 2 s of the previous);
  - near-duplicates found by the perceptual-hash step (`near_dup_pairs`).

### Keeping the history: replay

Every comparison is stored in `elo_comparisons`: both photos, the outcome, the time, and both
photos' public rating and model state before and after. Schema migration 160 does three things:

1. It rebuilds that table. SQLite cannot change the `CHECK` constraint to allow `'draw'` in place.
   Ids and the AUTOINCREMENT counter are kept.
2. It adds the model columns.
3. It **plays the whole old history again** with Glicko-2.

The user's choices never change, only what is derived from them. Votes cast between two comparisons
of a photo show up as the gap between one row's "after" and the next row's "before". They are
carried over exactly. Replaying is deterministic: running it twice gives the same state, and a
test checks this.

### Undo, double picks, speed

- **Undo** restores both photos' public rating and model state from the history row. Pressing `z`
  again steps further back, newest first. If a photo changed since (a later vote or comparison),
  the server refuses with a clear message instead of guessing.
- **Double picks** are stopped in three places:
  - A held key does not repeat.
  - A pick within 250 ms of a pair appearing does not count. No human judges two photos that fast.
  - Each POST carries a `client_token`, and a repeated token counts once.
- **Speed.** The screen keeps two pairs ready, with their images loaded and decoded. A pick swaps to
  the next pair at once; the smoke test measures under 50 ms, and the new pair is at least 90%
  visible within 200 ms (about 130 ms). The save runs behind it in a strict queue, so an undo
  pressed during a save waits for that save and then undoes it.
- **Server time.** How often each pair was compared, the comparisons per day (the streak) and per
  scope, and the current session are kept in memory per database and follow the table: new rows
  are added by id, an undo takes its row out, a move that renames paths starts over, and a row
  count that does not match rebuilds it. Migration 161 adds covering indexes for reading every
  rating below a folder. With 10,000 photos and 75,000 comparisons (7.5 per photo), the next-pair
  request went from 159 to 40 ms (median, over HTTP) and saving a pick from 88 to 31 ms.
- **An album as the range.** A smart album is a registered scope (`elo.register_scope('album', ...)`,
  key `album:<id>`), so its duels, history, progress and Ranglijst are its own. Its photos are asked
  of the index and kept per selection version (plus the album's `updated_at` and the disabled show
  folders), like a folder's: an album of all 10,000 photos (75,000 comparisons) answers the
  next-pair request in 36 ms (was 86 ms), a pick in 24 ms (was 76 ms) and the Ranglijst in 41 ms
  (was 91 ms), medians over HTTP, against 27, 16 and 29 ms for the same photos as a folder.
- **Choosing the range.** The collection picker at the top (`c` from the duel or the Ranglijst) lists
  the whole library, every show and every smart album that can hold photos. Typing filters it,
  `↑`/`↓` and Enter choose, and the keys go straight back to the duel. `Esc` closes only the
  picker. The last choice is remembered in the browser for the next time the window opens; an album
  that no longer exists falls back to the whole library with a short notice. An open album also has
  a **Welke is mooier?** button in Albums.
- **Far-apart ratings.** Votes have no upper cap, so two photos can be thousands of points apart.
  The expected score is clamped just inside 0 and 1, so the update never divides by zero, also not
  while migration 160 replays an old history.

## Measuring it

### The simulation harness

`code/tests/elo_simulation.py` is seeded and runs in pytest.

- Each simulated photo has a hidden true quality, normally distributed on the Elo scale.
- A simulated user picks the better photo with the Bradley-Terry probability of the *true*
  qualities, so close photos are near coin flips.
- A share of picks is plain random. These are the inconsistent clicks real people make.

After every few comparisons, the system's ranking is scored against the truth:

- **Spearman's ρ** and **Kendall's τ** for the whole order;
- **top-10 precision**: how many of the true top 10 are in the system's top 10.

The baseline is a verbatim copy of the old code. The new system is the production code: the same
`choose_pairs` and `glicko_pair` that the server calls.

Two kinds of user, with 100 photos and 20 seeds:

- **standaard** (hesitant): quality spread 200 points, 5% random picks.
- **beslist** (decisive): quality spread 300 points, 2% random picks.

### Same accuracy, fewer comparisons

The first three rows of each table use the accuracy the **old** system reaches with its full
budget (15 comparisons per photo, 1500 in total). The table shows how many comparisons each system
needs to get there. The "extra level" rows are a lower bar, not the old system's final
level; that saving depends more on the seeds (hesitant, seeds 101 to 120: 607 against 575, 5%).
The headline numbers are the first three rows: 15 to 24% fewer comparisons for a hesitant user,
35 to 45% for a decisive one.

**Hesitant user** (100 photos):

| Goal | Old (Elo) | New (Glicko-2 + information) | Saving |
|---|---:|---:|---:|
| Spearman ρ 0.89 | 1390 | 1078 | **22%** |
| Kendall τ 0.72 | 1405 | 1072 | **24%** |
| Top-10 precision 0.67 | 825 | 700 | 15% |
| Top-10 precision 0.60 (extra level) | 666 | 475 | 29% |
| Spearman ρ 0.80 (rough order) | 592 | 513 | 13% |

**Decisive user** (100 photos):

| Goal | Old (Elo) | New | Saving |
|---|---:|---:|---:|
| Spearman ρ 0.92 | 1168 | 757 | **35%** |
| Kendall τ 0.77 | 1287 | 799 | **38%** |
| Top-10 precision 0.78 | 1263 | 694 | **45%** |
| Top-10 precision 0.60 (extra level) | 425 | 269 | 37% |
| Spearman ρ 0.80 (rough order) | 335 | 316 | 6% |

With 200 photos (8 seeds) the pattern is the same and stronger at the top. For the hesitant user,
the new system reaches the old system's final top-10 precision with 61% fewer comparisons. For the
decisive user, it needs 44 to 45% fewer for Spearman and Kendall.

### Same budget, better ranking

Accuracy after the same number of comparisons (100 photos, 20 seeds), shown as ρ / τ / top-10:

| Comparisons | Hesitant: old | Hesitant: new | Decisive: old | Decisive: new |
|---:|---|---|---|---|
| 200 | 0.617 / 0.436 / 0.35 | 0.636 / 0.456 / 0.45 | 0.716 / 0.518 / 0.42 | 0.708 / 0.520 / 0.52 |
| 500 | 0.782 / 0.589 / 0.55 | 0.795 / 0.608 / 0.60 | 0.848 / 0.660 / 0.62 | 0.870 / 0.695 / 0.73 |
| 1000 | 0.869 / 0.691 / 0.65 | 0.883 / 0.710 / 0.72 | 0.912 / 0.751 / 0.76 | 0.944 / 0.805 / 0.80 |
| 1500 | 0.898 / 0.729 / 0.67 | 0.922 / 0.768 / 0.76 | 0.927 / 0.774 / 0.78 | 0.966 / 0.849 / 0.82 |

The old system levels off: from 1000 to 1500 comparisons the decisive user gains only 0.015 ρ,
because of the fixed-K noise floor. The new system keeps improving.

### What did not improve

- **The first rough order.** Around 2 to 5 comparisons per photo, both systems are equally fast at
  the coarse whole-list order (Spearman 0.80). With 200 photos and a hesitant user, the new system
  is even about 13% slower there. Part of the budget goes deliberately to the top of the list, where
  the gain is largest. Early on, almost every photo is new, and neither system can know much yet.
- **Newcomers.** The old system already gave photos with few comparisons strong priority. When new
  photos join a ranked set, both systems place them about equally fast: the mean rank error is
  0.13 vs 0.14 of the list after 8 comparisons per newcomer.
- **The model choice alone.** In an experiment, a full Bayesian Bradley-Terry refit after every
  choice was only about 0.01 ρ better than online Glicko-2. Glicko-2 was kept because it updates
  in O(1), and that makes an exact, per-comparison undo possible.

### "Ranking X% settled"

The screen shows how settled a collection's ranking is: the mean over its photos of
`clamp((350 - rd) / (350 - 75))`. In the simulation, about 55% settled corresponds to roughly
2 comparisons per photo, 81% to 5 and 94% to 10. At 15 per photo it is 99%, and the remaining
gains there are small. It is an honest, uncertainty-based progress bar, not a promise of perfection.

## Reproducing

```bash
cd code
venv/bin/python tests/elo_simulation.py --items 100 --seeds 20      # the tables above
venv/bin/python tests/elo_simulation.py --items 200 --seeds 8
venv/bin/python -m pytest -q tests/test_elo.py tests/test_elo_simulation.py
```

`test_elo_simulation.py` checks the saving in a smaller setting (60 photos, 8 seeds), so the claim
stays true while the code evolves.
