# Feature Specification: Log Source Tags

**Feature Branch**: `main` (current branch; no branch-creation hook registered)

**Created**: 2026-09-29

**Status**: Draft

**Input**: User description: "separate kllm ui logs from inference logs via tags (should be filterable in the logs ui)"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Tell UI logs from inference logs (Priority: P1)

As a cluster operator, I want every log entry tagged as kllm UI or inference so I can
see which activity produced it in the combined trail.

**Why this priority**: The trail already mixes control activity and model-serving activity.
A visible tag is what makes those two kinds separable.

**Independent Test**: Open Logs with a known set of control-application entries and
model-serving entries, including control messages that mention starting or supervising
inference, and read the tag on each row.

**Acceptance Scenarios**:

1. **Given** entries from the kllm control application and from model serving, **When**
   the operator views the trail, **Then** each entry shows exactly one tag: UI,
   Inference, or Unclassified.
2. **Given** a control-application entry, including one about starting, stopping, or
   supervising model serving, **When** it is displayed, **Then** its tag is UI.
3. **Given** model-serving output, or activity that exists only to spread that serving
   across machines, **When** it is displayed, **Then** its tag is Inference.
4. **Given** an entry whose producing activity cannot be determined, **When** it is
   displayed, **Then** its tag is Unclassified.
5. **Given** any displayed entry, **When** the operator inspects it, **Then** its time,
   node, existing source label, message, and tag are all visible.
6. **Given** retained history already in the trail, **When** the page shows it, **Then**
   those entries carry the same tags as newly recorded entries from the same activity.

---

### User Story 2 - Filter the Logs page by tag (Priority: P1)

As a cluster operator, I want to filter the Logs page by tag so I can read UI activity
or inference activity without the other kind filling the trail.

**Why this priority**: Separation is only useful if the operator can hide the unwanted
kind from the current view.

**Independent Test**: Load a trail containing UI, Inference, and Unclassified entries,
then select each filter choice and confirm which rows remain visible.

**Acceptance Scenarios**:

1. **Given** a newly opened Logs page, **When** the trail loads, **Then** All tags is
   selected and UI, Inference, and Unclassified entries are all eligible for display.
2. **Given** a mixed trail, **When** the operator selects UI, **Then** only UI entries
   are visible. Selecting Inference or Unclassified likewise shows only that tag.
3. **Given** a loaded trail, **When** the operator changes the tag filter, **Then** rows
   already in the current visit appear or hide immediately, and hidden rows remain part
   of that visit until Clear.
4. **Given** the UI filter and a trail that contains only inference entries, **When**
   the filter is applied, **Then** the page states that the current tag filter has no
   matching entries. That state is distinct from loading, a cleared display, a failed
   connection, and a visit that has received no entries.
5. **Given** a tag filter other than All, **When** a new entry of a different tag arrives,
   **Then** it stays hidden until the filter includes its tag. A new entry of the selected
   tag appears under the current refresh mode.
6. **Given** All tags is selected again, **When** the trail is shown, **Then** entries
   hidden only by the previous tag filter become visible if they are still part of this visit.

---

### User Story 3 - Keep tag filtering within the existing trail (Priority: P2)

As a cluster operator, I want tag filtering to work with the node filter, refresh
choices, and Clear so troubleshooting controls continue to behave as they do today.

**Why this priority**: The Logs page is already used to isolate a machine and to clear
the display. The new filter has to combine with those controls rather than reset them.

**Independent Test**: Set a node, a refresh mode, and a tag filter; generate matching
and non-matching entries; Clear; then change the tag and node selections.

**Acceptance Scenarios**:

1. **Given** a selected node and a selected tag, **When** the trail is shown, **Then**
   a row is visible only when it matches both selections. All nodes and All tags impose
   no extra restriction on their own dimension.
2. **Given** a tag filter, node selection, and refresh mode, **When** any one of them
   changes, **Then** the other two stay as the operator set them.
3. **Given** rows hidden by the tag filter, **When** Clear is pressed, **Then** those
   hidden rows leave the current visit along with the visible rows. Node, tag, and
   refresh selections stay unchanged.
4. **Given** a cleared visit, **When** the operator switches tag or node, **Then**
   cleared entries stay absent for the rest of that visit. Stored logs and another
   open Logs page are unchanged.
5. **Given** a keyboard-only operator at desktop or narrow-screen width, **When** they
   use the tag filter, node filter, refresh choices, Clear, and a long message, **Then**
   every control and the tag on each row are reachable with visible focus and without
   page-wide horizontal overflow.
6. **Given** a history gap or an omitted older portion of the trail, **When** a tag
   filter is active, **Then** the page still identifies that limitation and does not
   describe it as an effect of the tag filter.

### Edge Cases

- A trail with only one tag still offers every filter choice. The choices with no rows
  show the filter-empty state; All shows the available rows.
- Unclassified entries never appear in the UI-only or Inference-only views.
- Two entries with the same message and different tags remain distinct rows.
- A control-application message whose text is about inference keeps the UI tag.
- An entry hidden by the tag filter still counts toward the existing recent-trail limit.
  Hiding it does not bring back older rows that the trail has already omitted.
- Clear before the first successful load remains a no-op, including when a tag filter
  was changed first. The first successful load is shown according to the selected filter
  and is not discarded.
- Changing the tag filter while the operator is reading older rows keeps the reading
  position when the anchor row is still visible. Returning to the newest edge resumes
  following new matching entries.
- Departed or unknown nodes keep their identity. The tag filter does not change node
  labels, local-only coverage labeling, or disconnected-trail behavior.
- A new visit starts on All tags. The previous visit's tag selection is not restored.
- Operators cannot add, rename, or remove the UI, Inference, and Unclassified tags.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Each log entry on the Logs page MUST display exactly one tag: UI,
  Inference, or Unclassified.
- **FR-002**: Entries produced by the kllm control application MUST be tagged UI,
  including entries about starting, stopping, or supervising model serving.
- **FR-003**: Entries produced by model serving, including activity used only to spread
  that serving across machines, MUST be tagged Inference.
- **FR-004**: Entries whose producing activity cannot be determined MUST be tagged
  Unclassified and MUST remain distinguishable from UI and Inference entries.
- **FR-005**: The Logs page MUST provide a single-choice tag filter with exactly All,
  UI, Inference, and Unclassified. A new visit MUST start on All.
- **FR-006**: The visible trail MUST contain only entries matching both the selected
  tag and the selected node. All tags and All nodes MUST leave that dimension unrestricted.
- **FR-007**: Changing the tag filter MUST show or hide entries already held for the
  current visit immediately, without waiting for the next refresh and without dropping
  hidden entries from that visit.
- **FR-008**: Changing the tag filter MUST leave node selection, refresh mode, stored
  logs, and other viewers unchanged.
- **FR-009**: Clear MUST remove the visit's entire displayed set, including entries
  hidden by the tag filter or the node filter, and MUST leave the current tag, node,
  and refresh selections in place. Cleared entries MUST stay absent for the rest of
  that visit.
- **FR-010**: A tag filter with no matching entries MUST be textually distinct from
  loading, an empty visit, a cleared visit, local-only coverage, and a failed connection.
- **FR-011**: The tag filter and each entry's tag MUST be keyboard operable, visibly
  focused, and labeled. The page MUST remain usable at desktop and narrow-screen widths
  without page-wide horizontal overflow.
- **FR-012**: Existing entry details — time, node, source, and message — plus node
  selection and the Live, 5s, 30s, and 1m refresh choices MUST remain available.
- **FR-013**: Known history gaps and omission of older trail entries MUST stay visible
  while a tag filter is active and MUST be identified separately from rows hidden by
  the filter.
- **FR-014**: The tag set MUST stay fixed at UI, Inference, and Unclassified. The Logs
  page MUST NOT offer custom tag creation, renaming, or deletion.

### Key Entities *(include if feature involves data)*

- **Log entry**: An existing trail row with time, node, source, and message, plus exactly
  one tag naming the activity that produced it.
- **Log tag**: One of UI (kllm control application), Inference (model serving and
  activity used only to distribute that serving), or Unclassified (producer unknown).
- **Tag filter**: The current visit's choice of All, UI, Inference, or Unclassified.
  It restricts the display together with the existing node selection.
- **Log view session**: The existing visit state — node, refresh mode, held entries,
  reading position, and cleared state — plus the tag filter. A new visit starts at All.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: At least four of five operators can open Logs and switch from the mixed
  trail to inference-only, then to UI-only, within 20 seconds without assistance.
- **SC-002**: In a visit holding 50 UI entries, 50 Inference entries, and 10 Unclassified
  entries, UI shows all 50 UI entries and no others; Inference shows all 50 Inference
  entries and no others; Unclassified shows only those 10; All shows all 110. Each
  change is reflected within one second.
- **SC-003**: With only UI entries held, selecting Inference shows the filter-empty
  explanation and zero rows within one second. Selecting All shows the UI entries again
  within one second.
- **SC-004**: With one node and one tag selected, every visible row matches both, and
  rows matching only one stay hidden until that selection is widened. Node and refresh
  mode remain unchanged in 100% of tag-filter changes during the check.
- **SC-005**: Clear removes tag-hidden rows from the visit within one second. Those rows
  stay absent through later tag and node changes in that visit, while stored logs and a
  second Logs page still contain them.
- **SC-006**: The tag filter, tag labels, node selection, all four refresh modes, Clear,
  and long-message inspection are usable with keyboard alone at 1280-by-720 and
  390-by-844 without page-wide horizontal overflow.

## Assumptions

- The Logs page from the existing cluster log trail is the surface for this feature.
  Time, node, source, message, node filter, Live/5s/30s/1m, display-only Clear, coverage
  labels, and the recent-trail limit stay in place.
- A tag names the activity that produced the entry. Control-application text about
  inference remains UI. Model-serving output remains Inference.
- Activity used only to spread model serving across machines is Inference. It is not a
  fourth tag and it is not UI activity.
- Entries already retained for the trail are classified by those same producer rules
  when shown. This feature does not add a separate archive or rewrite stored history
  outside the trail the operator already sees.
- The tag filter applies to the current Logs visit, starts at All, and is not remembered
  on the next visit. Hidden entries stay in the visit until Clear.
- Clear continues to mean clear this visit's display, including rows hidden by node or
  tag. It does not delete stored logs or affect another viewer.
- This feature does not collect a new stream of browser-console messages, chat replies,
  or model output presented as conversation. It classifies the cluster trail already
  shown on Logs.
- Command-line log reading keeps its current source choices and behavior. This feature
  does not add a command-line tag filter.
- Operators do not create tags. Search, export, retention changes, and a new log page
  are outside this feature.
- Existing private-cluster access rules apply. Tag labels contain no secrets.
