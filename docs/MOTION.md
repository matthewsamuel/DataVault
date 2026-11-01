# Motion in DataVault: "Lichtval"

DataVault is a photo archive. The photos are the hero, and the motion language exists to make
them feel alive and to make the app feel like one calm, precise instrument. We call it
**Lichtval** ("fall of light"): things arrive the way light settles on a surface. They come
quickly, decelerate softly and come to rest exactly in place, sometimes with a small, physical
give.

This document is the contract for every screen. The tokens live in `code/frontend/style.css`
(the `Beweging` block inside `@tokens:start`) and the helpers in `code/frontend/motion.js`
(`window.Motion`). A live demo is the **Beweging** section at the bottom of `styleguide.html`.
Frame timing is checked by `code/tests/ui/motion_perf.py`.

---

## 1. Personality

The motion behaves like a curator's hands: quick, sure and quiet around the photos, with a
moment of delight when the user is rewarded.

| We are | We are not |
| --- | --- |
| **Precise.** Motion lands exactly; nothing drifts or wobbles after it arrives. | Floaty, slow or decorative. |
| **Physical.** Springs, not timers. Things have mass and are interruptible mid-flight. | Linear, robotic or "tweened". |
| **Spatial.** Every element comes from somewhere and goes somewhere that makes sense. | Teleporting, or flying in from random directions. |
| **Quiet around photos.** The image never bounces, spins or blurs for effect. | Ken Burns on a thumbnail, tilting cards, or parallax on photos you are judging. |
| **Generous at rewards.** A finished export, a winner in a duel or a check badge may pop. | Bouncy for every click. |

Think Apple Photos for the image continuity, Linear for the speed and restraint of the
chrome, and an Awwwards site of the day for the choreography of a page arriving. It is always
in service of finding, judging and keeping photos.

---

## 2. Principles

1. **Purpose.** Every motion answers one of four questions: *where did it come from*, *where
   did it go*, *what changed*, or *did it work*. If it answers none of these, remove it.
2. **Continuity.** The same object keeps its identity across states. A thumbnail grows into the
   viewer and shrinks back into the same tile (shared element). The green nav indicator glides
   from the old tab to the new one. A dismissed toast closes its gap and the others slide up
   (FLIP). Never cross-fade two things that are really one thing.
3. **Physics.** Use springs (`--spring-*`) for anything that moves. A spring can be
   interrupted from where it is (`fromCurrent`, `key`), so motion never fights the user. Use
   curves (`--ease-*`) only for opacity and colour.
4. **Rhythm.** Groups arrive in order with a short, even beat (`--stagger-step`, 28 ms), capped
   at `--stagger-max` (10) steps. A page should feel *composed*, not like a list being printed.
5. **Restraint where the user works fast.** The Keurer keys, the Elo duel (Shows: "Welke is
   mooier?"), typing in search, arrow keys in a grid and the viewer's next and previous are
   **instant**. A key press is answered in the next frame. Motion there is at most a fade of 140
   ms or less that never delays the next input. The user's speed is the feature.
6. **Exit faster than enter.** Things arrive with a spring (about 280 to 520 ms perceived) and
   leave with an accelerating curve in `--dur-3` (200 ms). The app never makes you watch
   something leave.
7. **One hero per moment.** In one transition only one thing travels far. Everything else
   fades or moves a short distance (`--motion-distance-sm` or `-md`).

---

## 3. The three modes (Instellingen > Beweging)

| Mode | What moves | Set by |
| --- | --- | --- |
| **Volledig** (default) | Everything: springs, staggers, shared elements, view transitions, tickers. | The user, stored in `app_settings` (`ui_motion`) through `GET/PUT /api/ui/settings`. |
| **Rustig** | Only short fades (at most 200 ms). Nothing translates, scales, springs or staggers. | The user, *or automatically* when macOS asks for less motion (`prefers-reduced-motion`) and the user did not choose Uit. |
| **Uit** | Nothing. Every helper applies the end state at once. | The user. The smoke test forces Uit. |

`motion.js` loads first in `<head>`, reads the copy in `localStorage` and sets
`html[data-motion="volledig|rustig|uit"]` before the first paint. It then syncs with the
server, which wins. You never check `matchMedia` yourself. Ask `Motion.full()`,
`Motion.calm()` or `Motion.off()`, or simply call the helpers, because they already do the
right thing in every mode.

In CSS the tokens adapt per mode. In Rustig the distances become `0px`, the scales `1`, the
springs a short ease-out and `--stagger-step` `0ms`. In Uit every duration is `0ms` and all CSS
animations and transitions are stopped. **If your CSS uses the tokens, it is already
mode-aware.** Never write a literal duration or distance.

---

## 4. Tokens

### Durations

| Token | Volledig | Use |
| --- | --- | --- |
| `--dur-1` | 90 ms | Press feedback, hover colour, a checkbox tick |
| `--dur-2` | 140 ms | Chips, toggles, tooltips, the fade of fast flows (Keurer, Elo) |
| `--dur-3` | 200 ms | Menus, popovers, **every exit** |
| `--dur-4` | 280 ms | Modal and page backdrop fade, a section arriving |
| `--dur-5` | 420 ms | Large surfaces, panel slides |
| `--dur-6` | 640 ms | Number tickers, the theme reveal, a one-off hero moment |

The old names `--duration-fast`, `--duration-base` and `--duration-slow` now point at `--dur-2`,
`--dur-3` and `--dur-4`.

### Curves

| Token | Curve | Use |
| --- | --- | --- |
| `--ease-standard` | `cubic-bezier(0.2, 0, 0, 1)` | Anything that changes in place (colour, size of a bar) |
| `--ease-emphasized` | `cubic-bezier(0.05, 0.7, 0.1, 1)` | A big reveal that should start fast and land slowly |
| `--ease-exit` | `cubic-bezier(0.3, 0, 0.8, 0.15)` | Leaving: accelerates away |
| `--ease-out` | `cubic-bezier(0.2, 0.8, 0.2, 1)` | Fades (existing components) |

### Springs (CSS `linear()`, mass 1)

Every spring has a duration token for the time it needs to settle. Use them together:
`animation: name var(--spring-smooth-dur) var(--spring-smooth) both;`

| Token | Physics | Feels like | Use |
| --- | --- | --- | --- |
| `--spring-snappy` | k 420, c 34 (about 1% overshoot) | A precise click into place | Indicators, toasts, FLIP reflow, small controls, press release |
| `--spring-smooth` | k 190, c 25 (no overshoot) | Gliding to rest | Modals, page blocks, reveals, cards arriving |
| `--spring-bouncy` | k 320, c 20 (12% overshoot) | A happy pop | **Only rewards**: check badges, a duel winner, "Klaar", a toast icon |
| `--spring-gentle` | k 150, c 24 (critically damped) | Heavy and calm | Big surfaces, the thumbnail to viewer morph |

For a case the four do not cover, `Motion.springEasing(stiffness, damping)` returns
`{easing, duration}` from the same physics. In WAAPI, `Motion.spring(el, frames, 'smooth')`
reads the tokens, so CSS and JS always agree.

### Rhythm, distance and scale

| Token | Value | Use |
| --- | --- | --- |
| `--stagger-step` | 28 ms | Delay between siblings |
| `--stagger-max` | 10 | After 10 steps the rest arrive together |
| `--motion-distance-xs` | 2 px | Hover lift of small controls |
| `--motion-distance-sm` | 6 px | Exits, a toast nudge |
| `--motion-distance-md` | 12 px | Blocks rising in, reveals |
| `--motion-distance-lg` | 24 px | Modals rising, toasts sliding in, direction of a page change |
| `--motion-scale-in` | 0.96 | Starting scale of a modal or toast |
| `--motion-scale-press` | 0.97 | `.btn:active` |
| `--motion-lift` | -2 px | Hover lift of a photo tile |

---

## 5. Choreography grammar

### Enter
Content enters with opacity 0 to 1 plus a short travel (`rise`: from 12 px below) on
`--spring-smooth`. Opacity reaches 1 in the first third, then the spring settles the
position. Modals also scale from `--motion-scale-in`.

### Exit
Content leaves in `--dur-3` with `--ease-exit`: it fades and sinks
`--motion-distance-sm` with a slight scale of 0.98. Toasts leave to the right, the way they came.
Leaving elements stop taking clicks immediately (`pointer-events: none`).

### Stagger
Siblings follow in document order with a 28 ms step, top-left to bottom-right. Only what is
**in the viewport** staggers. Things below the fold use `Motion.reveal` when they scroll into
view. Never stagger more than about 10 items one by one, or a grid of 200 tiles.

### Hierarchy order
When a screen arrives, the order is **title, then controls, then content, then meta**:

1. The page header (icon and title, then subtitle),
2. the toolbar (filters, segmented controls),
3. the hero content (photo grid, chart, list),
4. quiet meta (counts, hints, footers).

`Motion.enterPage` applies this order automatically to the visible top-level blocks of a section.

### Direction
Space follows the top nav. Moving to a tab on the **right** brings the page in from the right,
and a tab on the left brings it in from the left. Going *deeper* (folder to subfolder, album to
photo) rises from below or grows from the item clicked. Going *back up* sinks from above
(`Motion.presets.sink`).

### Shared elements
When one object changes form (a thumbnail becoming the viewer photo, a card becoming a detail
panel, a chip becoming a filter), it travels as **one** object with `Motion.sharedElement` or
FLIP. The source is hidden while the ghost travels, so the photo is never seen twice. The
crop of the thumbnail (`object-fit: cover`) opens up smoothly into the full uncropped image.

### Interruptions
Every helper takes a `key`. A new animation with the same key on the same element replaces the
old one, starting from where it was (`fromCurrent: true`). Clicking fast never queues motion.

---

## 6. The green accent in motion

The green (`--accent`) is the app's pulse, so **it is the thing that moves first and lands
last**:

- **Navigation.** The 2 px green indicator glides between tabs on `--spring-snappy`
  (`Motion.glide`). It is the only element that travels the full width of the nav.
- **Selection.** A selected tile gets its green ring with a `--dur-2` fade and its check badge
  pops on `--spring-bouncy`.
- **Progress.** Bars fill with `--ease-standard`. Indeterminate progress keeps its own loop.
- **Focus.** The focus ring appears at once. It is never delayed by motion.
- **Success.** A green check may pop (`Motion.presets.pop` with `bouncy`) once per real
  success.

Green is never animated over large surfaces. There is no green flash, no green glow pulse
(except the existing subtle logo glow) and no green sweep across a page.

---

## 7. The helpers (`window.Motion`)

All helpers return something with `.finished` (a Promise) and `.cancel()`, also when nothing
moves. They never block input. Keyframes go from the start state to the element's own style.

```js
// Mode
Motion.mode();               // 'volledig' | 'rustig' | 'uit'
Motion.full(); Motion.calm(); Motion.off();
Motion.onChange(mode => ...);

// Tokens in JS (read from style.css, mode-aware)
Motion.ms('--dur-4');        // 280 (0 in Uit)
Motion.px('--motion-distance-md');
Motion.springTiming('smooth');   // {easing: 'linear(...)', duration: 520}

// Animate (Web Animations); in Rustig only opacity survives, in Uit nothing runs
Motion.animate(el, [{opacity: 0}, {opacity: 1}], {duration: '--dur-3', easing: '--ease-out'});
Motion.spring(el, [{transform: 'scale(0.9)'}, {transform: 'none'}], 'snappy', {key: 'press'});

// Presets: 'fade', 'rise', 'sink', 'slide' (args: dir, distance), 'scale', 'pop'
Motion.enter(card);                       // rise on the smooth spring
Motion.enter(panel, 'slide', {args: [-1]});
Motion.exit(card, {remove: true});        // fades and sinks in --dur-3, then removes

// Groups
Motion.stagger(grid.querySelectorAll('.thumb-tile'), 'rise');
Motion.reveal(list.children);             // each enters when it scrolls into view

// Reflow: things slide to their new place (sort, filter, delete)
Motion.flip(grid.children, () => applySort());
const play = Motion.flipCapture(cards); render(); play(newCards);

// One object, two places
Motion.sharedElement(thumbImg, bigImg, {direction: 'open'});   // or 'close'

// Numbers
Motion.countUp(el, 12480);                // counts up in --dur-6, Dutch formatting

// Indicator (nav, segmented control, tabs): pseudo element ::after by default
Motion.glide(oldTab, newTab, {inset: el => Motion.px('--space-md')});

// A whole section arriving (done by switchMainView for every main view)
Motion.enterPage(section, {direction: 1});

// View Transitions (document.startViewTransition, falls back to an instant update)
Motion.viewTransition(() => swapLayout(), {kind: 'swap'});

// Scroll-linked (ScrollTimeline when the browser has it, else one rAF per scroll)
Motion.scrollLinked(header, [{boxShadow: 'none'}, {boxShadow: 'var(--shadow-sm)'}], {start: 0, end: 80});
```

CSS-only pieces:

- `.motion-scroll-reveal`: a block rises in as it scrolls into view (`animation-timeline: view()`,
  Volledig only).
- `[data-reveal="pending"]`: set by `Motion.reveal`, hidden until revealed.
- `[data-motion-key="<file path>"]` on a photo tile: the viewer grows from it and shrinks
  back into it, even after browsing to another photo.
- `[data-motion-skip]`: `Motion.enterPage` leaves this block alone (for fixed layers or a map).
- `html[data-vt="theme" | "swap" | <your kind>]`: present during a view transition, for
  `::view-transition-old(root)` and `::view-transition-new(root)` rules.

**`viewTransition` runs your update one frame later.** Use it only where the code after it
does not immediately read the new DOM (theme, grid or list, switching an album). The main
view switch is therefore *not* a view transition. It is synchronous plus `Motion.enterPage`,
so code that calls `switchMainView()` and then works with the new section keeps working.

### What already uses it (the global pieces)

| Piece | Where | Motion |
| --- | --- | --- |
| Page change | `switchMainView` (app.js) | Green indicator glides, the section fades, its blocks slide in from the nav direction with a stagger |
| Every `.modal` and `.dv-overlay` | style.css with the `is-leaving` observer in motion.js | Rises on `--spring-smooth` from 24 px and 0.96, then leaves in `--dur-3`. Nothing to do in your code: toggle `.active` as before |
| Toasts | `showToast` and `hideToast` (app.js) | Slide in on snappy, the icon pops, a draining timer line in the toast's tone (paused on hover), exit to the right, the others close the gap (FLIP). A repeated toast gives a small nudge instead of a duplicate |
| Viewer | `openViewer` and `closeModal` (app.js) | The thumbnail grows into the viewer and shrinks back (`data-motion-key` on Files tiles) |
| Theme | `setTheme` (app.js) | View transition: the new theme spreads out as a circle from the control that was used |
| Buttons | `.btn:active` | Press to 0.97 in `--dur-1`, release on the snappy spring |
| Instellingen > Beweging | settings.js | The three modes, with a live preview tile row |

---

## 8. Do and don't per screen group

The three groups that build on this foundation:

- **Shell:** top nav, global search and its overlay, Home, Instellingen, Pasha chat, job
  dock, keyboard overview.
- **Media:** Bestanden, Tijdlijn, Albums, Kaart, Reeksen (Beste uit reeks), Duplicaten,
  viewer, Keurer, Shows (slideshow and the Elo duel "Welke is mooier?"), Slim zoeken results,
  export.
- **Data:** Spotify, Herstelcentrum, DataGatherer, Berichten, Activiteit, back-ups, charts,
  stat tiles and tables.

### Shell
- **Do** let the search overlay rise like a modal and stagger its first results (max 8).
  Results that stream in later use `Motion.reveal`, never a full-list re-stagger.
- **Do** count the Home stat tiles up with `Motion.countUp` the first time Home shows in a
  session.
- **Do** give Pasha's chat bubbles a `rise` from 6 px on `--spring-smooth`. The typing dots
  may breathe. The dog avatar may give one small `bouncy` nod when an answer arrives.
- **Do** glide the selected segment of a `.segmented` control with `Motion.glide` or FLIP.
- **Don't** animate the nav on scroll beyond a hairline shadow fading in
  (`Motion.scrollLinked`, 0 to 80 px).
- **Don't** delay focus. The keyboard overview opens with the modal motion. Its contents do
  not stagger.

#### What the shell does (`code/frontend/motion-shell.js`, `window.ShellMotion`)

The shell motion loads as the last script. Every caller guards with `if (window.ShellMotion)`,
so a page without it keeps working. Its CSS is the `BEWEGING: SCHIL` block in `style.css`.

| Piece | Hook | Motion |
| --- | --- | --- |
| Nav indicator | `ShellMotion.navGlide` (switchMainView) | The green line stretches elastically: the front edge leads and the back edge follows, on the smooth spring's time |
| Nav hover | `initNavHover` | A soft pill glides under the pointer from tab to tab (clip-path, snappy) |
| Page change | `ShellMotion.enterView` | `Motion.enterPage` plus the title icon settling in. A block more than 3 screens tall (a huge grid) is left out of the slide and only fades with the section |
| Home | `homeIntro`, `statValue`, `storageFill`, `otdShow` | Nav drops in, the greeting rises letter by letter, tiles count up once per session, the storage bar fills, 'Op deze dag' opens like a curtain, the job dock arrives last |
| Job dock | `jobDockBefore` / `jobDockAfter` (renderJobDock) | Bars grow from their old value, one sheen on a running bar, a burst and pop when a job finishes (also on the tiny icon), unroll and fold on collapse |
| Toasts | `initToastSwipe` | Swipe right to dismiss, rubber band to the left |
| Modals | `overlayOrigin` | `transform-origin` at the click or the focused trigger, so a window grows from its button |
| Menus | CSS `shell-menu-in` / `-out` | The theme menu and `.menu` spring open, items follow on the stagger step |
| Theme | `themeChanged` | Soft-edged circle (masked view transition) and the icon turns half a turn |
| Segments | `initSegmented` | A thumb glides between the old and new `.segmented` / `.view-toggle` choice |
| Shortcut overview | `initShortcutEcho` | The key you press lights up |
| Pasha | `initChat` | Bubbles rise from their own side, breathing dots, one nod on an answer |
| Empty and error states | `watchStates` | Icon settles with a halo, an error shakes once; the same state redrawn by polling does not animate again |
| Skeletons | CSS | A sheen moved by transform, staggered diagonally; Rustig breathes in opacity only |
| Herstelcentrum | `ShellMotion.keyedFlip` (recoveryRender) | First load staggers in, then keyed FLIP; a changed entry glows once |

### Media
- **Do** tag every photo tile that opens the viewer with `data-motion-key="<file.path>"`.
  The shared element then works from Tijdlijn, Albums, search, Reeksen and the map list too.
- **Do** stagger the first screenful of a grid (`Motion.stagger`, max 10 steps) and use
  `Motion.reveal` or `.motion-scroll-reveal` for the rest. For grids of hundreds, reveal by row,
  not by tile.
- **Do** FLIP the grid when sorting, filtering or removing a tile (`Motion.flip`), and
  `Motion.exit` the removed tile first. Moved or deleted photos visibly *go*.
- **Do** give tiles a `--motion-lift` hover with a shadow change in `--dur-2`, and a
  selection ring fade with a `bouncy` check badge.
- **Do** keep the Keurer and the Elo duel **instant**: the next photo is there in the next
  frame. At most a `--dur-2` fade or 6 px slide in the direction of the decision (left keeps,
  right is the bin, up moves), and a new key press finishes any running animation
  (`anim.finish()`). The winner of a duel may get a `bouncy` pop *after* the next pair is
  already shown.
- **Do** let map markers and clusters fade and scale on `--spring-snappy` when they appear.
  Never animate the map tiles themselves.
- **Don't** apply parallax, Ken Burns, tilt, blur or colour shifts to photos in a grid or in
  the viewer. The slideshow (Shows) is the one place for slow Ken Burns, which already exists.
- **Don't** animate viewer next and previous with a slide longer than `--dur-2`. Arrow keys
  must feel instant.

### Data
- **Do** count numbers up (`Motion.countUp`) when a stat first appears, and only then. A
  polling refresh updates the number without a ticker unless the value changed. If it did,
  tick from the old value.
- **Do** draw charts in: bars grow from the baseline on `--spring-smooth` (`scaleY` with
  `transform-origin: bottom`) with a 28 ms stagger, and lines draw with `stroke-dashoffset`
  in `--dur-6` and `--ease-emphasized`.
- **Do** reveal long lists (Herstelcentrum actions, Activiteit days, DataGatherer sources) with
  `Motion.reveal`. An undo removes its row with `Motion.exit` and FLIPs the rest.
- **Do** show job and connector progress with `--ease-standard` width or `scaleX` changes,
  and pop a green check on completion.
- **Don't** stagger table rows that update live (jobs, sync logs). Change them in place.
- **Don't** animate numbers that the user is reading to make a decision (sizes in a delete
  confirmation). Show them at once.

---

## 9. Performance rules

- Animate **only `transform` and `opacity`** (and `clip-path` for the shared element). Never
  animate `width`, `height`, `top`, `left`, `margin` or `box-shadow` on many elements. For a
  shadow, cross-fade a pseudo-element's opacity.
- **No transform on a `.section`** or any ancestor of `position: fixed` content. It would move
  fixed layers (modals, the map). `Motion.enterPage` animates only the section's opacity and
  its blocks' transforms.
- Measure before you mutate (FLIP: `flipCapture` before, `play` after). Never interleave
  reads and writes in a loop.
- Use WAAPI (`Motion.animate`) or CSS. Never animate with `setInterval` or style writes per
  frame. The one rAF loop is `countUp`, which writes only `textContent`.
- `will-change` only on the element in flight (the shared ghost has it), never on a grid.
- Stagger at most about 10 elements individually. Larger sets go by row or reveal.
- **Budget:** no animation frame over 32 ms in `motion_perf.py`. Run it after you add a
  transition:

  ```sh
  python3 code/tests/ui/motion_perf.py               # small vault
  python3 code/tests/ui/motion_perf.py --showcase    # the full showcase vault
  ```

  Add your screen's key transitions to `TRANSITIONS` in that file.

---

## 10. Accessibility rules

- Respect the three modes. Use the tokens and helpers and you get this for free. A custom rAF
  animation must check `Motion.full()` first.
- macOS "Reduce motion" turns Volledig into Rustig. Uit is always available.
- Nothing essential is shown *only* by motion. A state change is also visible in the end
  state (colour, icon or text).
- Motion never moves focus and never delays it. Focus rings are not animated.
- Nothing flashes more than 3 times per second. Nothing loops forever except progress
  indicators, the skeleton shimmer and the slideshow.
- Content is readable during an entrance: opacity reaches 1 early in the spring.
- Leaving elements stop taking pointer input immediately. View transitions let clicks pass
  through (`::view-transition { pointer-events: none }`).
- The smoke test runs in Uit with `reduced_motion="reduce"`, so every screen must be fully
  correct with no motion at all.

---

## 11. Checklist for a new or changed screen

- [ ] Durations, curves, distances and springs come from the tokens. No literal `ms` or
      `cubic-bezier` in a rule.
- [ ] Entering follows the hierarchy order. Only the visible part staggers, and the rest uses
      reveal.
- [ ] Exits are shorter than entrances and accelerate.
- [ ] One hero per transition. The photo is never animated for decoration.
- [ ] Keurer, the Elo duel and keyboard navigation remain instant.
- [ ] Photo tiles carry `data-motion-key` when they open the viewer.
- [ ] It looks right in Volledig, Rustig and Uit, in both themes.
- [ ] `motion_perf.py` stays green, with your transition added to `TRANSITIONS`.
