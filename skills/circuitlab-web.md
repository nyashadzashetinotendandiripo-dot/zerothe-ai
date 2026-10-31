---
name: circuitlab-web
description: Work with CircuitLab circuits in the shared browser (no API exists)
status: active
tags: electronics, cad, browser
---
# CircuitLab in the browser

## When to use
The user asks about a CircuitLab circuit: open one, read a schematic, run a simulation, change values. There is no CircuitLab API or plugin — everything happens in the shared browser tab, which stays logged in via the app's saved session.

## Inputs
- What to do: read, simulate (DC / AC / transient), or edit values
- Which circuit: a circuitlab.com URL, or "the one already open"

## Steps
1. If no circuit is open, `browser_goto` the circuitlab.com URL (ask the user for it if missing).
2. If a login page appears, call `request_takeover` so the user signs in; never type their password.
3. `browser_snapshot` to map the page. The editor is a canvas: read component values from the side panel and results from the output/plot area with `browser_read_text`.
4. To simulate: click the Simulate tab, pick the analysis (DC, AC, transient), set stop time if asked, and Run. Read the results panel/plots as text.
5. To edit a value: click the component, change the value in the properties panel, re-run the simulation.
6. Report: what the circuit does, values used, simulation outcome in one screen. Save reusable findings with `memory_save`.

## Checks
- Every component value quoted came from the page, not from memory.
- A simulation result was actually run in this session before quoting numbers.

## Needs approval
- Saving over the user's own circuit, deleting anything, or sharing/publishing a circuit.
