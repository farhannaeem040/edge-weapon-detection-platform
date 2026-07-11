# Software Architecture Document

| Field | Value |
|-------|-------|
| Document ID | ARCH-001 |
| Title | Software Architecture Document |
| Project | Edge-Based Weapon Detection and Centralized Monitoring System Using NVIDIA Jetson and DeepStream |
| Version | 1.0 |
| Status | Final |
| Owner | Farhan Naeem |
| Related Documents | Project Charter (PC-001), Vision (VIS-001), Software Requirements Specification (SRS-001) |
| Last Updated | 11 July 2026 |

---

## Table of Contents

1. Introduction, Purpose, and Scope
2. Architectural Goals
3. Assumptions and Constraints
4. Architectural Drivers
5. Quality Attribute Scenarios
6. Architectural Style and Design Principles
7. System Context
8. Overall Architecture
9. Technology Architecture
10. Component Architecture
11. Runtime Architecture
12. Deployment Architecture
13. Data Architecture
14. API Architecture
15. Security Architecture
16. Device Lifecycle
17. Edge AI Architecture
18. Configuration Management
19. Live Stream Architecture
20. Alert Processing Workflow
21. Command Processing Workflow
22. Offline Resilience and Synchronization
23. Error Handling and Recovery
24. Logging, Monitoring & Diagnostics
25. Technology Stack and Rationale
26. Architectural Decisions (ADR Summary)
27. Traceability to the SRS
28. Risks, Trade-offs, and Future Considerations

---

## 1. Introduction, Purpose, and Scope

### 1.1 Purpose of this Document

This Software Architecture Document (SAD) describes how the system defined by the Project Charter (PC-001), Vision (VIS-001), and Software Requirements Specification (SRS-001) will be realized in software. It defines the structure, components, interfaces, and behavior of the system, and records the architectural decisions made to satisfy the approved requirements baseline.

This document does not define or redefine requirements. Where a requirement is referenced, it is referenced for traceability only; the SRS remains the authoritative source for *what* the system must do. This document describes *how*.

This document follows generally accepted software architecture documentation practices, informed by the structure and intent of IEEE 42010 (architecture description concepts such as stakeholders, concerns, viewpoints, and rationale). It does not claim formal conformance to IEEE 42010 or any other standard; it applies those ideas pragmatically, at a scope appropriate to a dissertation prototype.

### 1.2 Intended Audience

- The dissertation author, as the system's sole architect and implementer.
- The academic supervisor, as evaluator of architectural and engineering quality.
- Any future reader (e.g., examiner, or a future contributor extending this work) who needs to understand the system's structure without re-deriving it from source code.

### 1.3 Relationship to Other Documents

This document sits below the SRS and above Feature Specifications and Implementation Tasks in the project's documentation hierarchy. It assumes the reader has access to, and does not repeat the content of, the documents listed below.

| Document | Responsibility |
|----------|-----------------|
| Project Charter (PC-001) | Defines why the project exists: problem statement, objectives, stakeholders, high-level scope, constraints, and success criteria. |
| Vision (VIS-001) | Describes the intended users, their workflows, and the system's real-world capabilities, distinguishing prototype scope from future vision. |
| Software Requirements Specification (SRS-001) | Defines *what* the system must do: functional and non-functional requirements, business rules, constraints, assumptions, and traceability back to the Charter/Vision. |
| Software Architecture Document (ARCH-001, this document) | Defines *how* the system realizes the SRS: structure, components, interfaces, data, security, and technology decisions. |
| Feature Specifications (future) | Define detailed behavior of individual features, within the structure this document establishes. |
| Implementation Tasks (future) | Break approved feature specifications into discrete, implementable units of work. |

### 1.4 Scope of the Architecture

This document covers the architecture of the dissertation prototype as scoped by Charter §6–7 and Vision §7:

- One NVIDIA Jetson Orin Nano running the Jetson Agent (FastAPI control plane) and NVIDIA DeepStream (data plane), connected to one primary RTSP camera.
- One ASP.NET Core backend and one SQL Server database, co-located on a single host for this prototype.
- One Angular dashboard, single Admin login.
- Local network deployment only.

### 1.5 Out of Scope

Consistent with Charter §7 and Vision §8, this document does not address architecture for: multi-branch/multi-device fleets, cloud-based inference, high-availability or distributed deployment, multiple user roles, automatic software/model updates, or mobile clients. Where relevant, forward-looking notes on how the architecture could extend toward these are deferred to §28 (Risks, Trade-offs, and Future Considerations) and are explicitly not part of the prototype's realized design.

### 1.6 How to Read This Document

Each section that introduces or relies on an architectural decision states that decision explicitly and, where applicable, traces it back to the SRS requirement(s) it realizes. New architectural decisions are introduced only where the SRS intentionally deferred an implementation choice to the Architecture phase (for example, the streaming protocol, the authentication mechanism, or the local offline storage format); this document does not introduce architecture in areas the SRS already settled at the requirements level. Architectural decisions are consolidated in §26 (ADR Summary) for quick reference; §27 (Traceability to the SRS) provides the full requirement-to-architecture mapping.

---

## 2. Architectural Goals

### 2.1 Primary Architectural Goals

| Goal | Description | Source |
|------|--------------|--------|
| Clear separation of concerns | Distinct, independently reasoned-about boundaries between AI inference (data plane), edge orchestration (control plane), backend business logic, and presentation. | Vision §9; Engineering Principle 6 |
| Modularity | Components are independently understandable and replaceable (e.g., hardware abstraction for siren, pluggable inference runtime). | Engineering Principles 3, 6 |
| Maintainability | The system should be easy to understand, modify, and extend by a single developer within a short timeframe, and by a future reader without access to the author. | Charter §5; Engineering Principles 3, 9 |
| Testability | Every architectural component should be testable in isolation where practical. | Charter §13; Engineering Principle 4 |
| Operational resilience | The edge device continues its core function (local detection, logging, recording, offline buffering) independent of connectivity to the server. | SRS FR-SYN-001, NFR-REL-001 |
| Demonstrability | The complete, end-to-end workflow must be demonstrable as a working system, since dissertation evaluation is of the system as a whole rather than of isolated components. | Charter §13; Vision §9 |
| Prototype-appropriate security | Security mechanisms are scoped to what a trusted local-network dissertation prototype genuinely requires, while still enforcing the boundaries the SRS mandates. | NFR-SEC-001–004 |

### 2.2 Architecture Governance Principles

| Principle | Description |
|-----------|--------------|
| Explainability of architectural decisions | Every major structural choice must be justifiable on its own terms, not merely "because it works." |
| Requirements traceability | Every architectural element should be traceable to a specific SRS requirement or an explicitly identified, approved architectural decision — never invented silently. |
| No silent introduction of requirements or architectural decisions | New architecture is introduced only where the SRS intentionally deferred a choice to this phase (§1.6); requirements themselves are never altered by this document. |

### 2.3 Security Goal Definition

Security in this architecture is defined as appropriate for a **trusted local-network dissertation prototype**, while still enforcing:

- Authenticated Dashboard-to-Backend access (JWT).
- Authenticated Backend-to-Jetson command requests (shared secret).
- Authorized browser-to-Jetson live-stream access (backend-issued stream token).

Production-grade mechanisms (mTLS, OAuth/OIDC, certificates, key rotation, HTTPS) are explicitly out of scope for the prototype and are recorded as future work (§28).

### 2.4 Priority Order When Goals Conflict

Given the prototype's constraints (five-week timeframe, single developer, single Jetson device — Charter §10), this architecture explicitly prioritizes:

1. **Simplicity and demonstrability** over scalability or production-hardening.
2. **Clear separation of concerns** over minimizing the number of moving parts.
3. **Explainability** over cleverness.

### 2.5 Non-Goals

Consistent with §1.5 and Charter §7/Vision §8, this architecture explicitly does not optimize for: high availability, multi-tenancy, horizontal scalability, or production-grade security posture. These are noted as future considerations in §28.

---

## 3. Assumptions and Constraints

### 3.1 Constraints Inherited from the SRS

| ID | Constraint | Architectural Note |
|----|------------|---------------------|
| CON-001 | Five-week implementation timeline | Drives §2's "simplicity over hardening" priority order. |
| CON-002 | Single developer | Same as above. |
| CON-003 | One Jetson Orin Nano available | No fleet/multi-device concerns in this architecture. |
| CON-004 | One primary RTSP camera | Single-camera pipeline assumed throughout §17–20. |
| CON-005 | Local network deployment only | Underpins §15's trusted-LAN security model and §19's "no TURN server" choice. |
| CON-006 | Fixed technology stack, including "FastAPI-based Jetson Agent" (corrected from "Flask-based" per Architecture Decision 1 / ADR-001) | Basis for §9/§25. |
| CON-007 | Multi-tenant, multi-device-per-branch, multi-role explicitly out of scope | Consistent with §1.5. |

### 3.2 Assumptions Inherited from the SRS

| ID | Assumption | Architectural Note |
|----|------------|---------------------|
| ASM-001 | Jetson/camera hardware available and functioning | No hardware-failure architecture required. |
| ASM-002 | Roboflow datasets sufficient | Not architecture-relevant. |
| ASM-003 | TensorRT conversion supported | Not architecture-relevant. |
| ASM-004 | Local network reliable outside simulated outages | Bounds the scope of §22 (Offline Resilience). |
| ASM-005 | Single Admin credential acceptable | Underpins §15 having only one dashboard identity to authenticate. |
| ASM-006 | Physical installation/Activation Key entry is out-of-band | No remote/zero-touch provisioning flow required. |
| ASM-007 | Sufficient local storage for retention period | Bounds §13's filesystem/retention design. |
| ASM-008 | Server can resolve a working network address for each device | Realized by the persistent Device ID + dynamic resolution mechanism (§16). |

### 3.3 Architecture-Specific Assumptions

| ID | Assumption |
|----|------------|
| ARCH-ASM-001 | For this prototype, the ASP.NET Core backend, SQL Server, and Angular dashboard are co-located on a single physical/virtual host; this is a deployment convenience, not a logical design driver (§1.4). |
| ARCH-ASM-002 | The Jetson software environment provides compatible runtimes for the FastAPI Agent and NVIDIA DeepStream. The Agent launches and supervises the DeepStream application as a separate operating-system subprocess. |
| ARCH-ASM-003 | At most one browser session views a given Jetson's live stream at a time. |
| ARCH-ASM-004 | No NAT traversal is required for WebRTC because both the browser and the Jetson are on the same local network. |

### 3.4 Architecture-Specific Constraints

| ID | Constraint | Source Decision |
|----|------------|------------------|
| ARCH-CON-001 | No message broker is introduced for Agent↔DeepStream communication; a Unix domain socket is used instead. | ADR-005 |
| ARCH-CON-002 | DeepStream's lifecycle is managed exclusively by the Jetson Agent; systemd manages only the Agent process. | ADR-006 |
| ARCH-CON-003 | Server-initiated commands are executed synchronously; no command queue, job IDs, or polling/callback mechanism exists. | ADR-007 |
| ARCH-CON-004 | Jetson-side persistent storage is fixed to the `/opt/weapon-detection/` layout; SQLite holds metadata/paths only, never binary images or video. | ADR-008 |
| ARCH-CON-005 | Both REST APIs (Backend API, Agent API) use `/api/v1` versioning and a single uniform response envelope, with documented exceptions for binary/signaling payloads. | ADR-009 |
| ARCH-CON-006 | No OAuth/OIDC, mTLS, client certificates, key rotation, or refresh tokens are implemented. HTTP is used, not HTTPS, for the prototype. | ADR-002 |

---

## 4. Architectural Drivers

| Driver Requirement(s) | Architectural Consequence | Realized By |
|------------------------|------------------------------|--------------|
| FR-SYN-001–004, NFR-REL-001–002 | Edge device must operate autonomously and losslessly buffer events during disconnection. | Control-plane Agent + SQLite local store (ADR-001, ADR-004) |
| FR-DET-001, NFR-PRF-001–002 | Detection must run at real-time frame rates with <5s end-to-end alert latency; inference must not be blocked by orchestration work. | Data-plane/control-plane split + low-latency IPC (ADR-001, ADR-005) |
| NFR-SEC-001–004, BR-008 | Distinct trust boundaries each need an appropriately scoped authentication mechanism. | JWT + Activation Key + shared secret + stream authorization (ADR-002) |
| FR-MON-001, NFR-SEC-004 | Live viewing must be low-latency and authorized, without burdening the backend with media relay. | Direct browser↔Jetson WebRTC, backend-issued authorization (ADR-003) |
| FR-BRN-007, ASM-008 | Devices must be addressable by identity independent of network changes. | Persistent Device ID + dynamic IP/hostname resolution (§16) |
| SRS §3.3 Note on Operational Capabilities | The Agent must expose safely executable operational commands, including one (Restart Agent) that cannot return a normal post-restart response. | Synchronous dispatch with special-cased self-restart (ADR-007) |
| NFR-MNT-001 | System must maintain clear separation of concerns across AI/edge/backend/frontend. | Control-plane/data-plane split; layered backend (§6, §10) |
| CON-006 | A fixed, prototype-appropriate technology stack, chosen once and not revisited. | §9 Technology Architecture, §25 Technology Stack and Rationale |

---

## 5. Quality Attribute Scenarios

| # | Attribute | Scenario (Stimulus → Response) | Response Measure | Source |
|---|-----------|----------------------------------|---------------------|--------|
| QAS-1 | Availability / Resilience | Server connectivity is lost for an extended period while detections continue to occur. | Local detection, logging, recording, and event buffering continue without interruption; new remote siren commands are correctly refused as unavailable; on reconnect, all buffered events sync with original timestamps preserved. | FR-SYN-001–004, NFR-REL-001–002 |
| QAS-2 | Security | An unauthenticated client requests a protected dashboard/API endpoint. | Request is rejected (401) prior to reaching business logic. | NFR-SEC-001, FR-AUT-002 |
| QAS-3 | Security | The backend sends a command to the Agent without a valid shared secret, or a request arrives from a non-registered source. | Agent rejects the command without executing it. | NFR-SEC-003, BR-008 |
| QAS-4 | Security | A browser attempts to open a WebRTC session directly with the Jetson without a backend-issued authorization. | Jetson refuses the connection. | NFR-SEC-004 |
| QAS-5 | Performance | A weapon is detected under normal load (one camera, one concurrent alert). | Alert is visible on the dashboard within 5 seconds of detection. | NFR-PRF-001 |
| QAS-6 | Maintainability / Modularity | The AI model or inference runtime is changed. | No change is required to the Agent's control-plane logic beyond the fixed IPC message contract (JSON over the Unix socket). | NFR-MNT-001, ADR-005, FR-DET-001 note |
| QAS-7 | Reliability (best-effort, not guaranteed) | The supervised DeepStream subprocess exits unexpectedly. | The Agent detects the process exit and records/exposes the pipeline as unhealthy; recovery is available on demand via the approved Restart DeepStream command. No automatic restart, restart-count policy, retry interval, or guaranteed recovery time is committed to in this prototype. | ADR-006 |

---

## 6. Architectural Style and Design Principles

### 6.1 Overall Architectural Style

The system is a **distributed client-server architecture with an edge-computing node, using REST-based service interfaces and an internal control-plane/data-plane split on the Jetson**:

- **Jetson Agent (control plane)** — orchestration, state, communication, hardware control.
- **NVIDIA DeepStream (data plane)** — mechanical video-processing work only, no business logic.
- **ASP.NET Core Backend** — Clean/layered architecture, the system of record.
- **Angular Dashboard** — component-based SPA, presentation only.

### 6.2 Design Principles Applied

| Principle | Application |
|-----------|--------------|
| Separation of Concerns | Control plane vs. data plane (edge); layered backend; dashboard has no business logic. |
| SOLID | Backend organized into Domain/Application/Infrastructure/API layers (§10); hardware control behind an abstraction (siren interface), not coupled to GPIO libraries. |
| Dependency Injection | Used within the ASP.NET Core backend per standard framework convention; the Agent's hardware abstraction (GPIO vs. simulation) is injected/selected at startup rather than hardcoded. |
| Explainability | Every structural choice traces to a stated goal (§2) or governance principle (§2.2), or is logged as an ADR (§26). |
| No Silent Architecture | New architectural decisions are introduced only where the SRS deferred them (§8.5 of the SRS); this document adds nothing not already covered by the ADRs in §26. |

### 6.3 Component-Level Style

- **Backend**: Clean/layered architecture (Domain, Application, Infrastructure, API) — detailed in §10.
- **Jetson Agent**: modular control-plane orchestrator — detailed in §10.
- **DeepStream**: inference and mechanical media-processing data plane — detailed in §17.
- **Dashboard**: component-based SPA, feature modules per business capability — detailed in §10.

---

## 7. System Context

### 7.1 System Actors

| Actor | Description | Source |
|-------|--------------|--------|
| Admin User | Single authenticated account representing both System Administrator and Security Operator conceptual roles (BR-001). | Vision §4, SRS §2.2 |
| RTSP Camera | Physical camera providing the video stream; external hardware actor. | SRS §8.2 |
| Physical Siren/Actuator (where implemented) | External hardware actuator via the Agent's hardware abstraction; may be simulated/logged in the absence of physical hardware. | Vision §5.2 |

The Jetson device itself is a deployment node hosting system components (Agent, DeepStream), not an external actor — addressed in §12 Deployment Architecture.

### 7.2 System Boundary

Encloses: Angular Dashboard, ASP.NET Core Backend, SQL Server, Jetson Agent, NVIDIA DeepStream. Excludes: RTSP camera hardware, physical siren/actuator hardware, Admin's browser/workstation.

### 7.3 External Interactions

- The Admin User interacts with the Angular Dashboard through a web browser. The browser-based Dashboard authenticates to the ASP.NET Core Backend using a JWT Bearer token.
- Dashboard ↔ Backend: REST, JWT-authenticated.
- Browser ↔ Jetson Agent: direct WebRTC, authorized by the Backend.
- RTSP Camera → NVIDIA DeepStream: the camera supplies the configured RTSP stream directly to the DeepStream pipeline. The Agent configures and supervises DeepStream but does not consume the camera stream itself.
- Jetson Agent ↔ Physical Siren: GPIO or simulated actuation.

**Diagrams:** C4 Context diagram (Level 1) — actors surrounding the system boundary.
**Traceability:** Vision §4, SRS §2.2, SRS §8.2.

---

## 8. Overall Architecture

### 8.1 Canonical Architecture Statement

*The Jetson Agent is implemented as a FastAPI-based control plane responsible for supervising the DeepStream data plane, managing device state, handling REST APIs, coordinating offline synchronization, and controlling hardware peripherals.*

### 8.2 Container View

The system contains four application/runtime components — Angular Dashboard, ASP.NET Core Backend, Jetson Agent, and NVIDIA DeepStream — plus SQL Server as the persistent system-of-record container.

| Container | Responsibility | Technology |
|-----------|------------------|------------|
| Angular Dashboard | Presentation only | Angular |
| ASP.NET Core Backend | Auth, branch/device/alert management, config, health, command orchestration, stream authorization | ASP.NET Core, C# |
| SQL Server | System-of-record persistence | SQL Server |
| Jetson Agent (control plane) | Orchestration, REST APIs (both directions), offline resilience, hardware control | FastAPI, Python |
| NVIDIA DeepStream (data plane) | RTSP video ingestion, decoding and preprocessing, TensorRT inference, snapshot generation, segmented recording, WebRTC media production, and serialization of detection metadata. Contains no business, persistence, synchronization, command, or authorization logic. | DeepStream, TensorRT, YOLO26 |

### 8.3 Logical Structure

The four logical components (Dashboard, Backend, Database, Jetson Agent+DeepStream) are independent regardless of physical co-location (ARCH-ASM-001). Communication is bidirectional REST between Backend and Agent (two independently secured APIs), plus the direct browser↔Jetson WebRTC path for streaming, which bypasses the Backend entirely for media. Live media never travels through the Backend.

### 8.4 Data Flow Diagram (described)

Three primary flows:

1. **Detection/Alert flow**: Camera → DeepStream → Unix domain socket detection metadata → Jetson Agent → local event persistence in SQLite with associated filesystem media references → Backend REST API → SQL Server → Dashboard.
2. **Command flow**: Dashboard → Backend (JWT-validated) → Backend calls Jetson Agent REST API with shared-secret header → Agent validates (NFR-SEC-003/BR-008) → executes synchronously → result returned in the same HTTP response → Backend logs result → Dashboard reflects outcome.
3. **Streaming flow**: Browser ↔ Jetson Agent direct WebRTC, authorization issued by Backend, media never traverses the Backend.

**Diagrams:** C4 Container diagram (Level 2); logical component diagram; Data Flow Diagram (Level 0/1) per the three flows above.
**Traceability:** Realizes ADR-001 through ADR-016 collectively; SRS §2.1, §8.3.

---

## 9. Technology Architecture

### 9.1 Technology-to-Tier Mapping

| Tier | Technology | Protocol/Binding to Neighbor |
|------|------------|-------------------------------|
| Dashboard | Angular | HTTP REST over the trusted local network for the prototype (`/api/v1`, JWT bearer); HTTPS is deferred to §28 as production hardening. |
| Backend | ASP.NET Core (C#) | REST to Dashboard (JWT); REST to Agent (shared secret, outbound commands); REST from Agent (inbound: one-time activation via Activation Key; all subsequent ongoing calls — heartbeat/health, alert & snapshot submission, offline-event sync, configuration retrieval, operational-result reporting — authenticated via persistent Device ID + shared secret headers, validated by the Backend per ADR-002 (amended); see §15). |
| Database | SQL Server | Backend data-access layer to SQL Server |
| Jetson Agent | FastAPI (Python) | REST to/from Backend (`/api/v1`); Unix domain socket to DeepStream (inbound JSON detection events); WebRTC signaling to Browser (direct); local SQLite file I/O; GPIO or simulated hardware I/O |
| DeepStream | NVIDIA DeepStream, TensorRT, YOLO26 | RTSP inbound from Camera; Unix domain socket outbound to Agent (JSON detection metadata only) |

### 9.2 Runtime Boundaries

- **Process boundary**: Agent and DeepStream are separate OS processes on the same device, communicating only via the Unix domain socket (ARCH-ASM-002, ADR-005) — no shared memory, no in-process calls.
- **Deployment/network boundaries**:
  - Angular static assets, ASP.NET Core Backend, and SQL Server may be deployed on the same server host.
  - Admin browser ↔ Backend API traffic crosses the LAN.
  - Backend ↔ Jetson Agent REST traffic crosses the LAN.
  - Browser ↔ Jetson Agent WebRTC traffic crosses the LAN.
  - Backend ↔ SQL Server remains internal to the server host.
- **Trust boundaries**:
  - Browser/Dashboard → Backend: JWT Bearer authentication.
  - Backend → Agent command API: shared-secret validation.
  - Agent → Backend ongoing operations: Device ID + shared-secret validation by the Backend.
  - Browser → Agent WebRTC: backend-issued stream authorization.
  - Agent activation → Backend: one-time Activation Key exchange, issuing the persistent Device ID and shared secret.

### 9.3 Uniform API Conventions

Both the Backend API and the Jetson Agent API use `/api/v1` versioning and the shared response envelope (`{success, message, data}` / `{success, message, errorCode}`), applied identically regardless of which service is the caller, with documented exceptions for binary and signaling payloads (§14.3).

**Diagrams:** Technology architecture diagram — each tier's technology box, with connector labels showing protocol and boundary type.
**Traceability:** CON-006 (corrected), SRS §8.3, ADR-001, ADR-002, ADR-003, ADR-004, ADR-005, ADR-009.

---

## 10. Component Architecture

### 10.1 ASP.NET Core Backend — Internal Components

Clean/layered architecture (§6.3):

| Layer | Components |
|-------|-------------|
| API/Presentation | `AuthController` (accepts login/logout requests and delegates credential verification and token issuance to the Application-layer AuthService), `BranchController`, `DeviceController`, `AlertController`, `CommandController`, `ConfigController`, `HealthController`, `StreamAuthorizationController`. ASP.NET Core authentication/authorization middleware validates JWT Bearer tokens and enforces protected-endpoint access before any controller executes. |
| Application | AuthService, BranchService, DeviceService (Device ID + address resolution), AlertService, CommandDispatchService (attaches shared secret, calls Agent API), ConfigService, HealthMonitoringService, StreamAuthorizationService |
| Domain | Branch, Camera, Device, Alert, HealthRecord, Configuration entities. Core entity invariants and lifecycle rules (e.g., BR-004 alert lifecycle terminating at Acknowledged/False Positive, BR-003 Activation Key single-use) belong here. |
| Infrastructure | Backend data-access layer to SQL Server; outbound HTTP client to Jetson Agent API. Authentication, authorization, command-source validation, external communication, and orchestration rules belong in Application/API/Infrastructure as appropriate — in particular, BR-008 and Device ID/shared-secret validation are security/integration policies enforced here, not Domain entity logic. |

### 10.2 Jetson Agent — Internal Components

Modular control-plane orchestrator, single FastAPI process:

| Component | Responsibility |
|-----------|------------------|
| Pipeline Supervisor | Starts/stops/restarts the DeepStream subprocess (ADR-006) |
| Detection Ingest Handler | Listens on the Unix domain socket for detection metadata (ADR-005) |
| Alert Manager | Validates incoming detections, creates alert records |
| Local Persistence Manager | SQLite access (ADR-004), filesystem layout management (ADR-008) |
| Backend Sync Client | Issues all Agent-initiated calls to the Backend (heartbeat, alert/snapshot upload, offline sync, config retrieval, operational-result reporting), attaching the persistent Device ID and shared secret as request headers on every call. The Backend authenticates each request before processing (ADR-002, amended). |
| Command API | Server-initiated REST endpoints; validates shared secret, executes synchronously with Restart Agent's accept-then-delayed-restart sequencing (ADR-007) |
| Health Reporter | Periodic heartbeat construction and dispatch |
| Config Manager | Applies and persists synchronized configuration |
| Hardware Abstraction Layer | GPIO / simulated siren interface |
| WebRTC Signaling Component | Handles offer/answer/ICE exchange and authorization-token validation for direct browser streaming; does not itself produce media (§17, §19) |

### 10.3 NVIDIA DeepStream — Component View

Treated as a single opaque data-plane component (vendor pipeline, not custom-decomposed): RTSP ingestion → decode/preprocess → TensorRT inference (YOLO26) → snapshot generation, segmented recording, and WebRTC media branching via shared pipeline branching (`tee`) → detection-metadata serialization → Unix domain socket output. No internal business-logic decomposition is architected here.

### 10.4 Component Ownership Statement

No unresolved issue blocks approval of Sections 10–12 at their current abstraction level. Detailed media-pipeline ownership (snapshot extraction, continuous recording, WebRTC media production) is resolved in §17 and §20: DeepStream/GStreamer produces these mechanically; the Agent owns all retention, synchronization, and lifecycle decisions over the resulting files. This does not change the frozen control-plane/data-plane boundary. Any future change to this responsibility boundary requires a controlled amendment, not a silent edit.

### 10.5 Angular Dashboard — Component View

Feature-module structure (§6.3): Alerts module, Live Monitoring module, Branch Management module, Device Health module, Reports module, Auth module (JWT storage/attachment) — no business logic beyond presentation and API calls.

**Diagrams:** C4 Component diagrams (Level 3) for Backend and Jetson Agent; DeepStream shown as a single black-box component in both.
**Traceability:** NFR-MNT-001, FR-DET/FR-SYN/FR-HLT families, BR-008/NFR-SEC-003.

---

## 11. Runtime Architecture

### 11.1 Key Runtime Scenarios (sequence diagrams described)

1. **Pipeline startup**: systemd starts the Agent → Pipeline Supervisor reads config → launches DeepStream as a subprocess → DeepStream begins RTSP ingestion and inference.
2. **Detection-to-alert**: DeepStream emits detection metadata through the Unix domain socket. The Detection Ingest Handler passes it to the Alert Manager, which validates the configured alert threshold. For a qualifying event, the Agent persists the event in SQLite together with the associated filesystem media reference produced through the media-pipeline mechanism defined in §17. The event and required snapshot are then submitted to the Backend immediately when connected or retained for later synchronization when offline.
3. **Command dispatch (standard)**: Dashboard → Backend (JWT-checked) → CommandDispatchService → Agent Command API (shared-secret validation by the Agent) → executes synchronously → result returned in the same HTTP response → Backend logs result.
4. **Command dispatch (Restart Agent, special case)**: Backend → Agent Command API (shared secret) → Agent returns HTTP 200 acknowledging the accepted command → Agent schedules a short delayed self-restart → Agent process exits → systemd restarts the Agent process.
5. **Offline-to-recovery transition**: connectivity lost → Backend Sync Client calls fail/timeout → Local Persistence Manager continues buffering (FR-SYN-001/002) → connectivity restored → Backend Sync Client (attaching Device ID + shared secret, validated by the Backend) drains pending SQLite records → Backend preserves original timestamps (FR-SYN-004).

### 11.2 Process/Concurrency Model

The Jetson Agent runs as one FastAPI application under a single Uvicorn worker. Pipeline supervision, Unix-socket detection ingestion, heartbeat reporting, backend synchronization, configuration polling, and command handling run as coordinated asynchronous tasks within that process. Multiple Uvicorn workers are not used because they would duplicate singleton device-level responsibilities such as DeepStream supervision and synchronization. DeepStream remains a separate, Agent-supervised OS subprocess; all Agent↔DeepStream communication occurs exclusively through the Unix domain socket contract, with no shared memory.

**Diagrams:** Sequence diagrams for scenarios 1–5 above.
**Traceability:** ADR-005, ADR-006, ADR-007, ADR-010, FR-SYN-001–004, NFR-PRF-001.

---

## 12. Deployment Architecture

### 12.1 Physical/Deployment Nodes

| Node | Hosts | Notes |
|------|-------|-------|
| Jetson Orin Nano | Jetson Agent (FastAPI process, systemd-managed) + NVIDIA DeepStream (subprocess, Agent-managed) + local SQLite file + local filesystem media store | Single device, per CON-003. |
| Server Host | ASP.NET Core Backend + SQL Server + Angular static assets | Co-located for this prototype (ARCH-ASM-001); a deployment convenience, not a logical necessity. |
| Admin Workstation | Web browser only | Not part of the system boundary (§7.2); interacts via Dashboard and, for streaming, directly with the Jetson. |

### 12.2 Network Topology and Trust Boundaries

Single local network (CON-005) connecting all three nodes. No internet-facing component.

Trust boundaries, consolidated:

1. Browser/Dashboard → Backend: JWT Bearer token.
2. Agent → Backend ongoing operations: Device ID + shared secret.
3. Backend → Agent command API: Device ID/shared-secret-based registered-server validation, using the approved shared-secret header mechanism.
4. Browser → Agent WebRTC: Backend-issued stream authorization.
5. Agent activation → Backend: one-time Activation Key, exchanged for persistent Device ID and shared secret.

### 12.3 Physical Constraints

One primary RTSP camera is network-reachable by the Jetson and supplies its configured stream to the DeepStream pipeline (no USB or direct physical attachment implied). Where physical siren hardware is available, it is connected through the Jetson-supported hardware interface behind the Agent's hardware abstraction. Otherwise, the prototype uses the approved simulated/logged implementation.

**Diagrams:** UML Deployment diagram — three nodes, with artifacts per node and network connectors labeled by protocol.
**Traceability:** CON-003, CON-004, CON-005, ARCH-ASM-001.

---

## 13. Data Architecture

### 13.1 Backend (SQL Server) — Core Entities

| Entity | Key Fields | Notes |
|--------|------------|-------|
| Branch | BranchId, Name, Address, ContactDetails | Owns one or more Camera records |
| Camera | CameraId, BranchId, Name, RtspUrl, Enabled | |
| Device | DeviceId (persistent), BranchId, ProtectedSharedSecret, ActivationKeyHash, ActivationKeyStatus, LastKnownAddress, LastHeartbeatAt, Status | Shared secret stored protected (§13.3), not plain |
| Alert | AlertId, EventId, DeviceId, CameraId, Timestamp, WeaponType, Confidence, SnapshotPath, Status | Unique `(DeviceId, EventId)` constraint |
| HealthRecord | DeviceId, Timestamp, OnlineStatus, CameraStatus, PipelineStatus | |
| Configuration | DeviceId, ConfidenceThreshold, HeartbeatInterval, RetentionPeriod, Version | |
| AdminUser | UserId, PasswordHash | Never a recoverable password |
| AdminSession | SessionId (`jti`), UserId, IssuedAt, ExpiresAt, Revoked | Required for logout invalidation (§15.3) |

### 13.2 Jetson Agent (SQLite) — Local Schema

| Table | Purpose |
|-------|---------|
| DeviceIdentity | The Jetson's persistent local source for its Device ID and protected shared secret only. |
| ConfigCache | The persistent local source for the last successfully synchronized configuration, used during offline startup. |
| PendingEvents | Store-and-forward queue: EventId, DeviceId, SyncStatus, DetectionTimestamp, snapshot/recording filesystem references, upload-attempt metadata. Synchronized records are eligible for cleanup per §13.6, not automatically transient. |

Binary images/video are never stored in SQLite (BR-006) — only filesystem paths, under the `/opt/weapon-detection/` layout (ADR-008).

### 13.3 Credential and Secret Storage

- AdminUser passwords: stored only as salted password hashes (§15.2); never recoverable.
- Device shared secret: the Backend retains a **recoverable** form (required for Backend→Agent command authentication), stored encrypted at rest or protected via an application/host-level secret-protection mechanism — not as an unrestricted plain configuration value.
- On the Jetson, the shared secret is stored in the local SQLite/configuration store protected by restrictive OS file permissions.
- Activation Keys, shared secrets, JWTs, and stream tokens are never written to normal application logs.
- Production-grade key management/rotation is deferred to §28.

### 13.4 Event Idempotency

Each detection event is assigned an `EventId` on the Jetson at creation. The Backend enforces a unique `(DeviceId, EventId)` constraint on the Alert table, so retried synchronization after a network failure cannot create duplicate alerts.

### 13.5 Backend Snapshot Storage

Uploaded snapshot images are stored on the Server Host filesystem under an application-managed snapshot directory; SQL Server stores only metadata and the filesystem reference — no SQL Server BLOB storage or external object storage. Full video recordings remain Jetson-only (BR-006/FR-REC-003) and are never uploaded. The snapshot file and its Alert record are treated as one logical unit — a failed upload must not leave an alert falsely marked complete.

### 13.6 Media Cleanup Rules

- Continuous recordings are deleted when older than the configured retention period (FR-REC-002).
- A snapshot required for an unsynchronized event is never deleted.
- A local snapshot becomes cleanup-eligible only after the Backend acknowledges successful persistence of both the event metadata and the snapshot.
- A `PendingEvents` record is marked synchronized only after the Backend acknowledges both the event metadata and the required snapshot.
- Cleanup failures are logged and retried without deleting unsynchronized evidence.
- Detailed media-file production is defined in §17; ownership of retention/sync state belongs to the Agent.

**Diagrams:** ER diagram (Backend); local schema diagram (Agent SQLite).
**Traceability:** FR-BRN-007, FR-DET-004, FR-SYN-002–006, FR-REC-001–003, BR-006, ASM-007/008, NFR-SEC-002 (corrected).

---

## 14. API Architecture

### 14.1 Backend API

| Endpoint | Caller | Auth Header |
|----------|--------|---------------|
| `POST /api/v1/auth/login` | Dashboard | Credentials → issues JWT |
| `POST /api/v1/auth/logout` | Dashboard | JWT → revokes session |
| `POST /api/v1/activate` | Agent (one-time) | Activation Key |
| `POST /api/v1/heartbeat` | Agent | `X-Device-Id` + `X-Device-Secret` |
| `POST /api/v1/alerts` | Agent | `X-Device-Id` + `X-Device-Secret` |
| `POST /api/v1/alerts/{id}/snapshot` | Agent | `X-Device-Id` + `X-Device-Secret` |
| `POST /api/v1/sync/events` | Agent | `X-Device-Id` + `X-Device-Secret` |
| `GET /api/v1/config` | Agent | `X-Device-Id` + `X-Device-Secret` |
| `GET/POST /api/v1/branches`, `/devices`, `/cameras` | Dashboard | JWT |
| `GET /api/v1/alerts`, `GET /api/v1/alerts/{id}` | Dashboard | JWT |
| `PATCH /api/v1/alerts/{id}/status` | Dashboard | JWT |
| `GET /api/v1/alerts/{id}/snapshot` | Dashboard | JWT — binary response |
| `GET /api/v1/devices/{id}/health`, `/history` | Dashboard | JWT |
| `GET /api/v1/reports` (filterable) | Dashboard | JWT |
| `PUT /api/v1/devices/{id}/config` | Dashboard | JWT |
| `POST /api/v1/stream/authorize` | Dashboard | JWT — issues stream token |

Standard command results (Trigger/Stop Siren, Restart DeepStream, Reload Configuration) are returned synchronously in the Backend→Agent HTTP response and logged by the Backend; Restart Agent returns an acceptance response (HTTP 200) before its delayed self-exit. No separate command-result-reporting endpoint exists.

### 14.2 Jetson Agent API

| Endpoint | Auth Header |
|----------|---------------|
| `POST /api/v1/commands/siren/start` | `X-Device-Secret` |
| `POST /api/v1/commands/siren/stop` | `X-Device-Secret` |
| `POST /api/v1/pipeline/restart` | `X-Device-Secret` |
| `POST /api/v1/agent/restart` | `X-Device-Secret` |
| `POST /api/v1/config/reload` | `X-Device-Secret` |
| `POST /api/v1/stream/authorizations` | `X-Device-Secret` — Backend registers a short-lived stream token with the Agent |
| Browser-facing WebRTC signaling endpoint/channel | Versioned WebRTC signaling endpoint/channel on the Agent, authenticated with the backend-issued one-time stream authorization token; detailed signaling transport and message sequence are defined in §20/§19. |

### 14.3 Conventions

- Header names `X-Device-Id` / `X-Device-Secret` used consistently for all Agent-involved requests; capitalization not architecturally significant.
- Standard JSON envelope (`{success, message, data}` / `{success, message, errorCode}`) applies to normal JSON responses, with explicit exceptions: binary snapshot downloads return image content with appropriate content-type; multipart snapshot uploads carry binary + metadata; WebRTC SDP/ICE signaling uses its own required schema; HTTP status codes remain authoritative regardless of envelope presence.

**Diagrams:** API interaction diagram, grouped by caller direction.
**Traceability:** SRS §8.3, ADR-002 (amended), ADR-007, ADR-009.

---

## 15. Security Architecture

### 15.1 Trust Boundaries (authoritative, consolidated)

| # | Boundary | Mechanism |
|---|----------|-----------|
| 1 | Browser/Dashboard → Backend | JWT Bearer token, issued on login (FR-AUT-001–003, NFR-SEC-001) |
| 2 | Agent → Backend (ongoing operations) | Device ID + shared secret headers, validated by Backend |
| 3 | Backend → Agent (commands) | Device ID/shared-secret-based registered-server validation (NFR-SEC-003, BR-008) |
| 4 | Browser → Agent (WebRTC) | Backend-issued stream authorization token (NFR-SEC-004) |
| 5 | Agent activation → Backend | One-time Activation Key, exchanged for persistent Device ID + shared secret (NFR-SEC-002, corrected) |

### 15.2 Password Handling

Admin passwords are stored only as salted hashes using the standard ASP.NET Core password-hashing facility (or another approved adaptive hasher). Passwords are never stored or logged in plaintext.

### 15.3 Logout / Session Invalidation

A client discarding its JWT is insufficient — a copied token would remain usable. Design: the JWT carries a unique session identifier (`jti`) and expiry; the Backend maintains an `AdminSession` record (§13.1) tracking revocation; every protected request requires both a valid JWT signature/expiry **and** a non-revoked active session record; logout marks the current session revoked. No refresh-token flow is introduced.

### 15.4 WebRTC Stream Authorization

1. The authenticated Dashboard requests stream access from the Backend.
2. The Backend generates a cryptographically random, short-lived, single-use stream token scoped to the target Device ID.
3. The Backend registers that token and its expiry with the target Agent via `POST /api/v1/stream/authorizations`, authenticated with the device shared secret.
4. The Backend returns the Jetson's signaling endpoint and the token to the browser.
5. The browser presents the token during WebRTC signaling.
6. The Agent validates the token, Device ID/scope, expiry, and unused state.
7. The Agent consumes/invalidates the token on session acceptance or expiry.

The Agent holds pending stream tokens in memory only — stream authorization is short-lived and need not survive an Agent restart. No JWT verification keys, certificates, OAuth, or HMAC-signed tokens are introduced for this mechanism.

### 15.5 Activation Key Storage

Activation Keys are unique, single-use, stored by the Backend in a non-recoverable hashed form where practical, and marked consumed only as part of the successful activation transaction. The plaintext key is shown only at generation/provisioning time and is never logged.

### 15.6 Trusted-LAN Caveats

HTTP means Device Secrets, JWTs, and stream tokens are not protected from LAN interception — an accepted dissertation-prototype risk under CON-005, not a claim of production-grade security. Credentials never appear in URLs or logs. Agent and Backend APIs bind only to required LAN interfaces, with host firewall rules used where practical. HTTPS, certificate validation, secret rotation, and stronger key management remain mandatory future-hardening items (§28). OAuth/OIDC, mTLS, client certificates, HMAC signing, refresh tokens, and TURN servers are not implemented in this prototype.

**Diagrams:** Trust-boundary diagram; sequence diagrams for activation, login/logout with session revocation, and WebRTC stream authorization.
**Traceability:** NFR-SEC-001–004 (NFR-SEC-002 corrected), BR-008, FR-AUT-003, ADR-002 (amended), ADR-003.

---

## 16. Device Lifecycle

### 16.1 Lifecycle States

`Unprovisioned → Activation Pending → Activated → Online ⇄ Offline`, with `Reactivation Pending → Activated` as a credential-reset transition (not a permanent state).

### 16.2 Activation Sequence

Admin creates branch → Backend generates Activation Key → installer configures key on Agent (ASM-006) → Agent calls `POST /api/v1/activate` → Backend validates key, marks it consumed (FR-BRN-004), issues persistent Device ID + shared secret → Agent persists both locally (SQLite, §13.2) → Agent starts DeepStream.

### 16.3 Startup Behavior

Initial activation requires Backend connectivity. Every subsequent Agent startup: (1) loads the persistent Device ID, protected shared secret, and cached configuration from local storage; (2) starts/supervises DeepStream using cached configuration even if the Backend is unavailable; (3) begins buffering local events; (4) resumes authenticated synchronization once connectivity returns. DeepStream is not gated on a live activation exchange after the device's first successful activation.

### 16.4 Reactivation and Device ID Behavior

- Regenerating the Activation Key invalidates the previous, unused Activation Key.
- Successful reactivation of the branch's existing logical device **retains** the persistent Device ID.
- Successful reactivation issues a **new** shared secret and invalidates the previous one.
- The Agent replaces its locally stored secret atomically after successful activation.
- Historical alerts and health records remain correlated to the retained Device ID.
- Creating an entirely separate device identity is future fleet-management scope, not this prototype.

### 16.5 Offline Detection

Governed entirely by FR-HLT-001–004 (already frozen in the SRS) — no additional architectural mechanism required.

**Diagrams:** State diagram (§16.1); activation and reactivation sequence diagrams.
**Traceability:** FR-BRN-001–007, BR-002/003, FR-HLT-003, ASM-006/008, NFR-SEC-002 (corrected).

---

## 17. Edge AI Architecture

### 17.1 Responsibility Boundary

DeepStream/GStreamer (data plane) performs mechanical video-processing work: RTSP ingestion, decoding, preprocessing, inference, snapshot generation, segmented recording, WebRTC media production, and detection-metadata emission. It contains no business, persistence, synchronization, command, or authorization logic.

The Jetson Agent (control plane) performs supervision, validation, identity assignment, SQLite persistence, synchronization, retention-policy enforcement, security, signaling orchestration, command handling, and hardware control.

### 17.2 Media Production

- **Snapshot**: generated by DeepStream at detection time as part of its inference pipeline output, written to a filesystem location under `/opt/weapon-detection/snapshots/YYYY/MM/DD/` (ADR-008); the path is passed to the Agent via the same Unix socket detection-metadata message.
- **Continuous recording**: a separate, independent DeepStream sink writing continuously to `/opt/weapon-detection/recordings/YYYY/MM/DD/` (ADR-008), unrelated to individual detection events; retention/cleanup enforcement (FR-REC-002) is the Agent's responsibility (§13.6), acting on files DeepStream produces.
- **WebRTC media**: produced by DeepStream/GStreamer via shared pipeline branching (`tee`) from the same camera ingestion, so one RTSP camera connection is shared across inference, recording, and WebRTC branches rather than opening separate camera connections per consumer. The Agent performs WebRTC *signaling orchestration* (offer/answer/ICE relay, token validation per §15.4) but does not itself produce media.

This keeps the control-plane/data-plane boundary intact: DeepStream produces media files and streams as a mechanical consequence of its pipeline; the Agent owns all lifecycle decisions (retention, sync-gating, cleanup, signaling) about them.

### 17.3 Pipeline Configuration

DeepStream pipeline config (`pipeline/config.txt`, ADR-008) is generated/updated by the Agent's Config Manager from synchronized configuration (confidence threshold, camera URL) and applied on pipeline start/restart (ADR-006).

**Diagrams:** Sequence diagram — Camera → DeepStream (inference + snapshot/recording/WebRTC branch production) → Unix socket metadata → Agent.
**Traceability:** FR-DET-001–004, FR-REC-001–003, ADR-001, ADR-005, ADR-006, ADR-008, ADR-016.

---

## 18. Configuration Management

### 18.1 Distribution

Admin updates configuration via Dashboard → Backend persists to SQL Server (Configuration entity, §13.1) → Agent's periodic `GET /api/v1/config` (authenticated per ADR-002 amended) retrieves updates → Agent writes to `ConfigCache` (SQLite) and to `pipeline/config.txt` where DeepStream-relevant → applied without manual intervention (FR-SYN-005).

### 18.2 Reload

Server-initiated `POST /api/v1/config/reload` (synchronous, ADR-007) forces immediate re-application rather than waiting for the next poll cycle — an operational capability, not a dashboard feature (SRS §3.3 note).

### 18.3 Offline Startup

Per §16.3, `ConfigCache` is the authoritative local source when the Backend is unreachable at startup.

**Diagrams:** Sequence diagram — config push/poll/reload cycle.
**Traceability:** FR-SYN-005/006, NFR-CFG-001.

---

## 19. Live Stream Architecture

### 19.1 End-to-End Flow

Dashboard requests stream (`POST /api/v1/stream/authorize`, JWT) → Backend generates token, registers it with the Agent (`POST /api/v1/stream/authorizations`, device secret) → Backend returns Jetson signaling endpoint + token to browser → browser opens WebRTC signaling to Agent, presents token → Agent validates (§15.4) → the browser establishes a WebRTC media session directly with the media produced by DeepStream's WebRTC branch (§17.2), orchestrated via the Agent's signaling role → Backend never touches media.

### 19.2 Media Source

The WebRTC branch is produced by DeepStream/GStreamer from the live camera feed, shared via pipeline branching with the inference and recording branches (§17.2). One viewer at a time, no TURN server, LAN-only.

### 19.3 Session Lifecycle

Token consumed/invalidated on session acceptance or expiry (§15.4); no persistence of stream sessions across an Agent restart.

**Diagrams:** Sequence diagram — full stream-authorization-to-media flow.
**Traceability:** FR-MON-001, NFR-SEC-004, ADR-003, ADR-014, ADR-016.

---

## 20. Alert Processing Workflow

### 20.1 End-to-End Flow

Camera → DeepStream (inference + snapshot file write, §17.2) → Unix socket metadata (incl. snapshot path, EventId) → Agent's Alert Manager validates threshold (FR-DET-002/003) → Local Persistence Manager writes to `PendingEvents` (SQLite) → immediate upload attempt (`POST /api/v1/alerts` + `/alerts/{id}/snapshot`, Device ID + shared secret) if connected, else buffered → Backend validates, enforces `(DeviceId, EventId)` uniqueness (§13.4), persists Alert + snapshot file (§13.5) → Dashboard displays (FR-DET-007) → Operator acts (Acknowledge/False Positive/Siren/Download, FR-DET-008–012).

### 20.2 Completeness Guarantee

An alert is not marked synchronized on the Jetson until the Backend acknowledges **both** the event metadata and the snapshot (§13.6) — preventing an alert from existing without its required evidence.

**Diagrams:** Sequence diagram, full path; state diagram for alert status (New → Acknowledged/False Positive).
**Traceability:** FR-DET-001–012, FR-SYN-002–004.

---

## 21. Command Processing Workflow

Dashboard → Backend (JWT-validated) → CommandDispatchService → Agent Command API (`X-Device-Secret`) → Agent validates secret (rejects otherwise, BR-008/NFR-SEC-003) → executes synchronously → result returned in the same HTTP response → Backend logs the result for operational auditing (SRS §8.3). Restart Agent is the sole exception: Agent returns HTTP 200 acknowledging acceptance, schedules a delayed self-restart, then exits; systemd restarts the Agent process.

**Diagrams:** Realized by §11.1 scenarios 3–4 (cross-referenced, not repeated).
**Traceability:** FR-DET-010–012, BR-008, NFR-SEC-003, ADR-002 (amended), ADR-007.

---

## 22. Offline Resilience and Synchronization

On connectivity loss, the Agent continues local detection, logging, recording, and event buffering into `PendingEvents` unchanged (FR-SYN-001/002); an already-active siren continues per local behavior/safety policy; new remote siren commands are unavailable. Configuration continues from `ConfigCache` (§16.3/§18.3). On reconnect, the Backend Sync Client drains `PendingEvents` in timestamp order, submitting event metadata and snapshot together (§20.2); the Backend's `(DeviceId, EventId)` uniqueness constraint (§13.4) makes retried submissions safe; original detection timestamps are preserved (FR-SYN-004). No data loss occurs across an outage (NFR-REL-002), bounded by available local storage (ASM-007).

**Diagrams:** Realized by §11.1 scenario 5 and §13.4/§13.6 (cross-referenced).
**Traceability:** FR-SYN-001–006, NFR-REL-001–002, ASM-004/007.

---

## 23. Error Handling and Recovery

| Failure | Handling |
|---------|----------|
| Backend↔Agent network failure | Caller-side timeout/retry with backoff; Agent falls back to offline behavior (§22) |
| DeepStream subprocess exit | Detected by Pipeline Supervisor, surfaced as unhealthy pipeline state; recovery via Restart DeepStream command; automatic restart remains optional/best-effort, not guaranteed (QAS-7, ADR-006) |
| Malformed/invalid command | Rejected by the Agent's Command API before execution |
| Invalid Activation Key / shared secret | Rejected before business logic executes (§15.1, BR-008) |
| SQL Server unavailable | Backend returns an error envelope (§14.3); Dashboard surfaces failure; no local Backend buffering is architected in this prototype |
| Upload succeeds partially (metadata without snapshot, or vice versa) | Prevented by design per §20.2's completeness guarantee |

Exact retry counts, backoff intervals, and timeout values are Feature Specification detail, not structural architecture.

**Traceability:** NFR-REL-001–002, ADR-006.

---

## 24. Logging, Monitoring & Diagnostics

- **Backend**: structured application logs (framework-standard ASP.NET Core logging), including command-dispatch results (§21) for audit/troubleshooting (SRS §8.3).
- **Agent**: local log files under `/opt/weapon-detection/logs/` (ADR-008); log lines include DeviceId and, where applicable, EventId as a lightweight correlation mechanism — no distributed tracing infrastructure is introduced.
- **Health monitoring**: realized structurally by the existing `HealthRecord` entity and heartbeat flow (§13.1, FR-HLT-001–005).
- **Credential redaction**: Activation Keys, shared secrets, JWTs, and stream tokens are never written to logs on either tier (§15.6).
- **Log retention**: rotation/pruning thresholds are deferred to Feature Specifications.

**Traceability:** FR-HLT-001–005, §15.6.

---

## 25. Technology Stack and Rationale

| Layer | Technology | Rationale |
|-------|------------|--------------|
| Edge control plane | FastAPI (Python) | ADR-001 — async-native orchestration fit |
| Edge data plane | NVIDIA DeepStream, TensorRT, YOLO26 | Charter §4/§5 — fixed by project mandate |
| Edge process supervision | Direct subprocess management (no systemd for DeepStream) | ADR-006 |
| Edge persistence | SQLite | ADR-004 |
| Edge IPC | Unix domain socket | ADR-005 |
| Backend | ASP.NET Core (C#) | Charter §4, CON-006 |
| Backend persistence | SQL Server | Charter §4, CON-006 |
| Dashboard | Angular | Charter §4, CON-006 |
| Inter-service protocol | REST, `/api/v1`, uniform envelope (documented binary/signaling exceptions) | ADR-009 |
| Streaming | WebRTC | ADR-003 |
| Auth | JWT (Dashboard↔Backend); Device ID + shared secret (Agent↔Backend, bidirectional) | ADR-002 (amended) |

**Traceability:** CON-006 (corrected).

---

## 26. Architectural Decisions (ADR Summary)

| ADR | Title | Decision |
|-----|-------|----------|
| ADR-001 | Selection of FastAPI for the Jetson Agent | The Jetson Agent is implemented using FastAPI rather than Flask. Context: the Charter identified a Flask-based Agent as an initial implementation assumption; the Agent's responsibilities were refined into a full edge orchestration service (DeepStream lifecycle management, bidirectional REST communication, heartbeats, configuration sync, offline sync, hardware control, command processing, detection handling). Rationale: FastAPI's async-native request handling, structured data validation, and maintainable API architecture better suit an orchestration service. Consequences: no change to scope or requirements; improved maintainability; clearer control-plane/data-plane separation. This corrects CON-006 and SRS §1.2 (originally "Flask-based"), and is documented as a deliberate, explainable deviation from Charter §4/§6/§12's original wording (retained there as historical). |
| ADR-002 | Security Architecture (amended) | JWT Bearer for Dashboard↔Backend (username/password login, no refresh tokens). Activation Key for one-time Agent activation, after which the Backend issues a persistent Device ID and shared secret. The same shared secret is used bidirectionally: Agent→Backend headers (`X-Device-Id`, `X-Device-Secret`) authenticate all ongoing operational requests, validated by the Backend; Backend→Agent requests authenticate commands, validated by the Agent (satisfying NFR-SEC-003/BR-008). No OAuth/OIDC, mTLS, certificates, HMAC signing, refresh tokens, or key rotation. HTTP is accepted only because the prototype is restricted to the trusted local network (CON-005); HTTPS and stronger credential-lifecycle controls are future work (§28). This amendment resolved a documented gap and required a corresponding SRS correction to NFR-SEC-002. |
| ADR-003 | Live Streaming Protocol | WebRTC for browser playback, direct browser↔Jetson connection; the Backend authorizes and returns connection info only and is never in the media path. RTSP may still be used internally for DeepStream inference. No TURN server, LAN-only, no adaptive bitrate, no recording through WebRTC, one viewer at a time. |
| ADR-004 | Local Persistence | SQLite as the Jetson Agent's local persistent storage for unsynchronized detection events, snapshot/recording file references, pending-sync status, current device configuration, persistent Device ID, and shared secret. Binary images/video remain on the filesystem; SQLite stores metadata and paths only. Chosen for being lightweight, embedded, ACID-compliant, and appropriate for a single-device prototype; a fleet-scale alternative is future work. |
| ADR-005 | Agent↔DeepStream IPC | A local Unix Domain Socket connects the DeepStream process (data plane) and the FastAPI Jetson Agent (control plane). DeepStream serializes detection metadata (JSON) and sends it via the socket; the Agent owns all business logic after receipt. Chosen over a message broker (e.g., MQTT) to avoid an unnecessary running component while still providing low-latency, event-driven communication between co-located processes. |
| ADR-006 | DeepStream Process Supervision | The FastAPI Agent directly supervises the DeepStream process (start/stop/restart/monitor); systemd is not used for DeepStream lifecycle management, only for the Agent process itself. This makes Restart Pipeline trivial and lets Restart Agent rely on systemd restarting the Agent. Automatic restart-on-crash is optional/best-effort, not a guaranteed behavior in this prototype. |
| ADR-007 | Command Dispatch Pattern | Synchronous execution for Trigger/Stop Siren, Reload Configuration, and Restart DeepStream — no command queue, async job IDs, polling, or callbacks. Restart Agent is special-cased: it returns an HTTP 200 acceptance response, then spawns a delayed restart and exits, since the connection would otherwise die before a normal response could be returned. |
| ADR-008 | Filesystem Layout | A standardized `/opt/weapon-detection/` layout on the Jetson (`config/`, `database/`, `snapshots/YYYY/MM/DD/`, `recordings/YYYY/MM/DD/`, `logs/`, `runtime/`, `models/`, `pipeline/`). SQLite stores metadata, snapshot paths, and recording paths — never binary images or video. |
| ADR-009 | API Design | Both REST APIs (Backend, Jetson Agent) use `/api/v1` versioning and a uniform JSON response envelope (`{success, message, data}` / `{success, message, errorCode}`), with documented exceptions for binary snapshot responses, multipart uploads, and WebRTC signaling payloads. |
| ADR-010 | Jetson Agent Process Model | The Jetson Agent runs as one FastAPI application under a single Uvicorn worker. Pipeline supervision, Unix-socket detection ingestion, heartbeat reporting, backend synchronization, configuration polling, and command handling run as coordinated asynchronous tasks within that process. Multiple Uvicorn workers are not used because they would duplicate singleton device-level responsibilities. DeepStream remains a separate, Agent-supervised OS subprocess, communicating exclusively via the Unix domain socket. |
| ADR-011 | Backend Snapshot Storage | Uploaded snapshot images are stored on the Server Host filesystem under an application-managed directory; SQL Server stores metadata and the filesystem reference only. No SQL Server BLOB storage or external object storage is used in this prototype. |
| ADR-012 | Event Idempotency | Detection events are assigned a Jetson-generated `EventId`. The Backend enforces a unique `(DeviceId, EventId)` constraint on the Alert table so that retried offline synchronization cannot create duplicate alerts. |
| ADR-013 | Dashboard Session Revocation | JWTs carry a unique session identifier (`jti`) and expiry; the Backend maintains an `AdminSession` revocation record in SQL Server. Protected requests require both a valid JWT and a non-revoked session record. Logout marks the session revoked. No refresh-token flow is introduced. |
| ADR-014 | WebRTC Stream Token Mechanism | The Backend generates a cryptographically random, short-lived, single-use stream token scoped to a Device ID, registers it with the target Agent via the device shared secret, and returns the token and signaling endpoint to the browser. The Agent validates and consumes the token during WebRTC signaling, holding pending tokens in memory only (no persistence across Agent restarts required). No JWTs, certificates, OAuth, or HMAC signing are used for this mechanism. |
| ADR-015 | Device Reactivation Policy | Regenerating a branch's Activation Key invalidates the previous unused key. Successful reactivation retains the device's persistent Device ID but issues and atomically replaces the shared secret, invalidating the previous one. Historical alerts/health records remain correlated to the retained Device ID. Creating an entirely separate device identity is future fleet-management scope. |
| ADR-016 | Media Production Ownership | DeepStream/GStreamer (data plane) performs all mechanical video-processing work: RTSP ingestion, decoding, preprocessing, inference, snapshot generation, segmented recording, and WebRTC media production, sharing one RTSP camera connection across these outputs via pipeline branching. The Jetson Agent (control plane) owns supervision, validation, identity, persistence, synchronization, retention-policy enforcement, security, signaling orchestration, command handling, and hardware control. This preserves the control-plane/data-plane boundary established in ADR-001 while resolving the previously deferred question of which process physically produces media files/streams. |

---

## 27. Traceability to the SRS

| SRS ID(s) | Realized In (ARCH-001) |
|-----------|---------------------------|
| FR-AUT-001–003 | §15 (JWT, login/logout, session revocation) |
| FR-BRN-001–007 | §16 (activation/reactivation lifecycle), §13.1 (Device/Camera entities) |
| FR-DET-001–004 | §17 (inference, snapshot production), §20 (alert creation) |
| FR-DET-005–009 | §20 (upload, status transitions), §10.1 (Domain lifecycle rules) |
| FR-DET-010–012 | §21 (siren commands), §14.2 (Agent command endpoints) |
| FR-MON-001 | §19 (Live Stream Architecture) |
| FR-HLT-001–005 | §13.1 (HealthRecord), §24 (health/heartbeat framing) |
| FR-SYN-001–006 | §22 (Offline Resilience and Synchronization), §16.3/§18 (config offline behavior) |
| FR-REC-001–003 | §17.2 (recording production), §13.6 (retention/cleanup) |
| FR-RPT-001–004 | §13.1 (Alert/HealthRecord as report source), §14.1 (`/reports` endpoint), §10.5 (Dashboard Reports module) |
| NFR-PRF-001–002 | §4 (driver), §5 (QAS-5), §17 (inference data plane) |
| NFR-REL-001–002 | §22, §5 (QAS-1) |
| NFR-SEC-001–004 | §15 (Security Architecture, consolidated) |
| NFR-MNT-001 | §6 (Architectural Style), §10 (Component Architecture) |
| NFR-USB-001 | Primarily a UI-layout concern realized at Feature Specification level for the Alerts module (§10.5); no additional architectural mechanism required |
| NFR-TST-001 | §2 (Testability goal); satisfied structurally by component isolation (§10) |
| NFR-CFG-001 | §18 (Configuration Management) |
| BR-001 | §7.1 (single Admin User actor), Vision §4 |
| BR-002–003 | §16 (activation/reactivation) |
| BR-004 | §10.1 (Domain-layer lifecycle rule) |
| BR-005 | §13.1 (HealthRecord distinct from Alert) |
| BR-006 | §13.5, §17.2 (recordings Jetson-only) |
| BR-007 | Vision §5.2 workflow, realized via Dashboard status-transition operations (§14.1); not a distinct architectural mechanism |
| BR-008 | §15.1, §21 (command-source validation) |
| CON-001–005, CON-007 | §3 (inherited constraints); scope/timeline, not structurally realized |
| CON-006 | §9, §25 (corrected technology stack) |
| ASM-001–007 | §3 (inherited assumptions) |
| ASM-008 | §16 (Device ID/address resolution) |

---

## 28. Risks, Trade-offs, and Future Considerations

### 28.1 Architecture-Level Risks

| Risk | Trade-off Accepted |
|------|----------------------|
| Single point of failure at the co-located Server Host (Backend + SQL Server) | Accepted for a five-week, single-device prototype (ARCH-ASM-001); production would separate/replicate these. |
| No message queue for command delivery reliability | Synchronous dispatch (ADR-007) is simpler but means a command fails outright if the Agent is briefly unreachable, rather than retrying via a queue. |
| No production-grade transport security (HTTP only) | Accepted under the trusted-LAN assumption (CON-005); credentials are interceptable on the LAN in principle. |
| No guaranteed automatic DeepStream crash recovery | Best-effort only (QAS-7, ADR-006); a crashed pipeline requires an operator-issued Restart DeepStream command. |
| Single-viewer, no-TURN WebRTC | Sufficient for demonstration; not viable for concurrent multi-operator viewing or off-LAN access. |
| SQLite/single-device local persistence | Not fleet-scalable; a future multi-device architecture would need a different edge persistence strategy. |
| Backend has no resilience to its own SQL Server outage | Acceptable for a prototype with one operator and one demo environment. |

### 28.2 Future Considerations

Consistent with Vision §10 (Future Vision) and Charter §7 (Out of Scope), not part of this prototype's realized design:

- HTTPS, mTLS, OAuth/OIDC, certificate-based authentication, and shared-secret/key rotation.
- TURN server support and adaptive bitrate streaming for WebRTC, enabling off-LAN and multi-viewer access.
- Replacing SQLite with a fleet-appropriate edge persistence mechanism for multi-device deployments.
- A message-queue-based command delivery model for improved reliability at scale.
- Distinct user roles and permission levels (System Administrator vs. Security Operator) replacing the single Admin login.
- Multi-branch, multi-camera-per-branch, and fleet-scale device management.
- Remote software/model update mechanisms and advanced device diagnostics.

The dissertation prototype intentionally limits scope to validate the core architecture and workflow; future enhancements are expected to build on this foundation rather than change its fundamental design (Vision §10).

---

*End of Software Architecture Document (ARCH-001).*
