# Project Charter

| Field | Value |
|-------|-------|
| Document ID | PC-001 |
| Version | 1.0 |
| Status | Draft |
| Project | Edge-Based Weapon Detection and Centralized Monitoring System Using NVIDIA Jetson and DeepStream |
| Owner | Farhan Naeem |
| Reviewers | Farhan Naeem, ChatGPT, Claude Code |
| Last Updated | 11 July 2026 (technology-reference correction only — see §4, §6, §12) |

---

# 1. Executive Summary

This project aims to design, develop, deploy, and evaluate an edge-based weapon detection platform capable of detecting handguns and knives in real time using NVIDIA Jetson edge devices.

The solution combines Artificial Intelligence, Edge Computing, Computer Vision, and modern web technologies to create a centralized monitoring platform suitable for security environments such as bank branches.

Each branch will contain an NVIDIA Jetson Orin Nano connected to one or more RTSP camera streams. A fine-tuned YOLO26 model will perform local inference using NVIDIA DeepStream and TensorRT, allowing weapon detection without relying on cloud-based inference.

Whenever a weapon is detected, the Jetson device will generate an alert and communicate with a centralized ASP.NET Core server through REST APIs. The server will maintain branch information, device health, alerts, recordings, and configuration management while providing operators with a web dashboard developed using Angular.

The project will be implemented as a Software Engineering dissertation prototype focusing on system architecture, maintainability, deployment, and integration rather than solely on AI model development.

---

# 2. Background

Modern organizations deploy large numbers of surveillance cameras to improve physical security. However, monitoring multiple video streams continuously is a difficult task for human operators.

As the number of cameras increases, operators become more susceptible to fatigue, delayed responses, and missed incidents. Traditional CCTV systems generally rely on human observation before any action can be taken.

Recent advances in edge computing and deep learning make it possible to perform intelligent video analytics directly on embedded hardware. NVIDIA Jetson devices provide GPU acceleration that enables real-time inference while avoiding the latency, bandwidth requirements, and privacy concerns associated with cloud-based processing.

NVIDIA DeepStream further accelerates AI inference by providing an optimized GPU-based video analytics pipeline capable of processing RTSP streams efficiently.

These technologies provide an opportunity to build intelligent surveillance systems capable of assisting security personnel by automatically identifying potential threats and notifying operators in real time.

---

# 3. Problem Statement

Traditional surveillance systems depend primarily on human operators continuously monitoring video feeds.

This approach introduces several challenges:

- Human fatigue during continuous monitoring.
- Delayed identification of potential threats.
- Difficulty monitoring multiple cameras simultaneously.
- Increased response time during security incidents.
- Limited centralized visibility across multiple locations.

Although modern AI models are capable of detecting weapons with high accuracy, many existing solutions rely heavily on cloud infrastructure or provide only isolated detection models without offering a complete operational platform.

There is therefore a need for an integrated edge-based solution capable of performing real-time detection locally while providing centralized monitoring, device management, configuration, and alert handling.

---

# 4. Proposed Solution

The proposed solution is an integrated Edge AI surveillance platform consisting of edge devices deployed at remote branches and a centralized monitoring server.

Each branch will contain an NVIDIA Jetson Orin Nano running:

- A Flask-based Jetson Agent *(original technology proposal at project initiation; superseded by FastAPI — see Architecture Decision 1 / ADR-00X in ARCH-001)*
- NVIDIA DeepStream
- A fine-tuned YOLO26 TensorRT engine
- Local recording and logging services

The Jetson Agent will:

- Load configuration files.
- Connect to the central server.
- Receive branch configuration.
- Start the DeepStream pipeline.
- Monitor camera streams.
- Detect weapons locally.
- Generate alerts.
- Send health information periodically.
- Upload recordings and operational logs.

The centralized server will provide:

- User authentication.
- Branch management.
- Device registration.
- Configuration management.
- Health monitoring.
- Alert management.
- Camera viewing.
- Reporting.
- Dashboard visualization.

Communication between Jetson devices and the server will use secure REST APIs.

The overall solution will continue detecting weapons locally even if temporary network connectivity to the central server is lost.

---

# 5. Project Objectives

The objectives of this project are:

- Design a complete Edge AI weapon detection platform.
- Fine-tune a YOLO26 model for handgun and knife detection.
- Convert the trained model into a TensorRT engine.
- Deploy the model using NVIDIA DeepStream.
- Develop a Jetson Agent responsible for device communication and management.
- Develop an ASP.NET Core backend for centralized management.
- Develop an Angular dashboard for operators.
- Monitor Jetson health and branch status.
- Generate and manage weapon detection alerts.
- Demonstrate a fully functional end-to-end prototype.

---

# 6. Project Scope

The prototype includes:

## AI

- Fine-tuned YOLO26 model
- Gun detection
- Knife detection

## Edge Device

- NVIDIA Jetson Orin Nano
- DeepStream inference
- TensorRT engine
- FastAPI Agent *(corrected from "Flask Agent" — controlled baseline alignment per Architecture Decision 1 / ADR-00X)*

## Backend

- ASP.NET Core REST API
- SQL Server

## Dashboard

- Authentication
- Dashboard
- Branch Management
- Alert Management
- Device Health
- Camera Monitoring
- Reports

## Deployment

- Local network deployment
- One Jetson device
- One RTSP camera (Proof of Concept)

---

# 7. Out of Scope

The following are outside the scope of this dissertation prototype:

- Multi-tenant deployment.
- Cloud-based inference.
- Automatic model retraining.
- Multiple user roles.
- Multiple Jetson devices per branch.
- Automatic software updates.
- Mobile applications.
- High-availability deployment.
- Distributed microservices.

These items may be considered as future work.

---

# 8. Stakeholders

| Stakeholder | Responsibility |
|-------------|---------------|
| Dissertation Student | Design, implementation, testing, documentation |
| Academic Supervisor | Project supervision and evaluation |
| Security Operator | Monitor alerts and respond to incidents |
| System Administrator | Configure branches and manage devices |

---

# 9. Assumptions

- NVIDIA Jetson Orin Nano hardware is available.
- RTSP video streams are available.
- The prototype operates on a local network.
- Required datasets are available from Roboflow.
- TensorRT conversion is supported for the selected model.
- Internet connectivity may be intermittent but local inference continues.

---

# 10. Constraints

- Five-week implementation period.
- Dissertation prototype only.
- Limited hardware resources.
- Single developer.
- One Jetson device available for testing.
- One primary camera used during proof-of-concept.

---

# 11. Risks

| Risk | Mitigation |
|------|------------|
| Model accuracy is lower than expected | Combine multiple datasets and fine-tune the model |
| TensorRT conversion issues | Validate the model before deployment |
| Jetson performance limitations | Optimize DeepStream pipeline |
| Network interruptions | Continue local inference and synchronize later |
| Time limitations | Prioritize core functionality over optional features |

---

# 12. Deliverables

- Fine-tuned YOLO26 model
- TensorRT inference engine
- NVIDIA DeepStream pipeline
- FastAPI Jetson Agent *(corrected from "Flask Jetson Agent" — controlled baseline alignment per Architecture Decision 1 / ADR-00X)*
- ASP.NET Core backend
- Angular dashboard
- SQL Server database
- Engineering documentation
- GitHub repository
- Dissertation report
- Demonstration video

---

# 13. Success Criteria

The project will be considered successful if it can:

- Detect guns and knives in real time.
- Execute inference locally on the Jetson.
- Generate alerts for detected weapons.
- Display alerts on the dashboard.
- Monitor Jetson health.
- Register and manage branches.
- Maintain operation during temporary server disconnection.
- Demonstrate a complete end-to-end workflow from detection to operator notification.

---

# 14. Project Milestones

| Milestone | Description |
|-----------|-------------|
| M1 | Engineering Foundation |
| M2 | Project Documentation |
| M3 | Dataset Preparation |
| M4 | YOLO26 Fine-Tuning |
| M5 | TensorRT Conversion |
| M6 | DeepStream Integration |
| M7 | Jetson Agent Development |
| M8 | ASP.NET Core Backend |
| M9 | Angular Dashboard |
| M10 | Integration & Testing |
| M11 | Dissertation Completion |