# Viewing the wireframes

`index.html` puts every board in this folder on one zoomable canvas: each route's desktop board
beside its iPhone board, under the route's name. Boards are live iframes of the files themselves, so
the canvas always shows the current wireframes — there is nothing to rebuild after editing one.

## Open it

```
google-chrome design/wireframes/index.html
```

Double-clicking the file works too. There is no build step, nothing to install, and the viewer makes
no network request of its own. (The boards themselves ask for the IBM Plex web font and fall back to
system fonts offline, exactly as they do when opened one at a time.)

## From the Altitude UI

The project page carries a **Design** link whenever that project's checkout has
`design/wireframes/index.html`. It opens the boards in a new tab, on the same host and port as the
rest of the UI — desktop and phone alike, with nothing to start:

```
/design/<project>                                  redirects to
/design/<project>/<pass>/design/wireframes/index.html
```

The pass is that browser's read pass for the project's boards, valid the day it is made and the next
while the browser stays paired; a bookmark of `/design/<project>` keeps working.

`altd` serves them read-only from the project's deployment checkout, reading each file on the
request and sending it uncached, so the boards a merge lands are the boards the next reload draws.
The tree is mirrored under the prefix rather than flattened, so `wireframes.css`'s import of
`../../web/design/tokens.css` resolves exactly as it does under `serve.sh`. Only `design/wireframes/`
and `web/design/` are readable, only `.html .css .js .svg .png .jpg .woff2`, and a directory is a
404 rather than a listing.

That checkout is `main`, so boards still on a branch are the case `serve.sh` below covers.

## Move around

| | |
| --- | --- |
| Pan | drag, or scroll (two-finger scroll on a trackpad) |
| Zoom | ctrl/⌘ + scroll, trackpad pinch, or the `+` / `−` buttons |
| Fit everything | **Fit all**, or `0` |
| Fit one route | **Fit route**, or `R` |
| Actual size | **100%**, or `1` |
| One board | click it, or pick it in the list on the left |
| Step between boards | `←` `→` |
| Step between routes | `↑` `↓` |
| Hide the list | `L`, or the ☰ button |

On a phone: one finger pans, two fingers pinch to zoom, a tap fits the board you tapped.

The percentage beside the zoom buttons is the scale the boards are drawn at. Boards are scaled with
a CSS transform, not rasterised, so they stay crisp all the way in.

## On the phone

```
design/wireframes/serve.sh
```

It serves the repository over HTTP on port 8899 (`PORT=` overrides) and prints two URLs: one for
this machine and one for a phone on the same network. Open the second one in Safari. Stop it with
Ctrl-C. It serves the repository root rather than this folder because `wireframes.css` imports the
build's `web/design/tokens.css` from two levels up.

## Add a board

`boards.js` is written by `gen.py` from its `ROUTES` table, one row per route:

```python
("Settings", "Settings", "MobileSettings"),
```

Add the row and the matching `board(...)` calls, run `python3 design/wireframes/gen.py`, and the
canvas, the side list, and the keyboard order follow. A route with only a desktop or only a phone
board leaves the other slot empty, and a sheet that is not 1440×900 gets its own size from the board
itself.

A listed file that is not there renders as a red "File not found" tile naming the file, so a stale
entry shows itself the moment the viewer opens rather than leaving a blank space.
Altitude serves the viewer in a sandbox with an opaque origin, where it cannot probe; there a missing
board's frame shows the server's plain "not found" instead.
