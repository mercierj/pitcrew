# Preserve dashboard overview scroll during automatic refresh

## Goal

Automatic dashboard polling must update the overview without moving an operator
away from their current reading position.

## Behaviour

- Before an automatic refresh starts, capture the document viewport coordinates
  (`window.scrollX` and `window.scrollY`).
- After the local overview has re-rendered, restore those coordinates with an
  immediate `window.scrollTo`; do not focus an element or use smooth scrolling.
- Manual refreshes retain their current semantics and do not use the automatic
  scroll-preservation path.
- If the page height changes such that the prior position is no longer valid,
  the browser naturally clamps the restored value to its valid scroll range.

## Implementation boundary

The refresh coordinator in `dashboard/app.js` owns the capture and restoration.
Rendering modules remain unaware of polling and do not gain scroll-specific
state. The refresh API receives the existing `manual` flag, so no new public
interface is required.

## Verification

Add a focused dashboard test that confirms an automatic refresh captures and
restores the viewport coordinates, while manual refresh does not opt into that
path. Run the focused JavaScript tests and the dashboard test suite covering the
source-level polling contract.
