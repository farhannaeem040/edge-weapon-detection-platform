# Software Requirements Specification (SRS)

| Field | Value |
|-------|-------|
| Document ID | SRS-001 |
| Version | 1.0 |
| Status | Draft |
| Project | Edge-Based Weapon Detection and Centralized Monitoring System Using NVIDIA Jetson and DeepStream |
| Owner | Farhan Naeem |
| Reviewers | Farhan Naeem, ChatGPT, Claude Code |
| Related Documents | Project Charter (PC-001), Vision (VIS-001) |

---

## 1. Introduction

### 1.1 Purpose

This document specifies the functional and non-functional requirements for the dissertation prototype of the Edge-Based Weapon Detection and Centralized Monitoring System. It follows IEEE-style SRS conventions (uniquely identified, verifiable requirements; explicit constraints and assumptions; traceability to upstream sources) adapted to the scope of a single-developer, five-week dissertation prototype.

This SRS translates the Vision (VIS-001) into specific, testable requirements. It does not define system architecture or technical implementation approach; where a decision has been intentionally deferred to the Architecture phase, it is marked as such rather than expressed as a requirement.

### 1.2 Scope

This SRS covers the dissertation prototype only, consisting of: one NVIDIA Jetson Orin Nano running a Flask-based Jetson Agent, DeepStream, and a fine-tuned YOLO26/TensorRT detection pipeline; one RTSP camera; an ASP.NET Core backend with SQL Server; and an Angular operator dashboard, deployed on a local network with a single Admin login.

### 1.3 Definitions, Acronyms, and Abbreviations

| Term | Definition |
|------|------------|
| Agent | The Jetson Agent software running on the edge device |
| Alert | A record created when a weapon is detected |
| Branch | A physical location containing one Jetson device and one or more cameras |
| Activation Key | A unique, server-issued credential used by a Jetson Agent to register with the server |
| Heartbeat | A periodic health status message sent from the Agent to the server |
| FR | Functional Requirement |
| NFR | Non-Functional Requirement |
| BR | Business Rule |

### 1.4 References

- Project Charter (PC-001)
- Vision Document (VIS-001)
- Development Workflow (docs/foundation/development-workflow.md)
- Engineering Principles (docs/foundation/engineering-principles.md)

---

## 2. Overall Description

### 2.1 Product Perspective

The system consists of three cooperating components: the Jetson Agent (edge detection and local resilience), the central server (ASP.NET Core REST API + SQL Server), and the Angular dashboard (operator/administrator interface). The Agent operates autonomously and reconnects to the server opportunistically; the server is the system of record for branches, alerts, and configuration.

### 2.2 User Roles (Conceptual)

- **System Administrator** — branch/device management, configuration, health monitoring, reporting.
- **Security Operator** — alert review and response, live monitoring.
- Both roles are represented by a single Admin account in the prototype (see BR-001).

### 2.3 Operating Environment

Local network deployment; one Jetson Orin Nano; one RTSP camera; Windows/Linux server host for ASP.NET Core and SQL Server (host OS not prescribed by this document).

### 2.4 Design and Implementation Constraints

See Section 6 (Constraints).

### 2.5 Assumptions and Dependencies

See Section 7 (Assumptions).

---

## 3. Functional Requirements

Each requirement is uniquely identified as `FR-<CATEGORY>-<NNN>`.

### 3.1 Authentication (AUT)

| ID | Requirement |
|----|-------------|
| FR-AUT-001 | The system shall provide a single Admin account for authentication into the dashboard. |
| FR-AUT-002 | The system shall require authentication for all dashboard and API operations except Jetson Agent activation and login itself. |
| FR-AUT-003 | The system shall allow the Admin to log out, invalidating the current session/token. |

**Acceptance Criteria**: A user cannot access any dashboard view or protected API endpoint without a valid session; valid credentials produce a usable session; logout prevents further use of the invalidated session/token.

### 3.2 Branch and Device Onboarding (BRN)

| ID | Requirement |
|----|-------------|
| FR-BRN-001 | The system shall allow the Admin to create a branch, capturing branch name, address, contact details, and one or more camera RTSP URLs. |
| FR-BRN-002 | The system shall generate a unique Activation Key upon branch creation. |
| FR-BRN-003 | The system shall allow a Jetson Agent to authenticate using an Activation Key and, upon success, receive its full branch/camera/operational configuration. |
| FR-BRN-004 | The system shall mark an Activation Key as consumed after its first successful use and shall reject subsequent activation attempts using the same key. |
| FR-BRN-005 | The system shall allow the Admin to regenerate/reset a branch's Activation Key, invalidating the previous key. |
| FR-BRN-006 | The system shall not allow a Jetson Agent to create or self-register a new branch. |
| FR-BRN-007 | Once activated, a Jetson Agent shall retain a persistent device identity associated with its branch, such that the server can distinguish and correlate all subsequent communications (heartbeats, alerts, configuration checks) to the correct device across restarts and reconnections. |

**Acceptance Criteria**: A branch created via the dashboard produces a usable Activation Key; a Jetson Agent using a valid, unconsumed key receives configuration and activates successfully; reuse of a consumed key is rejected; regenerating a key invalidates the old one and a subsequent activation attempt with the old key fails.

### 3.3 Weapon Detection and Alerting (DET)

| ID | Requirement |
|----|-------------|
| FR-DET-001 | The Jetson Agent shall perform local weapon detection (gun, knife) using an on-device AI inference pipeline operating on the configured camera stream. The specific model architecture and inference runtime are an implementation choice, not a requirement of this document. |
| FR-DET-002 | The system shall only generate an alert for detections at or above a configurable confidence threshold. |
| FR-DET-003 | The confidence threshold shall be configurable and shall have a sensible default value applied when not otherwise set. |
| FR-DET-004 | Upon a qualifying detection, the Jetson Agent shall create an alert event containing: timestamp, weapon type, confidence score, camera identifier, and a captured snapshot image. |
| FR-DET-005 | The Jetson Agent shall transmit alert events to the central server. |
| FR-DET-006 | The system shall assign each alert a status, initially "New." |
| FR-DET-007 | The dashboard shall display alert details including branch name, camera name, timestamp, weapon type, confidence score, snapshot, live stream access, and current status. |
| FR-DET-008 | The Operator shall be able to set an alert's status to "Acknowledged." |
| FR-DET-009 | The Operator shall be able to set an alert's status to "False Positive." |
| FR-DET-010 | The Operator shall be able to trigger a remote siren command on the alert's associated Jetson device. |
| FR-DET-011 | The Operator shall be able to download an alert's snapshot image. |

**Acceptance Criteria**: A detection at or above the configured threshold produces exactly one alert visible on the dashboard with all required fields populated; a detection below threshold produces no alert; an operator can transition an alert from New to Acknowledged or False Positive and the change persists; a siren command issued by the operator is delivered to the correct device; snapshot download returns the correct image for the alert.

### 3.4 Routine Live Monitoring (MON)

| ID | Requirement |
|----|-------------|
| FR-MON-001 | The system shall allow the Operator to select any registered branch and view its associated camera's live stream, independent of any active alert. |

**Acceptance Criteria**: An operator can open a live view for any registered branch at any time, whether or not an alert is currently active for that branch. (Note: the specific live-stream delivery mechanism is an Architecture decision — see Section 8.)

### 3.5 Device Health Monitoring (HLT)

| ID | Requirement |
|----|-------------|
| FR-HLT-001 | The Jetson Agent shall send a periodic heartbeat to the server containing online status, camera connection status, and DeepStream pipeline status. |
| FR-HLT-002 | The heartbeat interval shall be configurable, with a prototype default of 30 seconds. |
| FR-HLT-003 | The system shall mark a device "Offline" after 3 consecutive missed heartbeats. |
| FR-HLT-004 | A missed-heartbeat/offline transition shall generate a health/system notification distinct from a weapon-detection alert. |
| FR-HLT-005 | The dashboard shall display, per branch/device: online/offline status, last heartbeat timestamp, camera connection status, and DeepStream pipeline status. |

**Acceptance Criteria**: A device that stops sending heartbeats is marked Offline only after 3 consecutive intervals have elapsed without one; a health notification (not a weapon alert) is generated at that point; the dashboard reflects current and last-known health status per device.

### 3.6 Offline Resilience and Synchronization (SYN)

| ID | Requirement |
|----|-------------|
| FR-SYN-001 | The Jetson Agent shall continue local detection, logging, recording, and siren activation during a loss of connectivity to the server. |
| FR-SYN-002 | The Jetson Agent shall store detection events generated while disconnected, including timestamp, weapon type, confidence score, camera information, snapshot image, recording reference, and event identifier. |
| FR-SYN-003 | Upon restored connectivity, the Jetson Agent shall synchronize all locally stored, unsent events to the server. |
| FR-SYN-004 | The server shall preserve the original detection timestamp of synchronized events, regardless of upload delay. |
| FR-SYN-005 | The Jetson Agent shall periodically check for and automatically apply updated configuration (e.g., confidence threshold, heartbeat interval, recording retention period, camera settings) published by the server, without manual intervention on the device. |
| FR-SYN-006 | The Jetson Agent shall persist the most recently synchronized configuration locally, such that it survives a device restart and is applied on startup without requiring reactivation or server contact. |

**Acceptance Criteria**: Detections made during a simulated network outage appear in the dashboard's historical records after reconnection, with their original (not upload) timestamp; a configuration value changed by the Admin on the server is applied by the Agent within one configuration-check cycle without a manual restart or reconfiguration step by the installer.

### 3.7 Recording (REC)

| ID | Requirement |
|----|-------------|
| FR-REC-001 | The Jetson Agent shall continuously record the camera stream locally. |
| FR-REC-002 | The local recording retention period shall be configurable, with a prototype default of 7 days, and the Jetson Agent shall delete recordings older than the configured period. |
| FR-REC-003 | Full video recordings shall not be uploaded to the central server; only detection snapshot images are transmitted to the server. |

**Acceptance Criteria**: Continuous recording occurs during normal operation; recordings older than the configured retention period are removed automatically; no full-video transfer to the server occurs at any point in normal or offline-sync operation.

### 3.8 Reporting (RPT)

| ID | Requirement |
|----|-------------|
| FR-RPT-001 | The system shall provide a historical list of alerts, filterable by branch, date/time range, weapon type, and status. |
| FR-RPT-002 | Each historical alert record shall display timestamp, branch, camera, weapon type, confidence score, snapshot image, and final operator decision. |
| FR-RPT-003 | The system shall provide historical device health information, including online/offline history and last heartbeat. |
| FR-RPT-004 | The system may display basic summary counts (e.g., total detections, total false positives) where obtainable without significant additional complexity. |

**Acceptance Criteria**: Filtering the alert history by any single or combined supported filter returns the correct matching subset; each returned record contains all specified fields; device health history reflects prior offline/online transitions.

---

## 4. Non-Functional Requirements

| ID | Category | Requirement |
|----|----------|-------------|
| NFR-PRF-001 | Performance | End-to-end latency from detection to the alert appearing on the dashboard shall be under 5 seconds under normal local-network conditions with one active camera and one concurrent alert. |
| NFR-PRF-002 | Performance | The Jetson Agent shall perform inference at a frame rate sufficient to support real-time detection on the target hardware (specific FPS target to be validated during model/pipeline evaluation, not fixed here). |
| NFR-REL-001 | Reliability | Local detection, logging, recording, and siren activation shall continue uninterrupted during loss of server connectivity. |
| NFR-REL-002 | Reliability | No detection event generated during an outage shall be lost; all shall be recoverable via synchronization once connectivity is restored. |
| NFR-SEC-001 | Security | All dashboard and API access (excluding login and Jetson activation) shall require an authenticated session. |
| NFR-SEC-002 | Security | Jetson Agent-to-server communication shall use an Activation Key for device authentication. |
| NFR-MNT-001 | Maintainability | The system shall maintain a clear separation of concerns between the AI/detection component, edge agent, backend, and frontend, consistent with the Engineering Principles. |
| NFR-USB-001 | Usability | All information required for an operator to decide between Acknowledge and False Positive shall be presented within the alert detail view, without requiring navigation to another screen. |
| NFR-TST-001 | Testability | Each functional requirement in this document shall be independently verifiable via a defined acceptance criterion (see Section 3 and Section 9). |
| NFR-CFG-001 | Configurability | Heartbeat interval, confidence threshold, and recording retention period shall be stored as configuration values (not hardcoded) and shall be changeable without a code change or redeployment. |

---

## 5. Business Rules

| ID | Rule |
|----|------|
| BR-001 | The System Administrator and Security Operator are conceptually distinct roles, but the prototype implements both through a single Admin account; no permission separation exists in this phase. |
| BR-002 | A branch must exist before any Jetson device can activate against it; devices cannot self-register. |
| BR-003 | An Activation Key may only be consumed once; a new key must be issued (via regeneration) to reactivate or replace a device. |
| BR-004 | An alert's lifecycle terminates at either "Acknowledged" or "False Positive"; no further status transitions or case-management states exist in this phase. |
| BR-005 | A missed-heartbeat health event is categorically distinct from a weapon-detection alert and must never be presented to the operator as if it were one. |
| BR-006 | Full video recordings remain local to the Jetson device at all times; only snapshot images and metadata are ever transmitted to the server. |
| BR-007 | The final decision on whether a detection constitutes a genuine security threat rests solely with the human operator; the AI system's role is limited to detection and alert generation. |

---

## 6. Constraints

| ID | Constraint |
|----|------------|
| CON-001 | Five-week implementation timeline (dissertation). |
| CON-002 | Single developer. |
| CON-003 | One Jetson Orin Nano device available for testing. |
| CON-004 | One primary RTSP camera for proof-of-concept. |
| CON-005 | Local network deployment only; no cloud-hosted inference or internet-facing deployment. |
| CON-006 | Fixed technology stack: YOLO26, NVIDIA DeepStream, TensorRT, Jetson Orin Nano, ASP.NET Core, Angular, SQL Server, Flask-based Jetson Agent. |
| CON-007 | Multi-tenant deployment, multiple Jetson devices per branch, and multiple user roles are explicitly out of scope for this phase. |

---

## 7. Assumptions

| ID | Assumption |
|----|------------|
| ASM-001 | Jetson Orin Nano hardware and the RTSP camera stream are available and functioning throughout development. |
| ASM-002 | Datasets sufficient for fine-tuning the YOLO26 model are available via Roboflow. |
| ASM-003 | TensorRT conversion is supported for the trained model without unresolved compatibility issues. |
| ASM-004 | The local network connecting the Jetson device and server is reliable within the demo environment, notwithstanding intentional/simulated outages for testing. |
| ASM-005 | A single Admin credential is an acceptable stand-in for two real-world roles for the purpose of this dissertation demonstration. |
| ASM-006 | Physical installation (installer manually configuring the Activation Key on the device) is performed out-of-band and is not automated by the system. |
| ASM-007 | Sufficient local storage exists on the Jetson device to support continuous recording under the defined retention period. |

---

## 8. External Interfaces

### 8.1 User Interfaces

- Angular web dashboard: login, alert list/detail, live stream view, branch/device management, health monitoring, reports.

### 8.2 Hardware Interfaces

- NVIDIA Jetson Orin Nano (edge inference host).
- RTSP-compatible camera(s).
- Optional siren/actuator connected to the Jetson device (mechanism to be defined at the Architecture stage).

### 8.3 Software Interfaces

- REST API between Jetson Agent and ASP.NET Core server (activation, heartbeat/health, alert submission, configuration retrieval, command retrieval).
- SQL Server database accessed by the ASP.NET Core backend.
- DeepStream/TensorRT runtime invoked by the Jetson Agent.

### 8.4 Communication Interfaces

- Local network (LAN) connectivity between Jetson device(s) and the central server.

### 8.5 Deferred Architectural Decisions

The following items are intentionally **not** specified as requirements in this SRS. They represent implementation approaches to be determined during the Architecture phase, constrained by the functional requirements above:

- **Live stream delivery protocol** (FR-MON-001): whether the dashboard consumes RTSP directly, a transcoded stream (e.g., HLS/WebRTC), or a fallback mechanism, is an architectural decision, not a requirement.
- **Siren actuation mechanism** (FR-DET-010): whether the Jetson drives a physical GPIO-connected siren, a networked relay, or a simulated/logged action for demonstration purposes, is an architectural decision.
- **Configuration synchronization transport** (FR-SYN-005, FR-SYN-006): whether configuration checks are piggybacked on the heartbeat cycle or use a separate channel, and the local persistence format used to survive a restart, are architectural decisions.
- **Snapshot and recording storage mechanism**: filesystem vs. database BLOB vs. object storage for the server-side snapshot store is an architectural decision, constrained by BR-006 and FR-REC-003.
- **Device identity mechanism** (FR-BRN-007): the specific form of persistent device identity (e.g., generated device ID, certificate, token) is an architectural decision, not a requirement.
- **AI model architecture and inference runtime** (FR-DET-001): the specific model, framework, and runtime used for on-device detection is an architectural/implementation decision, constrained only by the functional behavior specified in this SRS.
- **API design**: the shape, versioning, and protocol style of the REST APIs between the Jetson Agent, server, and dashboard (endpoint structure, request/response schemas, authentication headers, error formats) is an architectural decision, not specified by this SRS.
- **Database schema**: the specific SQL Server table structure, normalization approach, indexing, and entity relationships used to satisfy the functional requirements in Section 3 are architectural decisions, not specified by this SRS.

---

## 9. Requirements Traceability Matrix

| Requirement ID(s) | Source (Charter / Vision Section) | Refinement Decision |
|--------------------|-------------------------------------|----------------------|
| FR-AUT-001–003 | Charter §4 (Proposed Solution); Vision §4, §7 | Single Admin login (approved) |
| FR-BRN-001–006 | Charter §4; Vision §5.1, §7 | Activation Key regeneration/reset (approved refinement) |
| FR-DET-001–011 | Charter §4, §5; Vision §5.2, §7 | Configurable confidence threshold with default (approved refinement) |
| FR-MON-001 | Vision §5.3, §7 | Live stream protocol deferred to Architecture (approved refinement) |
| FR-HLT-001–005 | Charter §4; Vision §5.4, §7 | Configurable heartbeat (30–60s default), 3 missed = offline (approved refinement) |
| FR-SYN-001–005 | Vision §5.5, §7; Vision §6 | Automatic configuration sync (new approved requirement) |
| FR-REC-001–003 | Vision §5.5 (recording reference); Refinement session | Continuous recording, fixed retention, snapshot-only upload (approved refinement) |
| FR-RPT-001–004 | Charter §6; Vision §5.6, §7 | Tabular/filterable, no analytics (approved) |
| NFR-PRF-001 | Vision §9 (Success Criteria) | <5s latency target (approved refinement) |
| NFR-REL-001–002 | Vision §5.5, §9 | Store-and-forward resilience (approved) |
| NFR-SEC-001–002 | Charter §4 ("secure REST APIs") | Activation Key + session auth (approved) |
| NFR-MNT-001 | Engineering Principles; Vision §9 | Separation of concerns (approved) |
| NFR-USB-001 | Vision §5.2 | Single-view decision support (approved) |
| NFR-CFG-001 | Refinement session | Heartbeat/confidence configurability (approved refinement) |
| BR-001–007 | Vision §4, §5, §6 | Derived directly from approved Vision workflows |
| CON-001–007 | Charter §10; Charter §7 | Direct from Charter |
| ASM-001–007 | Charter §9 | Direct from Charter |

---

## 10. Acceptance Criteria Summary

The prototype will be considered to satisfy this SRS when:

1. Every FR listed in Section 3 has a corresponding, passing test or demonstrable scenario.
2. Every NFR listed in Section 4 has been measured or observed to meet its stated target (e.g., latency measurement, resilience test, configurability check).
3. The end-to-end scenario in Vision §9 (camera → DeepStream → YOLO26 → Agent → server → dashboard → operator action) can be demonstrated live without manual workarounds.
4. Offline detection, storage, and synchronization (FR-SYN-001–004) can be demonstrated via a simulated network interruption.
5. Configuration changes (heartbeat interval, confidence threshold) made via the server are shown to propagate to the Jetson Agent without manual device intervention (FR-SYN-005).

---

## 11. Out of Scope

Consistent with the Project Charter and Vision Document, the following remain explicitly out of scope for this SRS and the prototype it governs: multi-tenant deployment, cloud-based inference, automatic model retraining, multiple user roles/permission levels, multiple Jetson devices per branch, automatic software/model updates, mobile applications, high-availability deployment, distributed microservices, incident case management, external notifications (e.g., police, branch management), alert states beyond Acknowledged/False Positive, advanced analytics/predictive trends/risk scoring, and advanced remote device management (automatic recovery, remote updates, hardware diagnostics).
