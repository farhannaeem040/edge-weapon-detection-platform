# Edge-Based Weapon Detection and Centralized Monitoring System

## Overview

This repository contains the source code and documentation for a Software Engineering dissertation focused on designing and implementing an edge-based weapon detection platform.

The platform combines AI-powered weapon detection running on NVIDIA Jetson devices with a centralized web application for monitoring, configuration, and alert management.

The project follows a Spec-Driven Development workflow and is developed using Claude Code as an AI engineering assistant.

---

## Objectives

- Detect guns and knives using a fine-tuned YOLO26 model.
- Perform inference locally using NVIDIA DeepStream.
- Provide centralized monitoring through a web dashboard.
- Support branch registration and remote configuration.
- Monitor Jetson device health.
- Generate and manage weapon detection alerts.
- Demonstrate a complete end-to-end prototype.

---

## Technology Stack

### AI

- YOLO26
- NVIDIA DeepStream
- TensorRT

### Edge Device

- NVIDIA Jetson Orin Nano

### Backend

- ASP.NET Core

### Frontend

- Angular

### Database

- SQL Server

---

## Repository Structure

Documentation and specifications are located in the `docs` and `specs` directories.

Implementation source code is organized into independent modules for the server, dashboard, Jetson agent, AI pipeline, and supporting tools.

---

## Development Methodology

This project follows Spec-Driven Development.

Features are specified before implementation.

Claude Code is used to assist with planning, implementation, testing, and code review.