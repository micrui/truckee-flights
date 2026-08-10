# CLAUDE.md: truckee-flights

Public record of flight activity at Truckee Tahoe Airport (KTRK), built from ADS-B Exchange
public replay data. Site: https://micrui.github.io/truckee-flights (GitHub Pages from `/docs`).

## Voice and editorial rules (non-negotiable)

- Community member writing for neighbors. Not an entity doing outreach; no side-taking in the
  local airport-noise dispute.
- No aviation jargon in prose: "ops", "ramp", "MSL", "medevac" are out; plain English in.
- **Falsifiability is the admission criterion.** A claim appears on the site only if a reader
  could check it against a cited source. Unfalsifiable claims (economic benefit, testimonials,
  who deserves what) appear only as attributed statements, if at all.
- **Never** write self-referential neutrality statements ("this page reports and does not
  conclude"). The page is right there, cited. Show, don't declare.
- No apologetic framing. State locations, times, and amounts; let facts sit unadorned.
  If context risks reading as advocacy either way, quote a source or cut it.
- No editorializing labels. LLC/trust registration is normal practice, never a wealth tell.
  FAA display-blocking (LADD/PIA) is reported factually: counts and operators, no adjectives.
- No em-dashes anywhere, in site prose or repo docs. Use commas, colons, semicolons,
  parentheses, or a new sentence.
- Comparisons to other activities (skiing, climbing) only with numbers and sources; otherwise drop.
- **Hard line: never cross-reference named individuals against FAA registries or any other
  source. Aggregate facts only.** Operators are named only as they appear in public registry
  records already attached to an aircraft.

## Structure

- `pipeline.py` fetches ADS-B Exchange heatmaps/traces for a date; windows: "morning"
  (05:00–08:30) and "full" (22:00 prev day–08:30). Stdlib only.
- `summarize.py` builds event lists, overflight sessions (gap-split at 20 min), operator roles.
- `weekly.py`: `collect()` writes `data/weekly/*.json`; `render()` writes `docs/quiet-hours.html`,
  per-week pages in `docs/weeks/`, and `docs/operators.html`. Aircraft class is re-derived at
  render time via `klass()`; never trust cached class fields.
- `.github/workflows/weekly.yml` runs Mondays 18:00 UTC, full window, commits results.
- Chart semantics (`templates/week.html`): five lanes: fire (light red), medical (blue),
  jet (ink/black), turboprop (orange), light aircraft (green). Dwell spans are
  circle–line–circle; quick-turn connectors join arrival→departure under 45 minutes.
- `data/airports.csv`: OurAirports public-domain dump for origin/destination naming.

## Working discipline

- Build incrementally but push coherently: hold pushes, normalize, verify locally, single push.
- Every number on a page traces to a linked source. When a published claim is found wrong,
  correct it with a dated addendum, never silently.
- Stdlib-only Python. No external dependencies, no build step; pages are hand-written HTML
  using the shared CSS token set (light/dark via `prefers-color-scheme` + `data-theme`).

## Siblings

truckee-i80 (the freeway) and truckee-trains (the railroad) follow the same pattern and rules.
truckee-station is the home sensor project; this repo stays flight-data only.
