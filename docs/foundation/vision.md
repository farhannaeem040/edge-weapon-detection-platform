# Vision Document

| Field | Value |
|-------|-------|
| Document ID | VIS-001 |
| Version | 1.0 |
| Status | Draft |
| Project | Edge-Based Weapon Detection and Centralized Monitoring System Using NVIDIA Jetson and DeepStream |
| Owner | Farhan Naeem |
| Reviewers | Farhan Naeem, ChatGPT, Claude Code |
| Related Document | Project Charter (PC-001) |

---

## 1. Document Purpose

This document defines the product vision for the Edge-Based Weapon Detection and Centralized Monitoring System. It builds upon the Project Charter (PC-001) by describing the intended users, their workflows, and the core capabilities the system must provide.

The Vision document bridges the Project Charter and the Software Requirements Specification. It describes the real-world operational vision for the product while clearly identifying which parts of that vision are implemented in the dissertation prototype and which are considered future work.

This document does not define technical architecture or implementation details. Those are addressed in the Architecture and Software Requirements Specification documents.

---

## 2. Product Vision Statement

To provide security organizations with an edge-based AI monitoring platform that detects weapons in real time at the point of capture, and centrally presents these events to operators so that security incidents can be reviewed and responded to quickly, without depending on continuous human observation of every camera feed.

---

## 3. Background and Motivation

Traditional surveillance systems rely on human operators continuously watching video feeds to identify threats. As the number of monitored cameras grows, this approach becomes unreliable due to operator fatigue, delayed reaction times, and the practical difficulty of watching many streams simultaneously.

Advances in edge computing and deep learning make it possible to perform real-time video analytics directly on embedded hardware such as the NVIDIA Jetson platform. Performing detection at the edge avoids the latency, bandwidth demands, and privacy concerns of sending continuous video to the cloud, and allows detection to continue even during network interruptions.

This project is motivated by the need for an integrated platform that combines local, real-time AI detection with centralized visibility, configuration, and alert handling, rather than treating detection and monitoring as separate, disconnected concerns.

---

## 4. Target Users and Personas

The product is designed around two conceptual user roles. For the dissertation prototype, both roles are accessed through a single Admin login; the distinction below describes the real-world responsibilities each role represents.

### 4.1 System Administrator

Responsible for the operational setup and health of the platform.

- Registers and manages branches.
- Configures Jetson devices and cameras.
- Monitors device and pipeline health across branches.
- Reviews historical reports and system status.

### 4.2 Security Operator

Responsible for reviewing and responding to security events as they occur.

- Monitors incoming weapon detection alerts.
- Views live camera streams, both in response to alerts and for routine surveillance.
- Reviews alert evidence and determines whether it represents a genuine threat.
- Acknowledges alerts, marks false positives, and can trigger a siren for escalation.

In a real-world deployment, these would typically be distinct individuals with different access levels. In the dissertation prototype, both sets of responsibilities are performed by the same authenticated Admin user; multiple user roles and permission separation are considered future work.

---

## 5. User Workflows

### 5.1 Branch and Device Onboarding (System Administrator)

1. The administrator creates a new branch in the dashboard, providing branch name, address, contact details, camera RTSP URL(s), and other branch-specific configuration.
2. The server generates a unique Activation Key for the branch.
3. An installer installs the Jetson device at the branch and configures the Jetson Agent with the Activation Key.
4. On first startup, the Jetson Agent authenticates with the server using the Activation Key and receives its full configuration.
5. The Jetson Agent stores the configuration locally and starts the DeepStream detection pipeline.
6. The Jetson device is not able to self-register; branch and device onboarding is always initiated by the System Administrator.

### 5.2 Weapon Detection and Alert Response (Security Operator)

1. The Jetson device detects a weapon (gun or knife) using the local DeepStream/YOLO26 pipeline and generates an alert.
2. The alert is transmitted to the central server and appears on the operator's dashboard in a "New" state.
3. The operator reviews the alert, which includes: branch name, camera name, detection timestamp, weapon type, confidence score, a captured snapshot, access to the live camera stream, and current alert status.
4. Based on this evidence, the operator either:
   - Acknowledges the alert as a genuine security incident, or
   - Marks the alert as a False Positive.
5. If escalation is required, the operator can trigger a remote siren on the Jetson device, and can stop the siren once escalation is no longer needed.
6. The operator can download the snapshot for reporting or evidence purposes.
7. The AI system assists by detecting potential threats; the final security decision is always made by the operator.

### 5.3 Routine Monitoring (Security Operator)

Independent of any active alert, the operator can select any registered branch at any time to view its live camera stream, allowing the dashboard to serve as a general-purpose monitoring tool in addition to an alert-response tool.

### 5.4 Device Health Monitoring (System Administrator)

1. The Jetson Agent periodically sends health information to the server (planned interval: every 10 minutes), including online status, camera connection status, and DeepStream pipeline status.
2. The administrator views the health status of all registered branches and devices, including online/offline state and last heartbeat time.
3. If a heartbeat is not received within the expected window, the device is marked offline and a separate health/system notification is generated, distinct from a weapon-detection alert.

### 5.5 Offline Operation and Recovery

1. If connectivity between the Jetson device and the central server is lost, local detection, logging, recording, and siren activation continue uninterrupted.
2. Detections generated during the outage are stored locally by the Jetson Agent, including timestamp, weapon type, confidence score, camera information, captured image, recording reference, and event identifier.
3. The dashboard does not show these alerts in real time while the device is disconnected.
4. Once connectivity is restored, the Jetson Agent synchronizes stored events to the server, which preserves the original detection timestamp so the events appear correctly in historical records.

### 5.6 Reporting (System Administrator / Security Operator)

1. Users can view a historical log of weapon detection alerts, filterable by branch, date/time range, weapon type, and alert status.
2. Each historical alert record includes detection timestamp, branch, camera, weapon type, confidence score, captured image, and the final operator decision.
3. Basic device health history (online/offline history, last heartbeat) is also available.
4. Basic summary counts (e.g., total detections, total false positives) may be included where they can be provided without significant added complexity.

---

## 6. Core System Capabilities

- Local, real-time weapon detection (gun and knife) at the edge using a fine-tuned YOLO26 model on NVIDIA DeepStream/TensorRT.
- Continued local detection, logging, recording, and siren activation during network outages.
- Store-and-forward synchronization of offline-generated events to the central server, preserving original timestamps.
- Administrator-driven branch and device registration using a server-issued Activation Key.
- Centralized alert management with a simple lifecycle: New → Acknowledged or False Positive.
- Access to live camera streams both for alert verification and routine surveillance.
- Remote siren activation and deactivation for escalation.
- Device and pipeline health monitoring, distinct from weapon-detection alerts.
- Historical, filterable reporting on alerts and device health.

---

## 7. Prototype Scope

The dissertation prototype will demonstrate the complete workflow below, end to end:

Camera stream → DeepStream pipeline on Jetson → YOLO26 weapon detection → Jetson Agent communication → central server processing → Angular dashboard alert → operator review and action.

The prototype includes:

- One NVIDIA Jetson Orin Nano, one primary RTSP camera.
- A single Admin login representing both conceptual user roles.
- Branch and device onboarding via Activation Key.
- Alert generation, review, acknowledgment, false-positive marking, snapshot download, and remote siren trigger and stop.
- Routine live-stream monitoring independent of alerts.
- Device health monitoring (online/offline status, last heartbeat, pipeline and camera status).
- Store-and-forward offline event synchronization.
- Historical, filterable alert and health reporting with basic summary counts.
- Local network deployment.

---

## 8. Out-of-Scope Items

The following are outside the scope of the dissertation prototype and are not addressed by this Vision document beyond being noted as future direction:

- Multi-tenant deployment.
- Cloud-based inference.
- Automatic model retraining.
- Multiple distinct user roles/permission levels (beyond the single Admin login).
- Multiple Jetson devices per branch.
- Automatic software or model updates.
- Mobile applications.
- High-availability deployment.
- Distributed microservices architecture.
- Incident case management, external notifications (e.g., police, branch management), and alert states beyond Acknowledged/False Positive.
- Advanced analytics, predictive trends, or security risk scoring.
- Advanced remote device management (automatic recovery, remote updates, hardware diagnostics).

---

## 9. Success Criteria

Success for this project is defined primarily by demonstrating a complete, well-engineered, end-to-end system rather than by AI model accuracy alone. The prototype will be considered successful if it demonstrates:

- The full workflow operating successfully: camera stream → DeepStream pipeline → YOLO26 detection → Jetson Agent communication → server processing → dashboard alert → operator review and action.
- Real-time detection capability for the selected weapon classes (gun and knife) with acceptable inference performance on the Jetson device.
- Reliable communication between edge and server, including correct behavior during and after network interruptions.
- An effective alert generation and operator review/response workflow.
- Clear separation of concerns between AI, edge processing, backend, and frontend components.
- Requirements-driven development with documented design decisions.
- A maintainable, testable software architecture, evaluated as a complete system rather than as isolated components.

---

## 10. Future Vision

Beyond the dissertation prototype, the platform is intended to serve as the foundation for a scalable Edge AI security monitoring product. Potential future directions include:

- Support for multiple branches, each with multiple cameras.
- Fleet-scale deployment and management of many Jetson edge devices.
- Expansion of AI detection capabilities to additional security-related threats or objects.
- Integration with existing physical security infrastructure such as alarm systems, access control, or security operations platforms.
- More advanced analytics and reporting capabilities.
- Improved device management, including remote configuration, software updates, and model updates.
- Distinct user roles and permission levels reflecting the real-world separation between System Administrators and Security Operators.

The long-term objective is not limited to demonstrating a weapon detection model, but to establish an architecture in which AI-powered security analysis can be performed at the edge while being centrally managed. The dissertation prototype intentionally limits implementation scope to validate this architecture and workflow; future enhancements are expected to build upon this foundation rather than change its fundamental design.
