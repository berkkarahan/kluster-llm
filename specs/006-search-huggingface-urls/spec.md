# Feature Specification: Search Hugging Face URLs

**Feature Branch**: `main` (current branch; no branch-creation hook registered)

**Created**: 2026-09-29

**Status**: Draft

**Input**: User description: "search in MODELS tab must also accept explicit huggingface url and list downloadable options. rely on opendesign mcp for any design task."

**Brief clarification**: "do not change the current bar, search should still open a modal
with downloadable results"

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Browse downloadable options from a repository URL (Priority: P1)

As a cluster operator, I want to paste a Hugging Face model repository URL into the MODELS
search field and choose a downloadable model without rewriting the URL.

**Why this priority**: This is the primary requested workflow.

**Independent Test**: Search using a repository URL containing several supported GGUF
variants and verify that the available options belong to that repository.

**Acceptance Scenarios**:

1. **Given** a repository containing multiple supported GGUF variants, **When** its URL
   is submitted, **Then** all supported options in that repository are available to inspect
   and download.
2. **Given** a URL identifying a repository revision or folder, **When** it is submitted,
   **Then** the options belong to that revision and folder, including its subfolders.
3. **Given** search results, **When** the operator inspects an option, **Then** its repository,
   distinguishable filename or path, quantization when identifiable, size when available,
   and existing memory-fit indication are visible.
4. **Given** a repository containing no supported model files, **When** the search completes
   successfully, **Then** a clear empty result explains that no supported downloadable
   options were found.
5. **Given** listed options, **When** the operator chooses Download, **Then** only the selected
   option enters the existing download workflow; searching alone starts no download.
6. **Given** the current MODELS search bar, **When** the operator submits a keyword or URL,
   **Then** the existing results modal opens with downloadable options; the bar's appearance,
   placement, controls and surrounding navigation remain unchanged.

---

### User Story 2 — Find the exact model from a file URL (Priority: P1)

As a cluster operator, I want a copied Hugging Face file link to identify the exact
downloadable model.

**Why this priority**: File links are a common way to share a particular model variant.

**Independent Test**: Search with links to files in nested folders and non-default revisions,
then verify that the selected download matches the referenced file.

**Acceptance Scenarios**:

1. **Given** a supported file link using `blob`, `resolve`, `raw`, or `show_file_info`,
   **When** it is submitted, **Then** the matching supported file is listed.
2. **Given** files with identical basenames in different folders, **When** an exact file URL
   is searched, **Then** only the referenced path is offered.
3. **Given** a file link identifying a revision, **When** its result is downloaded, **Then**
   the download uses that same revision and path.
4. **Given** a missing or unsupported file, **When** its URL is submitted, **Then** the search
   explains the issue without inventing a downloadable option or substituting another file.

---

### User Story 3 — Recover from lookup problems and retain existing search (Priority: P2)

As a cluster operator, I want clear feedback when a URL cannot be used, while retaining
the search methods I already use.

**Why this priority**: Reliable feedback prevents failed lookups from being mistaken for
empty repositories.

**Independent Test**: Exercise malformed URLs, restricted repositories, interrupted lookups,
and existing keyword and repository searches.

**Acceptance Scenarios**:

1. **Given** an invalid or unsupported Hugging Face page URL, **When** it is submitted,
   **Then** the result explains which model-link forms are supported.
2. **Given** a lookup blocked by access restrictions or a connection failure, **When** it
   fails, **Then** an actionable error appears instead of an empty-result message.
3. **Given** configured credentials that permit repository access, **When** its URL is
   searched, **Then** available supported options are listed without exposing credentials.
4. **Given** a failed search, **When** the operator corrects the input or retries, **Then**
   the next result replaces the previous error.
5. **Given** an existing keyword, repository reference, quantization filter, or supported
   direct-download URL, **When** it is submitted, **Then** the existing search and download
   workflow remains usable.

### Edge Cases

- Surrounding whitespace, repository trailing slashes, encoded path characters, and
  irrelevant query parameters or fragments do not change the intended model target.
- A file path in the URL takes precedence over a conflicting `show_file_info` parameter.
- Missing repositories, revisions, folders, and files produce appropriate unavailable-target
  feedback.
- Access-related failures use the information available; they do not claim a private
  repository does not exist.
- Unknown file size or memory capacity is shown as unknown, not zero or a guaranteed fit.
- Documentation, unsupported model formats, auxiliary files, and individual split-model
  shards are not advertised as independently usable model options.
- Hugging Face profile, dataset, Space, and collection pages are not interpreted as model
  repositories.
- A file that becomes unavailable after search reports a download failure through the
  existing download workflow.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The existing MODELS search field MUST accept Hugging Face model URLs alongside
  existing search inputs.
- **FR-002**: Repository URLs MUST list supported downloadable model options from the
  referenced repository, honoring any specified revision and folder.
- **FR-003**: File URLs MUST identify the exact referenced supported file, preserving its
  repository, revision, and complete path through download selection.
- **FR-004**: Searches MUST support model links on `huggingface.co` and `hf.co`, including
  repository pages, tree pages, file pages, direct-file links, and existing file-information
  links.
- **FR-005**: Results MUST provide enough information to distinguish options, including
  repository and filename or path; quantization, size, and memory-fit information MUST
  appear when available.
- **FR-006**: Search MUST NOT initiate a download. Each downloadable option MUST offer an
  explicit action that starts its existing download workflow.
- **FR-007**: Only supported model files confirmed in the referenced location MUST be offered
  as downloadable options. Missing-file and failed-lookup fallbacks MUST NOT fabricate options.
- **FR-008**: The search experience MUST distinguish loading, successful results, successful
  empty results, invalid input, unavailable targets, access failures, and connection failures.
- **FR-009**: URL lookups MUST use existing configured Hugging Face access where applicable
  without revealing credentials in results or errors.
- **FR-010**: Existing keyword searches, repository references, quantization filtering, and
  supported direct-download inputs MUST remain usable.
- **FR-011**: Search feedback MUST describe the latest submitted input, and the operator MUST
  be able to correct or retry unsuccessful searches.
- **FR-012**: The current search bar and surrounding navigation MUST retain their existing
  appearance, placement and controls. Search MUST continue opening a modal containing
  downloadable results; URL support MUST NOT replace this flow with inline results,
  a new page, or a redesigned bar.

### Key Entities

- **Search target**: A keyword, repository reference, or URL identifying a repository and
  optionally a revision, folder, or file.
- **Downloadable option**: A supported model file with its source identity, distinguishable
  path, available descriptive metadata, and download action.
- **Search outcome**: The current search's results, empty state, or actionable failure.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For each supported repository-link form in acceptance testing, one search
  submission makes 100% of the expected supported options available without rewriting the URL.
- **SC-002**: For every exact-file acceptance case, selecting Download targets the referenced
  repository, revision, and path with no substitutions.
- **SC-003**: Every invalid, missing, inaccessible, and unsupported-target acceptance case
  produces explanatory feedback and zero fabricated download options.
- **SC-004**: An operator can go from a copied valid URL to a selected download using one
  paste, one search submission, and one download action, without leaving MODELS.
- **SC-005**: All existing search-input acceptance cases continue to produce their expected
  options.
- **SC-006**: Both keyword and URL searches open the results modal in acceptance testing,
  and before/after comparison confirms the existing bar and navigation are preserved.

## Assumptions

- "Downloadable options" means the standalone GGUF model files currently supported by the
  application. Model conversion and multi-part download orchestration are outside this feature.
- Links without an explicit revision refer to the repository's default revision.
- Public searches need no new sign-in flow. Restricted repositories use existing credential
  configuration and access permissions.
- Repository lookup depends on Hugging Face availability; a successfully inspected empty
  repository is different from a failed lookup.
- Any visual or interaction design task MUST use the requested MCP workflow through
  OpenDesign Cloud or Local Codex before design changes are implemented.
